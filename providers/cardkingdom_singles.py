"""
Card Kingdom Singles MTG Provider.

Uses Card Kingdom's official public JSON pricelist:
    GET https://api.cardkingdom.com/api/v2/pricelist

Downloads ~155k singles once per hour (gzip-compressed, ~3s) and indexes them
in memory by both `scryfall_id` and normalized card name for sub-millisecond
lookups during watchlist polling.
"""

from __future__ import annotations

import gc
import logging
import re
import threading
import time
import unicodedata
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

CK_SINGLES_PRICELIST_URL = "https://api.cardkingdom.com/api/v2/pricelist"
CK_BASE_URL = "https://www.cardkingdom.com/"
DEFAULT_CACHE_TTL_SECONDS = 3600  # 60 minutes


def _safe_float(val: Any) -> float:
    try:
        return round(max(0.0, float(val)), 2)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val: Any) -> int:
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return 0


def _normalize_card_name(name: str) -> str:
    """Normalizes card names (strip accents, lowercase, collapse whitespace)."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    s = ascii_only.lower().replace("’", "'").replace("`", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass(frozen=True)
class _CKEntry:
    name: str
    edition: str
    scryfall_id: str
    is_foil: bool
    price: float
    condition: str
    in_stock: bool
    qty_retail: int
    rel_url: str


def _parse_ck_entry(raw: dict[str, Any]) -> Optional[_CKEntry]:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    is_foil = str(raw.get("is_foil") or "").strip().lower() == "true"
    qty_retail = _safe_int(raw.get("qty_retail"))
    price_retail = _safe_float(raw.get("price_retail"))
    cond_vals = raw.get("condition_values") if isinstance(raw.get("condition_values"), dict) else {}

    nm_qty = _safe_int(cond_vals.get("nm_qty"))
    nm_price = _safe_float(cond_vals.get("nm_price"))
    ex_qty = _safe_int(cond_vals.get("ex_qty"))
    ex_price = _safe_float(cond_vals.get("ex_price"))
    vg_qty = _safe_int(cond_vals.get("vg_qty"))
    vg_price = _safe_float(cond_vals.get("vg_price"))
    g_qty = _safe_int(cond_vals.get("g_qty"))
    g_price = _safe_float(cond_vals.get("g_price"))

    if nm_qty > 0 and nm_price > 0:
        best_price, best_cond, in_stock = nm_price, "NM", True
    elif ex_qty > 0 and ex_price > 0:
        best_price, best_cond, in_stock = ex_price, "LP", True
    elif vg_qty > 0 and vg_price > 0:
        best_price, best_cond, in_stock = vg_price, "MP", True
    elif g_qty > 0 and g_price > 0:
        best_price, best_cond, in_stock = g_price, "HP", True
    elif qty_retail > 0 and price_retail > 0:
        best_price, best_cond, in_stock = price_retail, "NM", True
    else:
        best_price = nm_price or price_retail
        best_cond = "NM"
        in_stock = False

    return _CKEntry(
        name=name,
        edition=str(raw.get("edition") or "").strip(),
        scryfall_id=str(raw.get("scryfall_id") or "").strip().lower(),
        is_foil=is_foil,
        price=best_price,
        condition=best_cond,
        in_stock=in_stock,
        qty_retail=qty_retail,
        rel_url=str(raw.get("url") or "").lstrip("/"),
    )


class CardKingdomSinglesProvider:
    """Fetches, caches, and queries Card Kingdom's public singles pricelist."""

    # Class-level shared cache so multiple DealEngine instances across web requests
    # reuse the same warm index without re-downloading ~155k cards.
    _shared_lock = threading.Lock()
    _shared_by_scryfall: dict[tuple[str, bool], list[_CKEntry]] = {}
    _shared_by_name: dict[str, list[_CKEntry]] = {}
    _shared_base_url: str = CK_BASE_URL
    _shared_fetched_at: float = 0.0
    _shared_etag: Optional[str] = None
    _shared_last_modified: Optional[str] = None

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        use_shared_cache: bool = True,
    ):
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent",
            "Chimera-MTG-Tracker/1.0 (+https://github.com/chimera-mtg)",
        )
        self.session.headers.setdefault("Accept", "application/json")
        self.session.headers.setdefault("Accept-Encoding", "gzip, deflate")
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.use_shared_cache = bool(use_shared_cache and session is None)

        self._inst_lock = threading.Lock()
        self._inst_by_scryfall: dict[tuple[str, bool], list[_CKEntry]] = {}
        self._inst_by_name: dict[str, list[_CKEntry]] = {}
        self._inst_base_url: str = CK_BASE_URL
        self._inst_fetched_at: float = 0.0
        self._inst_etag: Optional[str] = None
        self._inst_last_modified: Optional[str] = None

    @classmethod
    def clear_shared_cache(cls) -> None:
        with cls._shared_lock:
            cls._shared_by_scryfall = {}
            cls._shared_by_name = {}
            cls._shared_base_url = CK_BASE_URL
            cls._shared_fetched_at = 0.0
            cls._shared_etag = None
            cls._shared_last_modified = None

    def _get_indexes(
        self,
        force_refresh: bool = False,
        timeout: float = 25.0,
    ) -> tuple[dict[tuple[str, bool], list[_CKEntry]], dict[str, list[_CKEntry]], str]:
        lock = self._shared_lock if self.use_shared_cache else self._inst_lock
        now = time.time()

        with lock:
            if self.use_shared_cache:
                by_sf = self._shared_by_scryfall
                by_name = self._shared_by_name
                base_url = self._shared_base_url
                fetched_at = self._shared_fetched_at
                etag = self._shared_etag
                last_mod = self._shared_last_modified
            else:
                by_sf = self._inst_by_scryfall
                by_name = self._inst_by_name
                base_url = self._inst_base_url
                fetched_at = self._inst_fetched_at
                etag = self._inst_etag
                last_mod = self._inst_last_modified

            if not force_refresh and by_name and (now - fetched_at) < self.cache_ttl_seconds:
                return by_sf, by_name, base_url

            headers: dict[str, str] = {}
            if not force_refresh and by_name:
                if etag:
                    headers["If-None-Match"] = etag
                if last_mod:
                    headers["If-Modified-Since"] = last_mod

            try:
                resp = self.session.get(
                    CK_SINGLES_PRICELIST_URL,
                    headers=headers,
                    timeout=timeout,
                )
                if resp.status_code == 304 and by_name:
                    if self.use_shared_cache:
                        type(self)._shared_fetched_at = time.time()
                    else:
                        self._inst_fetched_at = time.time()
                    return by_sf, by_name, base_url

                resp.raise_for_status()
                payload = resp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                meta = payload.get("meta") if isinstance(payload, dict) else {}

                if isinstance(data, list):
                    new_by_sf: dict[tuple[str, bool], list[_CKEntry]] = defaultdict(list)
                    new_by_name: dict[str, list[_CKEntry]] = defaultdict(list)

                    for raw in data:
                        if not isinstance(raw, dict):
                            continue
                        entry = _parse_ck_entry(raw)
                        if not entry:
                            continue
                        if entry.scryfall_id:
                            new_by_sf[(entry.scryfall_id, entry.is_foil)].append(entry)
                        norm_name = _normalize_card_name(entry.name)
                        if norm_name:
                            new_by_name[norm_name].append(entry)
                            if " // " in norm_name:
                                front = norm_name.split(" // ", 1)[0].strip()
                                if front:
                                    new_by_name[front].append(entry)

                    new_base = str((meta or {}).get("base_url") or CK_BASE_URL)
                    if not new_base.endswith("/"):
                        new_base += "/"

                    new_etag = resp.headers.get("ETag") or etag
                    new_last_mod = resp.headers.get("Last-Modified") or last_mod
                    now_ts = time.time()

                    by_sf_out = dict(new_by_sf)
                    by_name_out = dict(new_by_name)
                    if self.use_shared_cache:
                        cls = type(self)
                        cls._shared_by_scryfall = by_sf_out
                        cls._shared_by_name = by_name_out
                        cls._shared_base_url = new_base
                        cls._shared_fetched_at = now_ts
                        cls._shared_etag = new_etag
                        cls._shared_last_modified = new_last_mod
                    else:
                        self._inst_by_scryfall = by_sf_out
                        self._inst_by_name = by_name_out
                        self._inst_base_url = new_base
                        self._inst_fetched_at = now_ts
                        self._inst_etag = new_etag
                        self._inst_last_modified = new_last_mod

                    entry_count = len(data)
                    del payload, data, new_by_sf, new_by_name
                    gc.collect()
                    logger.info(f"Card Kingdom singles pricelist indexed: {entry_count} entries.")
                    return by_sf_out, by_name_out, new_base
            except Exception as exc:
                logger.warning(f"Failed to fetch Card Kingdom singles pricelist: {exc}")

            return by_sf, by_name, base_url

    @staticmethod
    def _matches_finish(entry: _CKEntry, finish: str) -> bool:
        f = (finish or "nonfoil").strip().lower()
        if f in ("any", ""):
            return True
        if f in ("foil", "etched"):
            return entry.is_foil
        return not entry.is_foil

    @staticmethod
    def _pick_best(candidates: list[_CKEntry]) -> Optional[_CKEntry]:
        if not candidates:
            return None
        in_stock = [c for c in candidates if c.in_stock and c.price > 0]
        if in_stock:
            # Prefer cheapest in-stock, breaking ties toward NM condition
            cond_rank = {"NM": 0, "LP": 1, "MP": 2, "HP": 3}
            in_stock.sort(key=lambda c: (c.price, cond_rank.get(c.condition, 9)))
            return in_stock[0]
        # Otherwise return cheapest out-of-stock reference
        priced = [c for c in candidates if c.price > 0]
        if priced:
            priced.sort(key=lambda c: c.price)
            return priced[0]
        return candidates[0]

    def search_card(
        self,
        card_name: str,
        *,
        scryfall_id: Optional[str] = None,
        finish: str = "nonfoil",
        is_any_version: bool = False,
        timeout: float = 25.0,
    ) -> Optional[dict[str, Any]]:
        """
        Looks up a single MTG card in Card Kingdom's cached pricelist.
        Returns a VendorPrice-compatible dict, or None if not found.
        """
        if not card_name or not card_name.strip():
            return None

        by_sf, by_name, base_url = self._get_indexes(timeout=timeout)
        if not by_name:
            return None

        f = (finish or "nonfoil").strip().lower()
        candidates: list[_CKEntry] = []

        # 1. Exact printing lookup by Scryfall ID when monitoring a specific printing
        if not is_any_version and scryfall_id:
            sf_key = scryfall_id.strip().lower()
            if f in ("any", ""):
                candidates.extend(by_sf.get((sf_key, False), []))
                candidates.extend(by_sf.get((sf_key, True), []))
            elif f in ("foil", "etched"):
                candidates.extend(by_sf.get((sf_key, True), []))
            else:
                candidates.extend(by_sf.get((sf_key, False), []))

        # 2. Name-based lookup (primary for Any Version, fallback if exact printing missing)
        if not candidates:
            norm = _normalize_card_name(card_name)
            name_hits = list(by_name.get(norm, []))
            if not name_hits and " // " in norm:
                front = norm.split(" // ", 1)[0].strip()
                name_hits = list(by_name.get(front, []))
            candidates = [e for e in name_hits if self._matches_finish(e, f)]

        best = self._pick_best(candidates)
        search_url = (
            "https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D="
            + urllib.parse.quote_plus(card_name)
        )

        if not best:
            return None

        product_url = f"{base_url}{best.rel_url}" if best.rel_url else search_url
        return {
            "vendor_name": "Card Kingdom",
            "price": float(best.price),
            "condition": best.condition,
            "in_stock": bool(best.in_stock),
            "qty_retail": best.qty_retail,
            "product_url": product_url,
            "search_url": search_url,
            "edition": best.edition,
        }
