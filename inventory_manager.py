"""
Inventory Manager module for Chimaera MTG.
Handles persistence, enrichment, batch merging/replacement, and physical cross-deck allocation tracking.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy import func, case, or_, and_
from models import db, UserInventoryCard, DeckAnalysis, utc_now
from providers.scryfall import ScryfallProvider
from card_utils import fix_mojibake, strip_accents, get_card_match_keys, normalize_card_name

logger = logging.getLogger(__name__)


class InventoryManager:
    """Manages user collection storage, Scryfall metadata enrichment, and deck allocation telemetry."""

    def __init__(self, scryfall_provider: Optional[ScryfallProvider] = None):
        self.scryfall_provider = scryfall_provider or ScryfallProvider()

    def import_inventory(
        self,
        user_id: int,
        parsed_cards: List[Dict[str, Any]],
        mode: str = "replace",
    ) -> Dict[str, Any]:
        """
        Persists parsed cards to user's collection using high-performance bulk batching.
        mode="replace": Purges user's existing inventory first.
        mode="merge": Accumulates quantity for matching (name, set_code, collector_number, foil).
        """
        if not user_id:
            raise ValueError("user_id is required to import inventory.")

        if not parsed_cards:
            return {
                "success": True,
                "mode": mode,
                "added_count": 0,
                "updated_count": 0,
                "total_cards": 0,
                "unique_cards": 0,
                "total_value": 0.0,
            }

        # 1. Look up cached metadata from existing database records (avoid redundant external calls)
        unique_names = list({c["name"] for c in parsed_cards if c.get("name")})
        cached_meta: Dict[str, Dict[str, Any]] = {}

        if unique_names:
            try:
                # Query existing metadata cache from DB for these card names
                name_lowers = [n.lower() for n in unique_names]
                # Query in slices if very large to prevent SQL parameter limits
                for i in range(0, len(name_lowers), 1000):
                    slice_lowers = name_lowers[i:i + 1000]
                    existing_meta_rows = (
                        db.session.query(
                            func.lower(UserInventoryCard.name),
                            UserInventoryCard.type_line,
                            UserInventoryCard.mana_cost,
                            UserInventoryCard.cmc,
                            UserInventoryCard.color_identity,
                            UserInventoryCard.image_uri,
                            UserInventoryCard.price_usd,
                            UserInventoryCard.price_usd_foil,
                            UserInventoryCard.oracle_text,
                        )
                        .filter(
                            UserInventoryCard.image_uri.isnot(None),
                            func.lower(UserInventoryCard.name).in_(slice_lowers),
                        )
                        .all()
                    )
                    for r in existing_meta_rows:
                        k = r[0]
                        if k not in cached_meta:
                            cached_meta[k] = {
                                "type_line": r[1],
                                "mana_cost": r[2],
                                "cmc": r[3],
                                "color_identity": r[4],
                                "image_uri": r[5],
                                "price_usd": r[6],
                                "price_usd_foil": r[7],
                                "oracle_text": r[8],
                            }
            except Exception as e:
                logger.warning(f"Failed to query local metadata cache: {e}")

        # 2. Check which names still lack metadata
        missing_names = [n for n in unique_names if n.lower() not in cached_meta]
        scryfall_map: Dict[str, Any] = {}

        # Fetch up to 75 missing names synchronously for instant enrichment without blocking
        if missing_names:
            sync_batch = missing_names[:75]
            try:
                scryfall_map, _ = self.scryfall_provider.get_cards_collection(sync_batch, fallback_named=False)
            except Exception as e:
                logger.error(f"Error fetching Scryfall metadata during import: {e}")

        # 3. Handle replace vs merge with bulk persistence
        now_dt = utc_now()
        added_count = 0
        updated_count = 0

        if mode == "replace":
            db.session.execute(db.delete(UserInventoryCard).where(UserInventoryCard.user_id == user_id))
            db.session.flush()

            records_to_insert = []
            for item in parsed_cards:
                name = item["name"]
                name_l = name.lower()
                set_code = item.get("set_code", "").upper()
                col_num = item.get("collector_number", "")
                foil = item.get("foil", "normal").lower()
                qty = max(1, int(item.get("quantity", 1)))

                # Resolve metadata
                meta = scryfall_map.get(name_l) or cached_meta.get(name_l, {})
                if not meta and " // " in name:
                    front = name.split(" // ")[0].lower()
                    meta = scryfall_map.get(front) or cached_meta.get(front, {})

                prices = meta.get("prices", {}) if isinstance(meta, dict) and "prices" in meta else {}
                price_usd = meta.get("price_usd") if isinstance(meta, dict) else None
                price_usd_foil = meta.get("price_usd_foil") if isinstance(meta, dict) else None
                if price_usd is None and prices.get("usd"):
                    try:
                        price_usd = float(prices["usd"])
                    except Exception:
                        pass
                if price_usd_foil is None and prices.get("usd_foil"):
                    try:
                        price_usd_foil = float(prices["usd_foil"])
                    except Exception:
                        pass
                if price_usd is None and item.get("purchase_price") is not None:
                    price_usd = item["purchase_price"]

                cid = meta.get("color_identity") if isinstance(meta, dict) else None
                cid_str = ",".join(cid) if isinstance(cid, list) else (cid if isinstance(cid, str) else None)
                cmc_val = meta.get("cmc") if isinstance(meta, dict) else None

                records_to_insert.append({
                    "user_id": user_id,
                    "name": name,
                    "raw_name": item.get("raw_name") or name,
                    "set_code": set_code,
                    "set_name": item.get("set_name") or (meta.get("set_name", "") if isinstance(meta, dict) else ""),
                    "collector_number": col_num,
                    "scryfall_id": item.get("scryfall_id") or (meta.get("id") if isinstance(meta, dict) else None),
                    "quantity": qty,
                    "foil": foil,
                    "condition": item.get("condition") or "Near Mint",
                    "language": item.get("language") or "en",
                    "purchase_price": item.get("purchase_price"),
                    "binder_name": item.get("binder_name") or "",
                    "rarity": item.get("rarity") or (meta.get("rarity", "") if isinstance(meta, dict) else ""),
                    "mana_cost": meta.get("mana_cost") if isinstance(meta, dict) else None,
                    "cmc": float(cmc_val) if cmc_val is not None else 0.0,
                    "type_line": meta.get("type_line") if isinstance(meta, dict) else None,
                    "oracle_text": meta.get("oracle_text") if isinstance(meta, dict) else None,
                    "color_identity": cid_str,
                    "image_uri": (meta.get("image_uri") or meta.get("small_image_uri")) if isinstance(meta, dict) else None,
                    "price_usd": price_usd,
                    "price_usd_foil": price_usd_foil,
                    "created_at": now_dt,
                    "updated_at": now_dt,
                })

            # Fast bulk insert in batches of 1000
            for i in range(0, len(records_to_insert), 1000):
                chunk = records_to_insert[i:i + 1000]
                db.session.bulk_insert_mappings(UserInventoryCard, chunk)
            db.session.commit()
            added_count = len(records_to_insert)

        else:
            # Mode == merge
            existing_cards = UserInventoryCard.query.filter_by(user_id=user_id).all()
            existing_map = {
                self._build_card_key(c.name, c.set_code, c.collector_number, c.foil): c
                for c in existing_cards
            }
            to_insert = []
            for item in parsed_cards:
                name = item["name"]
                name_l = name.lower()
                set_code = item.get("set_code", "").upper()
                col_num = item.get("collector_number", "")
                foil = item.get("foil", "normal").lower()
                qty = max(1, int(item.get("quantity", 1)))
                key = self._build_card_key(name, set_code, col_num, foil)

                if key in existing_map:
                    existing = existing_map[key]
                    existing.quantity += qty
                    existing.updated_at = now_dt
                    updated_count += 1
                else:
                    meta = scryfall_map.get(name_l) or cached_meta.get(name_l, {})
                    if not meta and " // " in name:
                        front = name.split(" // ")[0].lower()
                        meta = scryfall_map.get(front) or cached_meta.get(front, {})

                    prices = meta.get("prices", {}) if isinstance(meta, dict) and "prices" in meta else {}
                    price_usd = meta.get("price_usd") if isinstance(meta, dict) else None
                    price_usd_foil = meta.get("price_usd_foil") if isinstance(meta, dict) else None
                    if price_usd is None and prices.get("usd"):
                        try:
                            price_usd = float(prices["usd"])
                        except Exception:
                            pass
                    if price_usd_foil is None and prices.get("usd_foil"):
                        try:
                            price_usd_foil = float(prices["usd_foil"])
                        except Exception:
                            pass
                    if price_usd is None and item.get("purchase_price") is not None:
                        price_usd = item["purchase_price"]

                    cid = meta.get("color_identity") if isinstance(meta, dict) else None
                    cid_str = ",".join(cid) if isinstance(cid, list) else (cid if isinstance(cid, str) else None)
                    cmc_val = meta.get("cmc") if isinstance(meta, dict) else None

                    to_insert.append({
                        "user_id": user_id,
                        "name": name,
                        "raw_name": item.get("raw_name") or name,
                        "set_code": set_code,
                        "set_name": item.get("set_name") or (meta.get("set_name", "") if isinstance(meta, dict) else ""),
                        "collector_number": col_num,
                        "scryfall_id": item.get("scryfall_id") or (meta.get("id") if isinstance(meta, dict) else None),
                        "quantity": qty,
                        "foil": foil,
                        "condition": item.get("condition") or "Near Mint",
                        "language": item.get("language") or "en",
                        "purchase_price": item.get("purchase_price"),
                        "binder_name": item.get("binder_name") or "",
                        "rarity": item.get("rarity") or (meta.get("rarity", "") if isinstance(meta, dict) else ""),
                        "mana_cost": meta.get("mana_cost") if isinstance(meta, dict) else None,
                        "cmc": float(cmc_val) if cmc_val is not None else 0.0,
                        "type_line": meta.get("type_line") if isinstance(meta, dict) else None,
                        "oracle_text": meta.get("oracle_text") if isinstance(meta, dict) else None,
                        "color_identity": cid_str,
                        "image_uri": (meta.get("image_uri") or meta.get("small_image_uri")) if isinstance(meta, dict) else None,
                        "price_usd": price_usd,
                        "price_usd_foil": price_usd_foil,
                        "created_at": now_dt,
                        "updated_at": now_dt,
                    })
                    added_count += 1

            if to_insert:
                for i in range(0, len(to_insert), 1000):
                    chunk = to_insert[i:i + 1000]
                    db.session.bulk_insert_mappings(UserInventoryCard, chunk)
            db.session.commit()

        # 4. Trigger asynchronous background enrichment for remaining cards lacking images/metadata
        if len(missing_names) > 75:
            self._trigger_background_enrichment(user_id, missing_names[75:])

        # 5. Fast SQL collection telemetry
        telemetry = self.get_collection_telemetry(user_id)
        return {
            "success": True,
            "mode": mode,
            "added_count": added_count,
            "updated_count": updated_count,
            "total_cards": telemetry["total_cards"],
            "unique_cards": telemetry["unique_cards"],
            "total_value": telemetry["total_value"],
        }

    @staticmethod
    def _build_card_key(name: str, set_code: Optional[str], col_num: Optional[str], foil: Optional[str]) -> str:
        """Constructs unique composite key for a specific card printing and finish."""
        c_name = normalize_card_name(name).lower()
        c_set = (set_code or "").strip().lower()
        c_col = (col_num or "").strip().lower()
        c_foil = (foil or "normal").strip().lower()
        return f"{c_name}::{c_set}::{c_col}::{c_foil}"

    @staticmethod
    def get_user_card_allocations(user_id: int, current_deck_id: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
        """
        Scans all saved decks for the user to determine physical card allocations.
        Returns a mapping from lowercase card name (and match keys) to allocation info:
        {
            "card_name_lower": {
                "total_allocated": int,
                "other_allocated": int,  # copies allocated in decks other than current_deck_id
                "decks": [
                    {
                        "deck_id": int,
                        "deck_name": str,
                        "quantity": int,
                        "is_current": bool
                    }, ...
                ]
            }
        }
        """
        allocations: Dict[str, Dict[str, Any]] = {}
        if not user_id:
            return allocations

        decks = DeckAnalysis.query.filter_by(user_id=user_id).all()
        for d in decks:
            cards = d.get_parsed_cards()
            is_current = (current_deck_id is not None and d.id == current_deck_id)
            for c in cards:
                c_name = c.get("name", "").strip()
                if not c_name:
                    continue
                qty = max(1, int(c.get("quantity", 1)))
                match_keys = get_card_match_keys(c_name)

                deck_entry = {
                    "deck_id": d.id,
                    "deck_name": d.deck_name,
                    "quantity": qty,
                    "is_current": is_current,
                }

                for k in match_keys:
                    if k not in allocations:
                        allocations[k] = {
                            "total_allocated": 0,
                            "other_allocated": 0,
                            "decks": [],
                        }

                    allocations[k]["total_allocated"] += qty
                    if not is_current:
                        allocations[k]["other_allocated"] += qty

                    allocations[k]["decks"].append(deck_entry)

        return allocations

    @staticmethod
    def _trigger_background_enrichment(user_id: int, card_names: List[str]):
        """Spawns daemon thread to enrich missing card metadata in batches without blocking response."""
        import threading
        try:
            from flask import current_app
            app = current_app._get_current_object()
        except Exception:
            app = None

        if not app:
            return

        def _enrich_worker():
            with app.app_context():
                try:
                    logger.info(f"Starting background Scryfall enrichment for user {user_id} ({len(card_names)} cards)...")
                    scryfall = ScryfallProvider()
                    found_map, _ = scryfall.get_cards_collection(card_names, fallback_named=False)
                    if not found_map:
                        return

                    updated = 0
                    for name_key, meta in found_map.items():
                        if not meta:
                            continue
                        cards = UserInventoryCard.query.filter(
                            UserInventoryCard.user_id == user_id,
                            func.lower(UserInventoryCard.name) == name_key.lower(),
                        ).all()
                        for c in cards:
                            if not c.image_uri and (meta.get("image_uri") or meta.get("small_image_uri")):
                                c.image_uri = meta.get("image_uri") or meta.get("small_image_uri")
                            if not c.type_line and meta.get("type_line"):
                                c.type_line = meta.get("type_line")
                            if not c.mana_cost and meta.get("mana_cost"):
                                c.mana_cost = meta.get("mana_cost")
                            if (c.cmc is None or c.cmc == 0) and meta.get("cmc") is not None:
                                c.cmc = float(meta["cmc"])
                            if not c.color_identity and meta.get("color_identity"):
                                cid = meta["color_identity"]
                                c.color_identity = ",".join(cid) if isinstance(cid, list) else str(cid)
                            if not c.price_usd and meta.get("prices", {}).get("usd"):
                                try:
                                    c.price_usd = float(meta["prices"]["usd"])
                                except Exception:
                                    pass
                            if not c.price_usd_foil and meta.get("prices", {}).get("usd_foil"):
                                try:
                                    c.price_usd_foil = float(meta["prices"]["usd_foil"])
                                except Exception:
                                    pass
                            updated += 1
                    db.session.commit()
                    logger.info(f"Background Scryfall enrichment completed for user {user_id}: {updated} card records updated.")
                except Exception as ex:
                    logger.error(f"Error in background Scryfall enrichment for user {user_id}: {ex}", exc_info=True)

        t = threading.Thread(target=_enrich_worker, daemon=True)
        t.start()

    def get_collection_telemetry(self, user_id: int) -> Dict[str, Any]:
        """Fast SQL aggregation for collection metrics: total copies, unique cards, total value, foils."""
        if not user_id:
            return {
                "total_cards": 0,
                "unique_cards": 0,
                "total_value": 0.0,
                "foil_count": 0,
            }

        effective_price_sum = func.coalesce(
            func.sum(
                case(
                    (
                        UserInventoryCard.foil.in_(["foil", "etched"]),
                        func.coalesce(UserInventoryCard.price_usd_foil, UserInventoryCard.price_usd, UserInventoryCard.purchase_price, 0.0) * UserInventoryCard.quantity,
                    ),
                    else_=func.coalesce(UserInventoryCard.price_usd, UserInventoryCard.purchase_price, 0.0) * UserInventoryCard.quantity,
                )
            ),
            0.0,
        )

        row = db.session.query(
            func.coalesce(func.sum(UserInventoryCard.quantity), 0).label("total_cards"),
            func.count(UserInventoryCard.id).label("unique_cards"),
            effective_price_sum.label("total_value"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            UserInventoryCard.foil.in_(["foil", "etched"]),
                            UserInventoryCard.quantity,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("foil_count"),
        ).filter(UserInventoryCard.user_id == user_id).first()

        total_cards = int(row.total_cards) if row else 0
        unique_cards = int(row.unique_cards) if row else 0
        total_value = round(float(row.total_value), 2) if row else 0.0
        foil_count = int(row.foil_count) if row else 0

        return {
            "total_cards": total_cards,
            "unique_cards": unique_cards,
            "total_value": total_value,
            "foil_count": foil_count,
        }

    def get_allocated_cards_count(self, user_id: int) -> int:
        """Counts how many inventory cards for this user are currently allocated to any saved deck."""
        if not user_id:
            return 0
        decks = DeckAnalysis.query.filter_by(user_id=user_id).all()
        names = set()
        for d in decks:
            for c in d.get_parsed_cards():
                if isinstance(c, dict):
                    n = c.get("name", "").strip()
                    if n:
                        names.add(n.lower())
                elif isinstance(c, str) and c.strip():
                    names.add(c.strip().lower())
        if not names:
            return 0
        return UserInventoryCard.query.filter(
            UserInventoryCard.user_id == user_id,
            func.lower(UserInventoryCard.name).in_(list(names)),
        ).count()

    def get_paginated_inventory(
        self,
        user_id: int,
        page: int = 1,
        per_page: int = 50,
        q: Optional[str] = None,
        allocation: str = "all",
        foil: str = "all",
        sort: str = "name_asc",
        current_deck_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Server-side paginated, searchable, filtered inventory retrieval.
        Returns a dictionary with:
        - cards: list of serialized dicts for the current page with allocation data
        - total_count: total matching rows for pagination calculations
        - page: current page (1-indexed)
        - per_page: page size
        - total_pages: max(1, ceil(total_count / per_page))
        """
        page = max(1, int(page or 1))
        per_page = max(1, min(200, int(per_page or 50)))

        query = UserInventoryCard.query.filter_by(user_id=user_id)

        # 1. Text Search across name, set_code, type_line, binder_name
        if q and str(q).strip():
            term = f"%{str(q).strip()}%"
            query = query.filter(
                or_(
                    UserInventoryCard.name.ilike(term),
                    UserInventoryCard.set_code.ilike(term),
                    UserInventoryCard.type_line.ilike(term),
                    UserInventoryCard.binder_name.ilike(term),
                )
            )

        # 2. Finish / Foil Filter
        if foil == "normal":
            query = query.filter(UserInventoryCard.foil.notin_(["foil", "etched"]))
        elif foil in ("foil", "etched"):
            query = query.filter(UserInventoryCard.foil.in_(["foil", "etched"]))

        # 3. Allocation Filter
        if allocation in ("allocated", "free"):
            decks = DeckAnalysis.query.filter_by(user_id=user_id).all()
            allocated_names = set()
            for d in decks:
                for c in d.get_parsed_cards():
                    if isinstance(c, dict):
                        n = c.get("name", "").strip()
                        if n:
                            allocated_names.add(n.lower())
                    elif isinstance(c, str) and c.strip():
                        allocated_names.add(c.strip().lower())

            if allocation == "allocated":
                if allocated_names:
                    query = query.filter(func.lower(UserInventoryCard.name).in_(list(allocated_names)))
                else:
                    query = query.filter(db.false())
            elif allocation == "free":
                if allocated_names:
                    query = query.filter(~func.lower(UserInventoryCard.name).in_(list(allocated_names)))

        # 4. Sorting
        effective_price_expr = case(
            (
                UserInventoryCard.foil.in_(["foil", "etched"]),
                func.coalesce(UserInventoryCard.price_usd_foil, UserInventoryCard.price_usd, UserInventoryCard.purchase_price, 0.0),
            ),
            else_=func.coalesce(UserInventoryCard.price_usd, UserInventoryCard.purchase_price, 0.0),
        )

        if sort == "name_asc":
            query = query.order_by(UserInventoryCard.name.asc(), UserInventoryCard.id.asc())
        elif sort == "name_desc":
            query = query.order_by(UserInventoryCard.name.desc(), UserInventoryCard.id.asc())
        elif sort == "price_desc":
            query = query.order_by(effective_price_expr.desc(), UserInventoryCard.name.asc())
        elif sort == "price_asc":
            query = query.order_by(effective_price_expr.asc(), UserInventoryCard.name.asc())
        elif sort == "qty_desc":
            query = query.order_by(UserInventoryCard.quantity.desc(), UserInventoryCard.name.asc())
        elif sort == "qty_asc":
            query = query.order_by(UserInventoryCard.quantity.asc(), UserInventoryCard.name.asc())
        elif sort == "cmc_asc":
            query = query.order_by(UserInventoryCard.cmc.asc(), UserInventoryCard.name.asc())
        elif sort == "cmc_desc":
            query = query.order_by(UserInventoryCard.cmc.desc(), UserInventoryCard.name.asc())
        else:
            query = query.order_by(UserInventoryCard.name.asc(), UserInventoryCard.id.asc())

        total_count = query.count()
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        if page > total_pages:
            page = total_pages

        card_rows = query.offset((page - 1) * per_page).limit(per_page).all()

        # Build allocation mappings only for current deck / user context
        allocations = self.get_user_card_allocations(user_id, current_deck_id=current_deck_id)

        # Precompute total owned copies only for cards on this page
        page_card_names = {c.name for c in card_rows}
        page_match_keys = set()
        for cname in page_card_names:
            page_match_keys.update(get_card_match_keys(cname))

        # Query total owned for these keys
        total_owned_map = {}
        if page_match_keys:
            owned_rows = (
                db.session.query(func.lower(UserInventoryCard.name), func.sum(UserInventoryCard.quantity))
                .filter(UserInventoryCard.user_id == user_id, func.lower(UserInventoryCard.name).in_(list(page_match_keys)))
                .group_by(func.lower(UserInventoryCard.name))
                .all()
            )
            total_owned_map = {row[0]: int(row[1]) for row in owned_rows}

        cards = []
        for c in card_rows:
            c_dict = c.to_dict()
            alloc_info = {"total_allocated": 0, "other_allocated": 0, "decks": []}
            for k in get_card_match_keys(c.name):
                if k in allocations:
                    alloc_info = allocations[k]
                    break

            total_owned_of_name = c.quantity
            for k in get_card_match_keys(c.name):
                if k in total_owned_map:
                    total_owned_of_name = total_owned_map[k]
                    break

            other_allocated = alloc_info.get("other_allocated", 0)
            total_allocated = alloc_info.get("total_allocated", 0)
            available_copies = max(0, total_owned_of_name - other_allocated)

            c_dict["allocated_decks"] = alloc_info.get("decks", [])
            c_dict["total_allocated"] = total_allocated
            c_dict["other_allocated"] = other_allocated
            c_dict["available_copies"] = available_copies
            c_dict["is_allocated"] = (total_allocated > 0)
            c_dict["already_allocated_elsewhere"] = (other_allocated >= total_owned_of_name)

            cards.append(c_dict)

        return {
            "cards": cards,
            "total_count": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }

    def get_inventory_summary(
        self,
        user_id: int,
        current_deck_id: Optional[int] = None,
        include_cards: bool = True,
    ) -> Dict[str, Any]:
        """Calculates total card count, unique cards, total value, and attaches allocation status."""
        if not include_cards:
            telemetry = self.get_collection_telemetry(user_id)
            telemetry["cards"] = []
            return telemetry

        cards = UserInventoryCard.query.filter_by(user_id=user_id).order_by(UserInventoryCard.name.asc()).all()
        allocations = self.get_user_card_allocations(user_id, current_deck_id=current_deck_id)

        total_cards = sum(c.quantity for c in cards)
        unique_cards = len(cards)
        total_value = 0.0
        foil_count = 0

        # Precompute total owned copies per card name across all match keys in O(N)
        total_owned_map: Dict[str, int] = {}
        for item in cards:
            for k in get_card_match_keys(item.name):
                total_owned_map[k] = total_owned_map.get(k, 0) + item.quantity

        card_list = []
        for c in cards:
            c_dict = c.to_dict()
            alloc_info = {"total_allocated": 0, "other_allocated": 0, "decks": []}
            for k in get_card_match_keys(c.name):
                if k in allocations:
                    alloc_info = allocations[k]
                    break

            # Calculate availability in O(1)
            total_owned_of_name = c.quantity
            for k in get_card_match_keys(c.name):
                if k in total_owned_map:
                    total_owned_of_name = total_owned_map[k]
                    break

            other_allocated = alloc_info.get("other_allocated", 0)
            total_allocated = alloc_info.get("total_allocated", 0)
            available_copies = max(0, total_owned_of_name - other_allocated)

            c_dict["allocated_decks"] = alloc_info.get("decks", [])
            c_dict["total_allocated"] = total_allocated
            c_dict["other_allocated"] = other_allocated
            c_dict["available_copies"] = available_copies
            c_dict["is_allocated"] = (total_allocated > 0)
            c_dict["already_allocated_elsewhere"] = (other_allocated >= total_owned_of_name)

            total_value += c_dict["total_value"]
            if c.foil and c.foil.lower() in ("foil", "etched"):
                foil_count += c.quantity

            card_list.append(c_dict)

        return {
            "total_cards": total_cards,
            "unique_cards": unique_cards,
            "total_value": round(total_value, 2),
            "foil_count": foil_count,
            "cards": card_list,
        }
