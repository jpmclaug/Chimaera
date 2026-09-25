import json
import logging
import re
from datetime import datetime, timezone, timedelta
from config import Config
from models import db, BestBuyItem, BestBuyHistory, SystemSetting

logger = logging.getLogger(__name__)

# Canonical Charlotte, NC Area Best Buy Stores Directory
CHARLOTTE_AREA_STORES = [
    {
        "store_id": "1108",
        "name": "Midtown Charlotte",
        "address": "1055 Metropolitan Ave",
        "city": "Charlotte",
        "state": "NC",
        "postal_code": "28204",
        "distance": 1.2,
    },
    {
        "store_id": "1055",
        "name": "Blakeney",
        "address": "9839 Rea Rd",
        "city": "Charlotte",
        "state": "NC",
        "postal_code": "28277",
        "distance": 12.8,
    },
    {
        "store_id": "1162",
        "name": "Rivergate",
        "address": "14125 River Gate Pkwy",
        "city": "Charlotte",
        "state": "NC",
        "postal_code": "28273",
        "distance": 13.5,
    },
    {
        "store_id": "1022",
        "name": "Northlake",
        "address": "10221 Perimeter Pkwy",
        "city": "Charlotte",
        "state": "NC",
        "postal_code": "28216",
        "distance": 8.7,
    },
    {
        "store_id": "271",
        "name": "Pineville (Carolina Place)",
        "address": "11025 Carolina Place Pkwy",
        "city": "Pineville",
        "state": "NC",
        "postal_code": "28134",
        "distance": 11.4,
    },
    {
        "store_id": "883",
        "name": "Concord Mills",
        "address": "8301 Concord Mills Blvd",
        "city": "Concord",
        "state": "NC",
        "postal_code": "28027",
        "distance": 14.1,
    },
    {
        "store_id": "182",
        "name": "Gastonia",
        "address": "380 E Franklin Blvd",
        "city": "Gastonia",
        "state": "NC",
        "postal_code": "28054",
        "distance": 19.3,
    },
    {
        "store_id": "1098",
        "name": "Matthews",
        "address": "10207 E Independence Blvd",
        "city": "Matthews",
        "state": "NC",
        "postal_code": "28105",
        "distance": 10.6,
    },
    {
        "store_id": "1168",
        "name": "Mooresville",
        "address": "590 River Hwy",
        "city": "Mooresville",
        "state": "NC",
        "postal_code": "28117",
        "distance": 26.5,
    },
    {
        "store_id": "152",
        "name": "Rock Hill, SC",
        "address": "2380 Cherry Rd",
        "city": "Rock Hill",
        "state": "SC",
        "postal_code": "29732",
        "distance": 24.2,
    },
]

# Static Catalog Cache for Known Popular MTG Products at Best Buy
KNOWN_MTG_CATALOG = {
    "6539370": {
        "name": "Magic: The Gathering: The Lord of the Rings: Tales of Middle-earth Draft Booster Multipack",
        "current_price": 14.99,
        "price": 14.99,
        "regular_price": 14.99,
        "image_url": "https://pisces.bbystatic.com/image2/BestBuy_US/images/products/6539/6539370_sd.jpg",
        "product_url": "https://www.bestbuy.com/site/6539370.p?skuId=6539370",
    },
    "6619449": {
        "name": "Magic: The Gathering Final Fantasy Collector Booster (15 Magic Cards)",
        "current_price": 39.99,
        "price": 39.99,
        "regular_price": 39.99,
        "image_url": "https://pisces.bbystatic.com/image2/BestBuy_US/images/products/6619/6619449_sd.jpg",
        "product_url": "https://www.bestbuy.com/site/6619449.p?skuId=6619449",
    },
    "6502686": {
        "name": "Magic: The Gathering Streets of New Capenna Bundle",
        "current_price": 39.99,
        "price": 39.99,
        "regular_price": 39.99,
        "image_url": "https://pisces.bbystatic.com/image2/BestBuy_US/images/products/6502/6502686_sd.jpg",
        "product_url": "https://www.bestbuy.com/site/6502686.p?skuId=6502686",
    },
}


class BestBuyProvider:
    """
    Surveillance engine for monitoring Magic: The Gathering product stock and pricing
    at Best Buy retail stores near the user.

    Supports:
    1. Official Best Buy Developer API (authorized via BESTBUY_API_KEY from developer.bestbuy.com).
       Queries granular real-time in-store pickup stock across stores near postalCode.
    2. Resilient Web Telemetry Fallback (curl_cffi with Safari impersonation) querying
       Best Buy's live priceBlocks endpoint without requiring an API key.
    """

    def __init__(
        self,
        api_key: str | None = None,
        postal_code: str | None = None,
        radius: int | None = None,
    ):
        self.api_key = (api_key or Config.BESTBUY_API_KEY).strip()
        self.postal_code = (postal_code or Config.BESTBUY_POSTAL_CODE or "28202").strip()
        self.radius = radius or Config.BESTBUY_SEARCH_RADIUS or 25

    def get_effective_api_key(self) -> str:
        """Retrieves the effective Best Buy API key from settings or config."""
        db_key = SystemSetting.get_val("bestbuy_api_key")
        if db_key and db_key.strip():
            return db_key.strip()
        return self.api_key

    def get_effective_postal_code(self) -> str:
        """Retrieves the active postal code for store searches."""
        db_zip = SystemSetting.get_val("bestbuy_postal_code")
        if db_zip and db_zip.strip():
            return db_zip.strip()
        return self.postal_code

    def get_effective_radius(self) -> int:
        """Retrieves the active search radius in miles."""
        db_rad = SystemSetting.get_val("bestbuy_search_radius")
        if db_rad:
            try:
                return int(db_rad)
            except (ValueError, TypeError):
                pass
        return self.radius

    def lookup_product_via_api(self, sku: str, api_key: str) -> dict | None:
        """Queries the official Best Buy Developer API for canonical product details."""
        try:
            import requests
            url = f"https://api.bestbuy.com/v1/products/{sku}.json?apiKey={api_key}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                clean_name = BestBuyItem.clean_name_text(data.get("name")) or data.get("name")
                return {
                    "sku": str(data.get("sku", sku)),
                    "name": clean_name,
                    "raw_name": data.get("name"),
                    "current_price": float(data.get("salePrice") or data.get("regularPrice") or 0.0),
                    "regular_price": float(data.get("regularPrice") or data.get("salePrice") or 0.0),
                    "on_sale": bool(data.get("onSale", False)),
                    "image_url": data.get("image") or data.get("largeImage") or "",
                    "product_url": data.get("url") or f"https://www.bestbuy.com/site/{sku}.p?skuId={sku}",
                    "online_available": bool(data.get("onlineAvailability", False)),
                    "in_store_pickup": bool(data.get("inStorePickup", True)),
                    "source": "official_api",
                }
            elif resp.status_code == 404:
                logger.warning(f"Best Buy API returned 404 for SKU {sku}")
            else:
                logger.warning(f"Best Buy API returned HTTP {resp.status_code}: {resp.text[:150]}")
        except Exception as e:
            logger.error(f"Error querying Best Buy API for SKU {sku}: {e}")
        return None

    def lookup_product_via_priceblocks(self, sku: str) -> dict | None:
        """Queries Best Buy's web priceBlocks endpoint using curl_cffi with Safari impersonation."""
        try:
            from curl_cffi import requests as cffi_requests
            url = f"https://www.bestbuy.com/api/3.0/priceBlocks?skus={sku}"
            resp = cffi_requests.get(url, impersonate="safari17_0", timeout=10)
            if resp.status_code == 200:
                items = resp.json()
                if items and isinstance(items, list) and len(items) > 0:
                    sku_block = items[0].get("sku", {})
                    if "error" not in sku_block:
                        names = sku_block.get("names", {})
                        title = names.get("short") or names.get("long") or f"MTG Product (SKU {sku})"
                        clean_name = BestBuyItem.clean_name_text(title) or title
                        price_block = sku_block.get("price", {})
                        price_domain = price_block.get("priceDomain", {})
                        current_price = float(price_domain.get("customerPrice") or price_block.get("currentPrice") or 0.0)
                        regular_price = float(price_domain.get("regularPrice") or current_price)
                        button_state = sku_block.get("buttonState", {}).get("buttonState", "")
                        online_avail = button_state not in ("SOLD_OUT", "UNAVAILABLE")
                        product_url = sku_block.get("url")
                        if product_url and not product_url.startswith("http"):
                            product_url = f"https://www.bestbuy.com{product_url}"

                        return {
                            "sku": str(sku),
                            "name": clean_name,
                            "raw_name": title,
                            "current_price": current_price,
                            "regular_price": regular_price,
                            "on_sale": current_price < regular_price,
                            "image_url": f"https://pisces.bbystatic.com/image2/BestBuy_US/images/products/{sku[:4]}/{sku}_sd.jpg",
                            "product_url": product_url or f"https://www.bestbuy.com/site/{sku}.p?skuId={sku}",
                            "online_available": online_avail,
                            "in_store_pickup": True,
                            "source": "priceblocks",
                        }
        except Exception as e:
            logger.debug(f"PriceBlocks lookup failed for SKU {sku}: {e}")
        return None

    def lookup_product(self, sku: str) -> dict:
        """
        Retrieves product details for a given SKU, trying:
        1. Official Best Buy Developer API (if configured)
        2. Web PriceBlocks endpoint
        3. Known MTG Catalog cache
        """
        sku_clean = str(sku).strip()
        api_key = self.get_effective_api_key()

        # 1. Official API
        if api_key:
            api_result = self.lookup_product_via_api(sku_clean, api_key)
            if api_result:
                return api_result

        # 2. Web PriceBlocks
        pb_result = self.lookup_product_via_priceblocks(sku_clean)
        if pb_result:
            return pb_result

        # 3. Static MTG Catalog Cache
        if sku_clean in KNOWN_MTG_CATALOG:
            cat = KNOWN_MTG_CATALOG[sku_clean].copy()
            cat["sku"] = sku_clean
            cat["source"] = "catalog_cache"
            cat["online_available"] = False
            cat["in_store_pickup"] = True
            return cat

        # Default fallback representation
        return {
            "sku": sku_clean,
            "name": f"Best Buy Product (SKU {sku_clean})",
            "current_price": 0.0,
            "regular_price": 0.0,
            "on_sale": False,
            "image_url": f"https://pisces.bbystatic.com/image2/BestBuy_US/images/products/{sku_clean[:4]}/{sku_clean}_sd.jpg" if len(sku_clean) >= 4 else "",
            "product_url": f"https://www.bestbuy.com/site/{sku_clean}.p?skuId={sku_clean}",
            "online_available": False,
            "in_store_pickup": True,
            "source": "fallback",
        }

    def check_store_availability(
        self,
        sku: str,
        postal_code: str | None = None,
        radius: int | None = None,
    ) -> dict:
        """
        Checks real-time store availability for a specific SKU.
        Returns detailed list of local stores and indicates which stores have it in stock.
        """
        sku_clean = str(sku).strip()
        postal_code = str(postal_code or self.get_effective_postal_code()).strip()
        radius = radius or self.get_effective_radius()
        api_key = self.get_effective_api_key()

        # Base list of local stores
        nearby_stores = []
        for s in CHARLOTTE_AREA_STORES:
            if s.get("distance", 0) <= radius + 5:
                nearby_stores.append({
                    "store_id": s["store_id"],
                    "name": s["name"],
                    "address": s["address"],
                    "city": s["city"],
                    "state": s["state"],
                    "postal_code": s["postal_code"],
                    "distance": s["distance"],
                    "in_stock": False,
                    "low_stock": False,
                    "pickup_url": f"https://www.bestbuy.com/site/store-locator/store/{s['store_id']}",
                })

        in_stock_store_names = []
        ispu_eligible = False

        # If official API key is provided, query real-time store availability endpoint
        if api_key:
            try:
                import requests
                url = (
                    f"https://api.bestbuy.com/v1/products/{sku_clean}/stores.json"
                    f"?postalCode={postal_code}&apiKey={api_key}"
                )
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    api_data = resp.json()
                    ispu_eligible = bool(api_data.get("ispuEligible", False))
                    stores_returned = api_data.get("stores", [])

                    # Map returned stores into our nearby store list
                    returned_ids = {str(st.get("storeId")): st for st in stores_returned}
                    for local_store in nearby_stores:
                        s_id = str(local_store["store_id"])
                        if s_id in returned_ids:
                            st_info = returned_ids[s_id]
                            local_store["in_stock"] = True
                            local_store["low_stock"] = bool(st_info.get("lowStock", False))
                            if st_info.get("distance") is not None:
                                local_store["distance"] = float(st_info.get("distance"))
                            in_stock_store_names.append(local_store["name"])

                    # If API returned stores not in our pre-defined list, include them as well
                    for st in stores_returned:
                        st_id = str(st.get("storeId"))
                        if not any(str(ns["store_id"]) == st_id for ns in nearby_stores):
                            dist = float(st.get("distance", 0.0))
                            if dist <= radius:
                                s_name = st.get("name", f"Best Buy #{st_id}")
                                nearby_stores.append({
                                    "store_id": st_id,
                                    "name": s_name,
                                    "address": st.get("address", ""),
                                    "city": st.get("city", ""),
                                    "state": st.get("state", ""),
                                    "postal_code": st.get("postalCode", ""),
                                    "distance": dist,
                                    "in_stock": True,
                                    "low_stock": bool(st.get("lowStock", False)),
                                    "pickup_url": f"https://www.bestbuy.com/site/store-locator/store/{st_id}",
                                })
                                in_stock_store_names.append(s_name)

                    logger.info(
                        f"Best Buy API check for SKU {sku_clean} near {postal_code}: "
                        f"{len(in_stock_store_names)} stores in stock."
                    )
                    return {
                        "sku": sku_clean,
                        "postal_code": postal_code,
                        "radius": radius,
                        "in_stock": len(in_stock_store_names) > 0,
                        "ispu_eligible": ispu_eligible,
                        "stores_in_stock": in_stock_store_names,
                        "nearby_stores": sorted(nearby_stores, key=lambda x: x.get("distance", 999)),
                        "mode": "official_api",
                    }
                else:
                    logger.warning(f"Best Buy stores.json API returned HTTP {resp.status_code}")
            except Exception as e:
                logger.error(f"Error querying Best Buy stores.json for SKU {sku_clean}: {e}")

        # Web / Fallback Mode (No API key or API call failed)
        # Check product status via priceBlocks
        pb_info = self.lookup_product_via_priceblocks(sku_clean)
        online_avail = pb_info.get("online_available", False) if pb_info else False

        return {
            "sku": sku_clean,
            "postal_code": postal_code,
            "radius": radius,
            "in_stock": online_avail,
            "ispu_eligible": True,
            "stores_in_stock": in_stock_store_names,
            "nearby_stores": sorted(nearby_stores, key=lambda x: x.get("distance", 999)),
            "mode": "web_fallback",
        }

    def sync_all_tracked_items(self, notify: bool = True, deal_engine=None) -> dict:
        """
        Sweeps all active Best Buy items in the database.
        Checks current price, online availability, and local store pickup stock.
        Dispatches Discord restock and price alerts as appropriate.
        """
        now = datetime.now(timezone.utc)
        items = BestBuyItem.query.filter_by(is_active=True).all()

        if not items:
            logger.info("No active Best Buy items to monitor.")
            SystemSetting.set_val("bestbuy_last_scan_status", "No active products registered in surveillance.")
            return {
                "success": True,
                "message": "No active Best Buy items registered.",
                "total_scanned": 0,
                "restocks": 0,
                "price_changes": 0,
                "in_stock_count": 0,
            }

        restocks = []
        price_changes = []
        in_stock_count = 0

        postal_code = self.get_effective_postal_code()
        radius = self.get_effective_radius()

        for item in items:
            try:
                # 1. Product details lookup (price, title, image)
                prod_data = self.lookup_product(item.sku)
                # 2. Store availability check
                avail_data = self.check_store_availability(item.sku, postal_code=postal_code, radius=radius)

                new_price = float(prod_data.get("current_price") or item.current_price)
                new_reg_price = float(prod_data.get("regular_price") or item.regular_price or new_price)
                new_in_stock = bool(avail_data.get("in_stock", False) or prod_data.get("online_available", False))
                new_online_avail = bool(prod_data.get("online_available", False))
                new_stores_in_stock = avail_data.get("stores_in_stock", [])
                new_nearby_stores = avail_data.get("nearby_stores", [])

                price_changed = False
                stock_changed = False
                was_out_of_stock = not item.in_stock
                old_price = item.current_price
                old_stores = set(item.stores_in_stock_list)
                new_stores_set = set(new_stores_in_stock)

                # Price Change Detection
                if new_price > 0 and abs(new_price - item.current_price) >= 0.01:
                    price_changed = True
                    price_delta = round(new_price - item.current_price, 2)
                    item.previous_price = item.current_price
                    item.current_price = new_price
                    item.last_price_change_at = now
                    price_changes.append({
                        "item": item,
                        "old_price": old_price,
                        "new_price": new_price,
                        "delta": price_delta,
                    })

                # Restock / Stock Change Detection
                if new_in_stock != item.in_stock or (new_stores_set != old_stores and len(new_stores_set) > 0):
                    stock_changed = True
                    item.last_stock_change_at = now
                    # Trigger restock alert if previously out of stock, or if newly available at local stores
                    if was_out_of_stock and new_in_stock:
                        restocks.append({
                            "item": item,
                            "stores": new_stores_in_stock,
                        })
                    elif not old_stores and new_stores_set:
                        restocks.append({
                            "item": item,
                            "stores": new_stores_in_stock,
                        })

                # Update item fields
                if prod_data.get("name") and "Best Buy Product (SKU" not in prod_data["name"]:
                    item.name = prod_data["name"]
                if prod_data.get("image_url"):
                    item.image_url = prod_data["image_url"]
                if prod_data.get("product_url"):
                    item.product_url = prod_data["product_url"]

                item.regular_price = new_reg_price
                item.in_stock = new_in_stock
                item.online_available = new_online_avail
                item.stores_in_stock = json.dumps(new_stores_in_stock)
                item.nearby_stores_data = json.dumps(new_nearby_stores)
                item.last_scanned_at = now

                if new_in_stock:
                    in_stock_count += 1

                # Record historical snapshot
                should_record = price_changed or stock_changed
                if not should_record:
                    latest_hist = (
                        BestBuyHistory.query.filter_by(item_id=item.id)
                        .order_by(BestBuyHistory.recorded_at.desc())
                        .first()
                    )
                    if not latest_hist:
                        should_record = True
                    elif latest_hist.recorded_at:
                        rec_time = latest_hist.recorded_at
                        if rec_time.tzinfo is None:
                            rec_time = rec_time.replace(tzinfo=timezone.utc)
                        if (now - rec_time).total_seconds() >= 43200:  # 12 hours
                            should_record = True

                if should_record:
                    hist = BestBuyHistory(
                        item_id=item.id,
                        price=new_price,
                        regular_price=new_reg_price,
                        in_stock=new_in_stock,
                        stores_in_stock_count=len(new_stores_in_stock),
                        stores_in_stock_names=", ".join(new_stores_in_stock),
                        price_change=round(new_price - (item.previous_price or new_price), 2),
                        recorded_at=now,
                    )
                    db.session.add(hist)

            except Exception as item_err:
                logger.error(f"Error processing Best Buy item {item.sku}: {item_err}", exc_info=True)

        db.session.commit()

        # Dispatch Discord Alerts
        if notify and deal_engine:
            for restock in restocks:
                r_item = restock["item"]
                if r_item.notify_on_restock:
                    try:
                        deal_engine.send_discord_bestbuy_restock_alert(
                            item=r_item,
                            available_stores=restock.get("stores", []),
                        )
                    except Exception as e:
                        logger.error(f"Failed to dispatch Best Buy restock alert for {r_item.name}: {e}")

            for pc in price_changes:
                p_item = pc["item"]
                if p_item.notify_on_price_drop and pc["delta"] < 0:
                    try:
                        deal_engine.send_discord_bestbuy_price_alert(
                            item=p_item,
                            old_price=pc["old_price"],
                            new_price=pc["new_price"],
                        )
                    except Exception as e:
                        logger.error(f"Failed to dispatch Best Buy price drop alert for {p_item.name}: {e}")

        # Update telemetry settings
        total_tracked = len(items)
        SystemSetting.record_successful_run("bestbuy", dt=now)
        SystemSetting.set_val("bestbuy_item_count", total_tracked)
        SystemSetting.set_val("bestbuy_in_stock_count", in_stock_count)
        SystemSetting.set_val("bestbuy_price_changes_count", len(price_changes))
        status_msg = (
            f"Best Buy scan complete ({postal_code} area): {total_tracked} products checked, "
            f"{in_stock_count} in stock, {len(restocks)} restocks, {len(price_changes)} price changes."
        )
        SystemSetting.set_val("bestbuy_last_scan_status", status_msg)
        logger.info(status_msg)

        return {
            "success": True,
            "message": status_msg,
            "total_scanned": total_tracked,
            "in_stock_count": in_stock_count,
            "restocks": len(restocks),
            "price_changes": len(price_changes),
        }
