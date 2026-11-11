import logging
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from card_utils import fix_mojibake, normalize_card_name, strip_accents

logger = logging.getLogger(__name__)

TCG_MP_SEARCH_URL = "https://mp-search-api.tcgplayer.com/v1/search/request?q=&isList=false"
TCG_PRODUCT_LISTINGS_URL = "https://mp-search-api.tcgplayer.com/v1/product/{product_id}/listings"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Universes Within (SLX) <-> Universes Beyond Secret Lair (SLD) canonical cross-identity mapping
UNIVERSES_WITHIN_ALIASES: Dict[str, str] = {
    # D&D: Honor Among Thieves (SLD) <-> Universes Within (SLX)
    "casal, lurkwood pathfinder": "doric, nature's warden",
    "doric, nature's warden": "casal, lurkwood pathfinder",
    # Stranger Things (SLD) <-> Universes Within (SLX)
    "elmar, wyrm enforcer": "max, the daredevil",
    "max, the daredevil": "elmar, wyrm enforcer",
    "bjorna, nightfall alchemist": "lucas, the sharpshooter",
    "lucas, the sharpshooter": "bjorna, nightfall alchemist",
    "cecily, haunted mage": "eleven, the mage",
    "eleven, the mage": "cecily, haunted mage",
    "hargilde, kindly runechanter": "dustin, the gadgeteer",
    "dustin, the gadgeteer": "hargilde, kindly runechanter",
    "othelm, sigardian outcast": "mike, the dungeon master",
    "mike, the dungeon master": "othelm, sigardian outcast",
    "wernog, rider's chaplain": "will, the wise",
    "will, the wise": "wernog, rider's chaplain",
    "arvinox, the mind flail": "mind flayer, the shadow",
    "mind flayer, the shadow": "arvinox, the mind flail",
    "sophina, spearhead specialist": "chief jim hopper",
    "chief jim hopper": "sophina, spearhead specialist",
    # The Walking Dead (SLD) <-> Universes Within (SLX)
    "greymond, avacyn's stalwart": "rick, steadfast leader",
    "rick, steadfast leader": "greymond, avacyn's stalwart",
    # Street Fighter (SLD) <-> Universes Within (SLX)
    "chun-li, countless kicks": "zethi, arcane blademaster",
    "zethi, arcane blademaster": "chun-li, countless kicks",
    "dhalsim, pliable pacifist": "tadeas, juniper ascendant",
    "tadeas, juniper ascendant": "dhalsim, pliable pacifist",
    "guile, sonic soldier": "immard, the stormcleaver",
    "immard, the stormcleaver": "guile, sonic soldier",
    "ken, burning brawler": "aisha of sparks and smoke",
    "aisha of sparks and smoke": "ken, burning brawler",
    "ryu, world warrior": "vikya, scorching stalwart",
    "vikya, scorching stalwart": "ryu, world warrior",
}



class TCGplayerLotProvider:
    """
    Scans TCGplayer marketplace listings to discover single sellers holding
    lots (5+ cards) of items from a user's surveillance watchlist at decent prices,
    with support for anchor card bundling and budget/dollar amount constraints.
    """

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
                    wait_time = 1.0 * (attempt + 1)
                    time.sleep(wait_time)
                    continue
                else:
                    logger.debug(f"TCGplayer API {url} status {resp.status_code}: {resp.text[:200]}")
                    return None
            except Exception as e:
                logger.debug(f"TCGplayer API request error: {e}")
                if attempt == 2:
                    return None
                time.sleep(0.5)
        return None

    def evaluate_listing_decent_price(
        self,
        listing_price: float,
        market_price: Optional[float],
        target_price: Optional[float],
        tolerance_mode: str = "decent_10",
        baseline_cheapest_market: Optional[float] = None,
    ) -> Tuple[bool, str, Optional[float]]:
        """
        Evaluates whether a listing price meets the user's 'decent price' threshold.
        When baseline_cheapest_market is provided, benchmark against the cheapest
        available version of the card so fancy treatments (e.g. Masterpieces, showcase)
        do not falsely qualify as deals simply because they are discounted relative
        to an inflated special printing market price.
        Returns: (is_qualifying, price_label, delta_vs_baseline_market)
        """
        if listing_price <= 0:
            return False, "Invalid Price", None

        # Determine effective baseline market price:
        # If baseline_cheapest_market is known and lower than the printing's market_price,
        # use the baseline to prevent fancy treatments from appearing as good deals for the card.
        effective_market = market_price
        if baseline_cheapest_market and baseline_cheapest_market > 0:
            if effective_market and effective_market > 0:
                if effective_market > baseline_cheapest_market * 1.5:
                    effective_market = baseline_cheapest_market
            else:
                effective_market = baseline_cheapest_market

        # Calculate delta vs effective baseline market
        delta_pct = None
        if effective_market and effective_market > 0:
            delta_pct = round(((listing_price - effective_market) / effective_market) * 100.0, 1)

        # Target price priority check if set
        meets_target = bool(target_price and target_price > 0 and listing_price <= target_price)

        if tolerance_mode == "target_only":
            if meets_target:
                return True, "At/Below Target Price", delta_pct
            return False, "Above Target Price", delta_pct

        if tolerance_mode == "great_minus_10":
            # Strictly at least 10% below market OR <= target
            if meets_target:
                return True, "At/Below Target Price", delta_pct
            if effective_market and effective_market > 0 and listing_price <= round(effective_market * 0.90, 2):
                return True, "Great Deal (10%+ Below Market)", delta_pct
            return False, "Above Great Price", delta_pct

        if tolerance_mode == "market_0":
            # Strictly at or below market price OR <= target
            if meets_target:
                return True, "At/Below Target Price", delta_pct
            if effective_market and effective_market > 0 and listing_price <= effective_market:
                return True, "Fair Market / Below Market", delta_pct
            return False, "Above Market", delta_pct

        if tolerance_mode == "flex_20":
            # Up to +20% above market OR <= target
            if meets_target:
                return True, "At/Below Target Price", delta_pct
            if effective_market and effective_market > 0 and listing_price <= round(effective_market * 1.20, 2):
                return True, "Within 20% of Market", delta_pct
            return False, "Exceeds +20% Tolerance", delta_pct

        # Default: "decent_10" (At/below Target OR within +10% of Market)
        if meets_target:
            return True, "At/Below Target Price", delta_pct
        if effective_market and effective_market > 0 and listing_price <= round(effective_market * 1.10, 2):
            label = "At/Below Market" if listing_price <= effective_market else "Within 10% Market Tolerance"
            return True, label, delta_pct

        # If no market price is listed, fall back to target price if available
        if meets_target:
            return True, "At/Below Target Price", delta_pct

        return False, "Above Price Tolerance", delta_pct

    def _discover_sellers_for_anchor(
        self,
        anchor_card_name: str,
        anchor_target_price: Optional[float] = None,
        anchor_market_price: Optional[float] = None,
        tolerance_mode: str = "decent_10",
        allow_mp: bool = False,
        english_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Discovers top active sellers offering the specified anchor card at a decent price.
        """
        clean_name = anchor_card_name.split(" // ")[0].strip()
        search_names = [clean_name]
        alias_name = UNIVERSES_WITHIN_ALIASES.get(clean_name.lower())
        if alias_name:
            search_names.append(alias_name.title())

        term_filter = {"sellerStatus": "Live", "channelId": 0}
        if english_only:
            term_filter["language"] = ["English"]

        payload = {
            "algorithm": "sales_dismax",
            "from": 0,
            "size": 50,
            "filters": {
                "term": {
                    "productLineName": ["magic"],
                    "productTypeName": ["Cards"],
                    "productName": search_names,
                },
                "range": {},
                "match": {},
            },
            "listingSearch": {
                "context": {"cart": {}},
                "filters": {
                    "term": term_filter,
                    "range": {"quantity": {"gte": 1}},
                    "exclude": {"channelExclusion": 0},
                },
            },
            "context": {"cart": {}, "shippingCountry": "US"},
            "settings": {"useFuzzySearch": False, "didYouMean": {}},
            "sort": {"field": "price", "order": "asc"},
        }

        data = self._post_json(TCG_MP_SEARCH_URL, payload)
        if not data or not data.get("results"):
            return []

        products = data["results"][0].get("results", [])
        candidates = []
        seen_sellers: Set[str] = set()

        for prod in products:
            prod_m_price = prod.get("marketPrice")
            p_id = prod.get("productId")
            p_name = prod.get("productName")
            s_name = prod.get("setName")

            # Fetch active listings from dedicated product endpoint (up to 50 sellers)
            # Falling back to the search payload's embedded preview listings if needed
            listings = []
            if p_id is not None:
                try:
                    p_id_int = int(p_id)
                    l_payload = {
                        "filters": {
                            "term": term_filter,
                            "range": {"quantity": {"gte": 1}},
                            "exclude": {"channelExclusion": 0},
                        },
                        "context": {"cart": {}, "shippingCountry": "US"},
                        "sort": {"field": "price", "order": "asc"},
                        "size": 50,
                        "from": 0,
                    }
                    p_data = self._post_json(f"https://mp-search-api.tcgplayer.com/v1/product/{p_id_int}/listings", l_payload)
                    if p_data and p_data.get("results"):
                        listings = p_data["results"][0].get("results", [])
                except Exception as e:
                    logger.debug(f"Failed to fetch product listings for {p_id}: {e}")

            if not listings:
                listings = prod.get("listings", [])

            for listing in listings:
                s_key = listing.get("sellerKey")
                if not s_key or s_key in seen_sellers:
                    continue

                if english_only:
                    lang = listing.get("language", "English") or "English"
                    lang_abbr = (listing.get("languageAbbreviation") or "EN").upper()
                    if str(lang).strip().lower() != "english" or lang_abbr != "EN":
                        continue

                cond = listing.get("condition", "Near Mint")
                if not allow_mp and cond in ("Moderately Played", "Heavily Played", "Damaged"):
                    continue

                price = float(listing.get("price", 0.0))
                qualifies, label, delta_pct = self.evaluate_listing_decent_price(
                    listing_price=price,
                    market_price=prod_m_price,
                    target_price=anchor_target_price,
                    tolerance_mode=tolerance_mode,
                    baseline_cheapest_market=anchor_market_price,
                )

                if qualifies:
                    seen_sellers.add(s_key)
                    candidates.append({
                        "sellerKey": s_key,
                        "sellerName": listing.get("sellerName", "TCGplayer Seller"),
                        "sellerRating": listing.get("sellerRating", 100.0),
                        "sellerSales": listing.get("sellerSales", "1000+"),
                        "directSeller": bool(listing.get("directSeller")),
                        "goldSeller": bool(listing.get("goldSeller")),
                        "anchorListing": {
                            "productId": p_id,
                            "productName": p_name,
                            "setName": s_name,
                            "price": price,
                            "shippingPrice": float(listing.get("shippingPrice", 0.0) or listing.get("sellerShippingPrice", 0.0) or 0.0),
                            "condition": cond,
                            "printing": listing.get("printing", "Normal"),
                            "quantity": int(listing.get("quantity", 1)),
                            "marketPrice": prod_m_price,
                            "baseMarketPrice": anchor_market_price or prod_m_price,
                            "priceLabel": label,
                            "deltaPct": delta_pct,
                        },
                    })

                if len(candidates) >= 20:
                    break
            if len(candidates) >= 20:
                break

        return candidates

    def _discover_sellers_for_watchlist(
        self,
        candidate_card_names: List[str],
        seed_count: int = 8,
        english_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Samples candidate cards across the watchlist and finds sellers that frequently
        stock these cards at competitive prices.
        """
        if not candidate_card_names:
            return []

        # Select diverse seeds from the candidate names list
        step = max(1, len(candidate_card_names) // seed_count)
        seed_names = [candidate_card_names[i] for i in range(0, len(candidate_card_names), step)][:seed_count]
        if not seed_names:
            seed_names = candidate_card_names[:seed_count]

        clean_seeds = []
        for name in seed_names:
            c = name.split(" // ")[0].strip()
            clean_seeds.append(c)
            alias = UNIVERSES_WITHIN_ALIASES.get(c.lower())
            if alias:
                clean_seeds.append(alias.title())
        clean_seeds = list(set(clean_seeds))
        term_filter = {"sellerStatus": "Live", "channelId": 0}
        if english_only:
            term_filter["language"] = ["English"]

        payload = {
            "algorithm": "sales_dismax",
            "from": 0,
            "size": 50,
            "filters": {
                "term": {
                    "productLineName": ["magic"],
                    "productTypeName": ["Cards"],
                    "productName": clean_seeds,
                },
                "range": {},
                "match": {},
            },
            "listingSearch": {
                "context": {"cart": {}},
                "filters": {
                    "term": term_filter,
                    "range": {"quantity": {"gte": 1}},
                    "exclude": {"channelExclusion": 0},
                },
            },
            "context": {"cart": {}, "shippingCountry": "US"},
            "settings": {"useFuzzySearch": False, "didYouMean": {}},
            "sort": {"field": "price", "order": "asc"},
        }

        data = self._post_json(TCG_MP_SEARCH_URL, payload)
        if not data or not data.get("results"):
            return []

        products = data["results"][0].get("results", [])
        seller_frequency: Dict[str, Dict[str, Any]] = {}

        for prod in products:
            for listing in prod.get("listings", []):
                s_key = listing.get("sellerKey")
                if not s_key:
                    continue

                if english_only:
                    lang = listing.get("language", "English") or "English"
                    lang_abbr = (listing.get("languageAbbreviation") or "EN").upper()
                    if str(lang).strip().lower() != "english" or lang_abbr != "EN":
                        continue

                if s_key not in seller_frequency:
                    seller_frequency[s_key] = {
                        "sellerKey": s_key,
                        "sellerName": listing.get("sellerName", "TCGplayer Seller"),
                        "sellerRating": listing.get("sellerRating", 100.0),
                        "sellerSales": listing.get("sellerSales", "1000+"),
                        "directSeller": bool(listing.get("directSeller")),
                        "goldSeller": bool(listing.get("goldSeller")),
                        "count": 0,
                    }
                seller_frequency[s_key]["count"] += 1

        # Sort sellers by frequency appearance
        sorted_sellers = sorted(seller_frequency.values(), key=lambda x: x["count"], reverse=True)
        return sorted_sellers[:25]

    def _query_seller_watchlist_inventory(
        self,
        seller_key: str,
        all_card_names: List[str],
        english_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Queries TCGplayer for all cards in all_card_names stocked by this specific sellerKey.
        Splits into chunks of up to 100 names to safely stay within API URL/payload size limits.
        """
        all_products = []
        chunk_size = 80

        term_filter = {"sellerStatus": "Live", "channelId": 0, "sellerKey": [seller_key]}
        if english_only:
            term_filter["language"] = ["English"]

        for i in range(0, len(all_card_names), chunk_size):
            chunk = all_card_names[i : i + chunk_size]
            clean_chunk_set = set()
            for name in chunk:
                if not name:
                    continue
                c = name.split(" // ")[0].strip()
                clean_chunk_set.add(c)
                alias = UNIVERSES_WITHIN_ALIASES.get(c.lower())
                if alias:
                    clean_chunk_set.add(alias.title())
            clean_chunk = list(clean_chunk_set)
            if not clean_chunk:
                continue

            payload = {
                "algorithm": "sales_dismax",
                "from": 0,
                "size": 50,
                "filters": {
                    "term": {
                        "productLineName": ["magic"],
                        "productTypeName": ["Cards"],
                        "productName": clean_chunk,
                    },
                    "range": {},
                    "match": {},
                },
                "listingSearch": {
                    "context": {"cart": {}},
                    "filters": {
                        "term": term_filter,
                        "range": {"quantity": {"gte": 1}},
                        "exclude": {"channelExclusion": 0},
                    },
                },
                "context": {"cart": {}, "shippingCountry": "US"},
                "settings": {"useFuzzySearch": False, "didYouMean": {}},
                "sort": {"field": "product-sorting-name", "order": "asc"},
            }

            data = self._post_json(TCG_MP_SEARCH_URL, payload)
            if data and data.get("results"):
                res0 = data["results"][0]
                results = res0.get("results", [])
                all_products.extend(results)

        return all_products

    def _optimize_dollar_bundle(
        self,
        qualifying_cards: List[Dict[str, Any]],
        target_amount: float,
        bundle_type: str = "max_budget",
        required_card_id: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Selects a subset of qualifying cards to hit or stay within target_amount.
        - 'max_budget': Greedy knapsack to maximize number of unique cards without exceeding target_amount.
        - 'min_threshold': Selects lowest-cost cards until the sum reaches at least target_amount (e.g. $5 for free shipping).
        If required_card_id is provided, guarantees that card is included first if within budget.
        """
        if not qualifying_cards or target_amount <= 0:
            return qualifying_cards, round(sum(c["price"] for c in qualifying_cards), 2)

        required_card = None
        remaining_cards = []
        if required_card_id is not None:
            for c in qualifying_cards:
                if (c.get("id") == required_card_id or c.get("productId") == required_card_id) and required_card is None:
                    required_card = c
                else:
                    remaining_cards.append(c)
        else:
            remaining_cards = list(qualifying_cards)

        sorted_cards = sorted(remaining_cards, key=lambda c: c["price"])

        if bundle_type == "min_threshold":
            selected = []
            running_total = 0.0
            if required_card:
                selected.append(required_card)
                running_total += required_card["price"]
            for card in sorted_cards:
                if running_total >= target_amount:
                    break
                selected.append(card)
                running_total += card["price"]
            return selected, round(running_total, 2)
        else:
            # max_budget
            selected = []
            running_total = 0.0
            if required_card:
                if required_card["price"] > target_amount:
                    return [], 0.0
                selected.append(required_card)
                running_total += required_card["price"]
            for card in sorted_cards:
                if running_total + card["price"] <= target_amount:
                    selected.append(card)
                    running_total += card["price"]
            return selected, round(running_total, 2)

    def find_seller_lots(
        self,
        watchlist_items: List[Any],
        mode: str = "lots",  # 'lots' | 'anchor_card' | 'dollar_amount'
        anchor_card_id: Optional[int] = None,
        anchor_card_name: Optional[str] = None,
        min_cards: int = 5,
        price_tolerance_mode: str = "decent_10",
        max_card_price: Optional[float] = None,
        min_card_price: Optional[float] = None,
        target_bundle_amount: Optional[float] = None,
        bundle_amount_type: str = "max_budget",  # 'max_budget' | 'min_threshold'
        tag_filter: Optional[str] = None,
        allow_mp: bool = False,
        english_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Main entry point for finding single-seller lots across the user's watchlist.
        """
        start_time = time.time()

        # Build fast lookup map for WatchlistItems by normalized canonical name
        item_by_name: Dict[str, Any] = {}
        for item in watchlist_items:
            # Tag filter check
            if tag_filter and tag_filter.lower() != "all":
                item_tag = (getattr(item, "tag", "") or "").strip().lower()
                if tag_filter.lower() == "__untagged__":
                    if item_tag:
                        continue
                elif item_tag != tag_filter.lower():
                    continue

            # Per-card dollar filter check
            ref_price = getattr(item, "lowest_in_stock_price", None) or getattr(item, "market_price", None)
            if ref_price is not None:
                if max_card_price is not None and ref_price > max_card_price:
                    continue
                if min_card_price is not None and ref_price < min_card_price:
                    continue

            name = getattr(item, "name", "")
            if name:
                norm = normalize_card_name(name.split(" // ")[0].strip())
                item_by_name[norm] = item

        if not item_by_name:
            return {
                "success": True,
                "sellers": [],
                "total_sellers_found": 0,
                "scanned_cards_count": 0,
                "message": "No watchlist cards match the selected tag or price filters.",
                "duration_seconds": round(time.time() - start_time, 2),
            }

        all_names = [getattr(it, "name", "") for it in item_by_name.values()]

        # Step 1: Discover candidate sellers
        candidate_sellers: List[Dict[str, Any]] = []

        if mode == "anchor_card" and (anchor_card_id or anchor_card_name):
            # Resolve anchor item
            anchor_item = None
            if anchor_card_id:
                for it in watchlist_items:
                    if getattr(it, "id", None) == anchor_card_id:
                        anchor_item = it
                        break
            if not anchor_item and anchor_card_name:
                norm_a = normalize_card_name(anchor_card_name.split(" // ")[0].strip())
                anchor_item = item_by_name.get(norm_a)

            if anchor_item:
                anchor_name = getattr(anchor_item, "name", "")
                anchor_target = getattr(anchor_item, "target_price", None)
                anchor_market = getattr(anchor_item, "market_price", None)
                candidate_sellers = self._discover_sellers_for_anchor(
                    anchor_card_name=anchor_name,
                    anchor_target_price=anchor_target,
                    anchor_market_price=anchor_market,
                    tolerance_mode=price_tolerance_mode,
                    allow_mp=allow_mp,
                    english_only=english_only,
                )
        else:
            # Watchlist lot or dollar amount mode: discover sellers from candidate items
            candidate_sellers = self._discover_sellers_for_watchlist(
                candidate_card_names=all_names,
                seed_count=10,
                english_only=english_only,
            )

        if not candidate_sellers:
            return {
                "success": True,
                "sellers": [],
                "total_sellers_found": 0,
                "scanned_cards_count": len(all_names),
                "message": "No active sellers found stocking candidate watchlist cards at current filters.",
                "duration_seconds": round(time.time() - start_time, 2),
            }

        # Step 2: Concurrently query all watchlist cards for candidate sellers
        qualifying_sellers = []

        def process_seller(seller_info: dict) -> Optional[dict]:
            s_key = seller_info.get("sellerKey")
            if not s_key:
                return None

            products = self._query_seller_watchlist_inventory(s_key, all_names, english_only=english_only)
            if not products:
                return None

            # Group products by canonical normalized card name (including Universes Within cross-aliases)
            products_by_card: Dict[str, List[dict]] = {}
            for prod in products:
                prod_name = prod.get("productName", "")
                norm_name = normalize_card_name(prod_name.split(" // ")[0].strip())
                if norm_name in item_by_name:
                    products_by_card.setdefault(norm_name, []).append(prod)
                else:
                    alias = UNIVERSES_WITHIN_ALIASES.get(norm_name)
                    if alias and alias in item_by_name:
                        products_by_card.setdefault(alias, []).append(prod)

            qualifying_cards = []
            above_tolerance_cards = []
            shipping_estimates = []

            for norm_name, prod_list in products_by_card.items():
                w_item = item_by_name[norm_name]
                t_price = getattr(w_item, "target_price", None)

                # Determine the true baseline market price for ANY regular version of this card
                baseline_market = getattr(w_item, "lowest_in_stock_price", None) or getattr(w_item, "market_price", None)
                if not getattr(w_item, "is_any_version", True):
                    baseline_market = getattr(w_item, "market_price", None) or baseline_market

                candidate_qualifying = []
                candidate_above_tolerance = []

                for prod in prod_list:
                    prod_name = prod.get("productName", "")
                    clean_name = prod_name.split(" // ")[0].strip()
                    prod_m_price = prod.get("marketPrice")
                    p_id = prod.get("productId")
                    s_name = prod.get("setName", "")

                    for l in prod.get("listings", []):
                        lang = l.get("language", "English") or "English"
                        lang_abbr = (l.get("languageAbbreviation") or "EN").upper()
                        if english_only and (str(lang).strip().lower() != "english" or lang_abbr != "EN"):
                            continue

                        cond = l.get("condition", "Near Mint")
                        if not allow_mp and cond in ("Moderately Played", "Heavily Played", "Damaged"):
                            continue

                        price = float(l.get("price", 0.0))
                        if price <= 0:
                            continue

                        # Check max/min card price filters if specified in dollar mode
                        if max_card_price is not None and price > max_card_price:
                            continue
                        if min_card_price is not None and price < min_card_price:
                            continue

                        ship = float(l.get("shippingPrice", 0.0) or l.get("sellerShippingPrice", 0.0) or 0.0)
                        shipping_estimates.append(ship)

                        # Evaluate against baseline cheapest version of the card
                        qualifies, label, delta_pct = self.evaluate_listing_decent_price(
                            listing_price=price,
                            market_price=prod_m_price,
                            target_price=t_price,
                            tolerance_mode=price_tolerance_mode,
                            baseline_cheapest_market=baseline_market,
                        )

                        # Identify if this specific printing is an expensive special treatment
                        is_fancy = bool(prod_m_price and baseline_market and prod_m_price > baseline_market * 1.5)

                        card_entry = {
                            "id": getattr(w_item, "id", None),
                            "name": getattr(w_item, "name", prod_name),
                            "cleanName": clean_name,
                            "setName": s_name,
                            "productId": p_id,
                            "price": price,
                            "marketPrice": prod_m_price,
                            "baseMarketPrice": baseline_market or prod_m_price,
                            "isFancyTreatment": is_fancy,
                            "targetPrice": t_price,
                            "condition": cond,
                            "language": lang,
                            "languageAbbreviation": lang_abbr,
                            "printing": l.get("printing", "Normal"),
                            "quantity": int(l.get("quantity", 1)),
                            "imageUri": getattr(w_item, "image_uri", None),
                            "inBinder": bool(getattr(w_item, "in_binder", False)),
                            "binderCount": getattr(w_item, "binder_count", 0),
                            "tag": getattr(w_item, "tag", None),
                            "priceLabel": label,
                            "deltaPct": delta_pct,
                            "tcgplayerUrl": f"https://www.tcgplayer.com/product/{p_id}?seller={s_key}&page=1",
                            "storeSearchUrl": f"https://www.tcgplayer.com/search/magic/product?seller={s_key}&q={urllib.parse.quote_plus(clean_name)}&view=grid",
                        }

                        if qualifies:
                            candidate_qualifying.append(card_entry)
                        else:
                            candidate_above_tolerance.append(card_entry)

                if candidate_qualifying:
                    # Select the lowest-priced qualifying printing (best value for user's money)
                    best_card = min(candidate_qualifying, key=lambda c: c["price"])
                    qualifying_cards.append(best_card)
                elif candidate_above_tolerance:
                    above_tolerance_cards.append(min(candidate_above_tolerance, key=lambda c: c["price"]))

            # If anchor card mode, verify the anchor card is present in qualifying cards
            if mode == "anchor_card" and anchor_item:
                anchor_norm = normalize_card_name(getattr(anchor_item, "name", "").split(" // ")[0].strip())
                qualifying_names = {normalize_card_name(c["name"].split(" // ")[0].strip()) for c in qualifying_cards}
                if anchor_norm not in qualifying_names:
                    return None

            # Check min_cards threshold
            if len(qualifying_cards) < min_cards:
                return None

            # Sort qualifying cards by price ascending
            qualifying_cards.sort(key=lambda c: c["price"])

            bundle_info = None
            display_cards = qualifying_cards
            other_in_stock_cards = []

            # If target bundle amount is configured, optimize and enforce constraints on the bundle
            if target_bundle_amount and target_bundle_amount > 0:
                required_id = getattr(anchor_item, "id", None) if (mode == "anchor_card" and anchor_item) else None
                b_cards, b_total = self._optimize_dollar_bundle(
                    qualifying_cards=qualifying_cards,
                    target_amount=target_bundle_amount,
                    bundle_type=bundle_amount_type,
                    required_card_id=required_id,
                )

                # Validation: Does the optimized bundle satisfy the user's constraints?
                if len(b_cards) < min_cards:
                    # Seller cannot supply the requested min_cards within the target budget/threshold
                    return None

                if bundle_amount_type == "max_budget" and b_total > target_bundle_amount:
                    return None

                if bundle_amount_type == "min_threshold" and b_total < target_bundle_amount:
                    return None

                bundle_info = {
                    "cards": b_cards,
                    "count": len(b_cards),
                    "total": b_total,
                    "targetAmount": target_bundle_amount,
                    "bundleType": bundle_amount_type,
                }

                # Primary presentation and purchase target becomes the optimized bundle
                display_cards = b_cards
                b_pids = {c.get("productId") for c in b_cards}
                other_in_stock_cards = [c for c in qualifying_cards if c.get("productId") not in b_pids]

            # Compute financial totals against baseline market price for the displayed bundle/lot
            subtotal = sum(c["price"] for c in display_cards)
            total_market = sum(c["baseMarketPrice"] for c in display_cards if c.get("baseMarketPrice"))
            net_savings = round(total_market - subtotal, 2) if total_market > 0 else 0.0
            savings_pct = round((net_savings / total_market) * 100.0, 1) if total_market > 0 and net_savings > 0 else 0.0

            # Estimate shipping (use minimum observed shipping for this seller or 0.0 if direct/free)
            est_shipping = min(shipping_estimates) if shipping_estimates else 0.0
            if est_shipping >= 15.0:  # Cap outlier default values
                est_shipping = 0.0 if seller_info.get("directSeller") else 4.99

            # Generate direct TCGplayer store URL and multi-card lot search URL
            store_url = f"https://www.tcgplayer.com/search/magic/product?seller={s_key}&view=grid"
            search_params = [("seller", s_key), ("view", "grid")]
            for c in display_cards:
                clean_cname = c.get("cleanName") or c["name"].split(" // ")[0].strip()
                search_params.append(("productName", clean_cname))
            multi_card_search_url = "https://www.tcgplayer.com/search/magic/product?" + urllib.parse.urlencode(search_params)

            all_search_url = None
            if other_in_stock_cards:
                all_params = [("seller", s_key), ("view", "grid")]
                for c in qualifying_cards:
                    clean_cname = c.get("cleanName") or c["name"].split(" // ")[0].strip()
                    all_params.append(("productName", clean_cname))
                all_search_url = "https://www.tcgplayer.com/search/magic/product?" + urllib.parse.urlencode(all_params)

            return {
                "sellerKey": s_key,
                "sellerName": seller_info.get("sellerName", "TCGplayer Seller"),
                "sellerRating": seller_info.get("sellerRating", 100.0),
                "sellerSales": seller_info.get("sellerSales", "1000+"),
                "directSeller": seller_info.get("directSeller", False),
                "goldSeller": seller_info.get("goldSeller", False),
                "storeUrl": store_url,
                "multiCardSearchUrl": multi_card_search_url,
                "allCardsSearchUrl": all_search_url,
                "qualifyingCount": len(display_cards),
                "subtotal": round(subtotal, 2),
                "estShipping": round(est_shipping, 2),
                "landedTotal": round(subtotal + est_shipping, 2),
                "totalMarket": round(total_market, 2),
                "netSavings": net_savings,
                "savingsPct": savings_pct,
                "cards": display_cards,
                "otherInStockCount": len(other_in_stock_cards),
                "otherInStockCards": other_in_stock_cards,
                "allStockedCount": len(qualifying_cards),
                "allStockedSubtotal": round(sum(c["price"] for c in qualifying_cards), 2),
                "aboveToleranceCount": len(above_tolerance_cards),
                "aboveToleranceCards": above_tolerance_cards[:10],
                "bundleInfo": bundle_info,
            }

        # Concurrently evaluate candidate sellers (5 worker threads)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(process_seller, s) for s in candidate_sellers]
            for f in as_completed(futures):
                try:
                    res = f.result()
                    if res:
                        qualifying_sellers.append(res)
                except Exception as err:
                    logger.debug(f"Error evaluating seller: {err}")

        # Sort sellers: first by qualifying card count descending, then by net savings descending
        qualifying_sellers.sort(
            key=lambda x: (x["qualifyingCount"], x["netSavings"]),
            reverse=True,
        )

        elapsed = round(time.time() - start_time, 2)
        return {
            "success": True,
            "sellers": qualifying_sellers,
            "total_sellers_found": len(qualifying_sellers),
            "scanned_cards_count": len(all_names),
            "duration_seconds": elapsed,
            "mode": mode,
            "min_cards": min_cards,
            "tolerance_mode": price_tolerance_mode,
            "english_only": english_only,
        }
