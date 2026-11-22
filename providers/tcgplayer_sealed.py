import logging
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

TCG_MP_SEARCH_URL = "https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=false"
TCG_PRODUCT_LISTINGS_URL = "https://mp-search-api.tcgplayer.com/v1/product/{product_id}/listings"
TCG_PRODUCT_IMAGE_URL = "https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_400x400.jpg"
SCRYFALL_SETS_URL = "https://api.scryfall.com/sets"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

SEALED_PRODUCT_TYPES = [
    "Sealed Products",
    "Booster Box",
    "Booster Pack",
    "Fat Pack",
    "3x Magic Booster Packs",
    "Booster Battle Pack",
    "Box Sets",
    "Magic Booster Box Case",
    "Precon/Event Decks",
]

CATEGORY_LABELS: Dict[str, str] = {
    "booster_box": "Booster Box",
    "booster_pack": "Booster Pack",
    "bundle": "Bundle / Fat Pack",
    "prerelease_starter": "Prerelease / Starter / Jumpstart",
    "commander_deck": "Commander / Precon Deck",
    "case_other": "Case / Box Set",
}

DEFAULT_CATEGORIES = ["booster_box", "booster_pack", "bundle", "prerelease_starter"]


def _safe_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        num = float(val)
        return round(num, 2) if num > 0 else None
    except (ValueError, TypeError):
        return None


def _safe_nonneg_float(val: Any) -> float:
    if val is None or val == "":
        return 0.0
    try:
        num = float(val)
        return round(max(0.0, num), 2)
    except (ValueError, TypeError):
        return 0.0


def classify_sealed_product(
    product_name: str,
    product_type_name: str = "",
    description: str = "",
) -> Tuple[str, str, str]:
    """
    Classifies a TCGplayer MTG sealed product into (category, category_label, booster_subtype).
    Categories:
      - booster_box
      - booster_pack
      - bundle
      - prerelease_starter
      - commander_deck
      - case_other
    Booster Subtypes:
      - Collector | Play | Draft | Set | Jumpstart | Standard
    """
    name_l = (product_name or "").lower()
    ptype_l = (product_type_name or "").lower()

    # Determine booster subtype
    if "collector" in name_l:
        subtype = "Collector"
    elif "play booster" in name_l or "play display" in name_l:
        subtype = "Play"
    elif "draft booster" in name_l or "draft pack" in name_l or "draft display" in name_l:
        subtype = "Draft"
    elif "set booster" in name_l or "set display" in name_l:
        subtype = "Set"
    elif "jumpstart" in name_l:
        subtype = "Jumpstart"
    else:
        subtype = "Standard"

    # Determine canonical category
    if (
        "master case" in name_l
        or "display case" in name_l
        or "box case" in name_l
        or "bundle case" in name_l
        or "pack case" in name_l
        or "deck case" in name_l
        or re.search(r"\bcase\b", name_l)
        or "box case" in ptype_l
    ):
        cat = "case_other"
    elif (
        "booster pack" in name_l
        or "omega pack" in name_l
        or "sample pack" in name_l
        or "sleeved" in name_l
        or "value booster" in name_l
        or "3x magic booster" in ptype_l
        or "blister" in name_l
        or "hanger" in name_l
        or (ptype_l == "booster pack" and "box" not in name_l and "display" not in name_l)
    ):
        cat = "booster_pack"
    elif (
        "booster display" in name_l
        or "booster box" in name_l
        or ptype_l == "booster box"
    ):
        cat = "booster_box"
    elif (
        "prerelease" in name_l
        or "starter kit" in name_l
        or "beginner box" in name_l
        or "battle pack" in name_l
        or "theme booster" in name_l
        or "welcome deck" in name_l
        or "clash pack" in name_l
        or "jumpstart" in name_l
        or "battle pack" in ptype_l
    ):
        cat = "prerelease_starter"
    elif (
        "bundle" in name_l
        or "fat pack" in name_l
        or "compleat edition" in name_l
        or ptype_l == "fat pack"
    ):
        cat = "bundle"
    elif (
        "commander deck" in name_l
        or "event deck" in name_l
        or "intro pack" in name_l
        or "challenger deck" in name_l
        or "planeswalker deck" in name_l
        or "brawl deck" in name_l
        or "precon" in ptype_l
        or "intro pack" in ptype_l
    ):
        cat = "commander_deck"
    elif "box set" in name_l or "box sets" in ptype_l or "gift box" in name_l or "gift edition" in name_l:
        cat = "case_other"
    else:
        cat = "case_other"

    return cat, CATEGORY_LABELS.get(cat, "Sealed Product"), subtype


def extract_pack_count(
    product_name: str,
    description: str = "",
    category: str = "",
    booster_subtype: str = "",
) -> Optional[int]:
    """
    Extracts booster pack count from TCGplayer description or title to calculate landed price per pack.
    """
    if category == "booster_pack":
        m_multi = re.search(r"(\d+)\s*[xX]\s*.*booster", product_name or "", re.IGNORECASE)
        if m_multi:
            try:
                val = int(m_multi.group(1))
                if 1 <= val <= 12:
                    return val
            except ValueError:
                pass
        return 1

    if category in ("commander_deck",):
        return None

    # Clean HTML tags from description
    clean_desc = re.sub(r"<[^>]+>", " ", description or "")
    clean_desc = re.sub(r"\s+", " ", clean_desc)

    # Look for bullet lines like "• 30 Magic: The Gathering | Star Trek Play Boosters" or "12 Collector Boosters"
    patterns = [
        r"(?:•|-|\*|\b)\s*(\d{1,3})\s+(?:Magic:\s*The\s*Gathering[^.,;]{0,60}?)?(?:Play|Draft|Set|Collector|Jumpstart|Magic)?\s*Boosters?(?:\s+Packs?)?\b",
        r"contains?\s+(\d{1,3})\s+(?:[A-Za-z0-9:'\-®™| ]{0,45})?Boosters?\b",
        r"\((\d{1,3})\s*(?:Packs?|Boosters?)\)",
    ]
    for pat in patterns:
        m = re.search(pat, clean_desc, re.IGNORECASE)
        if m:
            try:
                val = int(m.group(1))
                if 2 <= val <= 216:
                    return val
            except ValueError:
                pass

    m_name = re.search(r"\((\d{1,3})\s*(?:Packs?|Boosters?)\)", product_name or "", re.IGNORECASE)
    if m_name:
        try:
            val = int(m_name.group(1))
            if 2 <= val <= 216:
                return val
        except ValueError:
            pass

    # Safe fallback heuristics when description omits count
    if category == "prerelease_starter" and "prerelease" in (product_name or "").lower():
        return 6
    if category == "booster_box" and booster_subtype == "Collector":
        return 12

    return None


def evaluate_sealed_deal(
    landed_price: float,
    market_price: Optional[float],
    target_price: Optional[float] = None,
    tolerance_mode: str = "market_0",
) -> Dict[str, Any]:
    """
    Evaluates a sealed product's landed price (listing + shipping) against
    TCGplayer Market Price and optional user Target Price.
    """
    if landed_price <= 0:
        return {
            "isDeal": False,
            "priceRating": "Unknown",
            "dealLabel": "Invalid Price",
            "savingsVsMarket": None,
            "savingsVsMarketPct": None,
            "deltaVsMarketPct": None,
            "savingsVsTarget": None,
            "savingsVsTargetPct": None,
            "meetsTarget": False,
        }

    savings_vs_market = None
    savings_vs_market_pct = None
    delta_vs_market_pct = None
    price_rating = "Unknown"

    if market_price and market_price > 0:
        savings_vs_market = round(market_price - landed_price, 2)
        savings_vs_market_pct = round((savings_vs_market / market_price) * 100.0, 1)
        delta_vs_market_pct = round(((landed_price - market_price) / market_price) * 100.0, 1)

        ratio = landed_price / market_price
        if ratio <= 0.90:
            price_rating = "Great Deal"
        elif ratio <= 0.95:
            price_rating = "Good Deal"
        elif ratio < 1.00:
            price_rating = "Below Market"
        elif ratio <= 1.03:
            price_rating = "Fair Market"
        else:
            price_rating = "Above Market"

    meets_target = False
    savings_vs_target = None
    savings_vs_target_pct = None
    if target_price and target_price > 0:
        savings_vs_target = round(target_price - landed_price, 2)
        savings_vs_target_pct = round((savings_vs_target / target_price) * 100.0, 1)
        meets_target = landed_price <= target_price

    mode = (tolerance_mode or "market_0").strip().lower()
    is_deal = False
    deal_label = price_rating

    if mode == "target_only":
        is_deal = meets_target
        deal_label = "At/Below Target" if meets_target else "Above Target"
    elif mode == "great_minus_10":
        if meets_target:
            is_deal = True
            deal_label = "At/Below Target"
        elif market_price and landed_price <= round(market_price * 0.90, 2):
            is_deal = True
            deal_label = "Great Deal (10%+ Below Market)"
        else:
            is_deal = False
            deal_label = "Above -10% Threshold"
    elif mode == "good_minus_5":
        if meets_target:
            is_deal = True
            deal_label = "At/Below Target"
        elif market_price and landed_price <= round(market_price * 0.95, 2):
            is_deal = True
            deal_label = "Good Deal (5%+ Below Market)"
        else:
            is_deal = False
            deal_label = "Above -5% Threshold"
    elif mode == "flex_10":
        if meets_target:
            is_deal = True
            deal_label = "At/Below Target"
        elif market_price and landed_price <= round(market_price * 1.10, 2):
            is_deal = True
            deal_label = price_rating if landed_price <= market_price else "Within +10% Market"
        else:
            is_deal = False
            deal_label = "Exceeds +10% Market"
    elif mode == "all":
        is_deal = True
        deal_label = "At/Below Target" if meets_target else price_rating
    else:
        # Default: market_0 (at or below market OR at or below target)
        if meets_target:
            is_deal = True
            deal_label = "At/Below Target"
        elif market_price and landed_price <= market_price:
            is_deal = True
            deal_label = price_rating
        else:
            is_deal = False
            deal_label = "Above Market"

    return {
        "isDeal": is_deal,
        "priceRating": price_rating,
        "dealLabel": deal_label,
        "savingsVsMarket": savings_vs_market,
        "savingsVsMarketPct": savings_vs_market_pct,
        "deltaVsMarketPct": delta_vs_market_pct,
        "savingsVsTarget": savings_vs_target,
        "savingsVsTargetPct": savings_vs_target_pct,
        "meetsTarget": meets_target,
    }


class TCGplayerSealedProvider:
    """
    Discovers MTG sets with sealed products on TCGplayer, lists sealed products
    per set (Booster Boxes, Booster Packs, Bundles, Prerelease/Starter/Jumpstart, etc.),
    and scans live unopened seller listings ranked by landed price (price + shipping).
    """

    _sets_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None
    _SETS_CACHE_TTL: float = 1800.0  # 30 minutes
    _scryfall_sets_cache: Optional[Tuple[float, Dict[str, Dict[str, Any]]]] = None
    _cache_lock = threading.Lock()

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Origin": "https://www.tcgplayer.com",
            "Referer": "https://www.tcgplayer.com/",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }

    def _post_json(self, url: str, payload: dict, timeout: float = 12.0) -> Optional[dict]:
        """Performs a POST request against TCGplayer internal MP APIs with retry on 429."""
        for attempt in range(3):
            try:
                resp = self.session.post(url, json=payload, headers=self.headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                else:
                    logger.debug(f"TCGplayer Sealed API {url} status {resp.status_code}: {resp.text[:200]}")
                    return None
            except Exception as e:
                logger.debug(f"TCGplayer Sealed API request error: {e}")
                if attempt == 2:
                    return None
                time.sleep(0.5)
        return None

    def _get_scryfall_set_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Loads Scryfall /sets metadata indexed by lowercased set name for code & release date enrichment."""
        now = time.time()
        with TCGplayerSealedProvider._cache_lock:
            if (
                TCGplayerSealedProvider._scryfall_sets_cache is not None
                and now - TCGplayerSealedProvider._scryfall_sets_cache[0] < TCGplayerSealedProvider._SETS_CACHE_TTL
            ):
                return TCGplayerSealedProvider._scryfall_sets_cache[1]

        meta: Dict[str, Dict[str, Any]] = {}
        try:
            resp = self.session.get(
                SCRYFALL_SETS_URL,
                headers={"User-Agent": "Chimera-MTGTracker/1.0", "Accept": "application/json"},
                timeout=8.0,
            )
            if resp.status_code == 200:
                for s in resp.json().get("data", []):
                    name_key = (s.get("name") or "").strip().lower()
                    if name_key:
                        meta[name_key] = {
                            "code": (s.get("code") or "").upper(),
                            "released_at": s.get("released_at") or "",
                            "set_type": s.get("set_type") or "",
                            "icon_svg_uri": s.get("icon_svg_uri") or "",
                        }
        except Exception as e:
            logger.debug(f"Could not enrich sealed sets from Scryfall: {e}")

        with TCGplayerSealedProvider._cache_lock:
            TCGplayerSealedProvider._scryfall_sets_cache = (now, meta)
        return meta

    def get_sealed_sets(self, query: Optional[str] = None, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Returns MTG sets that have sealed products listed on TCGplayer, enriched with
        Scryfall set codes and release dates (sorted newest first, then alphabetically).
        """
        now = time.time()
        sets_list: Optional[List[Dict[str, Any]]] = None

        if not force_refresh:
            with TCGplayerSealedProvider._cache_lock:
                if (
                    TCGplayerSealedProvider._sets_cache is not None
                    and now - TCGplayerSealedProvider._sets_cache[0] < TCGplayerSealedProvider._SETS_CACHE_TTL
                ):
                    sets_list = TCGplayerSealedProvider._sets_cache[1]

        if sets_list is None:
            payload = {
                "algorithm": "sales_dismax",
                "from": 0,
                "size": 1,
                "filters": {
                    "term": {
                        "productLineName": ["magic"],
                        "productTypeName": SEALED_PRODUCT_TYPES,
                    },
                    "range": {},
                    "match": {},
                },
                "listingSearch": {
                    "context": {"cart": {}},
                    "filters": {
                        "term": {"sellerStatus": "Live", "channelId": 0},
                        "range": {"quantity": {"gte": 1}},
                        "exclude": {"channelExclusion": 0},
                    },
                },
                "context": {"cart": {}, "shippingCountry": "US"},
                "settings": {"useFuzzySearch": True, "didYouMean": {}},
                "sort": {},
            }

            data = self._post_json(TCG_MP_SEARCH_URL, payload)
            sf_meta = self._get_scryfall_set_metadata()
            parsed: List[Dict[str, Any]] = []

            if data and data.get("results"):
                aggs = data["results"][0].get("aggregations", {})
                for bucket in aggs.get("setName", []):
                    url_val = (bucket.get("urlValue") or "").strip()
                    display_name = (bucket.get("value") or "").strip()
                    count = int(bucket.get("count") or 0)
                    if not url_val or not display_name or count <= 0:
                        continue

                    sf_info = sf_meta.get(display_name.lower(), {})
                    if not sf_info and ":" in display_name:
                        # Try matching stripped Universes Beyond / Commander prefix
                        alt = display_name.split(":", 1)[1].strip().lower()
                        sf_info = sf_meta.get(alt, {})

                    parsed.append({
                        "setUrlName": url_val,
                        "setName": display_name,
                        "setCode": sf_info.get("code", ""),
                        "releasedAt": sf_info.get("released_at", ""),
                        "productCount": count,
                    })

            # Sort: sets with known release dates newest-first, then by productCount desc, then name asc
            parsed.sort(
                key=lambda s: (
                    0 if s["releasedAt"] else 1,
                    -(int(s["releasedAt"].replace("-", "")) if re.match(r"^\d{4}-\d{2}-\d{2}$", s["releasedAt"]) else 0),
                    -s["productCount"],
                    s["setName"].lower(),
                )
            )
            sets_list = parsed
            if sets_list:
                with TCGplayerSealedProvider._cache_lock:
                    TCGplayerSealedProvider._sets_cache = (now, sets_list)

        if query and query.strip():
            q_lower = query.strip().lower()
            return [
                s for s in sets_list
                if q_lower in s["setName"].lower()
                or q_lower in s["setUrlName"].lower()
                or (s["setCode"] and q_lower == s["setCode"].lower())
                or (s["setCode"] and q_lower in s["setCode"].lower())
            ]

        return list(sets_list)

    def _normalize_search_product(self, prod: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalizes a raw TCGplayer search result dict into a structured sealed product dict."""
        raw_pid = prod.get("productId")
        if raw_pid is None:
            return None
        try:
            pid = int(raw_pid)
        except (ValueError, TypeError):
            return None

        p_name = (prod.get("productName") or "").strip()
        s_name = (prod.get("setName") or "").strip()
        s_url = (prod.get("setUrlName") or "").strip()
        s_code = (prod.get("setCode") or "").strip().upper()
        custom = prod.get("customAttributes") or {}
        description = custom.get("description") or ""
        detail_note = custom.get("detailNote") or ""
        release_date = custom.get("releaseDate") or ""
        is_presale = "presale" in detail_note.lower()

        cat, cat_label, subtype = classify_sealed_product(p_name, description=description)
        pack_count = extract_pack_count(p_name, description=description, category=cat, booster_subtype=subtype)

        market_price = _safe_float(prod.get("marketPrice"))
        median_price = _safe_float(prod.get("medianPrice"))
        lowest_price = _safe_float(prod.get("lowestPrice"))
        lowest_landed = _safe_float(prod.get("lowestPriceWithShipping")) or lowest_price
        total_listings = int(prod.get("totalListings") or 0)

        # Evaluate preview deal metrics using lowest landed price vs market price
        eval_info = evaluate_sealed_deal(
            landed_price=lowest_landed or 0.0,
            market_price=market_price,
            target_price=None,
            tolerance_mode="market_0",
        )

        price_per_pack = (
            round(lowest_landed / pack_count, 2)
            if (lowest_landed and pack_count and pack_count > 0)
            else None
        )
        market_per_pack = (
            round(market_price / pack_count, 2)
            if (market_price and pack_count and pack_count > 0)
            else None
        )

        return {
            "productId": pid,
            "productName": p_name,
            "setName": s_name,
            "setUrlName": s_url,
            "setCode": s_code,
            "category": cat,
            "categoryLabel": cat_label,
            "boosterSubtype": subtype,
            "packCount": pack_count,
            "imageUrl": TCG_PRODUCT_IMAGE_URL.format(product_id=pid),
            "marketPrice": market_price,
            "medianPrice": median_price,
            "lowestPrice": lowest_price,
            "lowestLandedPrice": lowest_landed,
            "pricePerPack": price_per_pack,
            "marketPerPack": market_per_pack,
            "totalListings": total_listings,
            "inStock": total_listings > 0 and lowest_landed is not None,
            "releaseDate": release_date,
            "isPresale": is_presale,
            "tcgplayerUrl": f"https://www.tcgplayer.com/product/{pid}?Language=English",
            "priceRating": eval_info["priceRating"],
            "savingsVsMarket": eval_info["savingsVsMarket"],
            "savingsVsMarketPct": eval_info["savingsVsMarketPct"],
            "deltaVsMarketPct": eval_info["deltaVsMarketPct"],
        }

    def get_set_sealed_products(
        self,
        set_url_names: List[str],
        categories: Optional[List[str]] = None,
        booster_subtype: Optional[str] = None,
        include_cases: bool = False,
        in_stock_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Fetches all sealed products for one or more TCGplayer set URL slugs,
        with optional category, booster subtype, and case filtering.
        """
        clean_sets = [s.strip() for s in (set_url_names or []) if s and s.strip()]
        if not clean_sets:
            return []

        raw_products: List[Dict[str, Any]] = []
        for page_from in (0, 50):
            payload = {
                "algorithm": "sales_dismax",
                "from": page_from,
                "size": 50,
                "filters": {
                    "term": {
                        "productLineName": ["magic"],
                        "productTypeName": SEALED_PRODUCT_TYPES,
                        "setName": clean_sets,
                    },
                    "range": {},
                    "match": {},
                },
                "listingSearch": {
                    "context": {"cart": {}},
                    "filters": {
                        "term": {"sellerStatus": "Live", "channelId": 0},
                        "range": {"quantity": {"gte": 1}},
                        "exclude": {"channelExclusion": 0},
                    },
                },
                "context": {"cart": {}, "shippingCountry": "US"},
                "settings": {"useFuzzySearch": True, "didYouMean": {}},
                "sort": {},
            }

            data = self._post_json(TCG_MP_SEARCH_URL, payload)
            if not data or not data.get("results"):
                break

            res0 = data["results"][0]
            page_items = res0.get("results", [])
            raw_products.extend(page_items)
            total_results = int(res0.get("totalResults") or 0)
            if len(raw_products) >= total_results or len(page_items) < 50:
                break

        if not raw_products:
            return []
        allowed_cats = set(c.strip().lower() for c in categories) if categories else None
        subtype_clean = (booster_subtype or "").strip().lower()
        if subtype_clean in ("all", ""):
            subtype_clean = ""

        products: List[Dict[str, Any]] = []
        seen_pids = set()

        for raw in raw_products:
            item = self._normalize_search_product(raw)
            if not item or item["productId"] in seen_pids:
                continue
            seen_pids.add(item["productId"])

            if not include_cases and item["category"] == "case_other":
                if not allowed_cats or "case_other" not in allowed_cats:
                    continue

            if allowed_cats is not None and item["category"] not in allowed_cats:
                continue

            if subtype_clean and item["boosterSubtype"].lower() != subtype_clean:
                continue

            if in_stock_only and not item["inStock"]:
                continue

            products.append(item)

        # Sort: booster_box first, then booster_pack, bundle, prerelease_starter, commander_deck, case_other
        cat_order = {
            "booster_box": 0,
            "booster_pack": 1,
            "bundle": 2,
            "prerelease_starter": 3,
            "commander_deck": 4,
            "case_other": 5,
        }
        products.sort(
            key=lambda p: (
                0 if p["inStock"] else 1,
                cat_order.get(p["category"], 9),
                p["productName"].lower(),
            )
        )
        return products

    def get_product_live_listings(
        self,
        product_id: int,
        english_only: bool = True,
        size: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Fetches live active listings for a single sealed product from TCGplayer,
        sorted by landed price (price + shipping) ascending.
        """
        term_filter: Dict[str, Any] = {"sellerStatus": "Live", "channelId": 0}
        if english_only:
            term_filter["language"] = ["English"]

        payload = {
            "filters": {
                "term": term_filter,
                "range": {"quantity": {"gte": 1}},
                "exclude": {"channelExclusion": 0},
            },
            "context": {"cart": {}, "shippingCountry": "US"},
            "sort": {"field": "price+shipping", "order": "asc"},
            "size": max(1, min(50, int(size))),
            "from": 0,
        }

        url = TCG_PRODUCT_LISTINGS_URL.format(product_id=int(product_id))
        data = self._post_json(url, payload)
        if not data or not data.get("results"):
            return []

        raw_listings = data["results"][0].get("results", [])
        parsed: List[Dict[str, Any]] = []

        for l in raw_listings:
            if english_only:
                lang = str(l.get("language") or "English").strip().lower()
                lang_abbr = str(l.get("languageAbbreviation") or "EN").strip().upper()
                if lang != "english" or lang_abbr != "EN":
                    continue

            item_price = _safe_float(l.get("price"))
            if not item_price:
                continue

            shipping = _safe_nonneg_float(
                l.get("shippingPrice") if l.get("shippingPrice") is not None else l.get("sellerShippingPrice")
            )
            landed = round(item_price + shipping, 2)
            s_key = str(l.get("sellerKey") or "").strip()

            parsed.append({
                "listingId": l.get("listingId"),
                "sellerKey": s_key,
                "sellerName": l.get("sellerName") or "TCGplayer Seller",
                "sellerRating": float(l.get("sellerRating") or 100.0),
                "sellerSales": str(l.get("sellerSales") or "0"),
                "directSeller": bool(l.get("directSeller")),
                "goldSeller": bool(l.get("goldSeller")),
                "verifiedSeller": bool(l.get("verifiedSeller")),
                "condition": l.get("condition") or "Unopened",
                "language": l.get("language") or "English",
                "languageAbbreviation": (l.get("languageAbbreviation") or "EN").upper(),
                "quantity": int(l.get("quantity") or 1),
                "price": item_price,
                "shippingPrice": shipping,
                "landedPrice": landed,
                "buyUrl": (
                    f"https://www.tcgplayer.com/product/{int(product_id)}?seller={s_key}&Language=English&page=1"
                    if s_key
                    else f"https://www.tcgplayer.com/product/{int(product_id)}?Language=English"
                ),
            })

        parsed.sort(key=lambda x: (x["landedPrice"], x["price"]))
        return parsed

    def _lookup_product_metadata(self, product_id: int, fallback_name: str = "") -> Optional[Dict[str, Any]]:
        """Fetches fresh product-level market price and metadata from TCGplayer search API."""
        query_name = fallback_name.strip()
        payload: Dict[str, Any] = {
            "algorithm": "sales_dismax",
            "from": 0,
            "size": 20,
            "filters": {
                "term": {
                    "productLineName": ["magic"],
                    "productTypeName": SEALED_PRODUCT_TYPES,
                },
                "range": {},
                "match": {},
            },
            "listingSearch": {
                "context": {"cart": {}},
                "filters": {
                    "term": {"sellerStatus": "Live", "channelId": 0},
                    "range": {"quantity": {"gte": 1}},
                    "exclude": {"channelExclusion": 0},
                },
            },
            "context": {"cart": {}, "shippingCountry": "US"},
            "settings": {"useFuzzySearch": True, "didYouMean": {}},
            "sort": {},
        }
        if query_name:
            url = f"https://mp-search-api.tcgplayer.com/v1/search/request?q={urllib.parse.quote(query_name)}&isList=false"
        else:
            url = TCG_MP_SEARCH_URL

        data = self._post_json(url, payload)
        if not data or not data.get("results"):
            return None

        for prod in data["results"][0].get("results", []):
            try:
                if int(prod.get("productId", -1)) == int(product_id):
                    return self._normalize_search_product(prod)
            except (ValueError, TypeError):
                continue
        return None

    def scan_sealed_deals(
        self,
        selected_products: List[Dict[str, Any]],
        tolerance_mode: str = "market_0",
        english_only: bool = True,
        max_listings_per_product: int = 10,
    ) -> Dict[str, Any]:
        """
        Scans live TCGplayer listings for each user-selected sealed product, evaluating
        Landed Price (price + shipping) against TCGplayer Market Price and optional Target Price.
        Also detects multi-product seller overlap for combined shipping opportunities.
        """
        start_time = time.time()
        if not selected_products:
            return {
                "success": True,
                "products": [],
                "multiProductSellers": [],
                "summary": {
                    "productsScanned": 0,
                    "dealsFound": 0,
                    "bestSavingsAmt": 0.0,
                    "bestSavingsPct": 0.0,
                    "multiSellerCount": 0,
                },
                "durationSeconds": 0.0,
            }

        def _scan_one(prod_in: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            raw_pid = prod_in.get("productId") or prod_in.get("product_id")
            if raw_pid is None:
                return None
            try:
                pid = int(raw_pid)
            except (ValueError, TypeError):
                return None

            p_name = (prod_in.get("productName") or prod_in.get("name") or f"Product #{pid}").strip()
            s_name = (prod_in.get("setName") or prod_in.get("set_name") or "").strip()
            s_code = (prod_in.get("setCode") or prod_in.get("set_code") or "").strip().upper()
            market_price = _safe_float(prod_in.get("marketPrice") if "marketPrice" in prod_in else prod_in.get("market_price"))
            target_price = _safe_float(prod_in.get("targetPrice") if "targetPrice" in prod_in else prod_in.get("target_price"))
            pack_count = prod_in.get("packCount") or prod_in.get("pack_count")
            cat = prod_in.get("category")
            cat_label = prod_in.get("categoryLabel")
            subtype = prod_in.get("boosterSubtype")

            if not cat or not cat_label or not subtype:
                c2, cl2, st2 = classify_sealed_product(p_name)
                cat = cat or c2
                cat_label = cat_label or cl2
                subtype = subtype or st2

            # Refresh market price if missing
            if market_price is None and p_name:
                refreshed = self._lookup_product_metadata(pid, fallback_name=p_name)
                if refreshed:
                    market_price = refreshed.get("marketPrice")
                    if not pack_count:
                        pack_count = refreshed.get("packCount")
                    if not s_name:
                        s_name = refreshed.get("setName", "")
                    if not s_code:
                        s_code = refreshed.get("setCode", "")

            listings = self.get_product_live_listings(
                product_id=pid,
                english_only=english_only,
                size=max(max_listings_per_product, 15),
            )

            evaluated_listings: List[Dict[str, Any]] = []
            for l in listings:
                ev = evaluate_sealed_deal(
                    landed_price=l["landedPrice"],
                    market_price=market_price,
                    target_price=target_price,
                    tolerance_mode=tolerance_mode,
                )
                l_copy = dict(l)
                l_copy.update(ev)
                l_copy["pricePerPack"] = (
                    round(l["landedPrice"] / pack_count, 2)
                    if (pack_count and pack_count > 0)
                    else None
                )
                evaluated_listings.append(l_copy)

            best_listing = evaluated_listings[0] if evaluated_listings else None
            qualifying_listings = [l for l in evaluated_listings if l["isDeal"]]

            best_landed = best_listing["landedPrice"] if best_listing else None
            best_eval = evaluate_sealed_deal(
                landed_price=best_landed or 0.0,
                market_price=market_price,
                target_price=target_price,
                tolerance_mode=tolerance_mode,
            )

            return {
                "productId": pid,
                "productName": p_name,
                "setName": s_name,
                "setCode": s_code,
                "category": cat,
                "categoryLabel": cat_label,
                "boosterSubtype": subtype,
                "packCount": pack_count,
                "imageUrl": prod_in.get("imageUrl") or TCG_PRODUCT_IMAGE_URL.format(product_id=pid),
                "tcgplayerUrl": f"https://www.tcgplayer.com/product/{pid}?Language=English",
                "marketPrice": market_price,
                "targetPrice": target_price,
                "bestLandedPrice": best_landed,
                "bestItemPrice": best_listing["price"] if best_listing else None,
                "bestShippingPrice": best_listing["shippingPrice"] if best_listing else None,
                "bestSellerName": best_listing["sellerName"] if best_listing else None,
                "bestBuyUrl": best_listing["buyUrl"] if best_listing else f"https://www.tcgplayer.com/product/{pid}?Language=English",
                "pricePerPack": (
                    round(best_landed / pack_count, 2)
                    if (best_landed and pack_count and pack_count > 0)
                    else None
                ),
                "isDeal": best_eval["isDeal"],
                "priceRating": best_eval["priceRating"],
                "dealLabel": best_eval["dealLabel"],
                "savingsVsMarket": best_eval["savingsVsMarket"],
                "savingsVsMarketPct": best_eval["savingsVsMarketPct"],
                "deltaVsMarketPct": best_eval["deltaVsMarketPct"],
                "savingsVsTarget": best_eval["savingsVsTarget"],
                "savingsVsTargetPct": best_eval["savingsVsTargetPct"],
                "meetsTarget": best_eval["meetsTarget"],
                "qualifyingListingsCount": len(qualifying_listings),
                "totalListingsScanned": len(evaluated_listings),
                "listings": evaluated_listings[:max_listings_per_product],
            }

        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_scan_one, p) for p in selected_products]
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    if res:
                        results.append(res)
                except Exception as err:
                    logger.debug(f"Error scanning sealed product: {err}")

        # Sort scanned products: active deals first, then largest % savings vs market desc
        results.sort(
            key=lambda r: (
                0 if r["isDeal"] else 1,
                -(r["savingsVsMarketPct"] if r["savingsVsMarketPct"] is not None else -999.0),
                r["productName"].lower(),
            )
        )

        # Compute multi-product seller overlap across scanned products
        seller_map: Dict[str, Dict[str, Any]] = {}
        for prod in results:
            seen_for_prod = set()
            for l in prod["listings"]:
                s_key = l.get("sellerKey")
                if not s_key or s_key in seen_for_prod:
                    continue
                seen_for_prod.add(s_key)
                if s_key not in seller_map:
                    seller_map[s_key] = {
                        "sellerKey": s_key,
                        "sellerName": l["sellerName"],
                        "sellerRating": l["sellerRating"],
                        "sellerSales": l["sellerSales"],
                        "directSeller": l["directSeller"],
                        "goldSeller": l["goldSeller"],
                        "storeUrl": f"https://www.tcgplayer.com/search/magic/product?seller={s_key}&view=grid",
                        "products": [],
                    }
                seller_map[s_key]["products"].append({
                    "productId": prod["productId"],
                    "productName": prod["productName"],
                    "price": l["price"],
                    "shippingPrice": l["shippingPrice"],
                    "landedPrice": l["landedPrice"],
                    "marketPrice": prod["marketPrice"],
                    "savingsVsMarket": l["savingsVsMarket"],
                    "buyUrl": l["buyUrl"],
                    "isDeal": l["isDeal"],
                })

        multi_sellers: List[Dict[str, Any]] = []
        for s_entry in seller_map.values():
            if len(s_entry["products"]) >= 2:
                subtotal = round(sum(p["price"] for p in s_entry["products"]), 2)
                max_ship = round(max(p["shippingPrice"] for p in s_entry["products"]), 2)
                est_landed = round(subtotal + max_ship, 2)
                total_mkt = round(sum((p["marketPrice"] or 0.0) for p in s_entry["products"]), 2)
                net_sav = round(total_mkt - est_landed, 2) if total_mkt > 0 else 0.0
                s_entry["productCount"] = len(s_entry["products"])
                s_entry["itemsSubtotal"] = subtotal
                s_entry["estCombinedShipping"] = max_ship
                s_entry["estCombinedTotal"] = est_landed
                s_entry["totalMarket"] = total_mkt
                s_entry["netSavingsVsMarket"] = net_sav
                multi_sellers.append(s_entry)

        multi_sellers.sort(key=lambda s: (-s["productCount"], -s["netSavingsVsMarket"]))

        deals_found = sum(1 for r in results if r["isDeal"])
        best_sav_amt = max((r["savingsVsMarket"] or 0.0 for r in results), default=0.0)
        best_sav_pct = max((r["savingsVsMarketPct"] or 0.0 for r in results), default=0.0)

        return {
            "success": True,
            "products": results,
            "multiProductSellers": multi_sellers[:10],
            "summary": {
                "productsScanned": len(results),
                "dealsFound": deals_found,
                "bestSavingsAmt": round(max(0.0, best_sav_amt), 2),
                "bestSavingsPct": round(max(0.0, best_sav_pct), 1),
                "multiSellerCount": len(multi_sellers),
            },
            "toleranceMode": tolerance_mode,
            "englishOnly": english_only,
            "durationSeconds": round(time.time() - start_time, 2),
        }

    def poll_sealed_product(
        self,
        product_id: int,
        product_name: str = "",
        english_only: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Polls a single watched sealed product on TCGplayer for DealEngine.poll_card().
        Returns live lowest landed price (listing + shipping) AND official TCGplayer market price.
        """
        try:
            pid = int(product_id)
        except (ValueError, TypeError):
            return None

        meta = self._lookup_product_metadata(pid, fallback_name=product_name)
        listings = self.get_product_live_listings(pid, english_only=english_only, size=5)

        market_price = meta.get("marketPrice") if meta else None
        best = listings[0] if listings else None

        if best:
            landed_price = best["landedPrice"]
            seller_name = best["sellerName"]
            ship_note = "Free Ship" if best["shippingPrice"] == 0 else f"+${best['shippingPrice']:.2f} ship"
            cond_label = f"Unopened ({seller_name}, {ship_note})"
            product_url = best["buyUrl"]
            in_stock = True
        elif meta and meta.get("lowestLandedPrice"):
            landed_price = meta["lowestLandedPrice"]
            cond_label = "Unopened (TCGplayer)"
            product_url = meta["tcgplayerUrl"]
            in_stock = bool(meta.get("inStock"))
        else:
            landed_price = 0.0
            cond_label = "Unopened"
            product_url = f"https://www.tcgplayer.com/product/{pid}?Language=English"
            in_stock = False

        return {
            "vendor_name": "TCGplayer",
            "price": float(landed_price),
            "market_price": float(market_price) if market_price is not None else None,
            "condition": cond_label[:20] if len(cond_label) > 20 else cond_label,
            "condition_full": cond_label,
            "in_stock": in_stock,
            "product_url": product_url,
            "search_url": f"https://www.tcgplayer.com/product/{pid}?Language=English",
            "image_url": TCG_PRODUCT_IMAGE_URL.format(product_id=pid),
        }
