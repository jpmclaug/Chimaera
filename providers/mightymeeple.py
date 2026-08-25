import logging
import re
import unicodedata
import urllib.parse
import requests

logger = logging.getLogger(__name__)

MIGHTY_MEEPLE_BASE = "https://mightymeeple.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Junk terms indicating non-playable single items or merchandise on Mighty Meeple
JUNK_TERMS = [
    "art card",
    "art series",
    "artist card",
    "token",
    "playmat",
    "sleeve",
    "sleeves",
    "deck box",
    "binder",
    "booster pack",
    "booster box",
    "collector booster",
    "bundle",
    "display",
    "case",
    "draft pack",
]


def normalize_card_text(text: str) -> str:
    """Normalizes card names and titles to lowercase ASCII with uniform whitespace."""
    if not text:
        return ""
    # Strip diacritics and accents (e.g. Dáin -> Dain)
    norm = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    # Replace non-alphanumeric characters with spaces
    cleaned = re.sub(r"[^\w\s]", " ", norm.lower())
    return " ".join(cleaned.split())


def extract_base_name_from_title(title: str) -> str:
    """
    Extracts the base card name from Mighty Meeple product titles:
    e.g. 'The One Ring (Borderless) [The Hobbit]' -> 'The One Ring'
    """
    # Remove bracketed set info: [The Lord of the Rings: Tales of Middle-Earth]
    t = re.sub(r"\[.*?\]", "", title)
    # Remove parenthesized variant info: (Borderless), (Extended Art), (Showcase)
    t = re.sub(r"\(.*?\)", "", t)
    return t.strip()


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

        # Normalize card name and extract primary face for DFC/split cards
        clean_name = unicodedata.normalize("NFKD", card_name).encode("ASCII", "ignore").decode("utf-8").strip()
        primary_name = clean_name.split(" // ")[0].strip()

        fallback_search_url = f"{MIGHTY_MEEPLE_BASE}/search?q={urllib.parse.quote_plus(clean_name)}&type=product"

        try:
            # 1. Search using exact phrase in double quotes to prevent Shopify partial word scatter
            quoted_q = f'"{primary_name}"'
            suggest_url = (
                f"{MIGHTY_MEEPLE_BASE}/search/suggest.json"
                f"?q={urllib.parse.quote_plus(quoted_q)}&resources[type]=product"
            )

            products = self._fetch_suggest_products(suggest_url)

            # 2. If exact phrase returned no products, fall back to unquoted query
            if not products:
                unquoted_url = (
                    f"{MIGHTY_MEEPLE_BASE}/search/suggest.json"
                    f"?q={urllib.parse.quote_plus(primary_name)}&resources[type]=product"
                )
                products = self._fetch_suggest_products(unquoted_url)

            if not products:
                return self._empty_result(card_name, fallback_search_url)

            # 3. Strictly filter products to ensure legitimate card name match
            candidate_products = self._filter_products(products, card_name, set_name, set_code)
            if not candidate_products:
                return self._empty_result(card_name, fallback_search_url)

            in_stock_matches = []
            out_of_stock_matches = []
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

                    is_avail = bool(prod.get("available", False))
                    entry = {
                        "vendor_name": "Mighty Meeple",
                        "price": round(price_num, 2),
                        "condition": "NM/LP" if is_avail else "NM",
                        "in_stock": is_avail,
                        "product_url": prod_url,
                    }
                    if is_avail and price_num > 0:
                        in_stock_matches.append(entry)
                    else:
                        out_of_stock_matches.append(entry)
                    continue

                # Match variants by finish and condition
                matched = self._match_variant(variants, is_foil_target)
                if matched:
                    matched["product_url"] = prod_url
                    if matched.get("in_stock") and matched.get("price", 0) > 0:
                        in_stock_matches.append(matched)
                    else:
                        out_of_stock_matches.append(matched)

            if in_stock_matches:
                in_stock_matches.sort(key=lambda m: m["price"])
                return in_stock_matches[0]

            if out_of_stock_matches:
                # Prefer out-of-stock matches that have a recorded positive price, sorted by lowest price
                priced_oos = [m for m in out_of_stock_matches if m.get("price", 0) > 0]
                if priced_oos:
                    priced_oos.sort(key=lambda m: m["price"])
                    return priced_oos[0]
                return out_of_stock_matches[0]

            # Out of stock fallback referencing the first valid matched product
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

    def _fetch_suggest_products(self, url: str) -> list[dict]:
        """Fetches product suggestions from Mighty Meeple Shopify suggest endpoint."""
        try:
            resp = self.session.get(url, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if "resources" in data and "results" in data["resources"]:
                    return data["resources"]["results"].get("products", [])
                elif "products" in data:
                    return data["products"]
        except Exception as e:
            logger.debug(f"Failed suggest query to {url}: {e}")
        return []

    _variant_cache: dict[str, list[dict]] = {}

    def _get_product_variants(self, handle: str) -> list[dict]:
        """Fetches Shopify product variant details via .js endpoint with caching."""
        if not handle:
            return []
        if handle in MightyMeepleProvider._variant_cache:
            return MightyMeepleProvider._variant_cache[handle]

        for attempt in range(2):
            try:
                url = f"{MIGHTY_MEEPLE_BASE}/products/{handle}.js"
                r = self.session.get(url, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    variants = data.get("variants", [])
                    if variants:
                        MightyMeepleProvider._variant_cache[handle] = variants
                        return variants
            except Exception as e:
                logger.debug(f"Attempt {attempt+1} failed to fetch variants for handle {handle}: {e}")
        return []

    def _is_card_name_match(self, title: str, card_name: str) -> bool:
        """Determines if a Mighty Meeple product title genuinely matches the target card name."""
        norm_target = normalize_card_text(card_name)
        if not norm_target:
            return False

        norm_title = normalize_card_text(title)

        # Exclude junk / non-playable products unless target card name contains that term
        for jt in JUNK_TERMS:
            if jt in norm_title and jt not in norm_target:
                return False

        # Extract base card name by stripping set brackets [ ] and variant parentheses ( )
        extracted_name = extract_base_name_from_title(title)
        norm_extracted = normalize_card_text(extracted_name)

        # 1. Exact match on extracted base card name
        if norm_extracted == norm_target:
            return True

        # 2. Split cards / DFC handling (e.g. Fire // Ice, Wear // Tear)
        if "//" in card_name or "/" in card_name:
            parts = [normalize_card_text(p) for p in re.split(r"/+", card_name) if p.strip()]
            if parts and all(p in norm_title for p in parts):
                return True

        # 3. Match exact word boundary in the title section preceding the set brackets
        before_set = title.split("[")[0]
        norm_before_set = normalize_card_text(before_set)
        pattern = rf"\b{re.escape(norm_target)}\b"
        if re.search(pattern, norm_before_set):
            return True

        return False

    def _filter_products(
        self,
        products: list[dict],
        card_name: str,
        set_name: str | None,
        set_code: str | None,
    ) -> list[dict]:
        """
        Filters products to those strictly matching card name, with optional set preference.
        Returns set-matched products if found, or all valid card matches as fallback.
        """
        set_matched = []
        fallback_matched = []
        primary_name = card_name.split(" // ")[0].strip()

        target_set_terms = []
        if set_name:
            target_set_terms.append(set_name.lower().strip())
        if set_code:
            clean_code = set_code.lower().strip()
            target_set_terms.append(f"[{clean_code}]")
            target_set_terms.append(clean_code)

        for p in products:
            title = p.get("title", "")

            # Verify genuine card name match
            if self._is_card_name_match(title, card_name) or self._is_card_name_match(title, primary_name):
                title_lower = title.lower()
                is_set_match = False
                if target_set_terms:
                    for st in target_set_terms:
                        if st in title_lower:
                            is_set_match = True
                            break

                if is_set_match:
                    set_matched.append(p)
                else:
                    fallback_matched.append(p)

        if set_matched:
            return set_matched
        return fallback_matched

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
            elif "damaged" in title_lower or "dmg" in title_lower:
                cond_score = 0
                cond_label = "Damaged"
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
