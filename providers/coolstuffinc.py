"""
CoolStuffInc MTG Provider (Singles & Sealed).

Queries CoolStuffInc's storefront search endpoint:
    GET https://www.coolstuffinc.com/main_search.php?pa=searchOnName&page=1&resultsPerPage=50&q=...

Uses a thread-local `curl_cffi` browser session bootstrapped against `/sq/?s=mtg`
so PHPSESSID is established, and parses schema.org Product/Offer rows with BeautifulSoup.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
import threading
import time
import unicodedata
import urllib.parse
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

from .sealed_matcher import match_sealed

logger = logging.getLogger(__name__)

CSI_BASE_URL = "https://www.coolstuffinc.com"
CSI_BOOTSTRAP_URL = f"{CSI_BASE_URL}/sq/?s=mtg"
CSI_SEARCH_URL = f"{CSI_BASE_URL}/main_search.php"
DEFAULT_CACHE_TTL_SECONDS = 600  # 10 minutes
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) "
    "Gecko/20100101 Firefox/135.0"
)

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
    "booster pack",
    "booster box",
    "collector booster",
    "bundle",
)

_COND_MAP = {
    "near mint": ("NM", 0),
    "nm": ("NM", 0),
    "lightly played": ("LP", 1),
    "lp": ("LP", 1),
    "played": ("LP", 1),
    "moderately played": ("MP", 2),
    "mp": ("MP", 2),
    "heavily played": ("HP", 3),
    "hp": ("HP", 3),
    "damaged": ("Damaged", 4),
    "dmg": ("Damaged", 4),
}


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    cleaned = re.sub(r"[^\w\s]", " ", norm.lower())
    return " ".join(cleaned.split())


def _safe_float(val: Any) -> float:
    try:
        cleaned = re.sub(r"[^\d.]", "", str(val or ""))
        return round(max(0.0, float(cleaned)), 2) if cleaned else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_qty(val: Any) -> int:
    s = str(val or "").strip().rstrip("+")
    try:
        return max(0, int(s))
    except (TypeError, ValueError):
        return 0


def _extract_base_name(title: str) -> str:
    t = re.sub(r"\[.*?\]", "", title or "")
    t = re.sub(r"\(.*?\)", "", t)
    return t.strip()


def _parse_offer_condition(text: str) -> tuple[bool, str, int]:
    low = (text or "").lower()
    is_foil = "foil" in low
    cond_label, cond_rank = ("NM", 0)
    for key, mapped in _COND_MAP.items():
        if re.search(r"\b" + re.escape(key) + r"\b", low):
            cond_label, cond_rank = mapped
            break
    full_label = f"{cond_label} Foil".strip() if is_foil else cond_label
    return is_foil, full_label, cond_rank


class CoolStuffIncProvider:
    """Fetches and parses CoolStuffInc MTG singles and sealed listings."""

    _shared_lock = threading.Lock()
    _shared_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def __init__(
        self,
        session: Optional[Any] = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        use_shared_cache: bool = True,
    ):
        self._custom_session = session
        self._thread_local = threading.local()
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.use_shared_cache = bool(use_shared_cache and session is None)

        self._inst_lock = threading.Lock()
        self._inst_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    @classmethod
    def clear_shared_cache(cls) -> None:
        with cls._shared_lock:
            cls._shared_cache.clear()

    def _create_session(self):
        try:
            from curl_cffi import requests as cffi_requests
            sess = cffi_requests.Session(impersonate="chrome120")
            return sess
        except ImportError:
            sess = requests.Session()
            sess.headers.update({"User-Agent": DEFAULT_USER_AGENT})
            return sess

    @property
    def session(self):
        if self._custom_session is not None:
            return self._custom_session
        sess = getattr(self._thread_local, "session", None)
        if sess is None:
            sess = self._create_session()
            self._thread_local.session = sess
        return sess

    @session.setter
    def session(self, value):
        self._custom_session = value

    def _ensure_session_cookie(self, timeout: float = 8.0, force: bool = False) -> None:
        if self._custom_session is not None and not force:
            return
        bootstrapped = getattr(self._thread_local, "bootstrapped", False)
        if bootstrapped and not force:
            return
        try:
            self.session.get(CSI_BOOTSTRAP_URL, timeout=max(2.0, float(timeout)))
            self._thread_local.bootstrapped = True
        except Exception as exc:
            logger.debug(f"CoolStuffInc session bootstrap warning: {exc}")

    def _fetch_search_rows(
        self,
        query: str,
        *,
        timeout: float = 10.0,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        cache_key = clean_q.lower()
        lock = self._shared_lock if self.use_shared_cache else self._inst_lock
        cache = self._shared_cache if self.use_shared_cache else self._inst_cache
        now = time.time()

        if not force_refresh and self.cache_ttl_seconds > 0:
            with lock:
                hit = cache.get(cache_key)
                if hit and (now - hit[0]) < self.cache_ttl_seconds:
                    return hit[1]

        self._ensure_session_cookie(timeout=min(timeout, 6.0))
        url = (
            f"{CSI_SEARCH_URL}?pa=searchOnName&page=1&resultsPerPage=50"
            f"&q={urllib.parse.quote_plus(clean_q)}"
        )

        html_text = ""
        for attempt in range(2):
            try:
                resp = self.session.get(url, timeout=max(2.0, float(timeout)))
                if resp.status_code == 403 and attempt == 0:
                    self._ensure_session_cookie(timeout=min(timeout, 6.0), force=True)
                    continue
                if resp.status_code == 200:
                    html_text = resp.text or ""
                    # If CSI returned the bare page without rows due to missing PHPSESSID, re-bootstrap once
                    if "product-search-row" not in html_text and attempt == 0 and self._custom_session is None:
                        self._ensure_session_cookie(timeout=min(timeout, 6.0), force=True)
                        continue
                    break
            except Exception as exc:
                logger.warning(f"CoolStuffInc search failed for '{clean_q}': {exc}")
                return []

        if not html_text:
            return []

        parsed = self._parse_search_html(html_text)
        if self.cache_ttl_seconds > 0 and parsed:
            with lock:
                if len(cache) >= 1000:
                    for old_k in list(cache.keys())[:250]:
                        cache.pop(old_k, None)
                cache[cache_key] = (time.time(), parsed)
        return parsed

    @staticmethod
    def _parse_search_html(html_text: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        rows_out: list[dict[str, Any]] = []

        for row in soup.select("div.product-search-row"):
            bc_el = row.select_one(".breadcrumb-trail")
            breadcrumb = " ".join(bc_el.get_text(" ", strip=True).split()) if bc_el else ""
            if breadcrumb and "magic: the gathering" not in breadcrumb.lower():
                continue

            name_el = row.select_one('span[itemprop="name"]')
            if not name_el:
                continue
            name = html_lib.unescape(name_el.get_text(strip=True))
            if not name:
                continue

            set_name = ""
            if "»" in breadcrumb:
                set_name = breadcrumb.split("»", 1)[1].strip()

            notes_el = row.select_one(".product-notes")
            notes = notes_el.get_text(" ", strip=True) if notes_el else ""
            notes = re.sub(r"^Notes:\s*", "", notes, flags=re.I).strip()

            rating_el = row.select_one("[data-pid]")
            pid = rating_el.get("data-pid") if rating_el else ""
            link_el = row.select_one("a.productLink")
            href = (link_el.get("href") or "").strip() if link_el else ""
            if pid:
                product_url = f"{CSI_BASE_URL}/p/{pid}"
            elif href:
                product_url = f"{CSI_BASE_URL}{href}" if href.startswith("/") else href
            else:
                product_url = ""

            img_el = row.select_one('img[itemprop="image"]')
            img_src = (img_el.get("src") or "").strip() if img_el else ""
            sku_code = ""
            if img_src:
                m_sku = re.search(r"/([^/]+)\.(?:jpg|jpeg|png|webp)", img_src, re.I)
                if m_sku:
                    sku_code = m_sku.group(1).lower()

            offers: list[dict[str, Any]] = []
            for offer_el in row.select('[itemprop="offers"]'):
                qty_el = offer_el.select_one(".card-qty")
                qty = _safe_qty(qty_el.get_text(strip=True) if qty_el else "0")

                price_el = offer_el.select_one('[itemprop="price"]')
                price = _safe_float(price_el.get("content") if (price_el and price_el.has_attr("content")) else (price_el.get_text(strip=True) if price_el else "0"))

                avail_el = offer_el.select_one('[itemprop="availability"]')
                avail_content = (avail_el.get("content") or "") if avail_el else ""
                in_stock = ("instock" in avail_content.lower() or qty > 0) and price > 0

                desc_el = offer_el.select_one('meta[itemprop="description"]')
                desc_text = (desc_el.get("content") or "") if desc_el else ""
                offer_text = " ".join(offer_el.get_text(" ", strip=True).split())
                cond_source = f"{desc_text} {offer_text}"
                is_foil, cond_label, cond_rank = _parse_offer_condition(cond_source)

                if price > 0:
                    offers.append({
                        "price": price,
                        "qty": qty,
                        "in_stock": in_stock,
                        "is_foil": is_foil,
                        "condition": cond_label,
                        "cond_rank": cond_rank,
                        "raw_text": offer_text,
                    })

            # Also check fallback "From: $X.XX" when out of stock and no offer blocks exist
            if not offers:
                from_el = row.select_one(".darkred b, .search-ppq-cell b")
                fallback_p = _safe_float(from_el.get_text(strip=True) if from_el else "0")
                ppq_text = " ".join(row.select_one(".search-ppq-cell").get_text(" ", strip=True).split()) if row.select_one(".search-ppq-cell") else ""
                if fallback_p > 0:
                    offers.append({
                        "price": fallback_p,
                        "qty": 0,
                        "in_stock": False,
                        "is_foil": False,
                        "condition": "NM",
                        "cond_rank": 0,
                        "raw_text": ppq_text,
                    })

            rows_out.append({
                "name": name,
                "set_name": set_name,
                "notes": notes,
                "pid": pid,
                "sku_code": sku_code,
                "product_url": product_url,
                "offers": offers,
            })

        return rows_out

    @staticmethod
    def _is_card_name_match(title: str, card_name: str) -> bool:
        norm_target = _normalize_text(card_name)
        if not norm_target:
            return False

        norm_title = _normalize_text(title)
        for jt in _JUNK_TERMS:
            jt_norm = _normalize_text(jt)
            if re.search(r"\b" + re.escape(jt_norm) + r"\b", norm_title):
                if not re.search(r"\b" + re.escape(jt_norm) + r"\b", norm_target):
                    return False

        base = _extract_base_name(title)
        norm_base = _normalize_text(base)
        if norm_base == norm_target:
            return True

        faces = [_normalize_text(f) for f in re.split(r"/+", base) if f.strip()]
        if norm_target in faces:
            return True

        if "//" in card_name or "/" in card_name:
            target_faces = [_normalize_text(f) for f in re.split(r"/+", card_name) if f.strip()]
            if target_faces == faces:
                return True
            if target_faces and faces and faces[0] == target_faces[0]:
                return True

        return False

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
        Searches CoolStuffInc for a single MTG card.
        Returns a VendorPrice-compatible dict, or None if not found.
        """
        if not card_name or not card_name.strip():
            return None

        clean_name = unicodedata.normalize("NFKD", card_name).encode("ASCII", "ignore").decode("utf-8").strip()
        primary_name = clean_name.split(" // ")[0].strip()
        search_url = (
            f"{CSI_SEARCH_URL}?pa=searchOnName&page=1&resultsPerPage=50"
            f"&q={urllib.parse.quote_plus(primary_name)}"
        )

        rows = self._fetch_search_rows(
            primary_name,
            timeout=min(timeout, 5.0) if fast_mode else timeout,
        )
        if not rows:
            return None

        f = (finish or "nonfoil").strip().lower()
        target_set_code = (set_code or "").strip().lower()
        target_set_name = (set_name or "").strip().lower()
        target_cn = str(collector_number or "").strip().lower().lstrip("0")

        candidates: list[tuple[int, int, float, dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            if not (self._is_card_name_match(row["name"], card_name) or self._is_card_name_match(row["name"], primary_name)):
                continue

            matching_offers = []
            for off in row["offers"]:
                if f in ("foil", "etched") and not off["is_foil"]:
                    continue
                if f == "nonfoil" and off["is_foil"]:
                    continue
                matching_offers.append(off)

            if not matching_offers:
                continue

            in_stock_offs = [o for o in matching_offers if o["in_stock"]]
            if in_stock_offs:
                in_stock_offs.sort(key=lambda o: (o["price"], o["cond_rank"]))
                best_off = in_stock_offs[0]
            else:
                matching_offers.sort(key=lambda o: (o["cond_rank"], o["price"]))
                best_off = matching_offers[0]

            printing_rank = 0
            if not is_any_version and (target_set_code or target_set_name or target_cn):
                row_set = row["set_name"].lower()
                row_notes = row["notes"].lower()
                row_sku = row["sku_code"].lower()

                set_match = bool(target_set_name and (target_set_name in row_set or row_set in target_set_name))
                if not set_match and target_set_code and row_sku.startswith(target_set_code):
                    set_match = True

                cn_match = False
                if target_cn:
                    padded = target_cn.zfill(4)
                    padded3 = target_cn.zfill(3)
                    if (
                        (target_set_code and f"{target_set_code}{padded}" in row_sku)
                        or (target_set_code and f"{target_set_code}{padded3}" in row_sku)
                        or re.search(rf"\b0*{re.escape(target_cn)}\b", row_notes)
                    ):
                        cn_match = True

                if set_match and cn_match:
                    printing_rank = 3
                elif set_match:
                    printing_rank = 2
                elif cn_match:
                    printing_rank = 1

            in_stock_rank = 1 if best_off["in_stock"] else 0
            candidates.append((printing_rank, in_stock_rank, -best_off["price"], row, best_off))

        if not candidates:
            return None

        if not is_any_version and any(c[0] >= 2 for c in candidates):
            max_tier = max(c[0] for c in candidates)
            candidates = [c for c in candidates if c[0] == max_tier]

        candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
        _, _, _, best_row, best_off = candidates[0]

        return {
            "vendor_name": "CoolStuffInc",
            "price": float(best_off["price"]),
            "condition": best_off["condition"],
            "in_stock": bool(best_off["in_stock"]),
            "qty_retail": best_off["qty"],
            "product_url": best_row["product_url"] or search_url,
            "search_url": search_url,
            "edition": best_row["set_name"],
        }

    def find_offer(
        self,
        item_name: str,
        *,
        force_refresh: bool = False,
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        """
        Finds the best matching sealed product on CoolStuffInc for `item_name`.
        Returns a VendorPrice-compatible dict, or None if no product matches.
        """
        if not item_name or not item_name.strip():
            return None

        # Strip 'MTG' / ' - ' and convert 'Display' -> 'Booster' so CSI search returns sealed boxes
        kw = re.sub(r"\b(?:MTG|Magic:\s*The\s*Gathering)\b", "", item_name, flags=re.IGNORECASE)
        kw = re.sub(r"\s+-\s+", " ", kw)
        kw = re.sub(r"\bDisplay\b", "Box", kw, flags=re.IGNORECASE)
        kw = re.sub(r"\s+", " ", kw).strip()
        if not kw:
            kw = item_name.strip()

        search_url = (
            f"{CSI_SEARCH_URL}?pa=searchOnName&page=1&resultsPerPage=50"
            f"&q={urllib.parse.quote_plus(kw)}"
        )

        rows = self._fetch_search_rows(kw, timeout=timeout, force_refresh=force_refresh)
        if not rows:
            return None

        candidates: list[tuple[float, int, float, dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            title = row["name"]
            m = match_sealed(title, item_name, listing_edition=row["set_name"])
            if not m.matched:
                continue

            if not row["offers"]:
                continue

            in_stock_offs = [o for o in row["offers"] if o["in_stock"]]
            if in_stock_offs:
                in_stock_offs.sort(key=lambda o: o["price"])
                best_off = in_stock_offs[0]
            else:
                row["offers"].sort(key=lambda o: o["price"])
                best_off = row["offers"][0]

            in_stock_rank = 1 if best_off["in_stock"] else 0
            candidates.append((m.score, in_stock_rank, -best_off["price"], row, best_off))

        if not candidates:
            return None

        candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
        _, _, _, best_row, best_off = candidates[0]

        return {
            "vendor_name": "CoolStuffInc",
            "price": float(best_off["price"]),
            "condition": "Unopened",
            "in_stock": bool(best_off["in_stock"]),
            "qty_retail": best_off["qty"],
            "product_url": best_row["product_url"] or search_url,
            "search_url": search_url,
            "matched_title": best_row["name"],
        }
