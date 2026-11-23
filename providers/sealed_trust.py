"""
Trust & scam-store filter for multi-merchant Shopify Global Catalog (Shop app) offers.

Classifies each offer as:
  - "trusted":    Shop GID, domain, or normalized store name is on the user's allowlist.
                  Eligible for active pricing and Discord deal alerts.
  - "unverified": Passed all scam heuristics, but not yet on the trusted allowlist.
                  Displayed in the UI as unverified (never triggers Discord alerts).
  - "suspicious": Failed a scam/clone heuristic (below market floor, clone farm,
                  spammy TLD + promo title, etc.). Dropped and logged only.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

TRUSTED_SHOPS_SETTING_KEY = "sealed_trusted_shops"
BLOCKED_SHOPS_SETTING_KEY = "sealed_blocked_shops"

# Sensible initial allowlist of reputable MTG Shopify storefronts.
# Users can add/remove entries via the Chimera UI or API (persisted in SystemSetting).
DEFAULT_TRUSTED_SHOPS: list[dict[str, str]] = [
    {"name": "Mighty Meeple", "domain": "mightymeeple.com", "shop_gid": ""},
    {"name": "Forge and Fire Gaming", "domain": "forgeandfiregaming.com", "shop_gid": ""},
    {"name": "Stomping Grounds", "domain": "stompinggroundstcg.com", "shop_gid": ""},
    {"name": "Double Infinity Gaming", "domain": "doubleinfinitygaming.com", "shop_gid": ""},
    {"name": "Flipside Gaming", "domain": "flipsidegaming.com", "shop_gid": ""},
    {"name": "Smoke and Mirrors Hobby", "domain": "smokeandmirrorshobby.com", "shop_gid": ""},
    {"name": "Zulu's Board Game Cafe", "domain": "zulusgames.com", "shop_gid": ""},
    {"name": "Face to Face Games", "domain": "facetofacegames.com", "shop_gid": ""},
    {"name": "Prodigy Games TCG", "domain": "prodigygamestcg.com", "shop_gid": ""},
    {"name": "Gamers Guild AZ", "domain": "gamersguildaz.com", "shop_gid": ""},
    {"name": "Safari Zone Collectibles", "domain": "safari-zone.com", "shop_gid": ""},
    {"name": "401 Games", "domain": "store.401games.ca", "shop_gid": ""},
]

_SPAM_TLDS = (".shop", ".store", ".top", ".xyz", ".vip", ".online", ".site", ".club", ".buzz")
_PROMO_TITLE_RE = re.compile(
    r"\b(?:hot\s+sale|flash\s+sale|mega\s+sale|clearance|cheap|discount\s+sale|limited\s+offer|best\s+price)\b",
    re.IGNORECASE,
)
# Matches trailing clone suffixes: " 2", " II", " III", " IV", " |||", " I"
_CLONE_SUFFIX_RE = re.compile(r"(?:\s+(?:[ivx]{1,4}|\|{1,4}|\d{1,2}))+$", re.IGNORECASE)


def _extract_host(url_or_domain: str) -> str:
    raw = (url_or_domain or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        host = urlparse(raw).hostname or ""
    else:
        host = raw.split("/")[0].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_title_key(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return re.sub(r"\s+", " ", s)


def _store_base_name(shop_name: str) -> str:
    s = re.sub(r"\s+", " ", (shop_name or "").strip().lower())
    s = _CLONE_SUFFIX_RE.sub("", s).strip()
    return s


def _has_clone_suffix(shop_name: str) -> bool:
    s = (shop_name or "").strip()
    if not s:
        return False
    return bool(_CLONE_SUFFIX_RE.search(s))


def get_trusted_shops() -> list[dict[str, str]]:
    """
    Loads the trusted Shopify store allowlist from SystemSetting.
    Falls back to DEFAULT_TRUSTED_SHOPS if not yet stored or outside app context.
    """
    try:
        from models import SystemSetting

        raw = SystemSetting.get_val(TRUSTED_SHOPS_SETTING_KEY)
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                cleaned: list[dict[str, str]] = []
                for entry in parsed:
                    if isinstance(entry, dict):
                        cleaned.append({
                            "name": str(entry.get("name") or "").strip(),
                            "domain": _extract_host(str(entry.get("domain") or "")),
                            "shop_gid": str(entry.get("shop_gid") or "").strip(),
                        })
                    elif isinstance(entry, str) and entry.strip():
                        val = entry.strip()
                        if val.startswith("gid://"):
                            cleaned.append({"name": "", "domain": "", "shop_gid": val})
                        elif "." in val:
                            cleaned.append({"name": "", "domain": _extract_host(val), "shop_gid": ""})
                        else:
                            cleaned.append({"name": val, "domain": "", "shop_gid": ""})
                return cleaned
    except Exception as exc:
        logger.debug(f"Using default trusted shops (SystemSetting unavailable): {exc}")
    return [dict(item) for item in DEFAULT_TRUSTED_SHOPS]


def save_trusted_shops(shops: list[dict[str, str]]) -> list[dict[str, str]]:
    """Persists the trusted Shopify store allowlist to SystemSetting."""
    from models import SystemSetting

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in shops:
        name = str(entry.get("name") or "").strip()
        domain = _extract_host(str(entry.get("domain") or ""))
        shop_gid = str(entry.get("shop_gid") or "").strip()
        if not (name or domain or shop_gid):
            continue
        key = (shop_gid.lower(), domain, name.lower())
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"name": name, "domain": domain, "shop_gid": shop_gid})

    SystemSetting.set_val(TRUSTED_SHOPS_SETTING_KEY, json.dumps(normalized))
    return normalized


def add_trusted_shop(name: str = "", domain: str = "", shop_gid: str = "") -> list[dict[str, str]]:
    """Adds a single store to the trusted allowlist, removing it from the blacklist if present."""
    remove_blocked_shop(name=name, domain=domain, shop_gid=shop_gid)
    current = get_trusted_shops()
    current.append({
        "name": (name or "").strip(),
        "domain": _extract_host(domain or ""),
        "shop_gid": (shop_gid or "").strip(),
    })
    return save_trusted_shops(current)


def remove_trusted_shop(name: str = "", domain: str = "", shop_gid: str = "") -> list[dict[str, str]]:
    """Removes matching store(s) from the trusted allowlist and persists it."""
    target_name = (name or "").strip().lower()
    target_domain = _extract_host(domain or "")
    target_gid = (shop_gid or "").strip().lower()
    current = get_trusted_shops()
    kept = []
    for entry in current:
        if target_gid and entry.get("shop_gid", "").lower() == target_gid:
            continue
        if target_domain and _extract_host(entry.get("domain", "")) == target_domain:
            continue
        if target_name and entry.get("name", "").strip().lower() == target_name:
            continue
        kept.append(entry)
    return save_trusted_shops(kept)


def get_blocked_shops() -> list[dict[str, str]]:
    """
    Loads the blacklisted Shopify store list from SystemSetting.
    Returns an empty list if not yet configured or outside app context.
    """
    try:
        from models import SystemSetting

        raw = SystemSetting.get_val(BLOCKED_SHOPS_SETTING_KEY)
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                cleaned: list[dict[str, str]] = []
                for entry in parsed:
                    if isinstance(entry, dict):
                        cleaned.append({
                            "name": str(entry.get("name") or "").strip(),
                            "domain": _extract_host(str(entry.get("domain") or "")),
                            "shop_gid": str(entry.get("shop_gid") or "").strip(),
                        })
                    elif isinstance(entry, str) and entry.strip():
                        val = entry.strip()
                        if val.startswith("gid://"):
                            cleaned.append({"name": "", "domain": "", "shop_gid": val})
                        elif "." in val:
                            cleaned.append({"name": "", "domain": _extract_host(val), "shop_gid": ""})
                        else:
                            cleaned.append({"name": val, "domain": "", "shop_gid": ""})
                return cleaned
    except Exception as exc:
        logger.debug(f"Using empty blocked shops (SystemSetting unavailable): {exc}")
    return []


def save_blocked_shops(shops: list[dict[str, str]]) -> list[dict[str, str]]:
    """Persists the blacklisted Shopify store list to SystemSetting."""
    from models import SystemSetting

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in shops:
        name = str(entry.get("name") or "").strip()
        domain = _extract_host(str(entry.get("domain") or ""))
        shop_gid = str(entry.get("shop_gid") or "").strip()
        if not (name or domain or shop_gid):
            continue
        key = (shop_gid.lower(), domain, name.lower())
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"name": name, "domain": domain, "shop_gid": shop_gid})

    SystemSetting.set_val(BLOCKED_SHOPS_SETTING_KEY, json.dumps(normalized))
    return normalized


def add_blocked_shop(name: str = "", domain: str = "", shop_gid: str = "") -> list[dict[str, str]]:
    """Adds a store to the blacklist and removes it from the trusted allowlist if present."""
    remove_trusted_shop(name=name, domain=domain, shop_gid=shop_gid)
    current = get_blocked_shops()
    current.append({
        "name": (name or "").strip(),
        "domain": _extract_host(domain or ""),
        "shop_gid": (shop_gid or "").strip(),
    })
    return save_blocked_shops(current)


def remove_blocked_shop(name: str = "", domain: str = "", shop_gid: str = "") -> list[dict[str, str]]:
    """Removes matching store(s) from the blacklist and persists it."""
    target_name = (name or "").strip().lower()
    target_domain = _extract_host(domain or "")
    target_gid = (shop_gid or "").strip().lower()
    current = get_blocked_shops()
    kept = []
    for entry in current:
        if target_gid and entry.get("shop_gid", "").lower() == target_gid:
            continue
        if target_domain and _extract_host(entry.get("domain", "")) == target_domain:
            continue
        if target_name and entry.get("name", "").strip().lower() == target_name:
            continue
        kept.append(entry)
    return save_blocked_shops(kept)


def is_shop_blocked(
    *,
    shop_gid: str = "",
    shop_domain: str = "",
    shop_url: str = "",
    shop_name: str = "",
    blocked_shops: Optional[Iterable[dict[str, str]]] = None,
) -> bool:
    """Checks whether a Shopify merchant matches any entry in the blacklist."""
    entries = list(blocked_shops) if blocked_shops is not None else get_blocked_shops()
    gid_l = (shop_gid or "").strip().lower()
    dom_l = _extract_host(shop_domain)
    url_host = _extract_host(shop_url)
    name_l = (shop_name or "").strip().lower()

    for entry in entries:
        e_gid = (entry.get("shop_gid") or "").strip().lower()
        e_dom = _extract_host(entry.get("domain") or "")
        e_name = (entry.get("name") or "").strip().lower()

        if e_gid and gid_l and e_gid == gid_l:
            return True
        if e_dom and (dom_l == e_dom or url_host == e_dom):
            return True
        if e_name and name_l and e_name == name_l:
            return True
    return False


def is_shop_trusted(
    *,
    shop_gid: str = "",
    shop_domain: str = "",
    shop_url: str = "",
    shop_name: str = "",
    trusted_shops: Optional[Iterable[dict[str, str]]] = None,
) -> bool:
    """Checks whether a Shopify merchant matches any entry in the trusted allowlist."""
    entries = list(trusted_shops) if trusted_shops is not None else get_trusted_shops()
    gid_l = (shop_gid or "").strip().lower()
    dom_l = _extract_host(shop_domain)
    url_host = _extract_host(shop_url)
    name_l = (shop_name or "").strip().lower()

    for entry in entries:
        e_gid = (entry.get("shop_gid") or "").strip().lower()
        e_dom = _extract_host(entry.get("domain") or "")
        e_name = (entry.get("name") or "").strip().lower()

        if e_gid and gid_l and e_gid == gid_l:
            return True
        if e_dom and (dom_l == e_dom or url_host == e_dom):
            return True
        if e_name and name_l and e_name == name_l:
            return True
    return False


@dataclass(frozen=True)
class TrustDecision:
    status: str  # "trusted" | "unverified" | "suspicious"
    reason: str


def evaluate_offers_trust(
    offers: list[dict[str, Any]],
    *,
    market_price: Optional[float] = None,
    min_market_ratio: float = 0.55,
    trusted_shops: Optional[Iterable[dict[str, str]]] = None,
    blocked_shops: Optional[Iterable[dict[str, str]]] = None,
) -> list[tuple[dict[str, Any], TrustDecision]]:
    """
    Evaluates a batch of offers from a single catalog search and returns
    (offer, TrustDecision) pairs.

    Batch context is required to detect clone-farm storefronts (identical
    normalized title + identical price across >= 3 stores, or numbered/Roman
    numeral store-name clones like "PULL STORE I / II / III").
    """
    allowlist = list(trusted_shops) if trusted_shops is not None else get_trusted_shops()
    blocklist = list(blocked_shops) if blocked_shops is not None else get_blocked_shops()

    # 1. Pre-compute batch clone-farm signals
    title_price_shops: dict[tuple[str, int], set[str]] = defaultdict(set)
    base_name_variants: dict[str, set[str]] = defaultdict(set)

    for offer in offers:
        title_key = _normalize_title_key(str(offer.get("title") or ""))
        price_cents = int(round(float(offer.get("price") or 0.0) * 100))
        shop_key = (
            str(offer.get("shop_gid") or "").strip().lower()
            or _extract_host(str(offer.get("shop_domain") or offer.get("shop_url") or offer.get("url") or ""))
            or str(offer.get("shop_name") or "").strip().lower()
        )
        if title_key and price_cents > 0 and shop_key:
            title_price_shops[(title_key, price_cents)].add(shop_key)

        sname = str(offer.get("shop_name") or "").strip()
        if sname:
            base = _store_base_name(sname)
            if base:
                base_name_variants[base].add(sname.lower())

    clone_title_price_keys = {k for k, shops in title_price_shops.items() if len(shops) >= 3}
    clone_base_names = {base for base, names in base_name_variants.items() if len(names) >= 2}

    results: list[tuple[dict[str, Any], TrustDecision]] = []
    for offer in offers:
        shop_gid = str(offer.get("shop_gid") or "")
        shop_domain = str(offer.get("shop_domain") or "")
        shop_url = str(offer.get("shop_url") or offer.get("url") or "")
        shop_name = str(offer.get("shop_name") or "")
        title = str(offer.get("title") or "")
        price = float(offer.get("price") or 0.0)

        # Rule 0: Explicit user blacklist always wins
        if is_shop_blocked(
            shop_gid=shop_gid,
            shop_domain=shop_domain,
            shop_url=shop_url,
            shop_name=shop_name,
            blocked_shops=blocklist,
        ):
            results.append((offer, TrustDecision("suspicious", "blacklisted")))
            continue

        # Rule 1: Explicit trusted allowlist
        if is_shop_trusted(
            shop_gid=shop_gid,
            shop_domain=shop_domain,
            shop_url=shop_url,
            shop_name=shop_name,
            trusted_shops=allowlist,
        ):
            results.append((offer, TrustDecision("trusted", "allowlisted")))
            continue

        # Rule 2: Price below market floor (e.g. < 55% of TCGplayer market price)
        if market_price is not None and market_price > 0 and price > 0:
            ratio = price / float(market_price)
            if ratio < min_market_ratio:
                results.append((
                    offer,
                    TrustDecision(
                        "suspicious",
                        f"below_market_floor({ratio:.0%}<{min_market_ratio:.0%})",
                    ),
                ))
                continue

        # Rule 3: Clone farm (identical title & price across >= 3 distinct stores)
        title_key = _normalize_title_key(title)
        price_cents = int(round(price * 100))
        if (title_key, price_cents) in clone_title_price_keys:
            results.append((offer, TrustDecision("suspicious", "clone_farm_title_price")))
            continue

        # Rule 4: Cloned store name with numeric/Roman numeral suffix (e.g. PULL STORE II / III / 2)
        # or literal pipe-bar clone markers (e.g. "PULL STORE |||") even when appearing solo
        base_name = _store_base_name(shop_name)
        if (base_name in clone_base_names and _has_clone_suffix(shop_name)) or re.search(r"\|\|", shop_name):
            results.append((offer, TrustDecision("suspicious", "clone_store_name")))
            continue

        # Rule 5: Spammy TLD (.shop/.store/.top) combined with promo-spam title ("Hot Sale", "Clearance")
        host = _extract_host(shop_url) or _extract_host(shop_domain)
        if host and any(host.endswith(tld) for tld in _SPAM_TLDS) and _PROMO_TITLE_RE.search(title):
            results.append((offer, TrustDecision("suspicious", "spam_tld_promo_title")))
            continue

        # Passed all scam heuristics, not yet allowlisted
        results.append((offer, TrustDecision("unverified", "passed_heuristics")))

    return results
