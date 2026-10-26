"""
Playtest Engine for Commander Playtest Arena.
Handles deck hydration, cryptographic shuffling, unique instance ID assignment,
state schema validation, and session restart.
"""

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Optional, Any
from models import db, DeckAnalysis, CommanderPlaytestSession, utc_now


def _gen_instance_id(seat: int, index: int) -> str:
    """Generates a collision-resistant unique instance ID for a card copy."""
    rand_suffix = secrets.token_hex(4)
    return f"inst_{seat}_{index}_{rand_suffix}"


def _normalize_ref_key(name: str) -> str:
    """Normalizes card name to a consistent lowercase dictionary key."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _extract_keywords(oracle_text: str, type_line: str) -> list[str]:
    """Extracts common combat-relevant MTG keywords from card metadata."""
    keywords_set = set()
    text = (oracle_text or "") + " " + (type_line or "")
    common_keywords = [
        "Flying", "Reach", "First strike", "Double strike", "Deathtouch",
        "Lifelink", "Trample", "Vigilance", "Haste", "Menace", "Hexproof",
        "Indestructible", "Infect", "Toxic", "Defender", "Flash", "Ward"
    ]
    for kw in common_keywords:
        # Match keyword as standalone word or at start of line
        pattern = rf"(?i)(?:^|[\n\r,•;]|\b){re.escape(kw)}(?:[\n\r,•;]|\b)"
        if re.search(pattern, text):
            keywords_set.add(kw)
    return sorted(list(keywords_set))


def _extract_pt(card_dict: dict) -> tuple[Optional[str], Optional[str]]:
    """Extracts power and toughness strings from card data or oracle text fallback."""
    p = card_dict.get("power")
    t = card_dict.get("toughness")
    if p is not None and t is not None:
        return str(p), str(t)

    # Check card_faces if split/DFC
    faces = card_dict.get("card_faces") or []
    if faces and isinstance(faces, list) and isinstance(faces[0], dict):
        fp = faces[0].get("power")
        ft = faces[0].get("toughness")
        if fp is not None and ft is not None:
            return str(fp), str(ft)

    return None, None


class PlaytestEngine:
    """Server-side coordinator for Commander Playtest Sessions."""

    @staticmethod
    def build_session(
        user_id: int,
        deck_ids: list[int],
        title: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """
        Hydrates uploaded decks into a full multi-seat playtest game state.
        Deals 7 cards, routes commanders to command zone, shuffles libraries.
        """
        if not deck_ids or len(deck_ids) < 1 or len(deck_ids) > 4:
            raise ValueError("Playtest session requires between 1 and 4 deck IDs.")

        settings = settings or {}
        starting_life = int(settings.get("starting_life", 40))
        hide_hands = bool(settings.get("hide_hands", True))
        follow_active = bool(settings.get("follow_active_player", True))
        auto_untap_draw = bool(settings.get("auto_untap_draw", True))

        seats = []
        cards_pool = {}
        card_refs = {}
        rand = secrets.SystemRandom()

        deck_records = []
        for d_id in deck_ids:
            deck = DeckAnalysis.query.filter_by(id=d_id).first()
            if not deck:
                raise ValueError(f"Deck ID {d_id} not found.")
            deck_records.append(deck)

        session_title = title.strip() if title and title.strip() else (
            f"{deck_records[0].deck_name} (Solo Goldfish)" if len(deck_records) == 1
            else " vs ".join(d.deck_name[:15] for d in deck_records)
        )

        for seat_idx, deck in enumerate(deck_records):
            parsed_cards = deck.get_parsed_cards()
            if not parsed_cards:
                raise ValueError(f"Deck '{deck.deck_name}' contains no parsed card data.")

            cmdr_names = [c.strip() for c in (deck.commander_name or "").split(",") if c.strip()]
            cmdr_names_norm = [c.lower() for c in cmdr_names]

            library_instances = []
            command_instances = []
            instance_counter = 0

            # Process cards
            for card in parsed_cards:
                raw_name = card.get("name") or "Unknown Card"
                clean_name = raw_name.strip()
                ref_key = _normalize_ref_key(clean_name)
                qty = max(1, int(card.get("quantity") or 1))

                # Populate card_refs if not already present
                if ref_key not in card_refs:
                    power, toughness = _extract_pt(card)
                    oracle_text = card.get("oracle_text") or ""
                    type_line = card.get("type_line") or ""
                    keywords = card.get("keywords") or _extract_keywords(oracle_text, type_line)

                    card_refs[ref_key] = {
                        "name": clean_name,
                        "type_line": type_line,
                        "mana_cost": card.get("mana_cost") or "",
                        "cmc": float(card.get("cmc") or 0.0),
                        "power": power,
                        "toughness": toughness,
                        "keywords": keywords,
                        "image_uri": card.get("image_uri") or card.get("small_image_uri") or "",
                        "small_image_uri": card.get("small_image_uri") or card.get("image_uri") or "",
                        "art_crop_uri": card.get("art_crop_uri") or "",
                        "oracle_text": oracle_text,
                        "produced_mana": card.get("produced_mana") or [],
                        "card_faces": card.get("card_faces") or [],
                        "rarity": card.get("rarity") or "",
                        "colors": card.get("colors") or [],
                    }

                # Determine if commander
                is_cmdr = bool(
                    card.get("section") == "commander" or
                    ref_key in cmdr_names_norm or
                    any(cn in ref_key for cn in cmdr_names_norm if len(cn) > 3)
                )

                if is_cmdr:
                    # Commanders are single copies in the Command Zone
                    inst_id = _gen_instance_id(seat_idx, instance_counter)
                    instance_counter += 1
                    command_instances.append(inst_id)
                    cards_pool[inst_id] = {
                        "card_ref": ref_key,
                        "tapped": False,
                        "counters": {},
                        "pt_mod": [0, 0],
                        "face": 0,
                        "token": False,
                        "summoning_sick": False,
                        "is_commander": True,
                    }
                else:
                    # Mainboard cards expanded by quantity
                    for _ in range(qty):
                        inst_id = _gen_instance_id(seat_idx, instance_counter)
                        instance_counter += 1
                        library_instances.append(inst_id)
                        cards_pool[inst_id] = {
                            "card_ref": ref_key,
                            "tapped": False,
                            "counters": {},
                            "pt_mod": [0, 0],
                            "face": 0,
                            "token": False,
                            "summoning_sick": False,
                            "is_commander": False,
                        }

            # Fallback: if no commander was marked in cards, create one from deck record
            if not command_instances and cmdr_names:
                for c_name in cmdr_names:
                    ref_key = _normalize_ref_key(c_name)
                    if ref_key not in card_refs:
                        card_refs[ref_key] = {
                            "name": c_name,
                            "type_line": "Legendary Creature",
                            "mana_cost": "",
                            "cmc": 0,
                            "power": None,
                            "toughness": None,
                            "keywords": [],
                            "image_uri": deck.commander_art or "",
                            "small_image_uri": deck.commander_art or "",
                            "art_crop_uri": deck.commander_art or "",
                            "oracle_text": "",
                            "produced_mana": [],
                            "card_faces": [],
                            "rarity": "mythic",
                            "colors": [],
                        }
                    inst_id = _gen_instance_id(seat_idx, instance_counter)
                    instance_counter += 1
                    command_instances.append(inst_id)
                    cards_pool[inst_id] = {
                        "card_ref": ref_key,
                        "tapped": False,
                        "counters": {},
                        "pt_mod": [0, 0],
                        "face": 0,
                        "token": False,
                        "summoning_sick": False,
                        "is_commander": True,
                    }

            # Cryptographic shuffle of library
            rand.shuffle(library_instances)

            # Deal opening hand of 7 cards
            opening_hand = library_instances[:7]
            remaining_library = library_instances[7:]

            # Initial commander damage tracker: records damage taken from each seat
            cmdr_dmg_map = {str(other_seat): 0 for other_seat in range(len(deck_records))}

            seats.append({
                "seat": seat_idx,
                "deck_id": deck.id,
                "name": f"Player {seat_idx + 1} ({deck.deck_name})",
                "commander_name": deck.commander_name or "Commander",
                "commander_art": deck.commander_art or "",
                "life": starting_life,
                "poison": 0,
                "commander_damage": cmdr_dmg_map,
                "commander_casts": {inst_id: 0 for inst_id in command_instances},
                "mulligan": {
                    "count": 0,
                    "kept": False,
                    "hand_size": 7,
                },
                "zones": {
                    "library": remaining_library,
                    "hand": opening_hand,
                    "battlefield": [],
                    "command": command_instances,
                    "graveyard": [],
                    "exile": [],
                },
            })

        initial_state = {
            "schema": 1,
            "turn": 1,
            "phase": "main1",
            "active_seat": 0,
            "viewing_seat": 0,
            "settings": {
                "hide_hands": hide_hands,
                "follow_active_player": follow_active,
                "auto_untap_draw": auto_untap_draw,
                "starting_life": starting_life,
            },
            "combat": None,
            "seats": seats,
            "cards": cards_pool,
            "card_refs": card_refs,
            "log": [{
                "t": utc_now().isoformat(),
                "seat": 0,
                "msg": f"Session initialized with {len(deck_records)} player(s). Ready for opening hand."
            }],
        }

        return {
            "title": session_title,
            "deck_ids": deck_ids,
            "num_players": len(deck_records),
            "state": initial_state,
        }

    @staticmethod
    def validate_state(state: dict) -> tuple[bool, str]:
        """
        Validates state payload for schema conformance, size, and instance ID integrity.
        Returns (is_valid, error_message).
        """
        if not isinstance(state, dict):
            return False, "State payload must be a JSON object."

        schema = state.get("schema")
        if schema != 1:
            return False, f"Unsupported state schema version: {schema}."

        seats = state.get("seats")
        if not isinstance(seats, list) or len(seats) < 1 or len(seats) > 4:
            return False, "Seats must be a list containing 1 to 4 players."

        cards = state.get("cards")
        if not isinstance(cards, dict):
            return False, "Cards pool must be a dictionary."

        card_refs = state.get("card_refs")
        if not isinstance(card_refs, dict):
            return False, "Card references must be a dictionary."

        # Payload size cap: 2 MB
        try:
            dumped = json.dumps(state)
            if len(dumped) > 2 * 1024 * 1024:
                return False, "State payload exceeds maximum 2 MB limit."
        except Exception as e:
            return False, f"Failed to serialize state: {str(e)}"

        # Validate that every card in zones exists in cards dictionary
        for s in seats:
            zones = s.get("zones") or {}
            for zone_name, inst_list in zones.items():
                if not isinstance(inst_list, list):
                    return False, f"Zone '{zone_name}' in seat {s.get('seat')} must be a list."
                for inst_id in inst_list:
                    if inst_id not in cards:
                        return False, f"Instance ID '{inst_id}' in zone '{zone_name}' is not in cards pool."

        return True, ""

    @staticmethod
    def restart_session(session: CommanderPlaytestSession) -> dict:
        """
        Restarts an existing session using the original decks:
        reshuffles, resets life totals and commanders, deals 7 new cards.
        """
        deck_ids = session.get_deck_ids()
        current_state = session.get_state()
        settings = current_state.get("settings", {})
        title = session.title

        built = PlaytestEngine.build_session(
            user_id=session.user_id,
            deck_ids=deck_ids,
            title=title,
            settings=settings,
        )

        session.state_json = json.dumps(built["state"])
        session.state_version += 1
        session.turn_number = 1
        session.status = "active"
        session.updated_at = utc_now()
        db.session.commit()

        return session.to_dict()
