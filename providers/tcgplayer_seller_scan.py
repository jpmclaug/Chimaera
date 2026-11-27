"""
TCGplayer Seller Value Scanner & Combined Sealed + Singles Cart Finder.

Reuses existing Chimaera TCGplayer providers (TCGplayerLotProvider and
TCGplayerSealedProvider) plus canonical card/sealed matchers to:
1. Scan an entire TCGplayer seller storefront (splitting into market-price bands
   past the 10,000 result limit) and surface watchlist hits, Commander deck
   upgrades, pure-value below-market singles, and below-market/watched sealed items
   while excluding cards the user already owns.
2. Discover TCGplayer sellers who carry BOTH wanted sealed products AND
   watchlist singles so orders can be consolidated into one cart and shipping charge.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import requests

from card_utils import fix_mojibake, get_card_match_keys, normalize_card_name
from tcgplayer_parser import extract_parenthetical_alias

from .sealed_matcher import match_sealed
from .tcgplayer_lots import TCGplayerLotProvider, UNIVERSES_WITHIN_ALIASES
from .tcgplayer_sealed import (
    DEFAULT_USER_AGENT,
    SEALED_PRODUCT_TYPES,
    TCG_MP_SEARCH_URL,
    TCG_PRODUCT_IMAGE_URL,
    TCGplayerSealedProvider,
    classify_sealed_product,
    evaluate_sealed_deal,
    extract_pack_count,
)

logger = logging.getLogger(__name__)

# Seller URL / key matching patterns
SELLER_KEY_EXACT_RE = re.compile(r"^[0-9a-fA-F]{6,16}$")
SELLER_URL_PATTERNS = (
    re.compile(r"[?&]seller=([0-9a-fA-F]{6,16})", re.IGNORECASE),
    re.compile(r"/sellerfeedback/([0-9a-fA-F]{6,16})", re.IGNORECASE),
    re.compile(r"/sellers?/([0-9a-fA-F]{6,16})", re.IGNORECASE),
)

# Descending price bands to bypass TCGplayer's 10,000 result offset ceiling
PRICE_BANDS: List[Tuple[float, Optional[float]]] = [
    (100.0, None),
    (20.0, 99.99),
    (5.0, 19.99),
    (2.0, 4.99),
    (0.01, 1.99),
]

SPECIAL_TREATMENT_MARKERS = (
    "extended art",
    "borderless",
    "showcase",
    "retro frame",
    "surge foil",
    "halo foil",
    "textured foil",
    "confetti foil",
    "galaxy foil",
    "fracture foil",
    "raised foil",
    "mana foil",
    "step-and-compleat",
    "serialized",
    "poster",
    "anime",
    "alt art",
    "alternate art",
    "masterpiece",
    "judge",
    "buy-a-box",
    "prerelease",
    "foil etched",
    "etched",
)

NON_PLAYABLE_MARKERS = (
    "art card",
    "art series",
    "helper card",
    "checklist",
    "minigame",
    "punch card",
    "token",
    "emblem",
    "world championship",
    "gold-bordered",
)

CONDITION_ALIASES = {
    "nm": "Near Mint",
    "near mint": "Near Mint",
    "lp": "Lightly Played",
    "lightly played": "Lightly Played",
    "mp": "Moderately Played",
    "moderately played": "Moderately Played",
    "hp": "Heavily Played",
    "heavily played": "Heavily Played",
    "dmg": "Damaged",
    "damaged": "Damaged",
}

POPULAR_SEED_CARDS = [
    "Sol Ring",
    "Command Tower",
    "Arcane Signet",
    "Swords to Plowshares",
    "Counterspell",
    "Cultivate",
]

# Thread-safe module-level TTL cache for seller inventory snapshots
_CACHE_LOCK = threading.Lock()
_SELLER_INV_CACHE: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]] = {}
SELLER_INV_CACHE_TTL = 600.0  # 10 minutes
SELLER_INV_CACHE_MAX = 32


class SellerNotFoundError(ValueError):
    """Raised when a TCGplayer seller key or store cannot be resolved."""


def clear_seller_scan_cache() -> None:
    """Clears the cached TCGplayer seller inventory snapshots (useful in tests and forced refreshes)."""
    with _CACHE_LOCK:
        _SELLER_INV_CACHE.clear()


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


def strip_tcg_variant_suffixes(raw_name: Optional[str]) -> str:
    """
    Strips TCGplayer variant parentheticals (e.g. 'Raise the Palisade (Extended Art)'
    -> 'Raise the Palisade') and normalizes formatting.
    """
    if not raw_name:
        return ""
    cleaned = re.sub(r"\s*\([^)]*\)", "", fix_mojibake(str(raw_name)))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return normalize_card_name(cleaned)


def canonical_card_keys(raw_name: Optional[str]) -> Set[str]:
    """
    Produces all canonical matching keys for a card name across:
    - raw name (accents + ASCII + front face)
    - variant-stripped base name
    - parenthetical Universes Beyond/Within alias (e.g. 'Hadoken (Lightning Bolt)')
    - UNIVERSES_WITHIN_ALIASES cross-identity map
    """
    if not raw_name:
        return set()
    keys: Set[str] = set()
    keys.update(get_card_match_keys(raw_name))

    stripped = strip_tcg_variant_suffixes(raw_name)
    if stripped:
        keys.update(get_card_match_keys(stripped))

    paren_alias = extract_parenthetical_alias(raw_name)
    if paren_alias:
        keys.update(get_card_match_keys(paren_alias))

    for k in list(keys):
        alias = UNIVERSES_WITHIN_ALIASES.get(k)
        if alias:
            keys.update(get_card_match_keys(alias))

    return keys


def detect_special_treatment(raw_name: Optional[str]) -> bool:
    """Returns True if the TCGplayer product title indicates a showcase/extended/special treatment."""
    if not raw_name:
        return False
    low = str(raw_name).lower()
    return any(marker in low for marker in SPECIAL_TREATMENT_MARKERS)


def is_non_playable_card_product(prod: Dict[str, Any]) -> bool:
    """Filters out art cards, tokens, emblems, and helper cards from pure-value singles picks."""
    name_low = (prod.get("productName") or "").lower()
    rarity_low = (prod.get("rarityName") or prod.get("rarity") or "").lower()
    if rarity_low in ("token", "art card", "emblem"):
        return True
    if any(marker in name_low for marker in NON_PLAYABLE_MARKERS):
        return True
    return False


def normalize_allowed_conditions(
    conditions: Optional[List[str]] = None,
    allow_mp: bool = False,
) -> Tuple[str, ...]:
    """Normalizes condition codes ('NM', 'LP', 'Near Mint', etc.) into canonical TCGplayer strings."""
    if not conditions:
        out = ["Near Mint", "Lightly Played"]
    else:
        out = []
        for raw in conditions:
            c_clean = CONDITION_ALIASES.get(str(raw or "").strip().lower())
            if c_clean and c_clean not in out:
                out.append(c_clean)
        if not out:
            out = ["Near Mint", "Lightly Played"]

    if allow_mp and "Moderately Played" not in out:
        out.append("Moderately Played")
    return tuple(out)


class _TcgThrottle:
    """
    Lightweight thread-safe rate limiter + retry wrapper for TCGplayer MP search API.
    Caps concurrency at 4 in-flight requests and spaces requests by at least 120ms,
    with jittered exponential backoff on HTTP 429 or 5xx responses.
    """

    def __init__(self, max_inflight: int = 4, min_interval: float = 0.12):
        self._sem = threading.BoundedSemaphore(max(1, int(max_inflight)))
        self._rate_lock = threading.Lock()
        self._min_interval = max(0.0, float(min_interval))
        self._last_req_ts = 0.0

    def _wait_slot(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_req_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_req_ts = time.monotonic()

    def post_json(
        self,
        session: requests.Session,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        timeout: float = 12.0,
        max_attempts: int = 4,
    ) -> Optional[Dict[str, Any]]:
        for attempt in range(max_attempts):
            try:
                with self._sem:
                    self._wait_slot()
                    resp = session.post(url, json=payload, headers=headers, timeout=timeout)

                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 or resp.status_code >= 500:
                    backoff = (0.8 * (2**attempt)) + random.uniform(0.05, 0.25)
                    time.sleep(min(backoff, 6.0))
                    continue
                logger.debug(
                    "TCGplayer MP API %s status %s: %s",
                    url,
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            except requests.RequestException as exc:
                logger.debug("TCGplayer MP API request error (attempt %d): %s", attempt + 1, exc)
                if attempt + 1 >= max_attempts:
                    return None
                time.sleep(0.4 * (attempt + 1) + random.uniform(0.05, 0.15))
            except ValueError as exc:
                logger.debug("TCGplayer MP API JSON decode error: %s", exc)
                return None
        return None


def build_user_scout_context(user_id: int) -> Dict[str, Any]:
    """
    Gathers all user-scoped intelligence needed for Seller Value Scans and Combined Carts:
    - Watchlist singles & sealed items (enriched with binder ownership without calling Scryfall)
    - Owned inventory map keyed by canonical card keys
    - Cached Commander deck upgrade recommendations keyed by canonical card keys
    - Saved TCGplayer favorite sellers
    """
    from sqlalchemy.orm import undefer
    from models import DeckAnalysis, SavedTcgSeller, UserInventoryCard, WatchlistItem

    inv_rows = (
        UserInventoryCard.query.with_entities(
            UserInventoryCard.name,
            UserInventoryCard.quantity,
            UserInventoryCard.binder_name,
        )
        .filter_by(user_id=user_id)
        .all()
    )

    owned_map: Dict[str, int] = defaultdict(int)
    binder_map: Dict[str, Set[str]] = defaultdict(set)
    for c_name, c_qty, c_binder in inv_rows:
        qty = max(1, int(c_qty or 1))
        b_name = (c_binder or "").strip()
        for k in canonical_card_keys(c_name):
            owned_map[k] += qty
            if b_name:
                binder_map[k].add(b_name)

    watchlist_items = WatchlistItem.query.filter_by(user_id=user_id).all()
    for item in watchlist_items:
        item_keys = canonical_card_keys(item.name)
        count = max((owned_map.get(k, 0) for k in item_keys), default=0)
        binders: Set[str] = set()
        for k in item_keys:
            if k in binder_map:
                binders.update(binder_map[k])
        item.in_binder = count > 0
        item.binder_count = count
        item.binder_names = sorted(binders)

    decks = (
        DeckAnalysis.query.options(undefer(DeckAnalysis.upgrades_json))
        .filter_by(user_id=user_id)
        .order_by(DeckAnalysis.updated_at.desc())
        .all()
    )

    deck_upgrades: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_key_deck: Set[Tuple[str, int]] = set()
    decks_with_upgrades_count = 0
    decks_missing_upgrades: List[Dict[str, Any]] = []

    for deck in decks:
        upgrades = deck.get_upgrades()
        shopping_cards: List[Dict[str, Any]] = []
        if isinstance(upgrades, dict) and upgrades:
            all_sc = upgrades.get("all_shopping_cards")
            if isinstance(all_sc, list):
                shopping_cards.extend(c for c in all_sc if isinstance(c, dict))
            else:
                sl = upgrades.get("shopping_list")
                if isinstance(sl, dict):
                    for bracket_list in sl.values():
                        if isinstance(bracket_list, list):
                            shopping_cards.extend(c for c in bracket_list if isinstance(c, dict))

        if not shopping_cards:
            decks_missing_upgrades.append({"id": deck.id, "name": deck.deck_name})
            continue

        decks_with_upgrades_count += 1
        for card in shopping_cards:
            c_name = (card.get("name") or card.get("add") or card.get("card_name") or "").strip()
            if not c_name:
                continue
            ref_price = _safe_float(card.get("price_usd"))
            synergy = None
            if card.get("synergy") is not None:
                try:
                    synergy = round(float(card["synergy"]), 3)
                except (ValueError, TypeError):
                    synergy = None

            entry = {
                "deckId": deck.id,
                "deckName": deck.deck_name,
                "commanderName": deck.commander_name,
                "synergy": synergy,
                "refPrice": ref_price,
                "role": card.get("role") or card.get("category") or "",
            }
            for k in canonical_card_keys(c_name):
                pair = (k, deck.id)
                if pair not in seen_key_deck:
                    seen_key_deck.add(pair)
                    deck_upgrades[k].append(entry)

    saved_sellers = (
        SavedTcgSeller.query.filter_by(user_id=user_id)
        .order_by(SavedTcgSeller.created_at.desc())
        .all()
    )

    return {
        "watchlist_items": watchlist_items,
        "owned_map": dict(owned_map),
        "deck_upgrades": dict(deck_upgrades),
        "decks_with_upgrades_count": decks_with_upgrades_count,
        "decks_missing_upgrades": decks_missing_upgrades,
        "saved_sellers": saved_sellers,
    }


class TCGplayerSellerScanner:
    """
    Scans individual TCGplayer seller storefronts for value deals and combines
    sealed products with watchlist singles to consolidate shipping on Black Friday.
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        lot_provider: Optional[TCGplayerLotProvider] = None,
        sealed_provider: Optional[TCGplayerSealedProvider] = None,
        throttle: Optional[_TcgThrottle] = None,
    ):
        self.session = session or requests.Session()
        self.headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Origin": "https://www.tcgplayer.com",
            "Referer": "https://www.tcgplayer.com/",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }
        self.throttle = throttle or _TcgThrottle(max_inflight=4, min_interval=0.12)
        self.lot_provider = lot_provider or TCGplayerLotProvider(session=self.session)
        self.sealed_provider = sealed_provider or TCGplayerSealedProvider(session=self.session)

    def _post(self, url: str, payload: Dict[str, Any], timeout: float = 12.0) -> Optional[Dict[str, Any]]:
        """Single HTTP POST entrypoint (throttled + retried) for easy unit test mocking."""
        return self.throttle.post_json(
            self.session,
            url,
            payload,
            headers=self.headers,
            timeout=timeout,
        )

    @staticmethod
    def extract_seller_key(raw: Optional[str]) -> Optional[str]:
        """Extracts a lowercase TCGplayer sellerKey hex token from a store URL, feedback URL, or raw key."""
        text = (raw or "").strip()
        if not text:
            return None

        for pat in SELLER_URL_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(1).lower()

        if SELLER_KEY_EXACT_RE.match(text):
            return text.lower()

        return None

    @staticmethod
    def _seller_meta_from_listing(listing: Dict[str, Any], fallback_key: str = "") -> Dict[str, Any]:
        s_key = (str(listing.get("sellerKey") or fallback_key or "")).strip().lower()
        programs = listing.get("sellerPrograms") or []
        if not isinstance(programs, list):
            programs = []
        programs_str = [str(p) for p in programs]
        certified = (
            "CertifiedHobbyShop" in programs_str
            or "WizardsPlayNetwork" in programs_str
        )
        rating_raw = listing.get("sellerRating")
        try:
            rating = round(float(rating_raw), 2) if rating_raw is not None else None
        except (ValueError, TypeError):
            rating = None

        return {
            "sellerKey": s_key,
            "sellerName": (listing.get("sellerName") or f"Seller {s_key}").strip(),
            "sellerRating": rating,
            "sellerSales": str(listing.get("sellerSales") or "").strip(),
            "directSeller": bool(listing.get("directSeller")),
            "goldSeller": bool(listing.get("goldSeller")),
            "verifiedSeller": bool(listing.get("verifiedSeller")),
            "sellerPrograms": programs_str,
            "certifiedShop": certified,
            "storeUrl": (
                f"https://www.tcgplayer.com/search/magic/product?seller={s_key}&view=grid"
                if s_key
                else "https://www.tcgplayer.com"
            ),
        }

    @staticmethod
    def _build_seller_search_payload(
        seller_key: str,
        product_types: List[str],
        *,
        from_offset: int = 0,
        size: int = 50,
        gte_market: Optional[float] = None,
        lte_market: Optional[float] = None,
        english_only: bool = True,
        sort_by_market: bool = True,
    ) -> Dict[str, Any]:
        term_listing: Dict[str, Any] = {
            "sellerStatus": "Live",
            "channelId": 0,
            "sellerKey": [seller_key],
        }
        if english_only:
            term_listing["language"] = ["English"]

        range_filters: Dict[str, Any] = {}
        mkt_range: Dict[str, float] = {}
        if gte_market is not None and gte_market > 0:
            mkt_range["gte"] = round(float(gte_market), 2)
        if lte_market is not None and lte_market > 0:
            mkt_range["lte"] = round(float(lte_market), 2)
        if mkt_range:
            range_filters["marketPrice"] = mkt_range

        return {
            "algorithm": "sales_dismax",
            "from": max(0, int(from_offset)),
            "size": max(1, min(50, int(size))),
            "filters": {
                "term": {
                    "productLineName": ["magic"],
                    "productTypeName": product_types,
                },
                "range": range_filters,
                "match": {},
            },
            "listingSearch": {
                "context": {"cart": {}},
                "filters": {
                    "term": term_listing,
                    "range": {"quantity": {"gte": 1}},
                    "exclude": {"channelExclusion": 0},
                },
            },
            "context": {"cart": {}, "shippingCountry": "US"},
            "settings": {"useFuzzySearch": False, "didYouMean": {}},
            "sort": {"field": "market-price", "order": "desc"} if sort_by_market else {},
        }

    def _probe_seller_key(self, seller_key: str) -> Optional[Dict[str, Any]]:
        """Probes a sellerKey across MTG Cards and Sealed to verify activity and fetch store metadata."""
        clean_key = seller_key.strip().lower()
        cards_total = 0
        sealed_total = 0
        meta: Optional[Dict[str, Any]] = None

        for label, ptypes in (("cards", ["Cards"]), ("sealed", SEALED_PRODUCT_TYPES)):
            payload = self._build_seller_search_payload(
                clean_key,
                ptypes,
                from_offset=0,
                size=5,
                english_only=False,
                sort_by_market=False,
            )
            data = self._post(TCG_MP_SEARCH_URL, payload)
            res0 = (data.get("results") or [{}])[0] if data and data.get("results") else {}
            tot = int(res0.get("totalResults") or 0)
            if label == "cards":
                cards_total = tot
            else:
                sealed_total = tot

            if meta is None:
                for prod in res0.get("results") or []:
                    for listing in prod.get("listings") or []:
                        l_key = str(listing.get("sellerKey") or "").strip().lower()
                        if not l_key or l_key == clean_key:
                            meta = self._seller_meta_from_listing(listing, fallback_key=clean_key)
                            break
                    if meta:
                        break
            if meta and label == "cards" and cards_total > 0:
                break

        if meta is None and (cards_total > 0 or sealed_total > 0):
            meta = {
                "sellerKey": clean_key,
                "sellerName": f"Seller {clean_key}",
                "sellerRating": None,
                "sellerSales": "",
                "directSeller": False,
                "goldSeller": False,
                "verifiedSeller": False,
                "sellerPrograms": [],
                "certifiedShop": False,
                "storeUrl": f"https://www.tcgplayer.com/search/magic/product?seller={clean_key}&view=grid",
            }

        if meta is not None:
            meta["probeCardsTotal"] = cards_total
            meta["probeSealedTotal"] = sealed_total
        return meta

    def _saved_seller_to_dict(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            s_key = str(item.get("sellerKey") or item.get("seller_key") or "").strip().lower()
            s_name = str(item.get("sellerName") or item.get("seller_name") or "").strip()
            note = str(item.get("note") or "").strip()
            return {
                "sellerKey": s_key,
                "sellerName": s_name or f"Seller {s_key}",
                "note": note,
                "sellerRating": item.get("sellerRating") or item.get("seller_rating"),
                "sellerSales": item.get("sellerSales") or item.get("seller_sales") or "",
                "directSeller": bool(item.get("directSeller") or item.get("is_direct")),
                "goldSeller": bool(item.get("goldSeller") or item.get("is_gold")),
                "certifiedShop": bool(item.get("certifiedShop") or item.get("is_certified_shop")),
                "storeUrl": item.get("storeUrl") or f"https://www.tcgplayer.com/search/magic/product?seller={s_key}&view=grid",
            }
        return item.to_dict()

    def resolve_seller(
        self,
        raw: Optional[str],
        saved_sellers: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Resolves a TCGplayer store URL, 8-char sellerKey, saved seller name, or
        best-effort popular-card seller name into structured store metadata.
        """
        clean_raw = (raw or "").strip()
        if not clean_raw:
            raise SellerNotFoundError(
                "Please enter a TCGplayer store URL (e.g. ...?seller=43d828c6) or 8-character seller key."
            )

        saved_dicts = [self._saved_seller_to_dict(s) for s in (saved_sellers or [])]
        saved_by_key = {s["sellerKey"]: s for s in saved_dicts if s.get("sellerKey")}

        # 1. URL or direct 8-character hex key
        extracted_key = self.extract_seller_key(clean_raw)
        if extracted_key:
            resolved_by = "key" if SELLER_KEY_EXACT_RE.match(clean_raw) else "url"
            probed = self._probe_seller_key(extracted_key)
            if probed:
                probed["resolvedBy"] = resolved_by
                return probed
            if extracted_key in saved_by_key:
                fallback = dict(saved_by_key[extracted_key])
                fallback["resolvedBy"] = "saved"
                return fallback
            raise SellerNotFoundError(
                f"No active MTG listings found for seller key '{extracted_key}'. "
                "Verify the store URL on TCGplayer."
            )

        # 2. Match against saved sellers (exact name, key, then unambiguous substring)
        q_low = clean_raw.lower()
        saved_match: Optional[Dict[str, Any]] = None
        for s in saved_dicts:
            if s.get("sellerName", "").lower() == q_low or s.get("sellerKey", "").lower() == q_low:
                saved_match = s
                break
        if saved_match is None:
            partial_matches = [
                s for s in saved_dicts
                if q_low in s.get("sellerName", "").lower() or (s.get("note") and q_low in s["note"].lower())
            ]
            if len(partial_matches) == 1:
                saved_match = partial_matches[0]

        if saved_match and saved_match.get("sellerKey"):
            probed = self._probe_seller_key(saved_match["sellerKey"])
            if probed:
                probed["resolvedBy"] = "saved"
                return probed
            fallback = dict(saved_match)
            fallback["resolvedBy"] = "saved"
            return fallback

        # 3. Best-effort name sampling across popular commander staples
        seed_payload = {
            "algorithm": "sales_dismax",
            "from": 0,
            "size": 30,
            "filters": {
                "term": {
                    "productLineName": ["magic"],
                    "productTypeName": ["Cards"],
                    "productName": POPULAR_SEED_CARDS,
                },
                "range": {},
                "match": {},
            },
            "listingSearch": {
                "context": {"cart": {}},
                "filters": {
                    "term": {"sellerStatus": "Live", "channelId": 0, "language": ["English"]},
                    "range": {"quantity": {"gte": 1}},
                    "exclude": {"channelExclusion": 0},
                },
            },
            "context": {"cart": {}, "shippingCountry": "US"},
            "settings": {"useFuzzySearch": False, "didYouMean": {}},
            "sort": {"field": "price", "order": "asc"},
        }
        data = self._post(TCG_MP_SEARCH_URL, seed_payload)
        products = ((data or {}).get("results") or [{}])[0].get("results", []) if data and data.get("results") else []

        exact_hits: Dict[str, Dict[str, Any]] = {}
        sub_hits: Dict[str, Dict[str, Any]] = {}

        def _collect_listing(listing: Dict[str, Any]) -> None:
            s_name = (listing.get("sellerName") or "").strip()
            s_key = (str(listing.get("sellerKey") or "")).strip().lower()
            if not s_name or not s_key:
                return
            name_low = s_name.lower()
            if name_low == q_low:
                exact_hits[s_key] = listing
            elif q_low in name_low:
                sub_hits[s_key] = listing

        for prod in products:
            for l in prod.get("listings") or []:
                _collect_listing(l)

        if not exact_hits and not sub_hits and products:
            for prod in products[:3]:
                pid = prod.get("productId")
                if pid is None:
                    continue
                try:
                    listings = self.sealed_provider.get_product_live_listings(int(pid), english_only=True, size=50)
                except Exception:
                    listings = []
                for l in listings:
                    _collect_listing(l)
                if exact_hits:
                    break

        chosen = None
        if exact_hits:
            chosen = next(iter(exact_hits.values()))
        elif len(sub_hits) == 1:
            chosen = next(iter(sub_hits.values()))

        if chosen:
            s_key = str(chosen.get("sellerKey") or "").strip().lower()
            probed = self._probe_seller_key(s_key) or self._seller_meta_from_listing(chosen, fallback_key=s_key)
            probed["resolvedBy"] = "name_sample"
            return probed

        raise SellerNotFoundError(
            f"Could not resolve seller '{clean_raw}' by name (TCGplayer disables seller-name search). "
            "Open any item or storefront from that seller on TCGplayer and paste the URL containing ?seller=... or /sellerfeedback/..."
        )

    def _normalize_product_listings(
        self,
        prod: Dict[str, Any],
        seller_key: str,
        english_only: bool,
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        raw_pid = prod.get("productId")
        if raw_pid is None:
            return [], None
        try:
            pid = int(raw_pid)
        except (ValueError, TypeError):
            return [], None

        prod_name = fix_mojibake((prod.get("productName") or "").strip())
        clean_name = strip_tcg_variant_suffixes(prod_name)
        product_type = (prod.get("productTypeName") or "").strip()
        is_sealed = bool(prod.get("sealed")) or product_type in SEALED_PRODUCT_TYPES
        market_price = _safe_float(prod.get("marketPrice"))
        lowest_price = _safe_float(prod.get("lowestPrice"))
        lowest_landed = _safe_float(prod.get("lowestPriceWithShipping"))
        median_price = _safe_float(prod.get("medianPrice"))
        foil_only = bool(prod.get("foilOnly"))
        normal_only = bool(prod.get("normalOnly"))
        set_name = (prod.get("setName") or "").strip()
        set_code = (prod.get("setCode") or "").strip().upper()
        rarity = (prod.get("rarityName") or "").strip()
        custom_attrs = prod.get("customAttributes") or {}
        description = custom_attrs.get("description") or ""
        detail_note = custom_attrs.get("detailNote") or ""
        release_date = custom_attrs.get("releaseDate") or ""
        is_presale = "presale" in str(detail_note).lower()

        rows: List[Dict[str, Any]] = []
        seller_meta: Optional[Dict[str, Any]] = None

        for l in prod.get("listings") or []:
            l_skey = str(l.get("sellerKey") or "").strip().lower()
            if l_skey and l_skey != seller_key:
                continue

            lang = str(l.get("language") or "English").strip()
            lang_abbr = str(l.get("languageAbbreviation") or "EN").strip().upper()
            if english_only and (lang.lower() != "english" or lang_abbr != "EN"):
                continue

            price = _safe_float(l.get("price"))
            if not price or price <= 0:
                continue

            shipping = _safe_nonneg_float(
                l.get("shippingPrice") if l.get("shippingPrice") is not None else l.get("sellerShippingPrice")
            )
            cond = (l.get("condition") or ("Unopened" if is_sealed else "Near Mint")).strip()
            printing = (l.get("printing") or "Normal").strip()
            try:
                qty = max(1, int(l.get("quantity") or 1))
            except (ValueError, TypeError):
                qty = 1

            if seller_meta is None:
                seller_meta = self._seller_meta_from_listing(l, fallback_key=seller_key)

            rows.append({
                "productId": pid,
                "productName": prod_name,
                "cleanName": clean_name or prod_name,
                "setName": set_name,
                "setCode": set_code,
                "rarity": rarity,
                "productType": product_type,
                "isSealed": is_sealed,
                "isPresale": is_presale,
                "releaseDate": release_date,
                "description": description if is_sealed else "",
                "marketPrice": market_price,
                "lowestPrice": lowest_price,
                "lowestLandedPrice": lowest_landed,
                "medianPrice": median_price,
                "foilOnly": foil_only,
                "normalOnly": normal_only,
                "listingId": l.get("listingId"),
                "price": price,
                "shippingPrice": shipping,
                "landedPrice": round(price + shipping, 2),
                "condition": cond,
                "printing": printing,
                "quantity": qty,
                "language": lang,
                "languageAbbreviation": lang_abbr,
                "imageUrl": TCG_PRODUCT_IMAGE_URL.format(product_id=pid),
                "tcgplayerUrl": f"https://www.tcgplayer.com/product/{pid}?seller={seller_key}&Language=English&page=1",
                "storeSearchUrl": (
                    f"https://www.tcgplayer.com/search/magic/product?seller={seller_key}"
                    f"&q={urllib.parse.quote_plus(clean_name or prod_name)}&view=grid"
                ),
            })

        return rows, seller_meta

    def fetch_seller_inventory(
        self,
        seller_key: str,
        product_types: Optional[List[str]] = None,
        *,
        min_market: float = 1.0,
        max_products: int = 2000,
        time_budget_s: float = 40.0,
        english_only: bool = True,
        force_refresh: bool = False,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Fetches a seller's active MTG inventory across TCGplayer search pages,
        splitting into descending market-price bands when totalResults > 9,500
        to bypass the API's 10,000-offset pagination ceiling.
        """
        clean_key = (seller_key or "").strip().lower()
        ptypes = list(product_types or ["Cards"])
        min_mkt = max(0.0, float(min_market or 0.0))
        max_prods = max(1, int(max_products or 2000))
        budget = max(0.5, float(time_budget_s or 40.0))

        cache_key = (
            clean_key,
            tuple(sorted(ptypes)),
            round(min_mkt, 2),
            max_prods,
            bool(english_only),
        )
        now_wall = time.time()
        if use_cache and not force_refresh:
            with _CACHE_LOCK:
                cached = _SELLER_INV_CACHE.get(cache_key)
                if cached and (now_wall - cached[0]) < SELLER_INV_CACHE_TTL:
                    payload = dict(cached[1])
                    payload["fromCache"] = True
                    return payload

        deadline = time.monotonic() + budget
        rows: List[Dict[str, Any]] = []
        seen_pids: Set[int] = set()
        seller_meta: Optional[Dict[str, Any]] = None
        total_in_store = 0
        products_fetched = 0
        partial = False

        def _ingest_products(page_items: List[Dict[str, Any]]) -> None:
            nonlocal products_fetched, seller_meta
            for prod in page_items:
                if products_fetched >= max_prods:
                    break
                raw_pid = prod.get("productId")
                try:
                    pid = int(raw_pid) if raw_pid is not None else None
                except (ValueError, TypeError):
                    pid = None
                if pid is None or pid in seen_pids:
                    continue
                seen_pids.add(pid)
                products_fetched += 1
                norm_rows, sm = self._normalize_product_listings(prod, clean_key, english_only)
                if seller_meta is None and sm is not None:
                    seller_meta = sm
                rows.extend(norm_rows)

        def _fetch_band(
            gte_val: Optional[float],
            lte_val: Optional[float],
            first_page_data: Optional[Dict[str, Any]] = None,
        ) -> Tuple[int, bool]:
            nonlocal partial
            offset = 0
            band_total = 0
            completed_band = False

            while offset <= 9950 and products_fetched < max_prods:
                if offset > 0 and time.monotonic() >= deadline:
                    partial = True
                    break

                if offset == 0 and first_page_data is not None:
                    data = first_page_data
                else:
                    if time.monotonic() >= deadline:
                        partial = True
                        break
                    payload = self._build_seller_search_payload(
                        clean_key,
                        ptypes,
                        from_offset=offset,
                        size=50,
                        gte_market=gte_val,
                        lte_market=lte_val,
                        english_only=english_only,
                        sort_by_market=True,
                    )
                    data = self._post(TCG_MP_SEARCH_URL, payload)

                if not data or not data.get("results"):
                    completed_band = offset > 0
                    break

                res0 = data["results"][0]
                if offset == 0:
                    band_total = int(res0.get("totalResults") or 0)
                page_items = res0.get("results") or []
                if not page_items:
                    completed_band = True
                    break

                _ingest_products(page_items)
                offset += len(page_items)

                if products_fetched >= max_prods:
                    if band_total > products_fetched or total_in_store > products_fetched:
                        partial = True
                    break

                if offset >= band_total or len(page_items) < 50:
                    completed_band = True
                    break

                if offset > 9950:
                    partial = True
                    break

            return band_total, completed_band

        if time.monotonic() >= deadline:
            partial = True
        else:
            probe_payload = self._build_seller_search_payload(
                clean_key,
                ptypes,
                from_offset=0,
                size=50,
                gte_market=min_mkt if min_mkt > 0 else None,
                lte_market=None,
                english_only=english_only,
                sort_by_market=True,
            )
            probe_data = self._post(TCG_MP_SEARCH_URL, probe_payload)
            if probe_data and probe_data.get("results"):
                total_in_store = int(probe_data["results"][0].get("totalResults") or 0)
                if total_in_store <= 9500:
                    _fetch_band(min_mkt if min_mkt > 0 else None, None, first_page_data=probe_data)
                else:
                    # Split into descending marketPrice bands so each band fits within 10k results
                    bands: List[Tuple[Optional[float], Optional[float]]] = []
                    for lo, hi in PRICE_BANDS:
                        if hi is not None and hi < min_mkt:
                            continue
                        adj_lo: Optional[float] = max(lo, min_mkt) if min_mkt > 0 else lo
                        if lo == 0.01 and min_mkt == 0.0:
                            adj_lo = None
                        bands.append((adj_lo, hi))

                    for idx, (b_lo, b_hi) in enumerate(bands):
                        if products_fetched >= max_prods:
                            if total_in_store > products_fetched:
                                partial = True
                            break
                        if time.monotonic() >= deadline:
                            partial = True
                            break
                        band_tot, band_done = _fetch_band(b_lo, b_hi)
                        if band_tot > 9950 and not band_done:
                            partial = True

        result = {
            "rows": rows,
            "sellerMeta": seller_meta,
            "totalInStore": total_in_store,
            "productsFetched": products_fetched,
            "partial": partial,
            "fromCache": False,
        }

        if use_cache and not partial and (rows or total_in_store == 0):
            with _CACHE_LOCK:
                if len(_SELLER_INV_CACHE) >= SELLER_INV_CACHE_MAX:
                    oldest_key = min(_SELLER_INV_CACHE.items(), key=lambda kv: kv[1][0])[0]
                    _SELLER_INV_CACHE.pop(oldest_key, None)
                _SELLER_INV_CACHE[cache_key] = (time.time(), dict(result))

        return result

    def score_value_listing(
        self,
        row: Dict[str, Any],
        *,
        min_discount_pct: float = 10.0,
        min_market: float = 1.0,
        allowed_conditions: Tuple[str, ...] = ("Near Mint", "Lightly Played"),
    ) -> Dict[str, Any]:
        """
        Evaluates whether a single-card listing is priced at a compelling value vs TCGplayer Market.
        Non-foil-only foil printings are flagged as foilUnverified and excluded from pure value picks.
        """
        price = float(row.get("price") or 0.0)
        mkt = row.get("marketPrice")
        lowest = row.get("lowestPrice")
        cond = row.get("condition") or "Near Mint"
        printing = (row.get("printing") or "Normal").lower()
        foil_only = bool(row.get("foilOnly"))

        is_foil_printing = any(tok in printing for tok in ("foil", "etched"))
        foil_unverified = bool(is_foil_printing and not foil_only)

        discount_pct: Optional[float] = None
        savings_amt: Optional[float] = None
        vs_lowest_pct: Optional[float] = None

        if mkt and mkt > 0 and price > 0:
            savings_amt = round(mkt - price, 2)
            discount_pct = round(((mkt - price) / mkt) * 100.0, 1)

        if lowest and lowest > 0 and price > 0:
            vs_lowest_pct = round(((lowest - price) / lowest) * 100.0, 1)

        verify_flag = bool(mkt and mkt > 0 and price > 0 and price < round(mkt * 0.50, 2))

        if discount_pct is None:
            value_label = "No Market Data"
        elif discount_pct >= 30.0:
            value_label = "Steal"
        elif discount_pct >= 20.0:
            value_label = "Great Value"
        elif discount_pct >= float(min_discount_pct):
            value_label = "Good Value"
        elif discount_pct >= -5.0:
            value_label = "Near Market"
        else:
            value_label = "Above Market"

        req_disc = float(min_discount_pct) + (5.0 if cond == "Lightly Played" else 0.0)

        eligible = True
        if row.get("isSealed") or is_non_playable_card_product(row):
            eligible = False
        elif cond not in allowed_conditions:
            eligible = False
        elif not mkt or mkt < float(min_market) or price <= 0:
            eligible = False
        elif foil_unverified:
            eligible = False
        elif discount_pct is None or savings_amt is None or savings_amt <= 0 or discount_pct < req_disc:
            eligible = False

        return {
            "eligible": eligible,
            "discountPct": discount_pct,
            "savingsVsMarket": savings_amt,
            "vsLowestPct": vs_lowest_pct,
            "valueLabel": value_label,
            "verifyFlag": verify_flag,
            "foilUnverified": foil_unverified,
            "requiredDiscountPct": req_disc,
        }

    @staticmethod
    def estimate_combined_shipping(
        sealed_items: List[Dict[str, Any]],
        single_items: List[Dict[str, Any]],
        direct_seller: bool = False,
    ) -> Dict[str, Any]:
        """
        Estimates combined order shipping when buying sealed and/or singles from one TCGplayer seller,
        accounting for TCGplayer's $5 free-shipping threshold on marketplace singles.
        """
        sealed_sub = sum(float(x.get("price") or 0.0) * max(1, int(x.get("selectedQty", 1))) for x in sealed_items)
        singles_sub = sum(float(x.get("price") or 0.0) * max(1, int(x.get("selectedQty", 1))) for x in single_items)
        subtotal = round(sealed_sub + singles_sub, 2)

        sealed_ship = max((float(x.get("shippingPrice") or 0.0) for x in sealed_items), default=0.0)
        max_single_ship = max((float(x.get("shippingPrice") or 0.0) for x in single_items), default=0.0)

        if single_items and subtotal >= 5.0 and max_single_ship <= 1.99:
            eff_single_ship = 0.0
        elif single_items and direct_seller and subtotal >= 50.0 and not sealed_items:
            eff_single_ship = 0.0
        else:
            eff_single_ship = max_single_ship

        est_shipping = round(max(sealed_ship, eff_single_ship), 2)
        separate_shipping = round(
            sum(float(x.get("shippingPrice") or 0.0) for x in sealed_items)
            + sum(float(x.get("shippingPrice") or 0.0) for x in single_items),
            2,
        )
        shipping_saved = round(max(0.0, separate_shipping - est_shipping), 2)

        return {
            "subtotal": subtotal,
            "estShipping": est_shipping,
            "separateShipping": separate_shipping,
            "shippingSaved": shipping_saved,
            "landedTotal": round(subtotal + est_shipping, 2),
        }

    @staticmethod
    def build_mass_entry_text(items: List[Dict[str, Any]]) -> str:
        """Formats selected single cards into TCGplayer Mass Entry lines ('1 Card Name')."""
        lines: List[str] = []
        seen: Set[str] = set()
        for item in items:
            if item.get("isSealed"):
                continue
            cname = (item.get("cleanName") or strip_tcg_variant_suffixes(item.get("productName")) or "").strip()
            if not cname:
                continue
            front = cname.split(" // ")[0].strip()
            key = front.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"1 {front}")
        return "\n".join(lines)

    def scan_seller_value(
        self,
        seller_input: Union[str, Dict[str, Any]],
        *,
        watchlist_items: Optional[List[Any]] = None,
        owned_map: Optional[Dict[str, int]] = None,
        deck_upgrades: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        saved_sellers: Optional[List[Any]] = None,
        decks_missing_upgrades: Optional[List[Dict[str, Any]]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Scans a specific TCGplayer seller's store and buckets recommendations into:
        - watchlistHits: watchlist singles at decent prices (annotated with owned qty)
        - deckUpgrades: Commander deck shopping-list upgrades (excluding owned when requested)
        - valuePicks: any other single priced >= min_discount_pct below market
        - sealedPicks: watched sealed items or any sealed product priced below market
        """
        start_ts = time.time()
        opts = options or {}

        min_discount_pct = max(0.0, min(80.0, float(opts.get("min_discount_pct", 10.0))))
        min_market = max(0.0, float(opts.get("min_market", 1.0)))
        sealed_min_discount_pct = max(0.0, min(80.0, float(opts.get("sealed_min_discount_pct", 0.0))))
        allow_mp = bool(opts.get("allow_mp", False))
        allowed_conditions = normalize_allowed_conditions(opts.get("conditions"), allow_mp=allow_mp)
        include_sealed = bool(opts.get("include_sealed", True))
        include_cases = bool(opts.get("include_cases", False))
        include_value = bool(opts.get("include_value", True))
        include_deck_upgrades = bool(opts.get("include_deck_upgrades", True))
        include_watchlist = bool(opts.get("include_watchlist", True))
        exclude_owned = bool(opts.get("exclude_owned", True))
        english_only = bool(opts.get("english_only", True))
        force_refresh = bool(opts.get("force_refresh", False))
        use_cache = bool(opts.get("use_cache", True))
        max_products = max(50, min(5000, int(opts.get("max_products", 2000))))
        time_budget_s = max(2.0, min(55.0, float(opts.get("time_budget_s", 42.0))))
        watchlist_tolerance_mode = str(opts.get("watchlist_tolerance_mode", "decent_10")).strip().lower()

        if isinstance(seller_input, dict) and seller_input.get("sellerKey"):
            seller = dict(seller_input)
            seller_key = str(seller["sellerKey"]).strip().lower()
        else:
            seller = self.resolve_seller(str(seller_input or ""), saved_sellers=saved_sellers)
            seller_key = seller["sellerKey"]

        owned_lookup = owned_map or {}
        upgrades_lookup = deck_upgrades or {}

        wl_singles: List[Any] = []
        wl_sealed: List[Any] = []
        for w in watchlist_items or []:
            if getattr(w, "is_sealed", False):
                wl_sealed.append(w)
            else:
                wl_singles.append(w)

        wl_by_key: Dict[str, Any] = {}
        cheap_watchlist_names: List[str] = []
        for w in wl_singles:
            w_name = getattr(w, "name", "") or ""
            for k in canonical_card_keys(w_name):
                wl_by_key.setdefault(k, w)
            ref_mkt = getattr(w, "market_price", None) or getattr(w, "lowest_in_stock_price", None)
            if ref_mkt is None or ref_mkt < min_market:
                if w_name:
                    cheap_watchlist_names.append(w_name)

        cards_budget = time_budget_s * (0.78 if include_sealed else 0.95)
        cards_inv = self.fetch_seller_inventory(
            seller_key,
            ["Cards"],
            min_market=min_market,
            max_products=max_products,
            time_budget_s=cards_budget,
            english_only=english_only,
            force_refresh=force_refresh,
            use_cache=use_cache,
        )
        card_rows = list(cards_inv["rows"])
        if cards_inv.get("sellerMeta"):
            for k, v in cards_inv["sellerMeta"].items():
                if v or k not in seller:
                    seller[k] = v

        # Supplement with cheap watchlist singles (< min_market) so budget staples are still found
        seen_card_pids = {r["productId"] for r in card_rows}
        if include_watchlist and cheap_watchlist_names and (time.time() - start_ts) < time_budget_s * 0.85:
            try:
                cheap_prods = self.lot_provider._query_seller_watchlist_inventory(
                    seller_key,
                    cheap_watchlist_names[:160],
                    english_only=english_only,
                )
                for prod in cheap_prods or []:
                    raw_pid = prod.get("productId")
                    try:
                        pid = int(raw_pid)
                    except (ValueError, TypeError):
                        continue
                    if pid in seen_card_pids:
                        continue
                    seen_card_pids.add(pid)
                    norm_rows, _ = self._normalize_product_listings(prod, seller_key, english_only)
                    card_rows.extend(norm_rows)
            except Exception as err:
                logger.debug("Cheap watchlist supplement error for seller %s: %s", seller_key, err)

        best_tracked_by_canon: Dict[str, Dict[str, Any]] = {}
        pure_value_by_pid: Dict[int, Dict[str, Any]] = {}
        owned_excluded_count = 0

        for row in card_rows:
            if row.get("isSealed") or is_non_playable_card_product(row):
                continue

            cond = row.get("condition") or "Near Mint"
            keys = canonical_card_keys(row.get("productName"))
            if not keys:
                continue
            canon_id = strip_tcg_variant_suffixes(row.get("productName")).split(" // ")[0].strip().lower()
            if not canon_id:
                canon_id = (row.get("cleanName") or row.get("productName") or "").lower()

            owned_qty = max((owned_lookup.get(k, 0) for k in keys), default=0)
            is_owned = owned_qty > 0

            # Base value metrics (computed for all single cards for UI display)
            val_eval = self.score_value_listing(
                row,
                min_discount_pct=min_discount_pct,
                min_market=min_market,
                allowed_conditions=allowed_conditions,
            )

            # 1. Watchlist match
            matched_wl = None
            if include_watchlist:
                for k in keys:
                    if k in wl_by_key:
                        matched_wl = wl_by_key[k]
                        break

            wl_qualifies = False
            wl_label = ""
            if matched_wl is not None and cond in allowed_conditions:
                w_target = getattr(matched_wl, "target_price", None)
                baseline_mkt = getattr(matched_wl, "lowest_in_stock_price", None) or getattr(matched_wl, "market_price", None)
                if not getattr(matched_wl, "is_any_version", True):
                    baseline_mkt = getattr(matched_wl, "market_price", None) or baseline_mkt

                if val_eval["foilUnverified"] and baseline_mkt is None and not (w_target and row["price"] <= w_target):
                    wl_qualifies = False
                else:
                    wl_qualifies, wl_label, _ = self.lot_provider.evaluate_listing_decent_price(
                        listing_price=row["price"],
                        market_price=row.get("marketPrice"),
                        target_price=w_target,
                        tolerance_mode=watchlist_tolerance_mode,
                        baseline_cheapest_market=baseline_mkt,
                    )

            # 2. Commander deck upgrade match
            matched_decks: List[Dict[str, Any]] = []
            deck_qualifies = False
            if include_deck_upgrades and cond in allowed_conditions and not (exclude_owned and is_owned):
                seen_deck_ids: Set[int] = set()
                for k in keys:
                    for d_info in upgrades_lookup.get(k, []):
                        did = d_info["deckId"]
                        if did not in seen_deck_ids:
                            seen_deck_ids.add(did)
                            matched_decks.append(d_info)

                if matched_decks:
                    ref_prices = [d["refPrice"] for d in matched_decks if d.get("refPrice") and d["refPrice"] > 0]
                    min_ref_price = min(ref_prices) if ref_prices else None
                    mkt = row.get("marketPrice")
                    if val_eval["foilUnverified"]:
                        if min_ref_price and row["price"] <= round(min_ref_price * 1.10, 2):
                            deck_qualifies = True
                    else:
                        if mkt and min_ref_price and mkt > min_ref_price * 1.5:
                            eff_deck_mkt = min_ref_price
                        else:
                            eff_deck_mkt = mkt or min_ref_price
                        if eff_deck_mkt and eff_deck_mkt > 0 and row["price"] <= round(eff_deck_mkt * 1.10, 2):
                            deck_qualifies = True

            # 3. Pure value pick match
            value_qualifies = False
            if include_value and val_eval["eligible"]:
                if exclude_owned and is_owned:
                    owned_excluded_count += 1
                else:
                    value_qualifies = True

            if not (wl_qualifies or deck_qualifies or value_qualifies):
                if (matched_decks or val_eval["eligible"]) and exclude_owned and is_owned:
                    owned_excluded_count += 1
                continue

            reasons: List[str] = []
            if wl_qualifies:
                reasons.append("watchlist")
            if deck_qualifies:
                reasons.append("deck_upgrade")
            if value_qualifies:
                reasons.append("value")

            item_entry = {
                "productId": row["productId"],
                "listingId": row["listingId"],
                "productName": row["productName"],
                "cleanName": row["cleanName"],
                "canonId": canon_id,
                "setName": row["setName"],
                "setCode": row["setCode"],
                "rarity": row["rarity"],
                "isSealed": False,
                "price": row["price"],
                "shippingPrice": row["shippingPrice"],
                "landedPrice": row["landedPrice"],
                "marketPrice": row["marketPrice"],
                "lowestPrice": row["lowestPrice"],
                "condition": cond,
                "printing": row["printing"],
                "quantity": row["quantity"],
                "language": row["language"],
                "imageUrl": row["imageUrl"],
                "tcgplayerUrl": row["tcgplayerUrl"],
                "storeSearchUrl": row["storeSearchUrl"],
                "discountPct": val_eval["discountPct"],
                "savingsVsMarket": val_eval["savingsVsMarket"],
                "vsLowestPct": val_eval["vsLowestPct"],
                "valueLabel": val_eval["valueLabel"],
                "verifyFlag": val_eval["verifyFlag"],
                "foilUnverified": val_eval["foilUnverified"],
                "isSpecialTreatment": detect_special_treatment(row["productName"]),
                "reasons": reasons,
                "ownedCount": owned_qty,
                "inBinder": is_owned,
                "watchlistItemId": getattr(matched_wl, "id", None) if wl_qualifies else None,
                "targetPrice": getattr(matched_wl, "target_price", None) if wl_qualifies else None,
                "watchlistLabel": wl_label if wl_qualifies else "",
                "decks": matched_decks if deck_qualifies else [],
            }

            if wl_qualifies or deck_qualifies:
                existing = best_tracked_by_canon.get(canon_id)
                if existing is None:
                    best_tracked_by_canon[canon_id] = item_entry
                else:
                    # Prefer item with watchlist reason, then lower price
                    new_prio = (1 if "watchlist" in reasons else 0, -item_entry["price"])
                    old_prio = (1 if "watchlist" in existing["reasons"] else 0, -existing["price"])
                    if new_prio > old_prio:
                        # Preserve any combined reasons/decks from the replaced entry
                        merged_reasons = list(dict.fromkeys(item_entry["reasons"] + existing["reasons"]))
                        item_entry["reasons"] = merged_reasons
                        if not item_entry["decks"] and existing["decks"]:
                            item_entry["decks"] = existing["decks"]
                        if not item_entry["watchlistItemId"] and existing["watchlistItemId"]:
                            item_entry["watchlistItemId"] = existing["watchlistItemId"]
                            item_entry["targetPrice"] = existing["targetPrice"]
                            item_entry["watchlistLabel"] = existing["watchlistLabel"]
                        best_tracked_by_canon[canon_id] = item_entry
                    else:
                        existing["reasons"] = list(dict.fromkeys(existing["reasons"] + item_entry["reasons"]))
                        if not existing["decks"] and item_entry["decks"]:
                            existing["decks"] = item_entry["decks"]
            else:
                pid = row["productId"]
                prev_pid = pure_value_by_pid.get(pid)
                if prev_pid is None or item_entry["price"] < prev_pid["price"]:
                    pure_value_by_pid[pid] = item_entry

        watchlist_hits: List[Dict[str, Any]] = []
        deck_upgrade_hits: List[Dict[str, Any]] = []
        value_picks: List[Dict[str, Any]] = []

        tracked_canons = set(best_tracked_by_canon.keys())
        for entry in best_tracked_by_canon.values():
            if "watchlist" in entry["reasons"] and include_watchlist:
                entry["primaryBucket"] = "watchlist"
                watchlist_hits.append(entry)
            elif "deck_upgrade" in entry["reasons"] and include_deck_upgrades:
                entry["primaryBucket"] = "deck_upgrade"
                deck_upgrade_hits.append(entry)
            elif "value" in entry["reasons"] and include_value:
                entry["primaryBucket"] = "value"
                value_picks.append(entry)

        # Cap pure value printings at 2 per canonical card name (skipping cards already in tracked buckets)
        value_by_canon: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for entry in pure_value_by_pid.values():
            if entry["canonId"] in tracked_canons:
                continue
            entry["primaryBucket"] = "value"
            value_by_canon[entry["canonId"]].append(entry)

        for canon_list in value_by_canon.values():
            canon_list.sort(key=lambda x: (-(x.get("discountPct") or 0.0), x["price"]))
            value_picks.extend(canon_list[:2])

        def _sort_key(item: Dict[str, Any], weight: float) -> Tuple[float, float, float]:
            sav = max(0.0, float(item.get("savingsVsMarket") or 0.0))
            disc = float(item.get("discountPct") or 0.0)
            return (-(sav * weight), -disc, item["price"])

        watchlist_hits.sort(key=lambda x: _sort_key(x, 1.5))
        deck_upgrade_hits.sort(key=lambda x: _sort_key(x, 1.3))
        value_picks.sort(key=lambda x: _sort_key(x, 1.0))

        # 4. Sealed picks scan
        sealed_picks: List[Dict[str, Any]] = []
        sealed_partial = False
        sealed_total_in_store = 0
        if include_sealed:
            rem_budget = max(4.0, time_budget_s - (time.time() - start_ts))
            sealed_inv = self.fetch_seller_inventory(
                seller_key,
                SEALED_PRODUCT_TYPES,
                min_market=0.0,
                max_products=300,
                time_budget_s=rem_budget,
                english_only=english_only,
                force_refresh=force_refresh,
                use_cache=use_cache,
            )
            sealed_partial = bool(sealed_inv.get("partial"))
            sealed_total_in_store = int(sealed_inv.get("totalInStore") or 0)
            if seller.get("sellerName", "").startswith("Seller ") and sealed_inv.get("sellerMeta"):
                for k, v in sealed_inv["sellerMeta"].items():
                    if v or k not in seller:
                        seller[k] = v

            seen_sealed_pids: Set[int] = set()
            for row in sorted(sealed_inv["rows"], key=lambda r: (r["landedPrice"], r["price"])):
                pid = row["productId"]
                if pid in seen_sealed_pids:
                    continue
                seen_sealed_pids.add(pid)

                matched_wl_sealed = None
                for w in wl_sealed:
                    w_pid = getattr(w, "tcgplayer_product_id", None)
                    if w_pid and int(w_pid) == pid:
                        matched_wl_sealed = w
                        break
                    if match_sealed(row["productName"], getattr(w, "name", "") or "").matched:
                        matched_wl_sealed = w
                        break

                mkt = row.get("marketPrice")
                w_target = getattr(matched_wl_sealed, "target_price", None) if matched_wl_sealed else None
                eval_info = evaluate_sealed_deal(
                    landed_price=row["landedPrice"],
                    market_price=mkt,
                    target_price=w_target,
                    tolerance_mode="market_0",
                )
                disc_pct = round(((mkt - row["price"]) / mkt) * 100.0, 1) if (mkt and mkt > 0) else None
                item_sav = round(mkt - row["price"], 2) if (mkt and mkt > 0) else None

                include_this = False
                sealed_reasons: List[str] = []
                if matched_wl_sealed is not None:
                    if (w_target and row["price"] <= w_target) or (mkt and row["price"] <= mkt) or eval_info["isDeal"]:
                        include_this = True
                        sealed_reasons.append("watchlist_sealed")
                        if item_sav and item_sav > 0:
                            sealed_reasons.append("below_market")
                elif mkt and mkt > 0 and row["price"] < mkt and (disc_pct or 0.0) >= sealed_min_discount_pct:
                    if row["landedPrice"] <= round(mkt * 1.05, 2):
                        include_this = True
                        sealed_reasons.append("below_market")

                if not include_this:
                    continue

                cat, cat_label, subtype = classify_sealed_product(
                    row["productName"],
                    row.get("productType") or "",
                    row.get("description") or "",
                )
                if matched_wl_sealed is None and not include_cases and cat == "case_other":
                    continue
                pack_count = extract_pack_count(
                    row["productName"],
                    row.get("description") or "",
                    category=cat,
                    booster_subtype=subtype,
                )
                price_per_pack = (
                    round(row["price"] / pack_count, 2)
                    if (pack_count and pack_count > 0)
                    else None
                )

                sealed_picks.append({
                    "productId": pid,
                    "listingId": row["listingId"],
                    "productName": row["productName"],
                    "cleanName": row["productName"],
                    "setName": row["setName"],
                    "setCode": row["setCode"],
                    "category": cat,
                    "categoryLabel": cat_label,
                    "boosterSubtype": subtype,
                    "packCount": pack_count,
                    "pricePerPack": price_per_pack,
                    "isSealed": True,
                    "isPresale": bool(row.get("isPresale")),
                    "releaseDate": row.get("releaseDate") or "",
                    "price": row["price"],
                    "shippingPrice": row["shippingPrice"],
                    "landedPrice": row["landedPrice"],
                    "marketPrice": mkt,
                    "condition": row["condition"],
                    "quantity": row["quantity"],
                    "imageUrl": row["imageUrl"],
                    "tcgplayerUrl": row["tcgplayerUrl"],
                    "discountPct": disc_pct,
                    "savingsVsMarket": item_sav,
                    "dealLabel": eval_info["dealLabel"],
                    "verifyFlag": bool(mkt and mkt > 0 and row["price"] < round(mkt * 0.50, 2)),
                    "isWatchedSealed": matched_wl_sealed is not None,
                    "watchlistItemId": getattr(matched_wl_sealed, "id", None) if matched_wl_sealed else None,
                    "targetPrice": w_target,
                    "reasons": sealed_reasons,
                    "primaryBucket": "sealed",
                })

            sealed_picks.sort(
                key=lambda s: (
                    0 if s["isWatchedSealed"] else 1,
                    1 if s.get("isPresale") else 0,
                    -(s.get("savingsVsMarket") or 0.0),
                    -(s.get("discountPct") or 0.0),
                    s["price"],
                )
            )

        # Default recommended cart: watchlist + deck upgrades + watched sealed (or top 10 value picks)
        default_singles = watchlist_hits + deck_upgrade_hits
        if not default_singles:
            default_singles = value_picks[:10]
        default_sealed = [s for s in sealed_picks if s.get("isWatchedSealed")]

        ship_summary = self.estimate_combined_shipping(
            default_sealed,
            default_singles,
            direct_seller=bool(seller.get("directSeller")),
        )
        default_market = round(
            sum(float(x.get("marketPrice") or x["price"]) for x in (default_singles + default_sealed)),
            2,
        )
        default_savings = round(default_market - ship_summary["landedTotal"], 2)

        all_recs = watchlist_hits + deck_upgrade_hits + value_picks + sealed_picks
        best_single_sav = max((float(x.get("savingsVsMarket") or 0.0) for x in all_recs), default=0.0)

        duration = round(time.time() - start_ts, 2)
        return {
            "success": True,
            "seller": seller,
            "watchlistHits": watchlist_hits,
            "deckUpgrades": deck_upgrade_hits,
            "valuePicks": value_picks,
            "sealedPicks": sealed_picks,
            "cartSummary": {
                "selectedCount": len(default_singles) + len(default_sealed),
                "singlesCount": len(default_singles),
                "sealedCount": len(default_sealed),
                "subtotal": ship_summary["subtotal"],
                "estShipping": ship_summary["estShipping"],
                "shippingSaved": ship_summary["shippingSaved"],
                "landedTotal": ship_summary["landedTotal"],
                "totalMarket": default_market,
                "netSavings": default_savings,
                "massEntryText": self.build_mass_entry_text(default_singles),
            },
            "summary": {
                "watchlistCount": len(watchlist_hits),
                "deckUpgradesCount": len(deck_upgrade_hits),
                "valuePicksCount": len(value_picks),
                "sealedPicksCount": len(sealed_picks),
                "totalRecommendations": len(all_recs),
                "ownedExcludedCount": owned_excluded_count,
                "bestSavingsAmt": round(max(0.0, best_single_sav), 2),
            },
            "storeStats": {
                "totalCardsInStore": cards_inv.get("totalInStore", 0),
                "cardsProductsScanned": cards_inv.get("productsFetched", 0),
                "totalSealedInStore": sealed_total_in_store,
                "partial": bool(cards_inv.get("partial") or sealed_partial),
                "fromCache": bool(cards_inv.get("fromCache")),
                "durationSeconds": duration,
                "decksMissingUpgrades": decks_missing_upgrades or [],
            },
            "options": {
                "min_discount_pct": min_discount_pct,
                "min_market": min_market,
                "conditions": list(allowed_conditions),
                "include_sealed": include_sealed,
                "include_value": include_value,
                "include_deck_upgrades": include_deck_upgrades,
                "include_watchlist": include_watchlist,
                "exclude_owned": exclude_owned,
                "english_only": english_only,
            },
        }

    def find_combined_carts(
        self,
        watchlist_items: List[Any],
        *,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Finds TCGplayer sellers carrying BOTH wanted sealed products (watched sealed
        items or below-market sealed offerings) AND watchlist singles, defaulting to
        min_sealed=1 and min_singles=1 so buyers can consolidate shipping.
        """
        start_ts = time.time()
        opts = options or {}
        min_sealed = max(1, min(10, int(opts.get("min_sealed", 1))))
        min_singles = max(1, min(20, int(opts.get("min_singles", 1))))
        tolerance_mode = str(opts.get("price_tolerance_mode", "decent_10")).strip().lower()
        sealed_min_discount_pct = max(0.0, min(80.0, float(opts.get("sealed_min_discount_pct", 0.0))))
        include_cases = bool(opts.get("include_cases", False))
        tag_filter = str(opts.get("tag_filter", "all") or "all").strip()
        allow_mp = bool(opts.get("allow_mp", False))
        english_only = bool(opts.get("english_only", True))
        max_sellers = max(1, min(40, int(opts.get("max_sellers", 25))))

        wl_sealed: List[Any] = []
        wl_singles: List[Any] = []

        for item in watchlist_items or []:
            if tag_filter.lower() != "all":
                item_tag = (getattr(item, "tag", "") or "").strip().lower()
                if tag_filter.lower() == "__untagged__":
                    if item_tag:
                        continue
                elif item_tag != tag_filter.lower():
                    continue
            if getattr(item, "is_sealed", False):
                wl_sealed.append(item)
            else:
                wl_singles.append(item)

        if not wl_singles:
            return {
                "success": True,
                "carts": [],
                "totalCartsFound": 0,
                "candidateSellersScanned": 0,
                "watchedSealedCount": len(wl_sealed),
                "watchedSinglesCount": 0,
                "message": "No watchlist singles match the selected tag filter.",
                "durationSeconds": round(time.time() - start_ts, 2),
            }

        single_by_norm: Dict[str, Any] = {}
        for item in wl_singles:
            name = getattr(item, "name", "") or ""
            if name:
                norm = normalize_card_name(name.split(" // ")[0].strip())
                single_by_norm[norm] = item
        all_single_names = [getattr(it, "name", "") for it in single_by_norm.values() if getattr(it, "name", "")]

        # Step 1: Discover candidate sellers from watched sealed items + watchlist single seeds
        candidate_map: Dict[str, Dict[str, Any]] = {}

        def _register_candidate(meta: Dict[str, Any], weight: int) -> None:
            s_key = (meta.get("sellerKey") or "").strip().lower()
            if not s_key:
                return
            if s_key not in candidate_map:
                candidate_map[s_key] = {
                    "sellerKey": s_key,
                    "sellerName": meta.get("sellerName") or f"Seller {s_key}",
                    "sellerRating": meta.get("sellerRating"),
                    "sellerSales": str(meta.get("sellerSales") or ""),
                    "directSeller": bool(meta.get("directSeller")),
                    "goldSeller": bool(meta.get("goldSeller")),
                    "score": 0,
                }
            candidate_map[s_key]["score"] += weight

        sealed_with_pid = [w for w in wl_sealed if getattr(w, "tcgplayer_product_id", None)][:15]
        if sealed_with_pid:
            with ThreadPoolExecutor(max_workers=4) as ex:
                fut_map = {
                    ex.submit(
                        self.sealed_provider.get_product_live_listings,
                        int(w.tcgplayer_product_id),
                        english_only=english_only,
                        size=40,
                    ): w
                    for w in sealed_with_pid
                }
                for fut in as_completed(fut_map):
                    try:
                        listings = fut.result() or []
                    except Exception:
                        listings = []
                    seen_in_prod: Set[str] = set()
                    for l in listings:
                        s_key = str(l.get("sellerKey") or "").strip().lower()
                        if not s_key or s_key in seen_in_prod:
                            continue
                        seen_in_prod.add(s_key)
                        _register_candidate(l, weight=10)

        try:
            seed_sellers = self.lot_provider._discover_sellers_for_watchlist(
                candidate_card_names=all_single_names,
                seed_count=12,
                english_only=english_only,
            )
            for s in seed_sellers or []:
                _register_candidate(s, weight=int(s.get("count") or 1))
        except Exception as err:
            logger.debug("Watchlist seed discovery failed in combined carts: %s", err)

        ranked_candidates = sorted(
            candidate_map.values(),
            key=lambda x: -x["score"],
        )[:max_sellers]

        if not ranked_candidates:
            return {
                "success": True,
                "carts": [],
                "totalCartsFound": 0,
                "candidateSellersScanned": 0,
                "watchedSealedCount": len(wl_sealed),
                "watchedSinglesCount": len(all_single_names),
                "message": "No candidate sellers found for your watched sealed or single items.",
                "durationSeconds": round(time.time() - start_ts, 2),
            }

        # Step 2: Concurrently evaluate sealed shelf + watchlist singles at each candidate seller
        def _eval_candidate(cand: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            s_key = cand["sellerKey"]

            # 2a. Seller's sealed products (no cache so tests and live runs stay deterministic & fresh)
            sealed_inv = self.fetch_seller_inventory(
                s_key,
                SEALED_PRODUCT_TYPES,
                min_market=0.0,
                max_products=100,
                time_budget_s=12.0,
                english_only=english_only,
                force_refresh=True,
                use_cache=False,
            )
            if not sealed_inv["rows"]:
                return None

            seller_meta = dict(cand)
            if sealed_inv.get("sellerMeta"):
                seller_meta.update({k: v for k, v in sealed_inv["sellerMeta"].items() if v is not None})

            qualifying_sealed: List[Dict[str, Any]] = []
            seen_sealed_pids: Set[int] = set()
            for row in sorted(sealed_inv["rows"], key=lambda r: (r["landedPrice"], r["price"])):
                pid = row["productId"]
                if pid in seen_sealed_pids:
                    continue
                seen_sealed_pids.add(pid)

                matched_w = None
                for w in wl_sealed:
                    w_pid = getattr(w, "tcgplayer_product_id", None)
                    if w_pid and int(w_pid) == pid:
                        matched_w = w
                        break
                    if match_sealed(row["productName"], getattr(w, "name", "") or "").matched:
                        matched_w = w
                        break

                mkt = row.get("marketPrice")
                w_target = getattr(matched_w, "target_price", None) if matched_w else None
                disc_pct = round(((mkt - row["price"]) / mkt) * 100.0, 1) if (mkt and mkt > 0) else None
                item_sav = round(mkt - row["price"], 2) if (mkt and mkt > 0) else None

                qualifies_sealed = False
                if matched_w is not None:
                    if (w_target and row["price"] <= w_target) or (mkt and row["price"] <= round(mkt * 1.05, 2)) or (mkt is None):
                        qualifies_sealed = True
                elif mkt and mkt > 0 and row["price"] < mkt and (disc_pct or 0.0) >= sealed_min_discount_pct:
                    qualifies_sealed = True

                if not qualifies_sealed:
                    continue

                cat, cat_label, subtype = classify_sealed_product(
                    row["productName"],
                    row.get("productType") or "",
                    row.get("description") or "",
                )
                if matched_w is None and not include_cases and cat == "case_other":
                    continue
                pack_count = extract_pack_count(
                    row["productName"],
                    row.get("description") or "",
                    category=cat,
                    booster_subtype=subtype,
                )
                qualifying_sealed.append({
                    "productId": pid,
                    "productName": row["productName"],
                    "setName": row["setName"],
                    "category": cat,
                    "categoryLabel": cat_label,
                    "boosterSubtype": subtype,
                    "packCount": pack_count,
                    "isSealed": True,
                    "isPresale": bool(row.get("isPresale")),
                    "releaseDate": row.get("releaseDate") or "",
                    "price": row["price"],
                    "shippingPrice": row["shippingPrice"],
                    "landedPrice": row["landedPrice"],
                    "marketPrice": mkt,
                    "targetPrice": w_target,
                    "discountPct": disc_pct,
                    "savingsVsMarket": item_sav,
                    "quantity": row["quantity"],
                    "imageUrl": row["imageUrl"],
                    "tcgplayerUrl": row["tcgplayerUrl"],
                    "isWatchedSealed": matched_w is not None,
                })

            if len(qualifying_sealed) < min_sealed:
                return None

            # 2b. Seller's watchlist singles
            single_products = self.lot_provider._query_seller_watchlist_inventory(
                s_key,
                all_single_names,
                english_only=english_only,
            )
            if not single_products:
                return None

            products_by_card: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for prod in single_products:
                pname = prod.get("productName") or ""
                norm = normalize_card_name(pname.split(" // ")[0].strip())
                if norm in single_by_norm:
                    products_by_card[norm].append(prod)
                else:
                    alias = UNIVERSES_WITHIN_ALIASES.get(norm)
                    if alias and alias in single_by_norm:
                        products_by_card[alias].append(prod)

            qualifying_singles: List[Dict[str, Any]] = []
            for norm, prod_list in products_by_card.items():
                w_item = single_by_norm[norm]
                t_price = getattr(w_item, "target_price", None)
                baseline_mkt = getattr(w_item, "lowest_in_stock_price", None) or getattr(w_item, "market_price", None)
                if not getattr(w_item, "is_any_version", True):
                    baseline_mkt = getattr(w_item, "market_price", None) or baseline_mkt

                best_for_card: Optional[Dict[str, Any]] = None
                for prod in prod_list:
                    pid = prod.get("productId")
                    pname = prod.get("productName") or ""
                    clean_name = strip_tcg_variant_suffixes(pname) or pname.split(" // ")[0].strip()
                    prod_mkt = _safe_float(prod.get("marketPrice"))
                    s_name = prod.get("setName") or ""

                    for l in prod.get("listings") or []:
                        lang = str(l.get("language") or "English").strip()
                        lang_abbr = str(l.get("languageAbbreviation") or "EN").strip().upper()
                        if english_only and (lang.lower() != "english" or lang_abbr != "EN"):
                            continue
                        cond = (l.get("condition") or "Near Mint").strip()
                        if not allow_mp and cond in ("Moderately Played", "Heavily Played", "Damaged"):
                            continue
                        price = _safe_float(l.get("price"))
                        if not price or price <= 0:
                            continue

                        qualifies, label, delta_pct = self.lot_provider.evaluate_listing_decent_price(
                            listing_price=price,
                            market_price=prod_mkt,
                            target_price=t_price,
                            tolerance_mode=tolerance_mode,
                            baseline_cheapest_market=baseline_mkt,
                        )
                        if not qualifies:
                            continue

                        ship = _safe_nonneg_float(
                            l.get("shippingPrice") if l.get("shippingPrice") is not None else l.get("sellerShippingPrice")
                        )
                        eff_mkt = baseline_mkt or prod_mkt
                        entry = {
                            "id": getattr(w_item, "id", None),
                            "name": getattr(w_item, "name", pname),
                            "productName": pname,
                            "cleanName": clean_name,
                            "setName": s_name,
                            "productId": pid,
                            "isSealed": False,
                            "price": price,
                            "shippingPrice": ship,
                            "marketPrice": eff_mkt,
                            "targetPrice": t_price,
                            "condition": cond,
                            "printing": l.get("printing") or "Normal",
                            "quantity": int(l.get("quantity") or 1),
                            "priceLabel": label,
                            "deltaPct": delta_pct,
                            "savingsVsMarket": round(eff_mkt - price, 2) if eff_mkt else None,
                            "imageUrl": getattr(w_item, "image_uri", None) or (
                                TCG_PRODUCT_IMAGE_URL.format(product_id=int(pid)) if pid else None
                            ),
                            "inBinder": bool(getattr(w_item, "in_binder", False)),
                            "binderCount": int(getattr(w_item, "binder_count", 0) or 0),
                            "tcgplayerUrl": f"https://www.tcgplayer.com/product/{pid}?seller={s_key}&Language=English&page=1",
                        }
                        if best_for_card is None or entry["price"] < best_for_card["price"]:
                            best_for_card = entry

                if best_for_card is not None:
                    qualifying_singles.append(best_for_card)

            if len(qualifying_singles) < min_singles:
                return None

            qualifying_sealed.sort(
                key=lambda x: (0 if x["isWatchedSealed"] else 1, 1 if x.get("isPresale") else 0, -(x.get("savingsVsMarket") or 0.0), x["price"])
            )
            qualifying_singles.sort(key=lambda x: x["price"])

            # Calculate consolidated cart economics using primary sealed item(s) + singles
            ship_math = self.estimate_combined_shipping(
                qualifying_sealed,
                qualifying_singles,
                direct_seller=bool(seller_meta.get("directSeller")),
            )
            sealed_sub = round(sum(x["price"] for x in qualifying_sealed), 2)
            singles_sub = round(sum(x["price"] for x in qualifying_singles), 2)
            total_mkt = round(
                sum(float(x.get("marketPrice") or x["price"]) for x in (qualifying_sealed + qualifying_singles)),
                2,
            )
            items_savings = round(total_mkt - ship_math["subtotal"], 2)
            net_savings = round(total_mkt - ship_math["landedTotal"], 2)
            watched_sealed_hits = sum(1 for x in qualifying_sealed if x["isWatchedSealed"])

            search_params = [("seller", s_key), ("view", "grid")]
            for c in qualifying_singles[:20]:
                cname = (c.get("cleanName") or c["name"]).split(" // ")[0].strip()
                search_params.append(("productName", cname))
            multi_url = "https://www.tcgplayer.com/search/magic/product?" + urllib.parse.urlencode(search_params)

            return {
                "sellerKey": s_key,
                "sellerName": seller_meta.get("sellerName") or f"Seller {s_key}",
                "sellerRating": seller_meta.get("sellerRating"),
                "sellerSales": str(seller_meta.get("sellerSales") or ""),
                "directSeller": bool(seller_meta.get("directSeller")),
                "goldSeller": bool(seller_meta.get("goldSeller")),
                "storeUrl": f"https://www.tcgplayer.com/search/magic/product?seller={s_key}&view=grid",
                "multiCardSearchUrl": multi_url,
                "sealedCount": len(qualifying_sealed),
                "watchedSealedCount": watched_sealed_hits,
                "singlesCount": len(qualifying_singles),
                "sealedSubtotal": sealed_sub,
                "singlesSubtotal": singles_sub,
                "itemsSubtotal": ship_math["subtotal"],
                "estShipping": ship_math["estShipping"],
                "separateShipping": ship_math["separateShipping"],
                "shippingSaved": ship_math["shippingSaved"],
                "landedTotal": ship_math["landedTotal"],
                "totalMarket": total_mkt,
                "itemsSavings": items_savings,
                "netSavings": net_savings,
                "totalBenefit": round(items_savings + ship_math["shippingSaved"], 2),
                "sealedItems": qualifying_sealed,
                "singleItems": qualifying_singles,
                "massEntryText": self.build_mass_entry_text(qualifying_singles),
            }

        carts: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_eval_candidate, c) for c in ranked_candidates]
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    if res:
                        carts.append(res)
                except Exception as err:
                    logger.debug("Combined cart candidate error: %s", err)

        carts.sort(
            key=lambda c: (
                1 if c["watchedSealedCount"] > 0 else 0,
                round(c["netSavings"] + c["shippingSaved"], 2),
                c["sealedCount"] + c["singlesCount"],
            ),
            reverse=True,
        )

        return {
            "success": True,
            "carts": carts,
            "totalCartsFound": len(carts),
            "candidateSellersScanned": len(ranked_candidates),
            "watchedSealedCount": len(wl_sealed),
            "watchedSinglesCount": len(all_single_names),
            "minSealed": min_sealed,
            "minSingles": min_singles,
            "toleranceMode": tolerance_mode,
            "durationSeconds": round(time.time() - start_ts, 2),
        }
