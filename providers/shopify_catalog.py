"""
Shopify Global Catalog (Shop app) Sealed MTG Product Provider.

Queries Shopify's public Universal Commerce Protocol (UCP) / Model Context
Protocol (MCP) Global Catalog endpoint:
    POST https://catalog.shopify.com/api/ucp/mcp  ->  tools/call: search_catalog

Supports:
  - Anonymous tier out-of-the-box (zero config)
  - Optional OAuth client_credentials token tier (5 req/s guaranteed) when
    SHOPIFY_CATALOG_CLIENT_ID and SHOPIFY_CATALOG_CLIENT_SECRET are set
  - Bounded rate limiting + exponential backoff on 429/5xx
  - Title/category matching via sealed_matcher
  - Scam/clone-farm filtering via sealed_trust
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Iterable, Optional

import requests

from config import Config
from .sealed_matcher import build_search_query, match_sealed
from .sealed_trust import evaluate_offers_trust, get_blocked_shops, get_trusted_shops

logger = logging.getLogger(__name__)

CATALOG_MCP_URL = "https://catalog.shopify.com/api/ucp/mcp"
SHOPIFY_AUTH_TOKEN_URL = "https://api.shopify.com/auth/access_token"
DEFAULT_UCP_AGENT_PROFILE_URL = (
    "https://shopify.dev/ucp/agent-profiles/examples/2026-08-25/valid-with-capabilities.json"
)


def _minor_to_major(amount: Any, currency: str = "USD") -> float:
    """Converts ISO 4217 minor units (e.g. 2599 USD cents) to major units (25.99)."""
    try:
        raw = float(amount)
    except (TypeError, ValueError):
        return 0.0
    if raw <= 0:
        return 0.0
    zero_decimal = {"JPY", "KRW", "VND", "CLP", "ISK"}
    if (currency or "USD").upper() in zero_decimal:
        return round(raw, 2)
    return round(raw / 100.0, 2)


def _format_vendor_name(prefix: str, shop_name: str, max_len: int = 50) -> str:
    clean_shop = (shop_name or "Shopify Store").strip()
    full = f"{prefix}{clean_shop}"
    if len(full) <= max_len:
        return full
    return full[: max_len - 1].rstrip() + "…"


class ShopifyCatalogProvider:
    """Queries Shopify's Global Catalog (Shop app) for sealed MTG offers."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        *,
        profile_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        min_request_interval: float = 0.9,
        min_market_ratio: Optional[float] = None,
    ):
        self.session = session or requests.Session()
        self.session.headers.setdefault("Content-Type", "application/json")
        self.session.headers.setdefault("Accept", "application/json, text/event-stream")
        self.session.headers.setdefault(
            "User-Agent",
            "Chimera-MTG-Tracker/1.0 (+https://github.com/chimera-mtg)",
        )

        self.profile_url = (
            profile_url
            or getattr(Config, "UCP_AGENT_PROFILE_URL", "")
            or DEFAULT_UCP_AGENT_PROFILE_URL
        ).strip()
        self.client_id = (
            client_id if client_id is not None else getattr(Config, "SHOPIFY_CATALOG_CLIENT_ID", "")
        ).strip()
        self.client_secret = (
            client_secret
            if client_secret is not None
            else getattr(Config, "SHOPIFY_CATALOG_CLIENT_SECRET", "")
        ).strip()
        self.min_request_interval = max(0.0, float(min_request_interval))
        self.min_market_ratio = (
            float(min_market_ratio)
            if min_market_ratio is not None
            else float(getattr(Config, "SEALED_MIN_MARKET_RATIO", 0.55))
        )

        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0
        self._rpc_id = 0

        self._token_lock = threading.Lock()
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def _throttle(self) -> None:
        if self.min_request_interval <= 0:
            return
        with self._rate_lock:
            elapsed = time.time() - self._last_request_at
            if elapsed < self.min_request_interval:
                time.sleep(self.min_request_interval - elapsed)
            self._last_request_at = time.time()

    def _get_bearer_token(self) -> Optional[str]:
        """Exchanges optional Shopify Catalog client credentials for a short-lived JWT."""
        if not self.client_id or not self.client_secret:
            return None

        now = time.time()
        with self._token_lock:
            if self._access_token and now < (self._token_expires_at - 300):
                return self._access_token
            try:
                resp = self.session.post(
                    SHOPIFY_AUTH_TOKEN_URL,
                    json={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "client_credentials",
                    },
                    timeout=12.0,
                )
                resp.raise_for_status()
                data = resp.json()
                token = data.get("access_token")
                expires_in = float(data.get("expires_in") or 3600)
                if token:
                    self._access_token = str(token)
                    self._token_expires_at = time.time() + expires_in
                    return self._access_token
            except Exception as exc:
                logger.warning(f"Shopify Catalog OAuth token fetch failed (falling back to anonymous): {exc}")
        return None

    def _call_search_catalog(
        self,
        query: str,
        *,
        max_price_cents: Optional[int] = None,
        shops: Optional[list[str]] = None,
        cursor: Optional[str] = None,
        timeout: float = 20.0,
    ) -> Optional[dict[str, Any]]:
        """Executes a single JSON-RPC tools/call -> search_catalog request with backoff."""
        filters: dict[str, Any] = {
            "available": True,
            "condition": ["new"],
            "ships_to": {"country": "US"},
        }
        if max_price_cents is not None and max_price_cents > 0:
            filters["price"] = {"max": int(max_price_cents)}
        if shops:
            filters["shops"] = list(shops)[:1000]

        catalog_params: dict[str, Any] = {
            "query": query,
            "context": {
                "address_country": "US",
                "currency": "USD",
                "language": "en",
            },
            "filters": filters,
        }
        if cursor:
            catalog_params["pagination"] = {"cursor": cursor}

        with self._rate_lock:
            self._rpc_id += 1
            req_id = self._rpc_id

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {
                "name": "search_catalog",
                "arguments": {
                    "meta": {"ucp-agent": {"profile": self.profile_url}},
                    "catalog": catalog_params,
                },
            },
        }

        headers: dict[str, str] = {}
        token = self._get_bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        backoff = 1.0
        for attempt in range(3):
            self._throttle()
            try:
                resp = self.session.post(
                    CATALOG_MCP_URL,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < 2:
                        retry_after = resp.headers.get("Retry-After")
                        sleep_s = float(retry_after) if (retry_after and retry_after.isdigit()) else backoff
                        time.sleep(min(sleep_s, 6.0))
                        backoff *= 2.0
                        continue
                    logger.warning(f"Shopify Catalog returned HTTP {resp.status_code} for '{query}'")
                    return None

                if resp.status_code != 200:
                    logger.warning(
                        f"Shopify Catalog HTTP {resp.status_code} for '{query}': {resp.text[:200]}"
                    )
                    return None

                body = resp.json()
                if not isinstance(body, dict):
                    return None
                if body.get("error"):
                    logger.warning(f"Shopify Catalog RPC error for '{query}': {body['error']}")
                    return None

                result = body.get("result") or {}
                structured = result.get("structuredContent")
                if isinstance(structured, dict):
                    return structured
                # Fallback: parse JSON text content block
                content = result.get("content") or []
                if isinstance(content, list) and content:
                    first_text = content[0].get("text") if isinstance(content[0], dict) else None
                    if first_text:
                        parsed = json.loads(first_text)
                        if isinstance(parsed, dict):
                            return parsed
                return None
            except Exception as exc:
                if attempt < 2:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                logger.warning(f"Shopify Catalog request failed for '{query}': {exc}")
                return None
        return None

    @staticmethod
    def flatten_catalog_response(structured: dict[str, Any]) -> list[dict[str, Any]]:
        """Flattens a UCP search_catalog structuredContent response into normalized offer dicts."""
        offers: list[dict[str, Any]] = []
        products = structured.get("products") or []
        if not isinstance(products, list):
            return offers

        for prod in products:
            if not isinstance(prod, dict):
                continue
            prod_title = str(prod.get("title") or "").strip()
            prod_handle = str(prod.get("handle") or "").strip()
            prod_rating = prod.get("rating") if isinstance(prod.get("rating"), dict) else None
            variants = prod.get("variants") or []
            if not isinstance(variants, list):
                continue

            for var in variants:
                if not isinstance(var, dict):
                    continue
                avail = var.get("availability")
                is_available = True
                if isinstance(avail, dict) and "available" in avail:
                    is_available = bool(avail.get("available"))
                if not is_available:
                    continue

                price_obj = var.get("price") or {}
                currency = str(price_obj.get("currency") or "USD").upper()
                if currency != "USD":
                    continue
                price = _minor_to_major(price_obj.get("amount"), currency)
                if price <= 0:
                    continue

                seller = var.get("seller") if isinstance(var.get("seller"), dict) else {}
                title = str(var.get("title") or prod_title).strip() or prod_title
                handle = str(var.get("handle") or prod_handle).strip()
                url = str(var.get("url") or "").strip()
                checkout_url = str(var.get("checkout_url") or "").strip()
                rating = var.get("rating") if isinstance(var.get("rating"), dict) else prod_rating

                offers.append({
                    "title": title,
                    "handle": handle,
                    "price": price,
                    "currency": currency,
                    "available": is_available,
                    "url": url,
                    "checkout_url": checkout_url or url,
                    "shop_gid": str(seller.get("id") or "").strip(),
                    "shop_name": str(seller.get("name") or "").strip(),
                    "shop_domain": str(seller.get("domain") or "").strip(),
                    "shop_url": str(seller.get("url") or "").strip(),
                    "rating": rating,
                })
        return offers

    def search(
        self,
        query: str,
        *,
        max_price_cents: Optional[int] = None,
        shops: Optional[list[str]] = None,
        limit_pages: int = 2,
    ) -> list[dict[str, Any]]:
        """Searches Shopify Global Catalog across up to `limit_pages` pages."""
        all_offers: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        pages = max(1, int(limit_pages))

        for _ in range(pages):
            structured = self._call_search_catalog(
                query,
                max_price_cents=max_price_cents,
                shops=shops,
                cursor=cursor,
            )
            if not structured:
                break
            all_offers.extend(self.flatten_catalog_response(structured))
            pagination = structured.get("pagination") or {}
            if not pagination.get("has_next_page") or not pagination.get("cursor"):
                break
            cursor = str(pagination["cursor"])

        return all_offers

    def find_offers(
        self,
        item_name: str,
        *,
        market_price: Optional[float] = None,
        max_price: Optional[float] = None,
        trusted_shops: Optional[Iterable[dict[str, str]]] = None,
        blocked_shops: Optional[Iterable[dict[str, str]]] = None,
        min_market_ratio: Optional[float] = None,
        limit_pages: int = 2,
    ) -> dict[str, Any]:
        """
        Searches Shopify Global Catalog for a watched sealed MTG item:
          1. If trusted allowlist contains Shop GIDs, runs a targeted trusted-shop query.
          2. Runs an open Global Catalog discovery query.
          3. Deduplicates, filters with sealed_matcher, and classifies trust/scam status.

        Returns:
          {
            "best_trusted": VendorPrice-ready dict or None,
            "best_unverified": VendorPrice-ready dict or None (in_stock=False so it never triggers alerts),
            "trusted_offers": list[dict],
            "unverified_offers": list[dict],
            "suspicious_offers": list[dict],
          }
        """
        empty = {
            "best_trusted": None,
            "best_unverified": None,
            "trusted_offers": [],
            "unverified_offers": [],
            "suspicious_offers": [],
        }
        query = build_search_query(item_name)
        if not query:
            return empty

        allowlist = list(trusted_shops) if trusted_shops is not None else get_trusted_shops()
        blocklist = list(blocked_shops) if blocked_shops is not None else get_blocked_shops()
        trusted_gids = [
            s["shop_gid"] for s in allowlist if isinstance(s, dict) and str(s.get("shop_gid") or "").startswith("gid://")
        ]
        max_price_cents = int(round(max_price * 100)) if (max_price and max_price > 0) else None

        raw_offers: list[dict[str, Any]] = []
        if trusted_gids:
            raw_offers.extend(
                self.search(
                    query,
                    max_price_cents=max_price_cents,
                    shops=trusted_gids,
                    limit_pages=1,
                )
            )

        raw_offers.extend(
            self.search(
                query,
                max_price_cents=max_price_cents,
                limit_pages=limit_pages,
            )
        )

        # Deduplicate by (shop_gid or shop_domain, url or title+price)
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for offer in raw_offers:
            shop_key = (offer.get("shop_gid") or offer.get("shop_domain") or offer.get("shop_name") or "").lower()
            item_key = (offer.get("url") or f"{offer.get('title')}|{offer.get('price')}").lower()
            k = (shop_key, item_key)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(offer)

        # Filter by sealed product matcher
        matched_offers: list[dict[str, Any]] = []
        for offer in deduped:
            m = match_sealed(
                str(offer.get("title") or ""),
                item_name,
                listing_handle=str(offer.get("handle") or ""),
            )
            if m.matched:
                enriched = dict(offer)
                enriched["match_score"] = m.score
                matched_offers.append(enriched)

        if not matched_offers:
            return empty

        ratio = self.min_market_ratio if min_market_ratio is None else float(min_market_ratio)
        decisions = evaluate_offers_trust(
            matched_offers,
            market_price=market_price,
            min_market_ratio=ratio,
            trusted_shops=allowlist,
            blocked_shops=blocklist,
        )

        trusted_offers: list[dict[str, Any]] = []
        unverified_offers: list[dict[str, Any]] = []
        suspicious_offers: list[dict[str, Any]] = []

        for offer, decision in decisions:
            row = dict(offer)
            row["trust_status"] = decision.status
            row["trust_reason"] = decision.reason
            if decision.status == "trusted":
                trusted_offers.append(row)
            elif decision.status == "unverified":
                unverified_offers.append(row)
            else:
                suspicious_offers.append(row)
                logger.info(
                    f"[SHOP-FILTER] Dropped suspicious offer for '{item_name}': "
                    f"${row['price']:.2f} @ {row.get('shop_name')} ({row.get('shop_domain')}) "
                    f"reason={decision.reason}"
                )

        trusted_offers.sort(key=lambda o: (o["price"], -o.get("match_score", 0.0)))
        unverified_offers.sort(key=lambda o: (o["price"], -o.get("match_score", 0.0)))
        suspicious_offers.sort(key=lambda o: o["price"])

        best_trusted = None
        if trusted_offers:
            top = trusted_offers[0]
            best_trusted = {
                "vendor_name": _format_vendor_name("Shop · ", top.get("shop_name") or "Shopify Store"),
                "price": float(top["price"]),
                "condition": "Unopened",
                "in_stock": True,
                "product_url": top.get("url") or top.get("checkout_url"),
                "search_url": top.get("shop_url") or top.get("url"),
                "shop_gid": top.get("shop_gid") or "",
                "shop_name": top.get("shop_name") or "",
                "shop_domain": top.get("shop_domain") or "",
                "checkout_url": top.get("checkout_url") or top.get("url"),
                "trust_status": "trusted",
            }

        best_unverified = None
        if unverified_offers:
            top_u = unverified_offers[0]
            best_unverified = {
                "vendor_name": _format_vendor_name("Shop (unverified) · ", top_u.get("shop_name") or "Shopify Store"),
                "price": float(top_u["price"]),
                "condition": "Unverified",
                # IMPORTANT: in_stock=False guarantees unverified stores NEVER count toward
                # WatchlistItem.is_deal or trigger Discord alerts while still showing in UI.
                "in_stock": False,
                "product_url": top_u.get("url") or top_u.get("checkout_url"),
                "search_url": top_u.get("shop_url") or top_u.get("url"),
                "shop_gid": top_u.get("shop_gid") or "",
                "shop_name": top_u.get("shop_name") or "",
                "shop_domain": top_u.get("shop_domain") or "",
                "checkout_url": top_u.get("checkout_url") or top_u.get("url"),
                "trust_status": "unverified",
            }

        return {
            "best_trusted": best_trusted,
            "best_unverified": best_unverified,
            "trusted_offers": trusted_offers,
            "unverified_offers": unverified_offers,
            "suspicious_offers": suspicious_offers,
        }
