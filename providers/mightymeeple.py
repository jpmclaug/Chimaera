import logging
import re
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

logger = logging.getLogger(__name__)

MIGHTY_MEEPLE_BASE = "https://mightymeeple.com"
BINDERPOS_PORTAL_BASE = "https://portal.binderpos.com"
MIGHTY_MEEPLE_STORE_ID = "7b044554-df1f-4771-a58e-39092834f726"
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
        collector_number: str | None = None,
        finish: str = "nonfoil",
    ) -> dict:
        """
        Searches Mighty Meeple inventory for card, checks condition variants and stock.
        Accurately pairs specific card variants using SKU and collector number.
        Returns a normalized vendor price dict.
        """
        if not card_name:
            return self._empty_result(card_name)

        # Normalize card name and extract primary face for DFC/split cards
        clean_name = unicodedata.normalize("NFKD", card_name).encode("ASCII", "ignore").decode("utf-8").strip()
        primary_name = clean_name.split(" // ")[0].strip()

        fallback_search_url = f"{MIGHTY_MEEPLE_BASE}/search?q={urllib.parse.quote_plus(clean_name)}&type=product"

        try:
            # 1. Search using both quoted and unquoted query to discover all variants
            quoted_q = f'"{primary_name}"'
            suggest_url_quoted = (
                f"{MIGHTY_MEEPLE_BASE}/search/suggest.json"
                f"?q={urllib.parse.quote_plus(quoted_q)}&resources[type]=product&resources[limit]=25"
            )
            suggest_url_unquoted = (
                f"{MIGHTY_MEEPLE_BASE}/search/suggest.json"
                f"?q={urllib.parse.quote_plus(primary_name)}&resources[type]=product&resources[limit]=25"
            )

            raw_prods_quoted = self._fetch_suggest_products(suggest_url_quoted)
            raw_prods_unquoted = self._fetch_suggest_products(suggest_url_unquoted)

            # Deduplicate products by handle while preserving discovery order
            seen_handles = set()
            products = []
            for p in raw_prods_quoted + raw_prods_unquoted:
                h = p.get("handle")
                if h and h not in seen_handles:
                    seen_handles.add(h)
                    products.append(p)

            if not products:
                return self._empty_result(card_name, fallback_search_url)

            # 2. Strictly filter products to match card name and specific variant / set
            candidate_products = self._filter_products(
                products=products,
                card_name=card_name,
                set_name=set_name,
                set_code=set_code,
                collector_number=collector_number,
            )
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

        # Exclude junk / non-playable products unless target card name contains that term (whole word matching)
        for jt in JUNK_TERMS:
            jt_norm = normalize_card_text(jt)
            if re.search(r"\b" + re.escape(jt_norm) + r"\b", norm_title):
                if not re.search(r"\b" + re.escape(jt_norm) + r"\b", norm_target):
                    return False

        # Extract base card name by stripping set brackets [ ] and variant parentheses ( )
        base = extract_base_name_from_title(title)
        if not base:
            return False
        norm_base = normalize_card_text(base)

        # 1. Exact match on extracted base card name
        if norm_base == norm_target:
            return True

        # 2. Alt-names / UB skins (e.g. 'Zidane Tribal - Ragavan, Nimble Pilferer', 'Zilortha, Strength Incarnate - Godzilla...')
        if any(sep in base for sep in (" - ", " — ", " – ")):
            parts = [normalize_card_text(p) for p in re.split(r"\s+[-—–]\s+", base) if p.strip()]
            if norm_target in parts:
                return True

        # 3. Double-faced / Split cards / Adventures / Flip cards
        title_faces = [normalize_card_text(f) for f in re.split(r"/+", base) if f.strip()]
        if norm_target in title_faces:
            return True

        if "//" in card_name or "/" in card_name:
            target_faces = [normalize_card_text(f) for f in re.split(r"/+", card_name) if f.strip()]
            if target_faces == title_faces:
                return True
            if len(target_faces) > 1 and len(title_faces) == 1 and title_faces[0] == target_faces[0]:
                return True

        return False

    def _filter_products(
        self,
        products: list[dict],
        card_name: str,
        set_name: str | None,
        set_code: str | None,
        collector_number: str | None = None,
    ) -> list[dict]:
        """
        Filters products strictly matching card name, with high-precision variant matching
        via SKU, collector number, promo types, and set name.
        """
        primary_name = card_name.split(" // ")[0].strip()

        # Step 1: Filter products that genuinely match the card name
        valid_name_products = []
        for p in products:
            title = p.get("title", "")
            if self._is_card_name_match(title, card_name) or self._is_card_name_match(title, primary_name):
                valid_name_products.append(p)

        if not valid_name_products:
            return []

        # If Any Version: return all valid name matches
        is_any = not set_code or set_code.strip().upper() in ("ANY", "")
        if is_any:
            return valid_name_products

        target_set_code = (set_code or "").upper().strip()
        target_coll_num = str(collector_number or "").lower().strip() if collector_number else ""
        target_coll_digits = re.sub(r"[^\d]", "", target_coll_num)
        target_set_name = (set_name or "").lower().strip()

        # Pre-fetch product variants concurrently for all candidates to guarantee instant SKU checks
        uncached_handles = [
            p.get("handle") for p in valid_name_products
            if p.get("handle") and p.get("handle") not in MightyMeepleProvider._variant_cache
        ]
        if uncached_handles:
            with ThreadPoolExecutor(max_workers=min(len(uncached_handles), 8)) as executor:
                list(executor.map(self._get_product_variants, uncached_handles))

        # Step 2: Specific Version Matching via SKU (Highest Precision)
        if target_set_code and target_coll_num:
            for p in valid_name_products:
                handle = p.get("handle")
                variants = self._get_product_variants(handle)
                title_lower = p.get("title", "").lower()
                for v in variants:
                    sku = (v.get("sku") or "").upper()
                    if not sku:
                        continue
                    sku_lower = sku.lower()
                    sku_parts = sku.split("-")
                    if len(sku_parts) >= 2:
                        sku_set = sku_parts[0]
                        sku_num = sku_parts[1].lower()

                        set_matches = (
                            sku_set == target_set_code
                            or (target_set_code.startswith("P") and sku_set == target_set_code)
                            or (sku_set.startswith("P") and sku_set[1:] == target_set_code)
                            or (target_set_code.startswith("P") and target_set_code[1:] == sku_set)
                        )

                        if set_matches:
                            if sku_num == target_coll_num:
                                if "promo pack" in title_lower and target_coll_num.endswith("s"):
                                    continue
                                if "prerelease" in title_lower and target_coll_num.endswith("p"):
                                    continue
                                return [p]

                            if target_coll_digits and sku_num == target_coll_digits:
                                if target_coll_num.endswith("p") and ("promo-pack" in sku_lower or "promo pack" in title_lower):
                                    return [p]
                                elif target_coll_num.endswith("s") and ("prerelease" in sku_lower or "prerelease" in title_lower):
                                    return [p]
                                elif not target_coll_num.endswith("p") and not target_coll_num.endswith("s"):
                                    return [p]

            # Step 2b: Substring SKU matching with promo guards
            for p in valid_name_products:
                handle = p.get("handle")
                variants = self._get_product_variants(handle)
                title_lower = p.get("title", "").lower()
                for v in variants:
                    sku = (v.get("sku") or "").upper()
                    if not sku:
                        continue
                    sku_lower = sku.lower()
                    if f"{target_set_code}-{target_coll_num.upper()}" in sku:
                        return [p]
                    if target_coll_digits and f"{target_set_code}-{target_coll_digits}" in sku:
                        if target_coll_num.endswith("p") and ("promo-pack" in sku_lower or "promo pack" in title_lower):
                            return [p]
                        elif target_coll_num.endswith("s") and ("prerelease" in sku_lower or "prerelease" in title_lower):
                            return [p]
                        elif not target_coll_num.endswith("p") and not target_coll_num.endswith("s"):
                            return [p]

        # Step 3: Description HTML match (contains collector number in table)
        if target_coll_num:
            for p in valid_name_products:
                desc = (p.get("description") or "").lower()
                if f"<td>{target_coll_num}</td>" in desc or f"<td>#{target_coll_num}</td>" in desc:
                    return [p]

        # Step 4: Set matching by title brackets [Set Name] or [SET_CODE]
        target_set_terms = []
        if target_set_name:
            target_set_terms.append(target_set_name)
        if target_set_code:
            target_set_terms.append(f"[{target_set_code.lower()}]")
            target_set_terms.append(target_set_code.lower())

        set_matched = []
        for p in valid_name_products:
            title_lower = p.get("title", "").lower()
            if any(st in title_lower for st in target_set_terms):
                set_matched.append(p)

        if set_matched:
            # If multiple products in the set, and collector number is standard (not ending in promo/special letters),
            # prefer product without variant parentheticals
            if len(set_matched) > 1 and target_coll_num and not target_coll_num.endswith("p") and not target_coll_num.endswith("s"):
                non_variant = []
                for p in set_matched:
                    title = p.get("title", "")
                    base = re.sub(r"\[.*?\]", "", title).strip()
                    if "(" not in base:
                        non_variant.append(p)
                if non_variant:
                    return non_variant
            return set_matched

        return valid_name_products

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

    # =========================================================================
    # Buylist Intelligence & Pricing API Methods (BinderPOS Portal)
    # =========================================================================

    def search_buylist(
        self,
        query: str,
        set_name: str | None = None,
        game: str = "mtg",
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """
        Queries Mighty Meeple's live BinderPOS buylist catalog.
        Returns matched cards with complete condition, finish, cash & credit variant prices.
        """
        if not query or not str(query).strip():
            return {"items": [], "total": 0}

        clean_query = str(query).strip()
        url = f"{BINDERPOS_PORTAL_BASE}/external/shopify/{MIGHTY_MEEPLE_STORE_ID}/cards/{game or 'mtg'}"
        params = {
            "keyword": clean_query,
            "limit": max(1, min(50, limit)),
            "offset": max(0, offset),
        }
        if set_name and str(set_name).strip():
            params["setName"] = str(set_name).strip()

        try:
            resp = self.session.get(url, params=params, timeout=12)
            if resp.status_code != 200:
                logger.warning(f"Mighty Meeple buylist query '{query}' returned HTTP {resp.status_code}")
                return {"items": [], "total": 0, "error": f"HTTP {resp.status_code}"}

            raw_list = resp.json()
            if not isinstance(raw_list, list):
                return {"items": [], "total": 0}

            items = []
            for raw in raw_list:
                parsed_variants = []
                for v in raw.get("variants", []):
                    cond_name = v.get("variantName", "Near Mint")
                    for bt in v.get("cardBuylistTypes", []):
                        finish_type = bt.get("type") or bt.get("legacyType") or "Normal"
                        parsed_variants.append({
                            "variant_id": bt.get("productVariantId"),
                            "condition": cond_name,
                            "finish": finish_type,
                            "store_sell_price": round(float(bt.get("storeSellPrice", 0.0) or 0.0), 2),
                            "cash_price": round(float(bt.get("buyPrice", 0.0) or 0.0), 2),
                            "credit_price": round(float(bt.get("creditBuyPrice", 0.0) or 0.0), 2),
                            "max_quantity": int(bt.get("maxPurchaseQuantity", 0) or 0),
                            "can_purchase_overstock": bool(bt.get("canPurchaseOverstock", False)),
                            "overstock_cash_price": round(float(bt.get("overStockBuyPrice", 0.0) or 0.0), 2),
                            "overstock_credit_price": round(float(bt.get("creditOverstockBuyPrice", 0.0) or 0.0), 2),
                        })

                # Compute convenient default quotes for LP and NM
                default_lp = self._find_variant_quote(parsed_variants, condition="Lightly Played")
                default_nm = self._find_variant_quote(parsed_variants, condition="Near Mint")

                items.append({
                    "id": raw.get("id"),
                    "card_name": raw.get("cardName") or clean_query,
                    "set_name": raw.get("setName") or "Unknown Set",
                    "rarity": (raw.get("rarity") or "").lower(),
                    "game": raw.get("gameName") or raw.get("game") or "Magic: The Gathering",
                    "game_id": raw.get("gameId") or (game or "mtg"),
                    "image_url": raw.get("imageUrl") or "",
                    "variants": parsed_variants,
                    # Defaults
                    "default_lp_credit": default_lp["credit_price"] if default_lp else (default_nm["credit_price"] if default_nm else 0.0),
                    "default_lp_cash": default_lp["cash_price"] if default_lp else (default_nm["cash_price"] if default_nm else 0.0),
                    "default_nm_credit": default_nm["credit_price"] if default_nm else 0.0,
                    "default_nm_cash": default_nm["cash_price"] if default_nm else 0.0,
                    "default_sell_price": default_lp["store_sell_price"] if default_lp else (default_nm["store_sell_price"] if default_nm else 0.0),
                    "default_max_qty": default_lp["max_quantity"] if default_lp else (default_nm["max_quantity"] if default_nm else 0),
                })

            return {"items": items, "total": len(items)}

        except Exception as e:
            logger.error(f"Error querying Mighty Meeple buylist for '{query}': {e}")
            return {"items": [], "total": 0, "error": str(e)}

    def _find_variant_quote(
        self,
        variants: list[dict],
        condition: str = "Lightly Played",
        finish: str = "nonfoil",
    ) -> dict | None:
        """Helper to find the best matching variant quote by condition and finish."""
        if not variants:
            return None

        cond_clean = (condition or "Lightly Played").lower().strip()
        is_foil_target = (finish or "nonfoil").lower().strip() in ("foil", "etched")

        # 1. Exact condition and exact finish match
        for v in variants:
            v_cond = v.get("condition", "").lower()
            v_foil = "foil" in v.get("finish", "").lower() or "etched" in v.get("finish", "").lower()
            if (cond_clean in v_cond or (cond_clean == "lp" and "light" in v_cond) or (cond_clean == "nm" and "near" in v_cond)) and (v_foil == is_foil_target):
                return v

        # 2. Condition match with any finish
        for v in variants:
            v_cond = v.get("condition", "").lower()
            if cond_clean in v_cond or (cond_clean == "lp" and "light" in v_cond) or (cond_clean == "nm" and "near" in v_cond):
                return v

        # 3. Fallback to first non-zero variant
        for v in variants:
            if v.get("credit_price", 0) > 0 or v.get("cash_price", 0) > 0:
                return v

        return variants[0] if variants else None

    def bulk_buylist_lookup(
        self,
        card_names: list[str],
        default_condition: str = "Lightly Played",
        default_payout: str = "credit",
        finish: str = "nonfoil",
        game: str = "mtg",
        max_workers: int = 6,
    ) -> dict:
        """
        Executes concurrent buylist price lookups across a manifest of card names.
        Structures total valuation (defaulting to LP Store Credit) and individual quotes.
        """
        if not card_names:
            return {
                "quotes": [],
                "summary": {
                    "total_cards": 0,
                    "matched_count": 0,
                    "unmatched_count": 0,
                    "total_credit_value": 0.0,
                    "total_cash_value": 0.0,
                    "total_sell_value": 0.0,
                    "default_condition": default_condition,
                    "default_payout": default_payout,
                },
            }

        # Deduplicate names preserving order
        unique_names = []
        seen = set()
        for name in card_names:
            clean = str(name).strip().strip("\"'").strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                unique_names.append(clean)

        raw_results = {}
        with ThreadPoolExecutor(max_workers=min(max_workers, 8)) as executor:
            future_to_name = {
                executor.submit(self.search_buylist, name, None, game, 10, 0): name
                for name in unique_names
            }
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    res = future.result()
                    raw_results[name] = res.get("items", [])
                except Exception as e:
                    logger.error(f"Buylist bulk worker error on '{name}': {e}")
                    raw_results[name] = []

        quotes = []
        total_credit = 0.0
        total_cash = 0.0
        total_sell = 0.0
        matched_count = 0
        unmatched_count = 0

        for name in unique_names:
            items = raw_results.get(name, [])
            if not items:
                unmatched_count += 1
                quotes.append({
                    "requested_name": name,
                    "matched": False,
                    "card_name": name,
                    "set_name": "Not Found",
                    "rarity": "",
                    "image_url": "",
                    "condition": default_condition,
                    "finish": finish,
                    "credit_price": 0.0,
                    "cash_price": 0.0,
                    "store_sell_price": 0.0,
                    "max_quantity": 0,
                    "prints_count": 0,
                    "all_prints": [],
                })
                continue

            primary_name = name.split(" // ")[0].strip()
            # Filter buylist items to those that genuinely match the card name
            matching_items = [
                itm for itm in items
                if self._is_card_name_match(itm.get("card_name", ""), name)
                or (primary_name and self._is_card_name_match(itm.get("card_name", ""), primary_name))
            ]

            if not matching_items:
                unmatched_count += 1
                quotes.append({
                    "requested_name": name,
                    "matched": False,
                    "card_name": name,
                    "set_name": "Not Found",
                    "rarity": "",
                    "image_url": "",
                    "condition": default_condition,
                    "finish": finish,
                    "credit_price": 0.0,
                    "cash_price": 0.0,
                    "store_sell_price": 0.0,
                    "max_quantity": 0,
                    "prints_count": 0,
                    "all_prints": [],
                })
                continue

            best_card = matching_items[0]

            matched_count += 1
            v_match = self._find_variant_quote(best_card.get("variants", []), condition=default_condition, finish=finish)
            c_price = v_match.get("credit_price", 0.0) if v_match else 0.0
            k_price = v_match.get("cash_price", 0.0) if v_match else 0.0
            s_price = v_match.get("store_sell_price", 0.0) if v_match else 0.0
            m_qty = v_match.get("max_quantity", 0) if v_match else 0

            total_credit += c_price
            total_cash += k_price
            total_sell += s_price

            quotes.append({
                "requested_name": name,
                "matched": True,
                "card_name": best_card.get("card_name"),
                "set_name": best_card.get("set_name"),
                "rarity": best_card.get("rarity"),
                "image_url": best_card.get("image_url"),
                "condition": v_match.get("condition", default_condition) if v_match else default_condition,
                "finish": v_match.get("finish", finish) if v_match else finish,
                "credit_price": c_price,
                "cash_price": k_price,
                "store_sell_price": s_price,
                "max_quantity": m_qty,
                "prints_count": len(matching_items),
                "all_prints": matching_items,
            })

        return {
            "quotes": quotes,
            "summary": {
                "total_cards": len(unique_names),
                "matched_count": matched_count,
                "unmatched_count": unmatched_count,
                "total_credit_value": round(total_credit, 2),
                "total_cash_value": round(total_cash, 2),
                "total_sell_value": round(total_sell, 2),
                "default_condition": default_condition,
                "default_payout": default_payout,
            },
        }

    def get_supported_games(self) -> list[dict]:
        """Fetches supported card game ecosystems from BinderPOS."""
        try:
            url = f"{BINDERPOS_PORTAL_BASE}/external/shopify/{MIGHTY_MEEPLE_STORE_ID}/supportedGames"
            resp = self.session.get(url, timeout=8)
            if resp.status_code == 200:
                raw = resp.json()
                if isinstance(raw, list):
                    clean = []
                    for g in raw:
                        gid = g.get("gameId")
                        gname = g.get("gameName") or gid
                        if gid and not gid.endswith("s"):  # Filter internal suffix aliases
                            clean.append({"game_id": gid, "game_name": gname})
                    return clean
        except Exception as e:
            logger.debug(f"Failed to fetch supported buylist games: {e}")

        # Fallback standard games
        return [
            {"game_id": "mtg", "game_name": "Magic: The Gathering"},
            {"game_id": "pokemon", "game_name": "Pokémon"},
            {"game_id": "lor", "game_name": "Disney Lorcana"},
            {"game_id": "yugioh", "game_name": "Yu-Gi-Oh!"},
            {"game_id": "one", "game_name": "One Piece"},
            {"game_id": "swu", "game_name": "Star Wars: Unlimited"},
            {"game_id": "fleshAndBlood", "game_name": "Flesh and Blood"},
        ]

    def get_buylist_sets(self, game: str = "mtg") -> list[str]:
        """Fetches all valid set names for a given game from BinderPOS."""
        try:
            url = f"{BINDERPOS_PORTAL_BASE}/api/cards/{game or 'mtg'}/sets"
            resp = self.session.get(url, timeout=8)
            if resp.status_code == 200:
                sets = resp.json()
                if isinstance(sets, list):
                    return sorted([str(s).strip() for s in sets if s and str(s).strip()])
        except Exception as e:
            logger.debug(f"Failed to fetch buylist sets for {game}: {e}")
        return []

