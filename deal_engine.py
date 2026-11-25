import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait, ALL_COMPLETED
from datetime import datetime, timezone
import requests
from sqlalchemy.exc import OperationalError, DBAPIError
from config import Config
from models import db, WatchlistItem, VendorPrice, SystemSetting, User, MicrocenterItem, ActivityLog
from card_utils import fix_mojibake
from providers import (
    ScryfallProvider,
    MightyMeepleProvider,
    EbayProvider,
    MicrocenterProvider,
    TCGplayerSealedProvider,
    CardKingdomSealedProvider,
    CardKingdomSinglesProvider,
    StarCityGamesProvider,
    CoolStuffIncProvider,
    ShopifyCatalogProvider,
)
from providers.sealed_trust import get_blocked_shops, get_trusted_shops

logger = logging.getLogger(__name__)


class DealEngine:
    """Core price aggregation and deal evaluation engine for Chimaera."""

    def __init__(self, app=None):
        self.app = app
        self.scryfall = ScryfallProvider()
        self.mightymeeple = MightyMeepleProvider()
        self.ebay = EbayProvider()
        self.tcgplayer_sealed = TCGplayerSealedProvider()
        self.cardkingdom_sealed = CardKingdomSealedProvider()
        self.cardkingdom_singles = CardKingdomSinglesProvider()
        self.starcitygames = StarCityGamesProvider()
        self.coolstuffinc = CoolStuffIncProvider()
        self.shopify_catalog = ShopifyCatalogProvider()
        self.microcenter = MicrocenterProvider(
            store_id=Config.MICROCENTER_STORE_ID,
            store_name=Config.MICROCENTER_STORE_NAME,
        )

    @staticmethod
    def _upsert_vendor_price(
        watchlist_id: int,
        vendor_name: str,
        price: float,
        condition: str,
        in_stock: bool,
        product_url: str | None,
        search_url: str | None,
        now: datetime,
    ) -> None:
        existing = VendorPrice.query.filter_by(
            watchlist_id=watchlist_id,
            vendor_name=vendor_name,
        ).first()
        if existing:
            existing.price = float(price)
            existing.condition = (condition or "Unopened")[:20]
            existing.in_stock = bool(in_stock)
            existing.product_url = product_url
            existing.search_url = search_url or product_url
            existing.last_checked = now
        else:
            db.session.add(VendorPrice(
                watchlist_id=watchlist_id,
                vendor_name=vendor_name[:50],
                price=float(price),
                condition=(condition or "Unopened")[:20],
                in_stock=bool(in_stock),
                product_url=product_url,
                search_url=search_url or product_url,
                last_checked=now,
            ))

    def poll_sealed_item(self, item: WatchlistItem, notify: bool = True, fast_mode: bool = False) -> dict:
        """
        Polls live prices for a watched sealed MTG product across:
          1. TCGplayer (lowest landed price + official market baseline)
          2. Card Kingdom (public sealed pricelist)
          3. Shopify Global Catalog / Shop app (trusted + unverified offers with scam filter)
        """
        item_id = item.id
        item_name = item.name
        pid = item.tcgplayer_product_id
        existing_market = item.tcgplayer_market_price

        logger.info(f"Polling sealed prices for: {item_name} (productId={pid})")

        tcg_data = None
        if pid:
            try:
                tcg_data = self.tcgplayer_sealed.poll_sealed_product(
                    product_id=pid,
                    product_name=item_name,
                    english_only=True,
                )
            except Exception as err:
                logger.warning(f"Error querying TCGplayer sealed product {pid} ({item_name}): {err}")

        effective_market = (
            tcg_data.get("market_price")
            if (tcg_data and tcg_data.get("market_price"))
            else existing_market
        )

        # Read feature flags & trusted/blocked lists on the main thread (within app context)
        ck_enabled = SystemSetting.get_bool("sealed_cardkingdom_enabled", default=True)
        scg_enabled = SystemSetting.get_bool("sealed_starcitygames_enabled", default=True)
        csi_enabled = SystemSetting.get_bool("sealed_coolstuffinc_enabled", default=True)
        shop_enabled = SystemSetting.get_bool("sealed_shopify_enabled", default=True)
        trusted_shops = get_trusted_shops() if shop_enabled else []
        blocked_shops = get_blocked_shops() if shop_enabled else []

        ck_data = None
        scg_data = None
        csi_data = None
        shop_result = None

        if ck_enabled or scg_enabled or csi_enabled or shop_enabled:
            def _fetch_ck():
                try:
                    return self.cardkingdom_sealed.find_offer(item_name)
                except Exception as err:
                    logger.warning(f"Error querying Card Kingdom sealed for '{item_name}': {err}")
                    return None

            def _fetch_scg():
                try:
                    return self.starcitygames.find_offer(item_name, tcgplayer_product_id=pid)
                except Exception as err:
                    logger.warning(f"Error querying Star City Games sealed for '{item_name}': {err}")
                    return None

            def _fetch_csi():
                try:
                    return self.coolstuffinc.find_offer(item_name)
                except Exception as err:
                    logger.warning(f"Error querying CoolStuffInc sealed for '{item_name}': {err}")
                    return None

            def _fetch_shop():
                try:
                    return self.shopify_catalog.find_offers(
                        item_name,
                        market_price=effective_market,
                        trusted_shops=trusted_shops,
                        blocked_shops=blocked_shops,
                    )
                except Exception as err:
                    logger.warning(f"Error querying Shopify Catalog for '{item_name}': {err}")
                    return None

            with ThreadPoolExecutor(max_workers=4) as pool:
                fut_ck = pool.submit(_fetch_ck) if ck_enabled else None
                fut_scg = pool.submit(_fetch_scg) if scg_enabled else None
                fut_csi = pool.submit(_fetch_csi) if csi_enabled else None
                fut_shop = pool.submit(_fetch_shop) if shop_enabled else None
                if fut_ck:
                    ck_data = fut_ck.result()
                if fut_scg:
                    scg_data = fut_scg.result()
                if fut_csi:
                    csi_data = fut_csi.result()
                if fut_shop:
                    shop_result = fut_shop.result()

        best_trusted_shop = shop_result.get("best_trusted") if isinstance(shop_result, dict) else None
        best_unverified_shop = shop_result.get("best_unverified") if isinstance(shop_result, dict) else None

        now = datetime.now(timezone.utc)
        for db_attempt in range(2):
            try:
                if tcg_data:
                    mkt = tcg_data.get("market_price")
                    if mkt is not None and mkt > 0:
                        item.tcgplayer_market_price = round(float(mkt), 2)
                    if not item.image_uri and tcg_data.get("image_url"):
                        item.image_uri = tcg_data["image_url"]

                    self._upsert_vendor_price(
                        watchlist_id=item_id,
                        vendor_name="TCGplayer",
                        price=float(tcg_data.get("price", 0.0)),
                        condition=tcg_data.get("condition", "Unopened"),
                        in_stock=bool(tcg_data.get("in_stock", False)),
                        product_url=tcg_data.get("product_url"),
                        search_url=tcg_data.get("search_url") or tcg_data.get("product_url"),
                        now=now,
                    )

                if ck_enabled and ck_data:
                    self._upsert_vendor_price(
                        watchlist_id=item_id,
                        vendor_name="Card Kingdom",
                        price=float(ck_data.get("price", 0.0)),
                        condition=ck_data.get("condition", "Unopened"),
                        in_stock=bool(ck_data.get("in_stock", False)),
                        product_url=ck_data.get("product_url"),
                        search_url=ck_data.get("search_url") or ck_data.get("product_url"),
                        now=now,
                    )

                if scg_enabled and scg_data:
                    self._upsert_vendor_price(
                        watchlist_id=item_id,
                        vendor_name="Star City Games",
                        price=float(scg_data.get("price", 0.0)),
                        condition=scg_data.get("condition", "Unopened"),
                        in_stock=bool(scg_data.get("in_stock", False)),
                        product_url=scg_data.get("product_url"),
                        search_url=scg_data.get("search_url") or scg_data.get("product_url"),
                        now=now,
                    )

                if csi_enabled and csi_data:
                    self._upsert_vendor_price(
                        watchlist_id=item_id,
                        vendor_name="CoolStuffInc",
                        price=float(csi_data.get("price", 0.0)),
                        condition=csi_data.get("condition", "Unopened"),
                        in_stock=bool(csi_data.get("in_stock", False)),
                        product_url=csi_data.get("product_url"),
                        search_url=csi_data.get("search_url") or csi_data.get("product_url"),
                        now=now,
                    )

                if shop_enabled and shop_result is not None:
                    # Clean up stale Shop rows when the cheapest store changes or disappears
                    trusted_vname = best_trusted_shop["vendor_name"] if best_trusted_shop else None
                    unverified_vname = best_unverified_shop["vendor_name"] if best_unverified_shop else None
                    existing_shop_rows = VendorPrice.query.filter(
                        VendorPrice.watchlist_id == item_id,
                        db.or_(
                            VendorPrice.vendor_name.like("Shop · %"),
                            VendorPrice.vendor_name.like("Shop (unverified) · %"),
                        ),
                    ).all()
                    for row in existing_shop_rows:
                        if row.vendor_name.startswith("Shop · ") and row.vendor_name != trusted_vname:
                            db.session.delete(row)
                        elif row.vendor_name.startswith("Shop (unverified) · ") and row.vendor_name != unverified_vname:
                            db.session.delete(row)

                    if best_trusted_shop:
                        self._upsert_vendor_price(
                            watchlist_id=item_id,
                            vendor_name=best_trusted_shop["vendor_name"],
                            price=float(best_trusted_shop.get("price", 0.0)),
                            condition=best_trusted_shop.get("condition", "Unopened"),
                            in_stock=True,
                            product_url=best_trusted_shop.get("product_url"),
                            search_url=best_trusted_shop.get("search_url") or best_trusted_shop.get("product_url"),
                            now=now,
                        )

                    if best_unverified_shop:
                        self._upsert_vendor_price(
                            watchlist_id=item_id,
                            vendor_name=best_unverified_shop["vendor_name"],
                            price=float(best_unverified_shop.get("price", 0.0)),
                            condition="Unverified",
                            in_stock=False,
                            product_url=best_unverified_shop.get("product_url"),
                            search_url=best_unverified_shop.get("search_url") or best_unverified_shop.get("product_url"),
                            now=now,
                        )

                db.session.commit()
                break
            except (OperationalError, DBAPIError) as db_err:
                logger.warning(f"DB error committing sealed VendorPrice for {item_name}: {db_err}")
                try:
                    db.session.rollback()
                except Exception:
                    pass
                if db_attempt == 0 and item_id is not None:
                    item = db.session.get(WatchlistItem, item_id)
                    if item is None:
                        return {
                            "card_id": item_id,
                            "card_name": item_name,
                            "is_deal": False,
                            "lowest_price": None,
                            "mm_in_stock": False,
                            "ck_in_stock": False,
                            "scg_in_stock": False,
                            "csi_in_stock": False,
                            "vendors": [],
                        }
                    continue
                raise

        if item is None and item_id is not None:
            item = db.session.get(WatchlistItem, item_id)

        if notify and item is not None and item.is_deal:
            try:
                best = item.best_vendor
                if best:
                    self.send_discord_deal_alert(
                        item=item,
                        best_vendor=best,
                        savings_amount=item.savings_amount,
                        savings_percent=item.savings_percent,
                        fast_mode=fast_mode,
                    )
            except Exception as notify_err:
                logger.warning(f"Error dispatching sealed Discord alert for {item_name}: {notify_err}")

        return {
            "card_id": item.id if item else item_id,
            "card_name": item.name if item else item_name,
            "is_deal": item.is_deal if item else False,
            "lowest_price": item.lowest_in_stock_price if item else None,
            "mm_in_stock": False,
            "ck_in_stock": item.ck_in_stock if item else False,
            "scg_in_stock": item.scg_in_stock if item else False,
            "csi_in_stock": item.csi_in_stock if item else False,
            "vendors": [v.to_dict() for v in item.vendor_prices] if item else [],
        }

    def poll_card(self, item: WatchlistItem, notify: bool = True, fast_mode: bool = False) -> dict:
        """
        Polls all 3 providers for a single WatchlistItem, upserts VendorPrice records,
        and dispatches a Discord deal alert if a new deal threshold is met.
        """
        if item.is_sealed and item.tcgplayer_product_id:
            return self.poll_sealed_item(item, notify=notify, fast_mode=fast_mode)
        # Proactive card name sanitization for corrupted encoding/mojibake
        if "\ufffd" in item.name or any(marker in item.name for marker in ("\u00c3", "\u00c2")):
            repaired = fix_mojibake(item.name)
            if repaired and repaired != item.name and "\ufffd" not in repaired:
                logger.info(f"Auto-repaired card name mojibake: '{item.name}' -> '{repaired}'")
                item.name = repaired
            elif "\ufffd" in item.name and item.scryfall_id:
                try:
                    sf_card = self.scryfall.get_card_by_id(item.scryfall_id)
                    if sf_card and sf_card.get("name"):
                        logger.info(f"Resolved canonical card name from Scryfall: '{item.name}' -> '{sf_card['name']}'")
                        item.name = sf_card["name"]
                except Exception as name_err:
                    logger.debug(f"Could not resolve canonical name from Scryfall: {name_err}")

        # Extract plain primitive values before spawning worker threads (never touch ORM/session in threads)
        item_id = item.id
        item_name = item.name
        is_any = item.is_any_version
        item_set_code = None if is_any else item.set_code
        item_collector_number = None if is_any else item.collector_number
        item_finish = item.finish
        item_scryfall_id = item.scryfall_id

        version_label = "Any Version" if is_any else (item_set_code or "N/A")
        logger.info(f"Polling prices for: {item_name} ({version_label}, {item_finish})")

        # 1. Scryfall / TCGplayer (with error isolation and fallback)
        tcg_data = None
        try:
            if is_any:
                tcg_data = self.scryfall.get_cheapest_tcgplayer_price(item_name, finish=item_finish)
            else:
                tcg_data = self.scryfall.get_tcgplayer_price(item_scryfall_id, finish=item_finish)
                # Fallback to cheapest price if specific printing has no direct price listed
                if not tcg_data or not tcg_data.get("in_stock") or tcg_data.get("price", 0) <= 0:
                    cheapest_fallback = self.scryfall.get_cheapest_tcgplayer_price(item_name, finish=item_finish)
                    if cheapest_fallback and cheapest_fallback.get("price", 0) > 0:
                        tcg_data = cheapest_fallback
        except Exception as sf_err:
            logger.warning(f"Error querying Scryfall/TCGplayer for {item_name}: {sf_err}")

        ref_price = tcg_data.get("price") if tcg_data and tcg_data.get("in_stock") else None
        set_name = None
        if not is_any and item_set_code:
            try:
                set_name = self.scryfall.get_set_name(item_set_code)
            except Exception as set_err:
                logger.debug(f"Error resolving set name for {item_set_code}: {set_err}")

        # 2. Mighty Meeple, 3. eBay, 4. Card Kingdom, 5. Star City Games, & 6. CoolStuffInc in parallel (using plain primitives only)
        ck_singles_enabled = SystemSetting.get_bool("singles_cardkingdom_enabled", default=True)
        scg_singles_enabled = SystemSetting.get_bool("singles_starcitygames_enabled", default=True)
        csi_singles_enabled = SystemSetting.get_bool("singles_coolstuffinc_enabled", default=True)

        def _fetch_mm():
            return self.mightymeeple.search_card(
                card_name=item_name,
                set_name=set_name,
                set_code=item_set_code,
                collector_number=item_collector_number,
                finish=item_finish,
                fast_mode=fast_mode,
                max_seconds=5.0 if fast_mode else None,
            )

        def _fetch_ebay():
            return self.ebay.search_card(
                card_name=item_name,
                set_code=item_set_code,
                finish=item_finish,
                reference_price=ref_price,
                timeout=4.0 if fast_mode else 8.0,
            )

        def _fetch_ck_single():
            return self.cardkingdom_singles.search_card(
                card_name=item_name,
                scryfall_id=item_scryfall_id,
                finish=item_finish,
                is_any_version=is_any,
                timeout=5.0 if fast_mode else 25.0,
            )

        def _fetch_scg_single():
            return self.starcitygames.search_card(
                card_name=item_name,
                set_name=set_name,
                set_code=item_set_code,
                collector_number=item_collector_number,
                finish=item_finish,
                is_any_version=is_any,
                fast_mode=fast_mode,
                timeout=5.0 if fast_mode else 8.0,
            )

        def _fetch_csi_single():
            return self.coolstuffinc.search_card(
                card_name=item_name,
                set_name=set_name,
                set_code=item_set_code,
                collector_number=item_collector_number,
                finish=item_finish,
                is_any_version=is_any,
                fast_mode=fast_mode,
                timeout=5.0 if fast_mode else 8.0,
            )

        mm_data = None
        ebay_data = None
        ck_data = None
        scg_data = None
        csi_data = None
        executor = ThreadPoolExecutor(max_workers=5)
        try:
            mm_fut = executor.submit(_fetch_mm)
            ebay_fut = executor.submit(_fetch_ebay)
            ck_fut = executor.submit(_fetch_ck_single) if ck_singles_enabled else None
            scg_fut = executor.submit(_fetch_scg_single) if scg_singles_enabled else None
            csi_fut = executor.submit(_fetch_csi_single) if csi_singles_enabled else None
            all_futs = [f for f in (mm_fut, ebay_fut, ck_fut, scg_fut, csi_fut) if f is not None]
            if fast_mode:
                futures_wait(all_futs, timeout=5.5, return_when=ALL_COMPLETED)
                if mm_fut.done():
                    try:
                        mm_data = mm_fut.result()
                    except Exception as mm_err:
                        logger.warning(f"Error querying Mighty Meeple for {item_name}: {mm_err}")
                else:
                    logger.warning(f"Mighty Meeple fast_mode poll timed out for {item_name}")
                if ebay_fut.done():
                    try:
                        ebay_data = ebay_fut.result()
                    except Exception as ebay_err:
                        logger.warning(f"Error querying eBay for {item_name}: {ebay_err}")
                else:
                    logger.warning(f"eBay fast_mode poll timed out for {item_name}")
                if ck_fut is not None:
                    if ck_fut.done():
                        try:
                            ck_data = ck_fut.result()
                        except Exception as ck_err:
                            logger.warning(f"Error querying Card Kingdom for {item_name}: {ck_err}")
                    else:
                        logger.warning(f"Card Kingdom fast_mode poll timed out for {item_name}")
                if scg_fut is not None:
                    if scg_fut.done():
                        try:
                            scg_data = scg_fut.result()
                        except Exception as scg_err:
                            logger.warning(f"Error querying Star City Games for {item_name}: {scg_err}")
                    else:
                        logger.warning(f"Star City Games fast_mode poll timed out for {item_name}")
                if csi_fut is not None:
                    if csi_fut.done():
                        try:
                            csi_data = csi_fut.result()
                        except Exception as csi_err:
                            logger.warning(f"Error querying CoolStuffInc for {item_name}: {csi_err}")
                    else:
                        logger.warning(f"CoolStuffInc fast_mode poll timed out for {item_name}")
            else:
                try:
                    mm_data = mm_fut.result()
                except Exception as mm_err:
                    logger.warning(f"Error querying Mighty Meeple for {item_name}: {mm_err}")
                try:
                    ebay_data = ebay_fut.result()
                except Exception as ebay_err:
                    logger.warning(f"Error querying eBay for {item_name}: {ebay_err}")
                if ck_fut is not None:
                    try:
                        ck_data = ck_fut.result()
                    except Exception as ck_err:
                        logger.warning(f"Error querying Card Kingdom for {item_name}: {ck_err}")
                if scg_fut is not None:
                    try:
                        scg_data = scg_fut.result()
                    except Exception as scg_err:
                        logger.warning(f"Error querying Star City Games for {item_name}: {scg_err}")
                if csi_fut is not None:
                    try:
                        csi_data = csi_fut.result()
                    except Exception as csi_err:
                        logger.warning(f"Error querying CoolStuffInc for {item_name}: {csi_err}")
        finally:
            if fast_mode:
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        now = datetime.now(timezone.utc)
        results = [tcg_data, mm_data, ebay_data, ck_data, scg_data, csi_data]

        # Upsert VendorPrice records with retry on transient Neon/Postgres SSL connection drop
        mm_was_in_stock = False
        ck_was_in_stock = False
        scg_was_in_stock = False
        csi_was_in_stock = False
        for db_attempt in range(2):
            try:
                mm_existing = VendorPrice.query.filter_by(watchlist_id=item_id, vendor_name="Mighty Meeple").first()
                mm_was_in_stock = bool(mm_existing and mm_existing.in_stock and mm_existing.price > 0)
                ck_existing = VendorPrice.query.filter_by(watchlist_id=item_id, vendor_name="Card Kingdom").first()
                ck_was_in_stock = bool(ck_existing and ck_existing.in_stock and ck_existing.price > 0)
                scg_existing = VendorPrice.query.filter_by(watchlist_id=item_id, vendor_name="Star City Games").first()
                scg_was_in_stock = bool(scg_existing and scg_existing.in_stock and scg_existing.price > 0)
                csi_existing = VendorPrice.query.filter_by(watchlist_id=item_id, vendor_name="CoolStuffInc").first()
                csi_was_in_stock = bool(csi_existing and csi_existing.in_stock and csi_existing.price > 0)

                for vendor_data in results:
                    if not vendor_data:
                        continue

                    v_name = vendor_data.get("vendor_name")
                    existing = (
                        VendorPrice.query.filter_by(
                            watchlist_id=item_id,
                            vendor_name=v_name,
                        ).first()
                    )

                    if existing:
                        existing.price = float(vendor_data.get("price", 0.0))
                        existing.condition = vendor_data.get("condition", "NM")
                        existing.in_stock = bool(vendor_data.get("in_stock", True))
                        existing.product_url = vendor_data.get("product_url")
                        existing.search_url = vendor_data.get("search_url") or vendor_data.get("product_url")
                        existing.last_checked = now
                    else:
                        new_vp = VendorPrice(
                            watchlist_id=item_id,
                            vendor_name=v_name,
                            price=float(vendor_data.get("price", 0.0)),
                            condition=vendor_data.get("condition", "NM"),
                            in_stock=bool(vendor_data.get("in_stock", True)),
                            product_url=vendor_data.get("product_url"),
                            search_url=vendor_data.get("search_url") or vendor_data.get("product_url"),
                            last_checked=now,
                        )
                        db.session.add(new_vp)

                db.session.commit()
                break
            except (OperationalError, DBAPIError) as db_err:
                logger.warning(f"DB error committing VendorPrice for {item_name} (attempt {db_attempt + 1}/2): {db_err}")
                try:
                    db.session.rollback()
                except Exception:
                    pass
                if db_attempt == 0 and item_id is not None:
                    item = db.session.get(WatchlistItem, item_id)
                    if item is None:
                        return {
                            "card_id": item_id,
                            "card_name": item_name,
                            "is_deal": False,
                            "lowest_price": None,
                            "mm_in_stock": False,
                            "ck_in_stock": False,
                            "scg_in_stock": False,
                            "csi_in_stock": False,
                            "vendors": [],
                        }
                    continue
                raise

        if item is None and item_id is not None:
            item = db.session.get(WatchlistItem, item_id)

        # Check for Mighty Meeple / Card Kingdom / SCG / CSI In-Stock Alerts and general deal notifications
        if notify and item is not None:
            try:
                mm_now_in_stock = bool(mm_data and mm_data.get("in_stock") and mm_data.get("price", 0) > 0)
                global_mm_alert = SystemSetting.get_bool("notify_mm_stock_enabled", default=True)
                card_mm_alert = bool(item.notify_mm_stock if item.notify_mm_stock is not None else True)

                if mm_now_in_stock and not mm_was_in_stock and global_mm_alert and card_mm_alert:
                    logger.info(f"Mighty Meeple restock detected for {item.name}! Dispatching stock alert...")
                    self.send_discord_mm_stock_alert(item=item, mm_data=mm_data, fast_mode=fast_mode)

                ck_now_in_stock = bool(ck_data and ck_data.get("in_stock") and ck_data.get("price", 0) > 0)
                global_ck_alert = SystemSetting.get_bool("notify_ck_stock_enabled", default=True)
                card_ck_alert = bool(item.notify_ck_stock if item.notify_ck_stock is not None else True)

                if ck_now_in_stock and not ck_was_in_stock and global_ck_alert and card_ck_alert:
                    logger.info(f"Card Kingdom restock detected for {item.name}! Dispatching stock alert...")
                    self.send_discord_ck_stock_alert(item=item, ck_data=ck_data, fast_mode=fast_mode)

                scg_now_in_stock = bool(scg_data and scg_data.get("in_stock") and scg_data.get("price", 0) > 0)
                global_scg_alert = SystemSetting.get_bool("notify_scg_stock_enabled", default=True)
                card_scg_alert = bool(item.notify_scg_stock if item.notify_scg_stock is not None else True)

                if scg_now_in_stock and not scg_was_in_stock and global_scg_alert and card_scg_alert:
                    logger.info(f"Star City Games restock detected for {item.name}! Dispatching stock alert...")
                    self.send_discord_scg_stock_alert(item=item, scg_data=scg_data, fast_mode=fast_mode)

                csi_now_in_stock = bool(csi_data and csi_data.get("in_stock") and csi_data.get("price", 0) > 0)
                global_csi_alert = SystemSetting.get_bool("notify_csi_stock_enabled", default=True)
                card_csi_alert = bool(item.notify_csi_stock if item.notify_csi_stock is not None else True)

                if csi_now_in_stock and not csi_was_in_stock and global_csi_alert and card_csi_alert:
                    logger.info(f"CoolStuffInc restock detected for {item.name}! Dispatching stock alert...")
                    self.send_discord_csi_stock_alert(item=item, csi_data=csi_data, fast_mode=fast_mode)

                if item.is_deal:
                    best = item.best_vendor
                    if best:
                        self.send_discord_deal_alert(
                            item=item,
                            best_vendor=best,
                            savings_amount=item.savings_amount,
                            savings_percent=item.savings_percent,
                            fast_mode=fast_mode,
                        )
            except Exception as notify_err:
                logger.warning(f"Error dispatching Discord alert for {item_name}: {notify_err}")

        return {
            "card_id": item.id if item else item_id,
            "card_name": item.name if item else item_name,
            "is_deal": item.is_deal if item else False,
            "lowest_price": item.lowest_in_stock_price if item else None,
            "mm_in_stock": item.mm_in_stock if item else False,
            "ck_in_stock": item.ck_in_stock if item else False,
            "scg_in_stock": item.scg_in_stock if item else False,
            "csi_in_stock": item.csi_in_stock if item else False,
            "vendors": [v.to_dict() for v in item.vendor_prices] if item else [],
        }

    def poll_all_cards(self, notify: bool = True) -> list[dict]:
        """Polls prices for all cards currently in the database."""
        items = WatchlistItem.query.all()
        summary = []
        logger.info(f"Starting scheduled poll for {len(items)} watchlist items...")

        for idx, item in enumerate(items):
            try:
                res = self.poll_card(item, notify=notify)
                summary.append(res)
            except Exception as e:
                logger.error(f"Error polling card {item.name} (ID: {item.id}): {e}")

            # Polite inter-target pacing to avoid vendor 429 rate limits
            if idx < len(items) - 1:
                time.sleep(0.5)

        deals_found = sum(1 for s in summary if s.get("is_deal"))
        now = datetime.now(timezone.utc)
        try:
            SystemSetting.record_successful_run("registry", dt=now)
            SystemSetting.set_val("last_poll_count", len(summary))
            SystemSetting.set_val("last_poll_deals", deals_found)
            SystemSetting.set_val(
                "last_poll_status",
                f"Surveillance cycle complete: {len(summary)} targets monitored, {deals_found} active deals triggered."
            )
        except Exception as e:
            logger.debug(f"Could not persist telemetry to SystemSetting: {e}")

        # Record persistent audit telemetry in ActivityLog for historical tracking
        try:
            target_user_id = items[0].user_id if items else None
            admin_user = User.query.filter_by(is_admin=True).first() if not target_user_id else None
            effective_uid = target_user_id or (admin_user.id if admin_user else None)
            admin_email = admin_user.email if admin_user else "system.worker@chimera.local"
            sweep_log = ActivityLog(
                user_id=effective_uid,
                user_email=admin_email,
                action="SURVEILLANCE_SWEEP",
                endpoint="worker.poll_all_cards",
                details=(
                    f"Surveillance cycle complete: {len(summary)} targets monitored, "
                    f"{deals_found} active deals detected."
                ),
                created_at=now,
            )
            db.session.add(sweep_log)
            db.session.commit()
        except Exception as log_err:
            logger.debug(f"Could not record ActivityLog for surveillance sweep: {log_err}")

        logger.info(f"Completed poll for all watchlist items ({len(summary)} scanned, {deals_found} deals).")
        return summary

    def poll_user_cards(self, items: list[WatchlistItem], notify: bool = True) -> list[dict]:
        """Polls prices for a specific user's WatchlistItems."""
        summary = []
        for idx, item in enumerate(items):
            try:
                res = self.poll_card(item, notify=notify)
                summary.append(res)
            except Exception as e:
                logger.error(f"Error polling card {item.name} (ID: {item.id}): {e}")

            # Polite inter-target pacing to avoid vendor 429 rate limits
            if idx < len(items) - 1:
                time.sleep(0.5)
        return summary

    def get_effective_webhook_url(
        self,
        user: User | None = None,
        override_url: str | None = None,
        is_test_event: bool = False,
    ) -> str | None:
        """
        Determines the target Discord webhook URL using the following priority hierarchy:
        1. Explicit override URL (e.g. from UI verification test field before saving)
        2. Testing / automated test environment (routes to DISCORD_TEST_WEBHOOK_URL)
        3. Per-user configured discord_webhook_url (if user/owner is present)
        4. Global default DISCORD_WEBHOOK_URL from configuration / environment
        """
        # 1. Explicit override / testing argument
        if override_url and str(override_url).strip():
            valid, clean_url = User.validate_discord_webhook_url(override_url)
            if valid and clean_url:
                return clean_url

        # Check if running in automated test mode
        is_testing = False
        if self.app and self.app.config.get("TESTING"):
            is_testing = True
        elif is_test_event:
            is_testing = True

        # 2. Automated test environment
        if is_testing:
            test_url = (
                (self.app.config.get("DISCORD_TEST_WEBHOOK_URL") if self.app else "")
                or Config.DISCORD_TEST_WEBHOOK_URL
            )
            if test_url:
                return test_url
            if self.app and self.app.config.get("TESTING"):
                return None  # In testing without test webhook configured, avoid hitting production URLs

        # 3. Per-user configured webhook
        if user and user.discord_webhook_url:
            valid, clean_url = User.validate_discord_webhook_url(user.discord_webhook_url)
            if valid and clean_url:
                return clean_url

        # 4. System default fallback
        global_url = (
            (self.app.config.get("DISCORD_WEBHOOK_URL") if self.app else "")
            or Config.DISCORD_WEBHOOK_URL
        )
        return global_url if global_url else None

    def send_discord_deal_alert(
        self,
        item: WatchlistItem,
        best_vendor: VendorPrice,
        savings_amount: float,
        savings_percent: float,
        user: User | None = None,
        webhook_url: str | None = None,
        fast_mode: bool = False,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed for an active deal."""
        target_user = user or (item.user if item else None)
        dest_url = self.get_effective_webhook_url(user=target_user, override_url=webhook_url)

        if not dest_url:
            logger.debug(f"No Discord webhook configured for user/system; skipping deal alert for {item.name if item else 'target'}.")
            return False

        try:
            # Build multi-vendor price comparison string
            comparison_lines = []
            if item:
                for vp in item.vendor_prices:
                    stock_tag = "✓ In Stock" if vp.in_stock else "✗ Out of Stock"
                    price_tag = f"${vp.price:.2f}" if vp.price > 0 else "N/A"
                    prefix = "★ **" if vp.id == best_vendor.id else ""
                    suffix = "** (Best Deal)" if vp.id == best_vendor.id else ""
                    comparison_lines.append(f"{prefix}{vp.vendor_name}: {price_tag} [{stock_tag}] ({vp.condition}){suffix}")

            comparison_text = "\n".join(comparison_lines) if comparison_lines else "No vendor data"

            fields = [
                {
                    "name": "Target Price",
                    "value": f"${item.target_price:.2f}" if item and item.target_price else "Not set",
                    "inline": True,
                },
                {
                    "name": "Deal Price",
                    "value": f"**${best_vendor.price:.2f}** ({best_vendor.vendor_name})",
                    "inline": True,
                },
                {
                    "name": "Savings",
                    "value": f"**${savings_amount:.2f}** ({savings_percent:.1f}%)",
                    "inline": True,
                },
                {
                    "name": "Live Price Comparison",
                    "value": comparison_text,
                    "inline": False,
                },
            ]

            if best_vendor.product_url:
                fields.append({
                    "name": "Direct Purchase",
                    "value": f"[Click here to buy on {best_vendor.vendor_name}]({best_vendor.product_url})",
                    "inline": False,
                })

            set_label = "Any Version" if (item and item.is_any_version) else (item.set_code if item else "Unknown")
            finish_label = item.finish.capitalize() if item else "Nonfoil"
            item_name = item.name if item else "Monitored Card"
            embed = {
                "title": f"🚨 Priority Deal: {item_name}",
                "description": f"**Set:** {set_label} | **Finish:** {finish_label}",
                "color": 0xDC143C,  # Tactical Crimson
                "fields": fields,
                "footer": {
                    "text": "Chimaera MTG Tactical Intelligence",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if item and item.image_uri:
                embed["thumbnail"] = {"url": item.image_uri}

            payload = {
                "username": "Chimaera Deal Tracker",
                "embeds": [embed],
            }

            max_attempts = 1 if fast_mode else 2
            req_timeout = 5 if fast_mode else 10
            for attempt in range(max_attempts):
                resp = requests.post(dest_url, json=payload, timeout=req_timeout)
                if resp.status_code in (200, 204):
                    logger.info(f"Discord deal alert sent successfully for {item_name} to {dest_url[:45]}...")
                    if not fast_mode:
                        time.sleep(0.5)
                    return True
                elif resp.status_code == 429:
                    if fast_mode or attempt + 1 >= max_attempts:
                        break
                    retry_sec = 2.0
                    try:
                        retry_sec = float(resp.json().get("retry_after", 2.0))
                    except Exception:
                        pass
                    logger.warning(f"Discord webhook rate limited (429) for {dest_url[:45]}. Retrying in {retry_sec:.1f}s...")
                    time.sleep(retry_sec)
                    continue
                elif resp.status_code in (401, 404):
                    logger.warning(f"Discord webhook invalid/unauthorized ({resp.status_code}) for {dest_url[:45]}...")
                    break
                else:
                    logger.warning(f"Discord webhook failed with status code {resp.status_code}: {resp.text}")
                    break
        except Exception as e:
            logger.error(f"Failed to send Discord webhook deal alert: {e}")

        return False

    def _send_discord_vendor_stock_alert(
        self,
        vendor_name: str,
        emoji: str,
        color: int,
        item: WatchlistItem,
        vendor_data: dict,
        user: User | None = None,
        webhook_url: str | None = None,
        fast_mode: bool = False,
    ) -> bool:
        """Shared helper to dispatch a rich Discord Webhook embed for vendor in-stock restocks."""
        target_user = user or (item.user if item else None)
        dest_url = self.get_effective_webhook_url(user=target_user, override_url=webhook_url)

        if not dest_url:
            logger.debug(f"No Discord Webhook URL configured; skipping {vendor_name} stock alert for {item.name if item else 'target'}.")
            return False

        try:
            price_val = float(vendor_data.get("price", 0.0))
            condition_val = vendor_data.get("condition", "NM")
            product_url = vendor_data.get("product_url")
            set_label = "Any Version" if (item and item.is_any_version) else (item.set_code if item else "Unknown")
            item_name = item.name if item else "Monitored Card"
            finish_label = item.finish.capitalize() if item else "Nonfoil"

            fields = [
                {
                    "name": f"{vendor_name} Price",
                    "value": f"**${price_val:.2f}** ({condition_val})",
                    "inline": True,
                },
                {
                    "name": "Stock Status",
                    "value": "✓ **IN STOCK NOW**",
                    "inline": True,
                },
            ]

            if item and item.target_price:
                fields.append({
                    "name": "Your Target Threshold",
                    "value": f"${item.target_price:.2f}",
                    "inline": True,
                })

            if item and item.market_price:
                fields.append({
                    "name": "TCGplayer Market Ref",
                    "value": f"${item.market_price:.2f}",
                    "inline": True,
                })

            if product_url:
                fields.append({
                    "name": "Direct Purchase",
                    "value": f"[🛒 Buy Now on {vendor_name}]({product_url})",
                    "inline": False,
                })

            fields.append({
                "name": "Alert Configuration",
                "value": f"You can toggle {vendor_name} stock alerts on or off anytime in the Chimaera Target Registry.",
                "inline": False,
            })

            embed = {
                "title": f"{emoji} {vendor_name} In-Stock: {item_name}",
                "description": f"**Set:** {set_label} | **Finish:** {finish_label}",
                "color": color,
                "fields": fields,
                "footer": {
                    "text": f"Chimaera MTG Tactical Intelligence // {vendor_name} Surveillance",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if item and item.image_uri:
                embed["thumbnail"] = {"url": item.image_uri}

            payload = {
                "username": "Chimaera Stock Monitor",
                "embeds": [embed],
            }

            max_attempts = 1 if fast_mode else 2
            req_timeout = 5 if fast_mode else 10
            for attempt in range(max_attempts):
                resp = requests.post(dest_url, json=payload, timeout=req_timeout)
                if resp.status_code in (200, 204):
                    logger.info(f"Discord {vendor_name} stock alert sent successfully for {item_name} to {dest_url[:45]}...")
                    if not fast_mode:
                        time.sleep(0.5)
                    return True
                elif resp.status_code == 429:
                    if fast_mode or attempt + 1 >= max_attempts:
                        break
                    retry_sec = 2.0
                    try:
                        retry_sec = float(resp.json().get("retry_after", 2.0))
                    except Exception:
                        pass
                    logger.warning(f"Discord webhook rate limited (429) for {dest_url[:45]}. Retrying in {retry_sec:.1f}s...")
                    time.sleep(retry_sec)
                    continue
                elif resp.status_code in (401, 404):
                    logger.warning(f"Discord webhook invalid/unauthorized ({resp.status_code}) for {dest_url[:45]}...")
                    break
                else:
                    logger.warning(f"Discord {vendor_name} alert failed with status code {resp.status_code}: {resp.text}")
                    break
        except Exception as e:
            logger.error(f"Failed to send Discord {vendor_name} stock alert: {e}")

        return False

    def send_discord_mm_stock_alert(
        self,
        item: WatchlistItem,
        mm_data: dict,
        user: User | None = None,
        webhook_url: str | None = None,
        fast_mode: bool = False,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed specifically for Mighty Meeple in-stock restocks."""
        return self._send_discord_vendor_stock_alert(
            vendor_name="Mighty Meeple",
            emoji="🎲",
            color=0x00CED1,
            item=item,
            vendor_data=mm_data,
            user=user,
            webhook_url=webhook_url,
            fast_mode=fast_mode,
        )

    def send_discord_ck_stock_alert(
        self,
        item: WatchlistItem,
        ck_data: dict,
        user: User | None = None,
        webhook_url: str | None = None,
        fast_mode: bool = False,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed specifically for Card Kingdom in-stock restocks."""
        return self._send_discord_vendor_stock_alert(
            vendor_name="Card Kingdom",
            emoji="👑",
            color=0x8B5CF6,
            item=item,
            vendor_data=ck_data,
            user=user,
            webhook_url=webhook_url,
            fast_mode=fast_mode,
        )

    def send_discord_scg_stock_alert(
        self,
        item: WatchlistItem,
        scg_data: dict,
        user: User | None = None,
        webhook_url: str | None = None,
        fast_mode: bool = False,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed specifically for Star City Games in-stock restocks."""
        return self._send_discord_vendor_stock_alert(
            vendor_name="Star City Games",
            emoji="⭐",
            color=0xF59E0B,
            item=item,
            vendor_data=scg_data,
            user=user,
            webhook_url=webhook_url,
            fast_mode=fast_mode,
        )

    def send_discord_csi_stock_alert(
        self,
        item: WatchlistItem,
        csi_data: dict,
        user: User | None = None,
        webhook_url: str | None = None,
        fast_mode: bool = False,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed specifically for CoolStuffInc in-stock restocks."""
        return self._send_discord_vendor_stock_alert(
            vendor_name="CoolStuffInc",
            emoji="❄️",
            color=0x38BDF8,
            item=item,
            vendor_data=csi_data,
            user=user,
            webhook_url=webhook_url,
            fast_mode=fast_mode,
        )

    def send_test_discord_notification(
        self,
        webhook_url: str | None = None,
        user: User | None = None,
    ) -> tuple[bool, str]:
        """Sends a verification ping to the specified, user, or test Discord Webhook."""
        dest_url = self.get_effective_webhook_url(user=user, override_url=webhook_url, is_test_event=True)
        if not dest_url:
            return False, "No Discord Webhook URL is configured or provided for testing."

        valid, clean_url = User.validate_discord_webhook_url(dest_url)
        if not valid:
            return False, clean_url

        try:
            dest_label = "User Channel" if (user and user.discord_webhook_url == clean_url) else ("Custom Test Webhook" if webhook_url else "Dedicated Test Channel")
            payload = {
                "username": "Chimaera Tactical Intelligence",
                "embeds": [
                    {
                        "title": "🛰️ Chimaera Webhook Telemetry Test",
                        "description": "Tactical market surveillance webhook integration is successfully configured and operational!",
                        "color": 0x008080,  # Sophisticated Teal
                        "fields": [
                            {"name": "Status", "value": "✓ Online & Monitoring", "inline": True},
                            {"name": "Engine", "value": "Chimaera v1.0", "inline": True},
                            {"name": "Routing", "value": dest_label, "inline": False},
                        ],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "footer": {"text": "Chimaera MTG Market Surveillance"},
                    }
                ],
            }
            resp = requests.post(clean_url, json=payload, timeout=8)
            if resp.status_code in (200, 204):
                return True, "Test webhook delivered successfully to Discord!"
            elif resp.status_code == 429:
                return False, "Discord returned Rate Limit (429). Please try again in a few seconds."
            elif resp.status_code in (401, 404):
                return False, f"Discord rejected webhook URL (HTTP {resp.status_code}). Please verify the webhook URL."
            return False, f"Discord returned status {resp.status_code}: {resp.text}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def sync_microcenter(self, notify: bool = True) -> dict:
        """Executes a full scrape and sync of MicroCenter Charlotte store inventory."""
        logger.info("Executing MicroCenter Charlotte store synchronization...")
        return self.microcenter.sync_inventory(notify=notify, deal_engine=self)

    def send_discord_microcenter_price_alert(
        self,
        item: MicrocenterItem,
        old_price: float,
        new_price: float,
        user: User | None = None,
        webhook_url: str | None = None,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed when a MicroCenter product price changes."""
        dest_url = self.get_effective_webhook_url(user=user, override_url=webhook_url)
        if not dest_url:
            logger.debug(f"No Discord webhook configured; skipping MicroCenter price alert for {item.name}.")
            return False

        try:
            delta = round(new_price - old_price, 2)
            pct = round((delta / old_price) * 100.0, 1) if old_price > 0 else 0.0
            is_drop = delta < 0
            change_label = f"Price Drop ({pct:+.1f}%)" if is_drop else f"Price Increase ({pct:+.1f}%)"
            embed_color = 0xDC143C if is_drop else 0x00CED1  # Crimson for deal/drop, Teal for change

            stock_label = item.stock_text or (f"{item.stock_count} IN STOCK" if item.stock_count is not None else "In Stock")
            if not item.in_stock:
                stock_label = "✗ OUT OF STOCK"

            fields = [
                {
                    "name": "Previous Price",
                    "value": f"${old_price:.2f}",
                    "inline": True,
                },
                {
                    "name": "New Price",
                    "value": f"**${new_price:.2f}**",
                    "inline": True,
                },
                {
                    "name": "Change",
                    "value": f"**{'-' if delta < 0 else '+'}${abs(delta):.2f}** ({pct:+.1f}%)",
                    "inline": True,
                },
                {
                    "name": "Charlotte Store Stock",
                    "value": f"📍 {stock_label}",
                    "inline": True,
                },
                {
                    "name": "Store Location",
                    "value": f"MicroCenter Store #{item.store_id} ({item.store_name}, NC)",
                    "inline": True,
                },
                {
                    "name": "SKU / Item ID",
                    "value": f"`{item.sku}`",
                    "inline": True,
                },
            ]

            if item.product_url:
                fields.append({
                    "name": "Direct Product Link",
                    "value": f"[🛒 View on MicroCenter.com]({item.product_url})",
                    "inline": False,
                })

            embed = {
                "title": f"🏷️ MicroCenter Price Alert: {item.name}",
                "description": f"**{change_label}** detected at MicroCenter Charlotte store.",
                "color": embed_color,
                "fields": fields,
                "footer": {
                    "text": "Chimaera MTG Tactical Intelligence // MicroCenter Charlotte Surveillance",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if item.image_url:
                embed["thumbnail"] = {"url": item.image_url}

            payload = {
                "username": "Chimaera MicroCenter Monitor",
                "embeds": [embed],
            }

            resp = requests.post(dest_url, json=payload, timeout=8)
            if resp.status_code in (200, 204):
                logger.info(f"Discord MicroCenter price alert sent for {item.name} to {dest_url[:45]}...")
                return True
            else:
                logger.warning(f"Discord MicroCenter alert failed with status code {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send Discord MicroCenter price alert: {e}")

        return False

    def send_discord_microcenter_restock_alert(
        self,
        item: MicrocenterItem,
        user: User | None = None,
        webhook_url: str | None = None,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed when a MicroCenter product restocks."""
        dest_url = self.get_effective_webhook_url(user=user, override_url=webhook_url)
        if not dest_url:
            return False

        try:
            stock_label = item.stock_text or (f"{item.stock_count} IN STOCK" if item.stock_count is not None else "In Stock")
            fields = [
                {
                    "name": "Current Price",
                    "value": f"**${item.current_price:.2f}**",
                    "inline": True,
                },
                {
                    "name": "Stock Level",
                    "value": f"✓ **{stock_label}**",
                    "inline": True,
                },
                {
                    "name": "Store Location",
                    "value": f"MicroCenter Store #{item.store_id} ({item.store_name}, NC)",
                    "inline": True,
                },
            ]

            if item.product_url:
                fields.append({
                    "name": "Direct Product Link",
                    "value": f"[🛒 Buy Now on MicroCenter.com]({item.product_url})",
                    "inline": False,
                })

            embed = {
                "title": f"📦 MicroCenter Restock: {item.name}",
                "description": "Product is back in stock at MicroCenter Charlotte!",
                "color": 0x00CED1,  # Tactical Teal
                "fields": fields,
                "footer": {
                    "text": "Chimaera MTG Tactical Intelligence // MicroCenter Charlotte Surveillance",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if item.image_url:
                embed["thumbnail"] = {"url": item.image_url}

            payload = {
                "username": "Chimaera Stock Monitor",
                "embeds": [embed],
            }

            resp = requests.post(dest_url, json=payload, timeout=8)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Failed to send Discord MicroCenter restock alert: {e}")
            return False

    def send_discord_microcenter_low_stock_alert(
        self,
        item: MicrocenterItem,
        user: User | None = None,
        webhook_url: str | None = None,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed when a MicroCenter product first hits low stock (<= 5 units)."""
        dest_url = self.get_effective_webhook_url(user=user, override_url=webhook_url)
        if not dest_url:
            return False

        try:
            stock_count_val = item.stock_count if item.stock_count is not None else "Low"
            item_display = item.display_name if hasattr(item, "display_name") else item.name
            fields = [
                {
                    "name": "Current Price",
                    "value": f"**${item.current_price:.2f}**",
                    "inline": True,
                },
                {
                    "name": "Charlotte Inventory Level",
                    "value": f"⚠️ **{stock_count_val} UNITS REMAINING**",
                    "inline": True,
                },
                {
                    "name": "Store Location",
                    "value": f"MicroCenter Store #{item.store_id} ({item.store_name}, NC)",
                    "inline": True,
                },
                {
                    "name": "SKU / Item ID",
                    "value": f"`{item.sku}`",
                    "inline": True,
                },
            ]

            if item.product_url:
                fields.append({
                    "name": "MicroCenter Product Link",
                    "value": f"[🛒 View on MicroCenter.com]({item.product_url})",
                    "inline": False,
                })

            embed = {
                "title": f"⚠️ MicroCenter Low Stock Alert: {item_display}",
                "description": f"Inventory running low at MicroCenter Charlotte! Only **{stock_count_val} units left** in store.",
                "color": 0xF59E0B,  # Tactical Amber
                "fields": fields,
                "footer": {
                    "text": "Chimaera MTG Tactical Intelligence // MicroCenter Charlotte Surveillance",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            payload = {
                "username": "Chimaera Stock Monitor",
                "embeds": [embed],
            }

            resp = requests.post(dest_url, json=payload, timeout=8)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Failed to send Discord MicroCenter low stock alert: {e}")
            return False
