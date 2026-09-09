import logging
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from config import Config
from models import db, MicrocenterItem, MicrocenterHistory, SystemSetting, User

logger = logging.getLogger(__name__)

# Charlotte, NC MicroCenter Store ID
CHARLOTTE_STORE_ID = "175"
CHARLOTTE_STORE_NAME = "Charlotte"

MICROCENTER_SEARCH_BASE = "https://www.microcenter.com/search/search_results.aspx"
MICROCENTER_DEFAULT_FQ = "category:Tabletop+Games|646,brand:Wizards+of+the+Coast,Subcategory:Trading+Card+Game"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
DEFAULT_IMPERSONATION_TARGETS = [
    "safari17_0",
    "safari",
    "safari_ios",
    "chrome124",
    "chrome",
]


class MicrocenterProvider:
    """
    Live inventory scraper and price surveillance engine for MicroCenter (Charlotte Store #175).
    Extracts Magic: The Gathering products, stock quantities, and pricing without requiring an API.
    """

    def __init__(
        self,
        store_id: str = CHARLOTTE_STORE_ID,
        store_name: str = CHARLOTTE_STORE_NAME,
        impersonation_targets: list[str] | None = None,
    ):
        self.store_id = str(store_id or CHARLOTTE_STORE_ID).strip()
        self.store_name = str(store_name or CHARLOTTE_STORE_NAME).strip()
        self.impersonation_targets = list(impersonation_targets or DEFAULT_IMPERSONATION_TARGETS)
        self.active_impersonation = self.impersonation_targets[0] if self.impersonation_targets else "safari17_0"

    def _get_session(self):
        """Creates a requests session configured with Charlotte store cookies and TLS impersonation."""
        try:
            from curl_cffi import requests as cffi_requests
            session = cffi_requests.Session()
            session.cookies.set("storeSelected", self.store_id, domain=".microcenter.com")
            session.cookies.set("myStore", "true", domain=".microcenter.com")
            session.cookies.set("rpp", "96", domain=".microcenter.com")
            return session, True
        except ImportError:
            import requests
            session = requests.Session()
            session.headers.update({
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            session.cookies.set("storeSelected", self.store_id, domain=".microcenter.com")
            session.cookies.set("myStore", "true", domain=".microcenter.com")
            session.cookies.set("rpp", "96", domain=".microcenter.com")
            return session, False

    @staticmethod
    def _is_cloudflare_challenge(resp) -> bool:
        """Detects if response is a Cloudflare managed challenge or block."""
        if resp is None:
            return True
        if resp.status_code in (403, 429, 503):
            return True
        if hasattr(resp, "headers") and "cf-mitigated" in resp.headers:
            return True
        text_lower = (getattr(resp, "text", "") or "")[:1500].lower()
        if "<title>just a moment...</title>" in text_lower or "challenges.cloudflare.com" in text_lower:
            return True
        return False

    def _fetch_with_impersonation(self, session, is_cffi: bool, url: str, timeout: int = 25):
        """Fetches a URL, attempting the active impersonation target and rotating through fallbacks if blocked."""
        if not is_cffi:
            return session.get(url, timeout=timeout)

        # Build list of targets starting with the currently active one
        targets = [self.active_impersonation] + [t for t in self.impersonation_targets if t != self.active_impersonation]
        last_resp = None

        for target in targets:
            try:
                resp = session.get(url, impersonate=target, timeout=timeout)
                if self._is_cloudflare_challenge(resp):
                    logger.warning(
                        f"MicroCenter request with impersonation '{target}' flagged by Cloudflare (HTTP {resp.status_code}). Attempting fallback..."
                    )
                    last_resp = resp
                    continue
                # Successful or non-challenge response
                self.active_impersonation = target
                return resp
            except Exception as e:
                logger.warning(f"Error fetching MicroCenter using impersonation '{target}': {e}")
                continue

        return last_resp

    def scrape_charlotte_inventory(self, rpp: int = 96, max_pages: int = 5) -> list[dict]:
        """
        Scrapes all Magic: The Gathering trading card products from the MicroCenter Charlotte store.
        Returns a list of normalized product dictionaries.
        """
        session, is_cffi = self._get_session()
        all_products = []
        seen_skus = set()

        for page in range(1, max_pages + 1):
            url = (
                f"{MICROCENTER_SEARCH_BASE}"
                f"?fq={MICROCENTER_DEFAULT_FQ}"
                f"&storeid={self.store_id}"
                f"&rpp={rpp}"
                f"&page={page}"
            )
            logger.info(f"Fetching MicroCenter Charlotte MTG inventory (Page {page}, Store {self.store_id})...")

            try:
                resp = self._fetch_with_impersonation(session, is_cffi, url, timeout=25)

                if resp is None or resp.status_code != 200 or self._is_cloudflare_challenge(resp):
                    status = resp.status_code if resp is not None else "No Response"
                    logger.warning(f"MicroCenter scraper received HTTP {status} on page {page}")
                    break

                page_products = self.parse_search_html(resp.text)
                if not page_products:
                    logger.info(f"No more products found on page {page}. Scraping complete.")
                    break

                for p in page_products:
                    sku = p.get("sku")
                    if sku and sku not in seen_skus:
                        seen_skus.add(sku)
                        all_products.append(p)

                # If fewer products returned than rpp, this is the last page
                if len(page_products) < rpp:
                    break

            except Exception as e:
                logger.error(f"Error scraping MicroCenter page {page}: {e}", exc_info=True)
                break

        logger.info(f"MicroCenter Charlotte inventory sweep complete: {len(all_products)} products found.")
        return all_products

    def parse_search_html(self, html_content: str) -> list[dict]:
        """Parses MicroCenter search results HTML into structured product dictionaries."""
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        product_wrappers = soup.select("li.product_wrapper, .product_wrapper")
        products = []

        for wrapper in product_wrappers:
            # 1. Product Name & URL & ID
            title_el = wrapper.select_one(".pDescription a")
            name = ""
            product_url = ""
            product_id = ""

            if title_el:
                name = title_el.get_text(strip=True)
                rel_url = title_el.get("href", "")
                product_url = f"https://www.microcenter.com{rel_url}" if rel_url.startswith("/") else rel_url
                product_id = title_el.get("data-id", "")

            if not name:
                img_link = wrapper.select_one("a.image2, a.productClickItemV2")
                if img_link:
                    name = img_link.get("data-name", "")
                    if not product_url:
                        rel_url = img_link.get("href", "")
                        product_url = f"https://www.microcenter.com{rel_url}" if rel_url.startswith("/") else rel_url

            if not name:
                continue

            # Extract product_id from title, image link, or any data-id attribute in wrapper
            if not product_id:
                for el in wrapper.select("[data-id]"):
                    val = el.get("data-id")
                    if val:
                        product_id = str(val).strip()
                        break

            if not product_id and product_url:
                m_pid = re.search(r"/product/(\d+)", product_url)
                if m_pid:
                    product_id = m_pid.group(1)

            # 2. SKU
            sku = ""
            sku_el = wrapper.select_one(".sku")
            if sku_el:
                sku = sku_el.get_text(strip=True).replace("SKU:", "").strip()
            if not sku:
                sku_input = wrapper.select_one("input[name='sku']")
                if sku_input:
                    sku = sku_input.get("value", "").strip()
            if not sku and product_id:
                sku = product_id

            # 3. Product Image URL
            image_url = ""
            img_el = wrapper.select_one("img.SearchResultProductImage, .image2 img")
            if img_el:
                image_url = img_el.get("src", "")

            # 4. Price
            price = 0.0
            price_el = wrapper.select_one("span[itemprop='price']")
            if price_el:
                price_txt = price_el.get_text(separator=" ", strip=True)
                price_match = re.search(r"(\d+\.\d{2}|\d+)", price_txt)
                if price_match:
                    try:
                        price = float(price_match.group(1))
                    except ValueError:
                        price = 0.0
            elif wrapper.select_one("a.image2") and wrapper.select_one("a.image2").get("data-price"):
                try:
                    price = float(wrapper.select_one("a.image2").get("data-price", 0))
                except ValueError:
                    price = 0.0

            # 5. Original / Strike-through Price
            original_price = None
            strike_el = wrapper.select_one(".strike, .original-price, .old-price, .rebate-price .price")
            if strike_el:
                stk_match = re.search(r"\$?(\d+\.\d{2})", strike_el.get_text(strip=True))
                if stk_match:
                    try:
                        parsed_orig = float(stk_match.group(1))
                        if parsed_orig > price:
                            original_price = parsed_orig
                    except ValueError:
                        pass

            # 6. Stock Status & Quantity
            stock_text = ""
            in_stock = False
            stock_count = None
            stock_el = wrapper.select_one(".stock")

            if stock_el:
                stock_text = " ".join(stock_el.get_text(strip=True).split())
                inv_cnt_el = stock_el.select_one(".inventoryCnt")
                if inv_cnt_el:
                    cnt_txt = inv_cnt_el.get_text(strip=True)
                    in_stock = "in stock" in cnt_txt.lower()
                    num_m = re.search(r"(\d+)\+?", cnt_txt)
                    if num_m:
                        stock_count = int(num_m.group(1))
                elif "in stock" in stock_text.lower():
                    in_stock = True
                    num_m = re.search(r"(\d+)\+?", stock_text)
                    if num_m:
                        stock_count = int(num_m.group(1))
                elif "sold out" in stock_text.lower() or "out of stock" in stock_text.lower():
                    in_stock = False
                    stock_count = 0
            else:
                btn = wrapper.select_one("button[name='ADDtoCART'], .STBTN")
                if btn:
                    in_stock = True
                    stock_text = "In Stock"

            products.append({
                "sku": sku,
                "product_id": product_id,
                "name": name,
                "price": price,
                "original_price": original_price,
                "in_stock": in_stock,
                "stock_count": stock_count,
                "stock_text": stock_text,
                "product_url": product_url,
                "image_url": image_url,
                "store_id": self.store_id,
                "store_name": self.store_name,
            })

        return products

    def sync_inventory(self, notify: bool = True, deal_engine=None) -> dict:
        """
        Performs a full scrape and database synchronization of the MicroCenter Charlotte store inventory.
        Detects price changes, restocks, records historical snapshots, and dispatches alerts.
        """
        now = datetime.now(timezone.utc)
        scraped_products = self.scrape_charlotte_inventory()

        if not scraped_products:
            err_msg = "No products could be scraped from MicroCenter (Cloudflare challenge or connection error)."
            logger.warning(f"MicroCenter sync: {err_msg}")
            try:
                SystemSetting.set_val("microcenter_last_scan_status", f"Failed: {err_msg}")
            except Exception:
                pass
            return {
                "success": False,
                "error": err_msg,
                "message": err_msg,
                "total_scanned": 0,
                "new_items": 0,
                "updated_items": 0,
                "price_changes": 0,
                "restocks": 0,
            }

        new_count = 0
        updated_count = 0
        price_changes = []
        restocks = []
        low_stock_alerts = []
        scraped_skus = set()

        for item_data in scraped_products:
            sku = item_data.get("sku")
            if not sku:
                continue
            scraped_skus.add(sku)

            existing = MicrocenterItem.query.filter_by(sku=sku).first()
            new_price = float(item_data.get("price", 0.0))
            new_orig_price = item_data.get("original_price")
            new_in_stock = bool(item_data.get("in_stock", True))
            new_stock_count = item_data.get("stock_count")
            new_stock_text = item_data.get("stock_text") or ""
            product_url = item_data.get("product_url")
            image_url = item_data.get("image_url")
            raw_name = item_data.get("name")
            cleaned_name = MicrocenterItem.clean_name_text(raw_name) or raw_name
            prod_id = item_data.get("product_id")

            if not existing:
                # Brand new item discovered
                new_item = MicrocenterItem(
                    sku=sku,
                    product_id=prod_id,
                    name=cleaned_name,
                    product_url=product_url,
                    image_url=image_url,
                    current_price=new_price,
                    previous_price=None,
                    original_price=new_orig_price,
                    in_stock=new_in_stock,
                    stock_count=new_stock_count,
                    stock_text=new_stock_text,
                    store_id=self.store_id,
                    store_name=self.store_name,
                    first_seen_at=now,
                    last_scanned_at=now,
                    last_price_change_at=now,
                    last_stock_change_at=now,
                    is_active=True,
                )
                db.session.add(new_item)
                db.session.flush()

                # Record initial historical snapshot
                init_hist = MicrocenterHistory(
                    item_id=new_item.id,
                    price=new_price,
                    original_price=new_orig_price,
                    in_stock=new_in_stock,
                    stock_count=new_stock_count,
                    stock_text=new_stock_text,
                    price_change=0.0,
                    stock_change=0,
                    recorded_at=now,
                )
                db.session.add(init_hist)
                new_count += 1

                # Check if brand new item is already low stock (<= 5 units)
                if new_in_stock and (new_stock_count is not None and 0 < new_stock_count <= 5) and new_item.notify_on_low_stock:
                    low_stock_alerts.append(new_item)

            else:
                # Existing item - check for price and stock changes
                price_changed = False
                stock_changed = False
                was_out_of_stock = not existing.in_stock
                old_price = existing.current_price
                old_stock_count = existing.stock_count

                # Price Change Detection
                if abs(new_price - existing.current_price) >= 0.01:
                    price_changed = True
                    price_delta = round(new_price - existing.current_price, 2)
                    existing.previous_price = existing.current_price
                    existing.current_price = new_price
                    existing.last_price_change_at = now
                    price_changes.append({
                        "item": existing,
                        "old_price": old_price,
                        "new_price": new_price,
                        "delta": price_delta,
                    })

                # Stock / Restock Change Detection
                if new_in_stock != existing.in_stock or (new_stock_count is not None and new_stock_count != existing.stock_count):
                    stock_changed = True
                    existing.last_stock_change_at = now
                    if was_out_of_stock and new_in_stock:
                        restocks.append(existing)

                # Low Stock Alert Detection (First time hitting <= 5 units)
                is_low_stock = new_in_stock and (new_stock_count is not None and 0 < new_stock_count <= 5)
                was_not_low_stock = (not existing.in_stock) or (old_stock_count is None) or (old_stock_count > 5)
                if is_low_stock and was_not_low_stock and existing.notify_on_low_stock:
                    low_stock_alerts.append(existing)

                # Update metadata fields
                existing.name = cleaned_name or existing.name
                existing.product_id = prod_id or existing.product_id
                existing.product_url = product_url or existing.product_url
                if image_url:
                    existing.image_url = image_url
                if new_orig_price:
                    existing.original_price = new_orig_price
                existing.in_stock = new_in_stock
                existing.stock_count = new_stock_count
                existing.stock_text = new_stock_text
                existing.last_scanned_at = now
                existing.is_active = True

                # Check if we should record a historical snapshot
                latest_hist = (
                    MicrocenterHistory.query.filter_by(item_id=existing.id)
                    .order_by(MicrocenterHistory.recorded_at.desc())
                    .first()
                )

                should_record_hist = False
                if price_changed or stock_changed or not latest_hist:
                    should_record_hist = True
                elif latest_hist and latest_hist.recorded_at:
                    rec_time = latest_hist.recorded_at
                    if rec_time.tzinfo is None:
                        rec_time = rec_time.replace(tzinfo=timezone.utc)
                    if (now - rec_time).total_seconds() >= 72000:  # 20 hours
                        should_record_hist = True

                if should_record_hist:
                    hist_price_change = round(new_price - (latest_hist.price if latest_hist else new_price), 2)
                    hist_stock_change = (new_stock_count - latest_hist.stock_count) if (latest_hist and new_stock_count is not None and latest_hist.stock_count is not None) else 0
                    snap = MicrocenterHistory(
                        item_id=existing.id,
                        price=new_price,
                        original_price=new_orig_price or existing.original_price,
                        in_stock=new_in_stock,
                        stock_count=new_stock_count,
                        stock_text=new_stock_text,
                        price_change=hist_price_change,
                        stock_change=hist_stock_change,
                        recorded_at=now,
                    )
                    db.session.add(snap)

                updated_count += 1

        # Check for unlisted items from previous scans
        unlisted_items = MicrocenterItem.query.filter(
            MicrocenterItem.store_id == self.store_id,
            ~MicrocenterItem.sku.in_(scraped_skus)
        ).all()
        for unlisted in unlisted_items:
            unlisted.in_stock = False
            unlisted.stock_count = 0
            unlisted.stock_text = "Out of Stock / Unlisted"
            unlisted.last_scanned_at = now

        db.session.commit()

        # Dispatch Discord Alerts
        if notify and deal_engine:
            for pc in price_changes:
                item = pc["item"]
                if item.notify_on_price_change:
                    try:
                        deal_engine.send_discord_microcenter_price_alert(
                            item=item,
                            old_price=pc["old_price"],
                            new_price=pc["new_price"],
                        )
                    except Exception as e:
                        logger.error(f"Failed to dispatch MicroCenter price alert for {item.name}: {e}")

            for r_item in restocks:
                if r_item.notify_on_restock:
                    try:
                        deal_engine.send_discord_microcenter_restock_alert(item=r_item)
                    except Exception as e:
                        logger.error(f"Failed to dispatch MicroCenter restock alert for {r_item.name}: {e}")

            for ls_item in low_stock_alerts:
                if ls_item.notify_on_low_stock:
                    try:
                        deal_engine.send_discord_microcenter_low_stock_alert(item=ls_item)
                    except Exception as e:
                        logger.error(f"Failed to dispatch MicroCenter low stock alert for {ls_item.name}: {e}")

        # Update telemetry in SystemSetting
        total_tracked = MicrocenterItem.query.filter_by(store_id=self.store_id).count()
        in_stock_tracked = MicrocenterItem.query.filter_by(store_id=self.store_id, in_stock=True).count()
        SystemSetting.record_successful_run("microcenter", dt=now)
        SystemSetting.set_val("microcenter_item_count", total_tracked)
        SystemSetting.set_val("microcenter_in_stock_count", in_stock_tracked)
        SystemSetting.set_val("microcenter_price_changes_count", len(price_changes))
        SystemSetting.set_val(
            "microcenter_last_scan_status",
            f"Charlotte store scan complete: {len(scraped_products)} items scanned, {len(price_changes)} price changes, {len(restocks)} restocks."
        )

        return {
            "success": True,
            "message": f"Successfully synchronized {len(scraped_products)} MicroCenter Charlotte products.",
            "total_scanned": len(scraped_products),
            "new_items": new_count,
            "updated_items": updated_count,
            "price_changes": len(price_changes),
            "restocks": len(restocks),
            "in_stock_count": in_stock_tracked,
        }
