import logging
from datetime import datetime, timezone
import requests
from config import Config
from models import db, WatchlistItem, VendorPrice, SystemSetting, User, MicrocenterItem
from providers import ScryfallProvider, MightyMeepleProvider, EbayProvider, MicrocenterProvider

logger = logging.getLogger(__name__)


class DealEngine:
    """Core price aggregation and deal evaluation engine for Chimaera."""

    def __init__(self, app=None):
        self.app = app
        self.scryfall = ScryfallProvider()
        self.mightymeeple = MightyMeepleProvider()
        self.ebay = EbayProvider()
        self.microcenter = MicrocenterProvider(
            store_id=Config.MICROCENTER_STORE_ID,
            store_name=Config.MICROCENTER_STORE_NAME,
        )

    def poll_card(self, item: WatchlistItem, notify: bool = True) -> dict:
        """
        Polls all 3 providers for a single WatchlistItem, upserts VendorPrice records,
        and dispatches a Discord deal alert if a new deal threshold is met.
        """
        is_any = item.is_any_version
        version_label = "Any Version" if is_any else (item.set_code or "N/A")
        logger.info(f"Polling prices for: {item.name} ({version_label}, {item.finish})")

        # 1. Scryfall / TCGplayer
        if is_any:
            tcg_data = self.scryfall.get_cheapest_tcgplayer_price(item.name, finish=item.finish)
        else:
            tcg_data = self.scryfall.get_tcgplayer_price(item.scryfall_id, finish=item.finish)
        ref_price = tcg_data.get("price") if tcg_data and tcg_data.get("in_stock") else None

        # 2. Mighty Meeple
        set_name = self.scryfall.get_set_name(item.set_code) if not is_any and item.set_code else None
        mm_data = self.mightymeeple.search_card(
            card_name=item.name,
            set_name=set_name,
            set_code=None if is_any else item.set_code,
            collector_number=None if is_any else item.collector_number,
            finish=item.finish,
        )

        # 3. eBay
        ebay_data = self.ebay.search_card(
            card_name=item.name,
            set_code=None if is_any else item.set_code,
            finish=item.finish,
            reference_price=ref_price,
        )

        now = datetime.now(timezone.utc)
        results = [tcg_data, mm_data, ebay_data]

        # Check previous Mighty Meeple stock status before upserting
        mm_existing = VendorPrice.query.filter_by(watchlist_id=item.id, vendor_name="Mighty Meeple").first()
        mm_was_in_stock = bool(mm_existing and mm_existing.in_stock and mm_existing.price > 0)

        for vendor_data in results:
            if not vendor_data:
                continue

            v_name = vendor_data.get("vendor_name")
            existing = (
                VendorPrice.query.filter_by(
                    watchlist_id=item.id,
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
                    watchlist_id=item.id,
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

        # Check for Mighty Meeple In-Stock Alert
        mm_now_in_stock = bool(mm_data and mm_data.get("in_stock") and mm_data.get("price", 0) > 0)
        global_mm_alert = SystemSetting.get_bool("notify_mm_stock_enabled", default=True)
        card_mm_alert = bool(item.notify_mm_stock if item.notify_mm_stock is not None else True)

        if notify and mm_now_in_stock and not mm_was_in_stock and global_mm_alert and card_mm_alert:
            logger.info(f"Mighty Meeple restock detected for {item.name}! Dispatching stock alert...")
            self.send_discord_mm_stock_alert(item=item, mm_data=mm_data)

        # Check for general deal & send notification
        if notify and item.is_deal:
            best = item.best_vendor
            if best:
                self.send_discord_deal_alert(
                    item=item,
                    best_vendor=best,
                    savings_amount=item.savings_amount,
                    savings_percent=item.savings_percent,
                )

        return {
            "card_id": item.id,
            "card_name": item.name,
            "is_deal": item.is_deal,
            "lowest_price": item.lowest_in_stock_price,
            "mm_in_stock": item.mm_in_stock,
            "vendors": [v.to_dict() for v in item.vendor_prices],
        }

    def poll_all_cards(self, notify: bool = True) -> list[dict]:
        """Polls prices for all cards currently in the database."""
        items = WatchlistItem.query.all()
        summary = []
        logger.info(f"Starting scheduled poll for {len(items)} watchlist items...")

        for item in items:
            try:
                res = self.poll_card(item, notify=notify)
                summary.append(res)
            except Exception as e:
                logger.error(f"Error polling card {item.name} (ID: {item.id}): {e}")

        deals_found = sum(1 for s in summary if s.get("is_deal"))
        now = datetime.now(timezone.utc)
        try:
            SystemSetting.set_val("last_poll_time", now.isoformat())
            SystemSetting.set_val("last_poll_count", len(summary))
            SystemSetting.set_val("last_poll_deals", deals_found)
            SystemSetting.set_val(
                "last_poll_status",
                f"Surveillance cycle complete: {len(summary)} targets monitored, {deals_found} active deals triggered."
            )
        except Exception as e:
            logger.debug(f"Could not persist telemetry to SystemSetting: {e}")

        logger.info(f"Completed poll for all watchlist items ({len(summary)} scanned, {deals_found} deals).")
        return summary

    def poll_user_cards(self, items: list[WatchlistItem], notify: bool = True) -> list[dict]:
        """Polls prices for a specific user's WatchlistItems."""
        summary = []
        for item in items:
            try:
                res = self.poll_card(item, notify=notify)
                summary.append(res)
            except Exception as e:
                logger.error(f"Error polling card {item.name} (ID: {item.id}): {e}")
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

            resp = requests.post(dest_url, json=payload, timeout=8)
            if resp.status_code in (200, 204):
                logger.info(f"Discord deal alert sent successfully for {item_name} to {dest_url[:45]}...")
                return True
            elif resp.status_code == 429:
                logger.warning(f"Discord webhook rate limited (429) for {dest_url[:45]}...")
            elif resp.status_code in (401, 404):
                logger.warning(f"Discord webhook invalid/unauthorized ({resp.status_code}) for {dest_url[:45]}...")
            else:
                logger.warning(f"Discord webhook failed with status code {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send Discord webhook deal alert: {e}")

        return False

    def send_discord_mm_stock_alert(
        self,
        item: WatchlistItem,
        mm_data: dict,
        user: User | None = None,
        webhook_url: str | None = None,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed specifically for Mighty Meeple in-stock restocks."""
        target_user = user or (item.user if item else None)
        dest_url = self.get_effective_webhook_url(user=target_user, override_url=webhook_url)

        if not dest_url:
            logger.debug(f"No Discord Webhook URL configured; skipping Mighty Meeple stock alert for {item.name if item else 'target'}.")
            return False

        try:
            price_val = float(mm_data.get("price", 0.0))
            condition_val = mm_data.get("condition", "NM")
            product_url = mm_data.get("product_url")
            set_label = "Any Version" if (item and item.is_any_version) else (item.set_code if item else "Unknown")
            item_name = item.name if item else "Monitored Card"
            finish_label = item.finish.capitalize() if item else "Nonfoil"

            fields = [
                {
                    "name": "Mighty Meeple Price",
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
                    "value": f"[🛒 Buy Now on Mighty Meeple]({product_url})",
                    "inline": False,
                })

            fields.append({
                "name": "Alert Configuration",
                "value": "You can toggle Mighty Meeple stock alerts on or off anytime in the Chimaera Target Registry.",
                "inline": False,
            })

            embed = {
                "title": f"🎲 Mighty Meeple In-Stock: {item_name}",
                "description": f"**Set:** {set_label} | **Finish:** {finish_label}",
                "color": 0x00CED1,  # Tactical Teal
                "fields": fields,
                "footer": {
                    "text": "Chimaera MTG Tactical Intelligence // Mighty Meeple Surveillance",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if item and item.image_uri:
                embed["thumbnail"] = {"url": item.image_uri}

            payload = {
                "username": "Chimaera Stock Monitor",
                "embeds": [embed],
            }

            resp = requests.post(dest_url, json=payload, timeout=8)
            if resp.status_code in (200, 204):
                logger.info(f"Discord Mighty Meeple stock alert sent successfully for {item_name} to {dest_url[:45]}...")
                return True
            elif resp.status_code == 429:
                logger.warning(f"Discord webhook rate limited (429) for {dest_url[:45]}...")
            elif resp.status_code in (401, 404):
                logger.warning(f"Discord webhook invalid/unauthorized ({resp.status_code}) for {dest_url[:45]}...")
            else:
                logger.warning(f"Discord Mighty Meeple alert failed with status code {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send Discord Mighty Meeple stock alert: {e}")

        return False

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
