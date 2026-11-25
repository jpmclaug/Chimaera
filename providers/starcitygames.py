"""
Star City Games MTG Provider (Singles & Sealed).

Queries Star City Games' public HawkSearch JSON API:
    POST https://ajax.starcitygames.com/hawksearch/searchapi/api/v2/search

Returns structured JSON with card printings, SKU, TCGplayer ID, finish,
per-condition stock quantities, and live retail pricing.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
import urllib.parse
from typing import Any, Optional

import requests

from .sealed_matcher import match_sealed

logger = logging.getLogger(__name__)

SCG_SEARCH_API_URL = "https://ajax.starcitygames.com/hawksearch/searchapi/api/v2/search"
SCG_BASE_URL = "https://starcitygames.com"
DEFAULT_CACHE_TTL_SECONDS = 600  # 10 minutes

_JUNK_TERMS = (
    "art card",
    "art series",
    "artist card",
    "token",
    "playmat",
    "sleeve",
    "sleeves",
    "deck box",
    "binder",
)

_COND_MAP = {
    "near mint": ("NM", 0),
    "nm": ("NM", 0),
    "lightly played": ("LP", 1),
    "lp": ("LP", 1),
    "played": ("LP", 1),
    "pl": ("LP", 1),
    "moderately played": ("MP", 2),
    "mp": ("MP", 2),
    "heavily played": ("HP", 3),
    "hp": ("HP", 3),
    "damaged": ("Damaged", 4),
    "dmg": ("Damaged", 4),
}


def _safe_float(val: Any) -> float:
    if isinstance(val, list):
        val = val[0] if val else 0.0
    try:
        return round(max(0.0, float(val)), 2)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val: Any) -> int:
    if isinstance(val, list):
        val = val[0] if val else 0
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return 0


def _first_str(val: Any) -> str:
    if isinstance(val, list):
        return str(val[0]).strip() if val else ""
    return str(val or "").strip()


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    cleaned = re.sub(r"[^\w\s]", " ", norm.lower())
    return " ".join(cleaned.split())


def _extract_base_name(title: str) -> str:
    t = re.sub(r"\[.*?\]", "", title or "")
    t = re.sub(r"\(.*?\)", "", t)
    return t.strip()


def _normalize_condition(label: str) -> tuple[str, int]:
    low = (label or "").strip().lower()
    if low in _COND_MAP:
        return _COND_MAP[low]
    for key, mapped in _COND_MAP.items():
        if key in low:
            return mapped
    return ("NM", 0)


class StarCityGamesProvider:
    """Fetches and queries Star City Games' HawkSearch catalog for MTG singles and sealed."""

    _shared_lock = threading.Lock()
    _shared_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        use_shared_cache: bool = True,
    ):
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        self.session.headers.setdefault("Accept", "application/json")
        self.session.headers.setdefault("Content-Type", "application/json")
        self.session.headers.setdefault("Origin", SCG_BASE_URL)
        self.session.headers.setdefault("Referer", f"{SCG_BASE_URL}/")
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.use_shared_cache = bool(use_shared_cache and session is None)

        self._inst_lock = threading.Lock()
        self._inst_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

    @classmethod
    def clear_shared_cache(cls) -> None:
        with cls._shared_lock:
            cls._shared_cache.clear()

    def _query_hawksearch(
        self,
        keyword: str,
        product_type: str,
        *,
        max_per_page: int = 32,
        timeout: float = 8.0,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        clean_kw = (keyword or "").strip()
        if not clean_kw:
            return []

        cache_key = (product_type, clean_kw.lower())
        lock = self._shared_lock if self.use_shared_cache else self._inst_lock
        cache = self._shared_cache if self.use_shared_cache else self._inst_cache
        now = time.time()

        if not force_refresh and self.cache_ttl_seconds > 0:
            with lock:
                hit = cache.get(cache_key)
                if hit and (now - hit[0]) < self.cache_ttl_seconds:
                    return hit[1]

        payload = {
            "Keyword": clean_kw,
            "MaxPerPage": max_per_page,
            "FacetSelections": {
                "game": ["Magic: The Gathering"],
                "product_type": [product_type],
            },
        }
        try:
            resp = self.session.post(
                SCG_SEARCH_API_URL,
                json=payload,
                timeout=max(1.0, float(timeout)),
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("Results") if isinstance(data, dict) else []
            if not isinstance(results, list):
                results = []

            if self.cache_ttl_seconds > 0 and results:
                with lock:
                    if len(cache) >= 1000:
                        for old_k in list(cache.keys())[:250]:
                            cache.pop(old_k, None)
                    cache[cache_key] = (time.time(), results)
            return results
        except Exception as exc:
            logger.warning(f"Star City Games HawkSearch error for '{clean_kw}' ({product_type}): {exc}")
            return []

    @staticmethod
    def _is_card_name_match(doc: dict[str, Any], card_name: str) -> bool:
        norm_target = _normalize_text(card_name)
        if not norm_target:
            return False

        raw_name = _first_str(doc.get("card_name")) or _first_str(doc.get("item_display_name"))
        if not raw_name:
            return False

        norm_raw = _normalize_text(raw_name)
        for jt in _JUNK_TERMS:
            jt_norm = _normalize_text(jt)
            if re.search(r"\b" + re.escape(jt_norm) + r"\b", norm_raw):
                if not re.search(r"\b" + re.escape(jt_norm) + r"\b", norm_target):
                    return False

        base = _extract_base_name(raw_name)
        norm_base = _normalize_text(base)
        if norm_base == norm_target:
            return True

        # Double-faced / split card matching
        doc_faces = [_normalize_text(f) for f in re.split(r"/+", base) if f.strip()]
        if norm_target in doc_faces:
            return True

        if "//" in card_name or "/" in card_name:
            target_faces = [_normalize_text(f) for f in re.split(r"/+", card_name) if f.strip()]
            if target_faces == doc_faces:
                return True
            if target_faces and doc_faces and doc_faces[0] == target_faces[0]:
                return True

        return False

    @staticmethod
    def _matches_finish(doc: dict[str, Any], finish: str) -> bool:
        f = (finish or "nonfoil").strip().lower()
        if f in ("any", ""):
            return True
        doc_finish = (_first_str(doc.get("finish")) or _first_str(doc.get("mtg_finish"))).lower()
        is_foil = "foil" in doc_finish and "non-foil" not in doc_finish and "nonfoil" not in doc_finish
        if f in ("foil", "etched"):
            return is_foil
        return not is_foil

    @staticmethod
    def _extract_best_variant(doc: dict[str, Any]) -> Optional[dict[str, Any]]:
        attrs = doc.get("hawk_child_attributes") or []
        in_stock_opts: list[tuple[float, int, str, int]] = []
        oos_opts: list[tuple[float, int, str, int]] = []

        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            qty = _safe_int(attr.get("qty"))
            sale_p = _safe_float(attr.get("price_sale"))
            calc_p = _safe_float(attr.get("calculated_price"))
            base_p = _safe_float(attr.get("price"))
            price = sale_p if sale_p > 0 else (calc_p if calc_p > 0 else base_p)

            cond_label = "NM"
            cond_rank = 0
            raw_opts = attr.get("option_values") or []
            opt_json_str = _first_str(raw_opts)
            if opt_json_str:
                try:
                    opt_obj = json.loads(opt_json_str)
                    if isinstance(opt_obj, dict):
                        cond_label, cond_rank = _normalize_condition(str(opt_obj.get("label") or ""))
                except Exception:
                    cond_label, cond_rank = _normalize_condition(opt_json_str)

            row = (price, cond_rank, cond_label, qty)
            if qty > 0 and price > 0:
                in_stock_opts.append(row)
            elif price > 0:
                oos_opts.append(row)

        if in_stock_opts:
            in_stock_opts.sort(key=lambda r: (r[0], r[1]))
            best = in_stock_opts[0]
            return {
                "price": best[0],
                "condition": best[2],
                "in_stock": True,
                "qty_retail": best[3],
            }

        if oos_opts:
            oos_opts.sort(key=lambda r: (r[1], r[0]))
            best = oos_opts[0]
            return {
                "price": best[0],
                "condition": best[2],
                "in_stock": False,
                "qty_retail": 0,
            }

        fallback_price = _safe_float(doc.get("price_retail"))
        fallback_qty = _safe_int(doc.get("metric_inventory"))
        if fallback_price > 0:
            return {
                "price": fallback_price,
                "condition": "NM",
                "in_stock": fallback_qty > 0,
                "qty_retail": fallback_qty,
            }
        return None

    def search_card(
        self,
        card_name: str,
        *,
        set_name: Optional[str] = None,
        set_code: Optional[str] = None,
        collector_number: Optional[str] = None,
        finish: str = "nonfoil",
        is_any_version: bool = False,
        fast_mode: bool = False,
        timeout: float = 8.0,
    ) -> Optional[dict[str, Any]]:
        """
        Searches Star City Games for a single MTG card.
        Returns a VendorPrice-compatible dict, or None if not found.
        """
        if not card_name or not card_name.strip():
            return None

        clean_name = unicodedata.normalize("NFKD", card_name).encode("ASCII", "ignore").decode("utf-8").strip()
        primary_name = clean_name.split(" // ")[0].strip()
        search_url = f"{SCG_BASE_URL}/search/?search_query={urllib.parse.quote_plus(primary_name)}"

        results = self._query_hawksearch(
            primary_name,
            "Singles",
            max_per_page=24 if fast_mode else 40,
            timeout=min(timeout, 5.0) if fast_mode else timeout,
        )
        if not results:
            return None

        target_set_code = (set_code or "").strip().lower()
        target_set_name = (set_name or "").strip().lower()
        target_cn = str(collector_number or "").strip().lower().lstrip("0")

        candidates: list[tuple[int, int, float, dict[str, Any], dict[str, Any]]] = []
        for r in results:
            doc = r.get("Document") if isinstance(r, dict) else None
            if not isinstance(doc, dict):
                continue

            lang = _first_str(doc.get("language")).lower()
            if lang and lang != "english":
                continue

            if not (self._is_card_name_match(doc, card_name) or self._is_card_name_match(doc, primary_name)):
                continue

            if not self._matches_finish(doc, finish):
                continue

            variant = self._extract_best_variant(doc)
            if not variant:
                continue

            doc_set = (_first_str(doc.get("set")) or _first_str(doc.get("primary_category_name"))).lower()
            doc_cn = _first_str(doc.get("collector_number")).lower().lstrip("0")
            doc_sku = _first_str(doc.get("sku")).lower()

            printing_rank = 0
            if not is_any_version and (target_set_code or target_set_name or target_cn):
                set_match = False
                if target_set_name and (target_set_name in doc_set or doc_set in target_set_name):
                    set_match = True
                if target_set_code and (f"-{target_set_code}_" in doc_sku or f"-{target_set_code}-" in doc_sku):
                    set_match = True
                cn_match = bool(target_cn and doc_cn and doc_cn == target_cn)

                if set_match and cn_match:
                    printing_rank = 3
                elif set_match:
                    printing_rank = 2
                elif cn_match:
                    printing_rank = 1

            in_stock_rank = 1 if variant["in_stock"] else 0
            candidates.append((printing_rank, in_stock_rank, -variant["price"], doc, variant))

        if not candidates:
            return None

        # If specific printing requested and any printing matched, filter to highest printing tier
        if not is_any_version and any(c[0] >= 2 for c in candidates):
            max_tier = max(c[0] for c in candidates)
            candidates = [c for c in candidates if c[0] == max_tier]

        candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
        _, _, _, best_doc, best_var = candidates[0]

        rel_url = _first_str(best_doc.get("url_detail"))
        product_url = (
            f"{SCG_BASE_URL}{rel_url}"
            if rel_url.startswith("/")
            else (rel_url or search_url)
        )

        return {
            "vendor_name": "Star City Games",
            "price": float(best_var["price"]),
            "condition": best_var["condition"],
            "in_stock": bool(best_var["in_stock"]),
            "qty_retail": best_var["qty_retail"],
            "product_url": product_url,
            "search_url": search_url,
            "edition": _first_str(best_doc.get("set")) or _first_str(best_doc.get("primary_category_name")),
        }

    def find_offer(
        self,
        item_name: str,
        *,
        tcgplayer_product_id: Optional[int] = None,
        force_refresh: bool = False,
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        """
        Finds the best matching sealed product on Star City Games for `item_name`.
        Returns a VendorPrice-compatible dict, or None if no product matches.
        """
        if not item_name or not item_name.strip():
            return None

        # SCG names boxes "... Booster Box" whereas TCGplayer often says "... Booster Display"
        kw = re.sub(r"\s+-\s+", " ", item_name.strip())
        kw = re.sub(r"\bDisplay\b", "Box", kw, flags=re.IGNORECASE)
        kw = re.sub(r"\s+", " ", kw).strip()
        search_url = f"{SCG_BASE_URL}/search/?search_query={urllib.parse.quote_plus(kw)}"

        results = self._query_hawksearch(
            kw,
            "Sealed",
            max_per_page=24,
            timeout=timeout,
            force_refresh=force_refresh,
        )
        if not results:
            return None

        candidates: list[tuple[int, float, int, float, dict[str, Any], dict[str, Any]]] = []
        for r in results:
            doc = r.get("Document") if isinstance(r, dict) else None
            if not isinstance(doc, dict):
                continue

            lang = _first_str(doc.get("language")).lower()
            if lang and lang != "english":
                continue

            title = _first_str(doc.get("item_display_name")) or _first_str(doc.get("product_name"))
            if not title:
                continue
            edition = _first_str(doc.get("set")) or _first_str(doc.get("primary_category_name"))

            m = match_sealed(title, item_name, listing_edition=edition)
            if not m.matched:
                continue

            variant = self._extract_best_variant(doc)
            if not variant:
                continue

            tcg_match_bonus = 0
            if tcgplayer_product_id is not None:
                doc_tcg_id = _first_str(doc.get("tcg_player_id"))
                if doc_tcg_id and doc_tcg_id == str(tcgplayer_product_id):
                    tcg_match_bonus = 1

            in_stock_rank = 1 if variant["in_stock"] else 0
            candidates.append((tcg_match_bonus, m.score, in_stock_rank, -variant["price"], doc, variant))

        if not candidates:
            return None

        candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
        _, _, _, _, best_doc, best_var = candidates[0]

        rel_url = _first_str(best_doc.get("url_detail"))
        product_url = (
            f"{SCG_BASE_URL}{rel_url}"
            if rel_url.startswith("/")
            else (rel_url or search_url)
        )

        return {
            "vendor_name": "Star City Games",
            "price": float(best_var["price"]),
            "condition": "Unopened",
            "in_stock": bool(best_var["in_stock"]),
            "qty_retail": best_var["qty_retail"],
            "product_url": product_url,
            "search_url": search_url,
            "matched_title": _first_str(best_doc.get("item_display_name")),
        }
