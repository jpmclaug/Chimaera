"""
Card Kingdom Sealed MTG Product Provider.

Uses Card Kingdom's official public JSON pricelist:
    GET https://api.cardkingdom.com/api/sealed_pricelist

A single ~580 KB JSON download covers all sealed products with live retail
price and quantity, cached in memory (30 min TTL + ETag/Last-Modified) so a
full 30-item watchlist sweep makes at most one HTTP request.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from typing import Any, Optional

import requests

from .sealed_matcher import match_sealed

logger = logging.getLogger(__name__)

CK_SEALED_PRICELIST_URL = "https://api.cardkingdom.com/api/sealed_pricelist"
CK_BASE_URL = "https://www.cardkingdom.com/"
DEFAULT_CACHE_TTL_SECONDS = 1800  # 30 minutes


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


class CardKingdomSealedProvider:
    """Fetches and queries Card Kingdom's public sealed product pricelist."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ):
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent",
            "Chimera-MTG-Tracker/1.0 (+https://github.com/chimera-mtg)",
        )
        self.session.headers.setdefault("Accept", "application/json")
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))

        self._lock = threading.Lock()
        self._cache_items: list[dict[str, Any]] = []
        self._cache_base_url: str = CK_BASE_URL
        self._cache_fetched_at: float = 0.0
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None

    def get_pricelist(self, force_refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
        """
        Returns (items, base_url) from Card Kingdom's sealed pricelist, using
        in-memory TTL caching and conditional GET headers.
        """
        now = time.time()
        with self._lock:
            if (
                not force_refresh
                and self._cache_items
                and (now - self._cache_fetched_at) < self.cache_ttl_seconds
            ):
                return self._cache_items, self._cache_base_url

            headers: dict[str, str] = {}
            if not force_refresh and self._cache_items:
                if self._etag:
                    headers["If-None-Match"] = self._etag
                if self._last_modified:
                    headers["If-Modified-Since"] = self._last_modified

            try:
                resp = self.session.get(
                    CK_SEALED_PRICELIST_URL,
                    headers=headers,
                    timeout=20.0,
                )
                if resp.status_code == 304 and self._cache_items:
                    self._cache_fetched_at = time.time()
                    return self._cache_items, self._cache_base_url

                resp.raise_for_status()
                payload = resp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                meta = payload.get("meta") if isinstance(payload, dict) else {}
                if isinstance(data, list):
                    self._cache_items = data
                    base_url = str((meta or {}).get("base_url") or CK_BASE_URL)
                    if not base_url.endswith("/"):
                        base_url += "/"
                    self._cache_base_url = base_url
                    self._cache_fetched_at = time.time()
                    self._etag = resp.headers.get("ETag") or self._etag
                    self._last_modified = resp.headers.get("Last-Modified") or self._last_modified
                    logger.info(
                        f"Card Kingdom sealed pricelist refreshed: {len(self._cache_items)} products."
                    )
            except Exception as exc:
                logger.warning(f"Failed to fetch Card Kingdom sealed pricelist: {exc}")

            return self._cache_items, self._cache_base_url

    def find_offer(
        self,
        item_name: str,
        *,
        force_refresh: bool = False,
    ) -> Optional[dict[str, Any]]:
        """
        Finds the best matching sealed product on Card Kingdom for `item_name`.
        Returns a VendorPrice-compatible dict, or None if no product matches.
        """
        if not item_name or not item_name.strip():
            return None

        items, base_url = self.get_pricelist(force_refresh=force_refresh)
        if not items:
            return None

        candidates: list[tuple[float, int, float, dict[str, Any]]] = []
        for entry in items:
            title = str(entry.get("name") or "")
            edition = str(entry.get("edition") or "")
            if not title:
                continue
            res = match_sealed(title, item_name, listing_edition=edition)
            if not res.matched:
                continue

            price = _safe_float(entry.get("price_retail"))
            qty = _safe_int(entry.get("qty_retail"))
            in_stock_rank = 1 if (qty > 0 and price > 0) else 0
            # Sort key: highest match score, then in-stock, then lowest price
            candidates.append((res.score, in_stock_rank, -price if price > 0 else -999999.0, entry))

        if not candidates:
            return None

        candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
        best = candidates[0][3]

        price = _safe_float(best.get("price_retail"))
        qty = _safe_int(best.get("qty_retail"))
        in_stock = qty > 0 and price > 0
        rel_url = str(best.get("url") or "").lstrip("/")
        product_url = f"{base_url}{rel_url}" if rel_url else base_url
        search_url = (
            "https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D="
            + urllib.parse.quote_plus(item_name)
        )

        return {
            "vendor_name": "Card Kingdom",
            "price": price,
            "condition": "Unopened",
            "in_stock": in_stock,
            "qty_retail": qty,
            "product_url": product_url,
            "search_url": search_url,
            "matched_title": str(best.get("name") or ""),
            "matched_edition": str(best.get("edition") or ""),
        }
