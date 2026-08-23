import logging
import re
import urllib.parse
import requests

logger = logging.getLogger(__name__)

MIGHTY_MEEPLE_BASE = "https://mightymeeple.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class MightyMeepleProvider:
    """Live stock and price scanner targeting Mighty Meeple (BinderPOS/Shopify backend)."""

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def search_card(
        self,
        card_name: str,
        set_name: str | None = None,
        set_code: str | None = None,
        finish: str = "nonfoil",
    ) -> dict:
        """
        Searches Mighty Meeple inventory for card, checks condition variants and stock.
        Returns a normalized vendor price dict.
        """
        if not card_name:
            return self._empty_result(card_name)

        encoded_q = urllib.parse.quote_plus(card_name.strip())
        suggest_url = f"{MIGHTY_MEEPLE_BASE}/search/suggest.json?q={encoded_q}&resources[type]=product"
        fallback_search_url = f"{MIGHTY_MEEPLE_BASE}/search?q={encoded_q}&type=product"

        try:
            resp = self.session.get(suggest_url, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Mighty Meeple suggest returned {resp.status_code}")
                return self._empty_result(card_name, fallback_search_url)

            data = resp.json()
            products = []
            if "resources" in data and "results" in data["resources"]:
                products = data["resources"]["results"].get("products", [])
            elif "products" in data:
                products = data["products"]

            if not products:
                return self._empty_result(card_name, fallback_search_url)

            # Find matching products
            candidate_products = self._filter_products(products, card_name, set_name, set_code)
            if not candidate_products:
                candidate_products = products[:3]  # Fallback to first few results

            best_variant_match = None
            is_foil_target = (finish or "nonfoil").lower() in ("foil", "etched")

            for prod in candidate_products:
                handle = prod.get("handle")
                prod_url = prod.get("url") or f"/products/{handle}"
                if not prod_url.startswith("http"):
                    prod_url = f"{MIGHTY_MEEPLE_BASE}{prod_url}"

                # Query product detail json for exact variants
                variants = self._get_product_variants(handle)
                if not variants:
                    # Fallback to product min price
                    raw_price = prod.get("price") or prod.get("price_min")
                    try:
                        price_num = float(raw_price) if raw_price else 0.0
                    except (ValueError, TypeError):
                        price_num = 0.0

                    if price_num > 0 and prod.get("available", False):
                        return {
                            "vendor_name": "Mighty Meeple",
                            "price": round(price_num, 2),
                            "condition": "NM/LP",
                            "in_stock": True,
                            "product_url": prod_url,
                        }
                    continue

                # Match variants by finish and condition
                matched = self._match_variant(variants, is_foil_target)
                if matched:
                    matched["product_url"] = prod_url
                    if matched.get("in_stock"):
                        return matched
                    elif best_variant_match is None:
                        best_variant_match = matched

            if best_variant_match:
                return best_variant_match

            # Out of stock fallback
            first_prod = candidate_products[0]
            first_url = first_prod.get("url") or f"/products/{first_prod.get('handle', '')}"
            if not first_url.startswith("http"):
                first_url = f"{MIGHTY_MEEPLE_BASE}{first_url}"

            raw_p = first_prod.get("price") or first_prod.get("price_min") or "0.00"
            try:
                p_val = float(raw_p)
            except (ValueError, TypeError):
                p_val = 0.0

            return {
                "vendor_name": "Mighty Meeple",
                "price": round(p_val, 2),
                "condition": "NM",
                "in_stock": False,
                "product_url": first_url,
            }

        except Exception as e:
            logger.error(f"Error checking Mighty Meeple for '{card_name}': {e}")
            return self._empty_result(card_name, fallback_search_url)

    def _get_product_variants(self, handle: str) -> list[dict]:
        """Fetches Shopify product variant details via .js endpoint."""
        if not handle:
            return []
        try:
            url = f"{MIGHTY_MEEPLE_BASE}/products/{handle}.js"
            r = self.session.get(url, timeout=8)
            if r.status_code == 200:
                data = r.json()
                return data.get("variants", [])
        except Exception as e:
            logger.debug(f"Failed to fetch variants for handle {handle}: {e}")
        return []

    def _filter_products(
        self,
        products: list[dict],
        card_name: str,
        set_name: str | None,
        set_code: str | None,
    ) -> list[dict]:
        """Filters products to those matching card name and optionally set."""
        clean_target = re.sub(r"[^\w\s]", "", card_name.lower())
        matched = []

        for p in products:
            title = p.get("title", "")
            clean_title = re.sub(r"[^\w\s]", "", title.lower())

            # Card name must be present
            if clean_target in clean_title:
                # If set name or code provided, score preference
                if set_name and set_name.lower() in title.lower():
                    matched.insert(0, p)
                elif set_code and f"[{set_code.lower()}]" in title.lower():
                    matched.insert(0, p)
                else:
                    matched.append(p)

        return matched if matched else products

    def _match_variant(self, variants: list[dict], is_foil_target: bool) -> dict | None:
        """
        Picks the best variant according to finish and condition priority:
        1. NM In-stock matching finish
        2. LP In-stock matching finish
        3. MP In-stock matching finish
        4. Any in-stock matching finish
        5. Out-of-stock NM matching finish
        """
        scored_variants = []

        for v in variants:
            title = v.get("title", "") or v.get("name", "")
            title_lower = title.lower()
            is_foil = "foil" in title_lower
            is_available = v.get("available", False)

            # Filter finish compatibility
            if is_foil_target != is_foil:
                continue

            price_cents = v.get("price", 0)
            price_dollars = price_cents / 100.0 if isinstance(price_cents, (int, float)) else 0.0

            # Condition ranking
            cond_score = 0
            cond_label = "NM"
            if "near mint" in title_lower or "nm" in title_lower:
                cond_score = 4
                cond_label = "NM"
            elif "lightly played" in title_lower or "lp" in title_lower:
                cond_score = 3
                cond_label = "LP"
            elif "moderately played" in title_lower or "mp" in title_lower:
                cond_score = 2
                cond_label = "MP"
            elif "heavily played" in title_lower or "hp" in title_lower:
                cond_score = 1
                cond_label = "HP"
            else:
                cond_score = 2
                cond_label = "Played"

            finish_label = "Foil" if is_foil else ""
            full_cond = f"{cond_label} {finish_label}".strip()

            stock_score = 10 if is_available else 0
            total_score = stock_score + cond_score

            scored_variants.append((
                total_score,
                is_available,
                price_dollars,
                full_cond,
            ))

        if not scored_variants:
            return None

        # Sort by highest score, then lowest price
        scored_variants.sort(key=lambda item: (-item[0], item[2] if item[2] > 0 else 999999))
        best = scored_variants[0]

        return {
            "vendor_name": "Mighty Meeple",
            "price": round(best[2], 2),
            "condition": best[3],
            "in_stock": best[1],
        }

    def _empty_result(self, card_name: str, url: str | None = None) -> dict:
        """Returns empty/out-of-stock response."""
        encoded = urllib.parse.quote_plus(card_name) if card_name else ""
        return {
            "vendor_name": "Mighty Meeple",
            "price": 0.0,
            "condition": "NM",
            "in_stock": False,
            "product_url": url or f"{MIGHTY_MEEPLE_BASE}/search?q={encoded}&type=product",
        }
