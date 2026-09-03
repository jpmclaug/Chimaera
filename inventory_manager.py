"""
Inventory Manager module for Chimaera MTG.
Handles persistence, enrichment, batch merging/replacement, and physical cross-deck allocation tracking.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from models import db, UserInventoryCard, DeckAnalysis, utc_now
from providers.scryfall import ScryfallProvider

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
        Persists parsed cards to user's collection.
        mode="replace": Purges user's existing inventory first.
        mode="merge": Accumulates quantity for matching (name, set_code, collector_number, foil).
        """
        if not user_id:
            raise ValueError("user_id is required to import inventory.")

        if not parsed_cards:
            return {
                "imported_count": 0,
                "unique_cards": 0,
                "total_value": 0.0,
                "mode": mode,
            }

        # 1. Batch fetch Scryfall metadata for all unique card names
        unique_names = list({c["name"] for c in parsed_cards if c.get("name")})
        try:
            scryfall_map, _ = self.scryfall_provider.get_cards_collection(unique_names, fallback_named=False)
        except Exception as e:
            logger.error(f"Error fetching Scryfall metadata during inventory import: {e}", exc_info=True)
            scryfall_map = {}

        # 2. Handle replace vs merge
        if mode == "replace":
            UserInventoryCard.query.filter_by(user_id=user_id).delete()
            db.session.flush()
            existing_map = {}
        else:
            # Mode == merge: build key lookup
            existing_cards = UserInventoryCard.query.filter_by(user_id=user_id).all()
            existing_map = {
                self._build_card_key(
                    c.name, c.set_code, c.collector_number, c.foil
                ): c for c in existing_cards
            }

        # 3. Insert or update cards
        added_count = 0
        updated_count = 0

        for item in parsed_cards:
            name = item["name"]
            set_code = item.get("set_code", "").upper()
            col_num = item.get("collector_number", "")
            foil = item.get("foil", "normal").lower()
            qty = max(1, int(item.get("quantity", 1)))
            key = self._build_card_key(name, set_code, col_num, foil)

            meta = scryfall_map.get(name.lower(), {})
            # Also check before // for DFCs
            if not meta and " // " in name:
                meta = scryfall_map.get(name.split(" // ")[0].lower(), {})

            # Resolve prices
            prices = meta.get("prices", {}) if meta else {}
            price_usd = None
            price_usd_foil = None
            try:
                if prices.get("usd"):
                    price_usd = float(prices["usd"])
            except Exception:
                pass
            try:
                if prices.get("usd_foil"):
                    price_usd_foil = float(prices["usd_foil"])
            except Exception:
                pass

            # Fallback to purchase price if Scryfall price not available
            if price_usd is None and item.get("purchase_price") is not None:
                price_usd = item["purchase_price"]

            # Color identity
            cid_list = meta.get("color_identity", []) if meta else []
            cid_str = ",".join(cid_list) if cid_list else None

            # Scryfall ID
            scryfall_id = item.get("scryfall_id") or (meta.get("id") if meta else None)
            img_uri = meta.get("image_uri") or meta.get("small_image_uri") if meta else None
            mana_cost = meta.get("mana_cost") if meta else None
            cmc = meta.get("cmc") if meta else None
            type_line = meta.get("type_line") if meta else None
            oracle_text = meta.get("oracle_text") if meta else None
            rarity = item.get("rarity") or (meta.get("rarity") if meta else "")

            if mode == "merge" and key in existing_map:
                existing = existing_map[key]
                existing.quantity += qty
                existing.updated_at = utc_now()
                # Update price/metadata if previously missing
                if not existing.price_usd and price_usd:
                    existing.price_usd = price_usd
                if not existing.price_usd_foil and price_usd_foil:
                    existing.price_usd_foil = price_usd_foil
                if not existing.image_uri and img_uri:
                    existing.image_uri = img_uri
                updated_count += 1
            else:
                new_card = UserInventoryCard(
                    user_id=user_id,
                    name=name,
                    raw_name=item.get("raw_name") or name,
                    set_code=set_code,
                    set_name=item.get("set_name") or (meta.get("set_name") if meta else ""),
                    collector_number=col_num,
                    scryfall_id=scryfall_id,
                    quantity=qty,
                    foil=foil,
                    condition=item.get("condition") or "Near Mint",
                    language=item.get("language") or "en",
                    purchase_price=item.get("purchase_price"),
                    binder_name=item.get("binder_name") or "",
                    rarity=rarity,
                    mana_cost=mana_cost,
                    cmc=float(cmc) if cmc is not None else 0.0,
                    type_line=type_line,
                    oracle_text=oracle_text,
                    color_identity=cid_str,
                    image_uri=img_uri,
                    price_usd=price_usd,
                    price_usd_foil=price_usd_foil,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                db.session.add(new_card)
                if mode == "merge":
                    existing_map[key] = new_card
                added_count += 1

        db.session.commit()

        # Compute summary
        summary = self.get_inventory_summary(user_id)
        return {
            "success": True,
            "mode": mode,
            "added_count": added_count,
            "updated_count": updated_count,
            "total_cards": summary["total_cards"],
            "unique_cards": summary["unique_cards"],
            "total_value": summary["total_value"],
        }

    @staticmethod
    def _build_card_key(name: str, set_code: Optional[str], col_num: Optional[str], foil: Optional[str]) -> str:
        """Constructs unique composite key for a specific card printing and finish."""
        c_name = (name or "").strip().lower()
        c_set = (set_code or "").strip().lower()
        c_col = (col_num or "").strip().lower()
        c_foil = (foil or "normal").strip().lower()
        return f"{c_name}::{c_set}::{c_col}::{c_foil}"

    @staticmethod
    def get_user_card_allocations(user_id: int, current_deck_id: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
        """
        Scans all saved decks for the user to determine physical card allocations.
        Returns a mapping from lowercase card name to allocation info:
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
                c_name = c.get("name", "").strip().lower()
                if not c_name:
                    continue
                qty = max(1, int(c.get("quantity", 1)))

                if c_name not in allocations:
                    allocations[c_name] = {
                        "total_allocated": 0,
                        "other_allocated": 0,
                        "decks": [],
                    }

                allocations[c_name]["total_allocated"] += qty
                if not is_current:
                    allocations[c_name]["other_allocated"] += qty

                allocations[c_name]["decks"].append({
                    "deck_id": d.id,
                    "deck_name": d.deck_name,
                    "quantity": qty,
                    "is_current": is_current,
                })

                # Also map front face of DFC
                if " // " in c_name:
                    front_name = c_name.split(" // ")[0].strip()
                    if front_name not in allocations:
                        allocations[front_name] = {
                            "total_allocated": 0,
                            "other_allocated": 0,
                            "decks": [],
                        }
                    allocations[front_name]["total_allocated"] += qty
                    if not is_current:
                        allocations[front_name]["other_allocated"] += qty

        return allocations

    def get_inventory_summary(self, user_id: int, current_deck_id: Optional[int] = None) -> Dict[str, Any]:
        """Calculates total card count, unique cards, total value, and attaches allocation status."""
        cards = UserInventoryCard.query.filter_by(user_id=user_id).order_by(UserInventoryCard.name.asc()).all()
        allocations = self.get_user_card_allocations(user_id, current_deck_id=current_deck_id)

        total_cards = sum(c.quantity for c in cards)
        unique_cards = len(cards)
        total_value = 0.0
        foil_count = 0

        card_list = []
        for c in cards:
            c_dict = c.to_dict()
            name_lower = c.name.lower()
            alloc_info = allocations.get(name_lower, {"total_allocated": 0, "other_allocated": 0, "decks": []})

            # Calculate availability
            total_owned_of_name = sum(item.quantity for item in cards if item.name.lower() == name_lower)
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
