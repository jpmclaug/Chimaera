"""
EDHREC API Client & Metadata Ingestion Provider for Chimaera.
Queries EDHREC's public JSON API (https://json.edhrec.com/pages/commanders/{commander-slug}.json)
to extract popularity rankings, total deck counts, salt scores, curated strategy primers/articles,
archetype themes, combos (Commander Spellbook), and card synergy percentages.
"""

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import requests
from card_utils import fix_mojibake, strip_accents

logger = logging.getLogger(__name__)

EDHREC_BASE_JSON_URL = "https://json.edhrec.com/pages"
EDHREC_BASE_WEB_URL = "https://edhrec.com"
DEFAULT_USER_AGENT = "Chimera-MTGTracker/1.0 (Contact: support@chimera.local)"
DEFAULT_CACHE_TTL_HOURS = 24
MIN_REQUEST_INTERVAL = 0.5  # Max 2 requests per second


class EDHRECProvider:
    """Service client for EDHREC JSON endpoints with caching, rate limiting, and backoff."""

    _last_request_time: float = 0.0
    _rate_lock = threading.Lock()
    _memory_cache: Dict[str, Tuple[datetime, Dict[str, Any]]] = {}
    _mem_lock = threading.Lock()

    def __init__(self, session: Optional[requests.Session] = None, cache_ttl_hours: int = DEFAULT_CACHE_TTL_HOURS):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json;q=0.9,*/*;q=0.8",
        })
        self.cache_ttl_hours = cache_ttl_hours

    @staticmethod
    def normalize_slug(commander_name: str) -> str:
        """
        Normalizes a Commander card name into an EDHREC-compatible URL slug.
        - Lowercase
        - Removes non-alphanumeric characters (apostrophes, quotes, commas, periods)
        - Converts spaces and underscores to hyphens
        - Collapses multiple hyphens
        - Resolves split / DFC / transform cards using the front face (before ' // ')
        - Resolves partner pairings separated by comma, '&', or '+'
        """
        if not commander_name:
            return ""

        raw = strip_accents(fix_mojibake(str(commander_name))).strip()

        # Handle split / double-faced / transform cards: EDHREC indexes by front face
        if " // " in raw:
            raw = raw.split(" // ")[0].strip()

        # Check for partner pairings if input contains separator like ' + ' or ' & '
        if " + " in raw:
            parts = [EDHRECProvider.normalize_slug(p) for p in raw.split(" + ") if p.strip()]
            return "-".join(p for p in parts if p)
        if " & " in raw:
            parts = [EDHRECProvider.normalize_slug(p) for p in raw.split(" & ") if p.strip()]
            return "-".join(p for p in parts if p)

        # Convert to lowercase
        slug = raw.lower()

        # Remove single quotes, apostrophes, and quotation marks without replacing with hyphen
        # e.g. "Urza, Lord High Artificer" -> "urza-lord-high-artificer"
        # "Y'shtola, Night's Blessed" -> "yshtola-nights-blessed"
        slug = re.sub(r"['’\"`]", "", slug)

        # Replace non-alphanumeric characters with hyphens
        slug = re.sub(r"[^a-z0-9]+", "-", slug)

        # Collapse duplicate hyphens and trim
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug

    @classmethod
    def normalize_commander_input(cls, commander: Any) -> Tuple[str, Optional[str]]:
        """
        Resolves commander input (string or list of partner commanders) into
        (primary_or_joint_slug, fallback_slug).
        For partner commanders: returns (joint_slug, first_commander_slug).
        """
        if isinstance(commander, (list, tuple)):
            clean_cmdrs = [c.strip() for c in commander if c and str(c).strip()]
            if not clean_cmdrs:
                return "", None
            if len(clean_cmdrs) == 1:
                return cls.normalize_slug(clean_cmdrs[0]), None
            # Partner pairing
            joint = "-".join(cls.normalize_slug(c) for c in clean_cmdrs)
            primary = cls.normalize_slug(clean_cmdrs[0])
            return joint, primary

        raw = str(commander or "").strip()
        if not raw:
            return "", None

        # Check for comma-separated partner commanders e.g. "Thrasios, Triton Hero, Tymna the Weaver"
        # Only split if there are distinct names
        slug = cls.normalize_slug(raw)
        return slug, None

    def _throttle(self) -> None:
        """Enforces a maximum of 2 requests per second (0.5s interval)."""
        with self._rate_lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < MIN_REQUEST_INTERVAL:
                time.sleep(MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_time = time.time()

    def _get_from_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached JSON payload from database or memory cache if unexpired."""
        # 1. Try DB cache
        try:
            from models import EDHRECCache
            cached = EDHRECCache.get_cached(cache_key)
            if cached is not None:
                return cached
        except Exception:
            pass

        # 2. Try in-memory fallback
        with self._mem_lock:
            if cache_key in self._memory_cache:
                exp, data = self._memory_cache[cache_key]
                if exp > datetime.now(timezone.utc):
                    return data
                del self._memory_cache[cache_key]

        return None

    def _set_in_cache(self, cache_key: str, data: Dict[str, Any]) -> None:
        """Saves JSON payload to both DB cache and in-memory cache."""
        # 1. Try DB cache
        try:
            from models import EDHRECCache
            EDHRECCache.set_cached(cache_key, data, ttl_hours=self.cache_ttl_hours)
        except Exception:
            pass

        # 2. Save in memory cache
        with self._mem_lock:
            exp = datetime.now(timezone.utc) + timedelta(hours=self.cache_ttl_hours)
            self._memory_cache[cache_key] = (exp, data)

    def _request_with_backoff(self, url: str, max_retries: int = 3) -> Optional[requests.Response]:
        """Executes HTTP GET with rate limiting and exponential backoff on HTTP 429/503."""
        backoff = 1.0
        for attempt in range(max_retries):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    return resp
                if resp.status_code in (429, 503):
                    logger.warning(
                        f"EDHREC request to {url} returned HTTP {resp.status_code}. "
                        f"Backing off for {backoff:.1f}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                if resp.status_code == 404:
                    logger.info(f"EDHREC endpoint returned 404 for URL: {url}")
                    return resp
                logger.warning(f"EDHREC request to {url} failed with status {resp.status_code}")
                return resp
            except requests.RequestException as e:
                logger.warning(f"EDHREC network exception for {url}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                else:
                    return None
        return None

    def get_commander_data(
        self,
        commander: Any,
        theme: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetches commander metadata, rankings, primers, combos, and card synergy scores.
        Supports optional theme/sub-theme filtering and partner fallback.
        """
        slug, fallback_slug = self.normalize_commander_input(commander)
        if not slug:
            return None

        clean_theme = self.normalize_slug(theme) if theme else None
        cache_key = f"edhrec:cmdr:{slug}" + (f":theme:{clean_theme}" if clean_theme else "")

        if not force_refresh:
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached

        # Construct EDHREC JSON URL
        if clean_theme:
            url = f"{EDHREC_BASE_JSON_URL}/commanders/{slug}/{clean_theme}.json"
        else:
            url = f"{EDHREC_BASE_JSON_URL}/commanders/{slug}.json"

        resp = self._request_with_backoff(url)

        # Fallback to primary commander if partner slug returned 404
        if (not resp or resp.status_code == 404) and fallback_slug and fallback_slug != slug:
            logger.info(f"EDHREC partner query '{slug}' not found. Falling back to primary '{fallback_slug}'...")
            fallback_url = (
                f"{EDHREC_BASE_JSON_URL}/commanders/{fallback_slug}/{clean_theme}.json"
                if clean_theme
                else f"{EDHREC_BASE_JSON_URL}/commanders/{fallback_slug}.json"
            )
            resp = self._request_with_backoff(fallback_url)
            if resp and resp.status_code == 200:
                slug = fallback_slug

        if not resp or resp.status_code != 200:
            return None

        try:
            raw_data = resp.json()
        except Exception as e:
            logger.error(f"Failed to parse EDHREC JSON for {slug}: {e}")
            return None

        parsed = self._parse_commander_payload(slug, raw_data, active_theme=clean_theme)
        if parsed:
            self._set_in_cache(cache_key, parsed)

        return parsed

    def _parse_commander_payload(
        self,
        slug: str,
        data: Dict[str, Any],
        active_theme: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extracts structured metadata, articles, combos, themes, and card synergies."""
        container = data.get("container", {})
        json_dict = container.get("json_dict", {})
        card_meta = json_dict.get("card", {})
        panels = data.get("panels", {})

        # 1. Popularity & Meta Standing
        name = card_meta.get("name") or data.get("header") or slug.replace("-", " ").title()
        rank = card_meta.get("rank")
        num_decks = card_meta.get("num_decks", 0)
        salt_score = card_meta.get("salt")
        if salt_score is not None:
            try:
                salt_score = round(float(salt_score), 2)
            except (ValueError, TypeError):
                salt_score = None

        color_identity = card_meta.get("color_identity", [])
        edhrec_url = f"{EDHREC_BASE_WEB_URL}/commanders/{slug}"
        if active_theme:
            edhrec_url += f"/{active_theme}"

        # 2. Strategy Articles & Primers
        articles_raw = panels.get("articles", []) if isinstance(panels, dict) else []
        articles: List[Dict[str, Any]] = []
        if isinstance(articles_raw, list):
            for art in articles_raw[:5]:
                title = art.get("value") or art.get("alt")
                href = art.get("href")
                if not title or not href:
                    continue

                full_url = href if href.startswith("http") else f"{EDHREC_BASE_WEB_URL}{href}"
                author_name = art.get("author", {}).get("name") if isinstance(art.get("author"), dict) else None

                articles.append({
                    "title": title,
                    "url": full_url,
                    "date": art.get("date", ""),
                    "author": author_name or "EDHREC Contributor",
                    "media": art.get("media"),
                    "excerpt": art.get("excerpt", ""),
                })

        # 3. Available Archetype Themes
        themes: List[Dict[str, Any]] = []
        taglinks = panels.get("taglinks", []) if isinstance(panels, dict) else []
        if not taglinks:
            taglinks = data.get("tag_counts", [])

        if isinstance(taglinks, list):
            for t in taglinks:
                t_slug = t.get("slug")
                t_name = t.get("value") or t.get("name") or t_slug
                if t_slug and t_name:
                    themes.append({
                        "slug": t_slug,
                        "name": str(t_name).strip(),
                        "count": t.get("count", 0),
                        "is_active": (t_slug == active_theme),
                    })

        # 4. Known Combos (Commander Spellbook via EDHREC)
        combocounts = panels.get("combocounts", []) if isinstance(panels, dict) else []
        combos: List[Dict[str, Any]] = []
        if isinstance(combocounts, list):
            for c in combocounts:
                val = c.get("value") or c.get("alt", "")
                href = c.get("href", "")
                if not val:
                    continue
                pieces = [p.strip() for p in val.split("+") if p.strip()]
                combo_url = href if href.startswith("http") else f"{EDHREC_BASE_WEB_URL}{href}"
                combos.append({
                    "name": val,
                    "pieces": pieces,
                    "piece_count": len(pieces),
                    "url": combo_url,
                })

        # 5. Synergy-Driven Card Index
        cardlists = json_dict.get("cardlists", [])
        card_synergies: Dict[str, Dict[str, Any]] = {}
        high_synergy_cards: List[Dict[str, Any]] = []
        top_cards: List[Dict[str, Any]] = []

        if isinstance(cardlists, list):
            for cl in cardlists:
                header = cl.get("header", "")
                cardviews = cl.get("cardviews", [])
                if not isinstance(cardviews, list):
                    continue

                for cv in cardviews:
                    c_name = cv.get("name")
                    if not c_name:
                        continue

                    c_lower = c_name.strip().lower()
                    synergy_raw = cv.get("synergy", 0.0)
                    try:
                        synergy_val = float(synergy_raw)
                    except (ValueError, TypeError):
                        synergy_val = 0.0

                    num_d = cv.get("num_decks", 0)
                    pot_d = cv.get("potential_decks", 0)
                    inclusion = (num_d / pot_d) if (pot_d and pot_d > 0) else 0.0

                    card_entry = {
                        "name": c_name,
                        "synergy": round(synergy_val, 4),
                        "synergy_percent": round(synergy_val * 100.0, 1),
                        "num_decks": num_d,
                        "potential_decks": pot_d,
                        "inclusion_rate": round(inclusion, 4),
                        "inclusion_percent": round(inclusion * 100.0, 1),
                        "slug": cv.get("slug", ""),
                    }

                    if c_lower not in card_synergies or card_entry["synergy"] > card_synergies[c_lower]["synergy"]:
                        card_synergies[c_lower] = card_entry
                        if " // " in c_lower:
                            front_lower = c_lower.split(" // ")[0].strip()
                            card_synergies[front_lower] = card_entry

                    if "high synergy" in header.lower():
                        high_synergy_cards.append(card_entry)
                    elif "top cards" in header.lower():
                        top_cards.append(card_entry)

        return {
            "slug": slug,
            "name": name,
            "rank": rank,
            "num_decks": num_decks,
            "salt_score": salt_score,
            "color_identity": color_identity,
            "edhrec_url": edhrec_url,
            "articles": articles,
            "themes": themes,
            "combos": combos,
            "card_synergies": card_synergies,
            "high_synergy_cards": high_synergy_cards,
            "top_cards": top_cards,
            "active_theme": active_theme,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_top_salt_cards(self, force_refresh: bool = False) -> Dict[str, float]:
        """
        Fetches the top 100 salty cards from EDHREC (https://json.edhrec.com/pages/top/salt.json).
        Returns a dictionary mapping lowercase card name to its salt rating score.
        """
        cache_key = "edhrec:top_salt_cards"
        if not force_refresh:
            cached = self._get_from_cache(cache_key)
            if cached and isinstance(cached, dict):
                return {k.lower(): float(v) for k, v in cached.items()}

        url = f"{EDHREC_BASE_JSON_URL}/top/salt.json"
        resp = self._request_with_backoff(url)
        if not resp or resp.status_code != 200:
            return {}

        try:
            data = resp.json()
            cardlists = data.get("container", {}).get("json_dict", {}).get("cardlists", [])
            salt_map: Dict[str, float] = {}
            if cardlists and isinstance(cardlists, list):
                for cv in cardlists[0].get("cardviews", []):
                    c_name = cv.get("name")
                    salt = cv.get("salt")
                    if c_name and salt is not None:
                        try:
                            score = round(float(salt), 2)
                            salt_map[c_name.lower()] = score
                            if " // " in c_name:
                                salt_map[c_name.split(" // ")[0].strip().lower()] = score
                        except (ValueError, TypeError):
                            pass

            if salt_map:
                self._set_in_cache(cache_key, salt_map)
            return salt_map
        except Exception as e:
            logger.error(f"Error parsing EDHREC top salt cards: {e}")
            return {}
