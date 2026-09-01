"""
Card classification engine for Magic: The Gathering cards.
Analyzes enriched card metadata from Scryfall to assign functional roles:
- Ramp (Fast: CMC <= 2, Standard: CMC >= 3)
- Targeted Removal & Board Wipes
- Draw Actions (Engine, Cantrip, Burst)
- Tutors (General, Land)
- Taplands
"""

import re
from typing import Dict, Any, Tuple


class MTGCardClassifier:
    """Classifies MTG cards into functional EDH archetypes and roles based on oracle text, types, and CMC."""

    def __init__(self):
        # 1. Ramp Patterns
        self.re_mana_dork_rock = re.compile(
            r"(?:add\s+(?:\{[WUBRGC0-9X\/\s]+\}|one\s+mana|mana\s+of\s+any|two\s+mana))",
            re.IGNORECASE,
        )
        self.re_land_fetch = re.compile(
            r"search your library for.*?(?:basic\s+)?(?:forest|plains|island|swamp|mountain|land).*?(?:onto the battlefield|into your hand|tapped|revealed)",
            re.IGNORECASE,
        )
        self.re_treasure = re.compile(
            r"create\s+(?:a|two|three|\d+|X)?\s*Treasure\s+tokens?",
            re.IGNORECASE,
        )

        # 2. Targeted Removal Patterns
        self.re_targeted_removal = re.compile(
            r"\b(destroy|exile|counter|return|shuffle)\s+target\b|"
            r"\bowner of target\b|"
            r"\bdeals?\s+(\d+|X|that much)\s+damage to target\b|"
            r"\bdeals?\s+(\d+|X|that much)\s+damage divided.*?(?:among|targets)\b|"
            r"target creature you control fights target|"
            r"deals damage equal to its power to target|"
            r"target permanent's owner shuffles",
            re.IGNORECASE,
        )

        # 3. Board Wipe Patterns
        self.re_board_wipe = re.compile(
            r"\b(destroy|exile)\s+all\b|"
            r"\b(all|each)\s+(creatures?|permanents?|nonland permanents?)\s+gets?\s+-[X\d]+/-[X\d]+|"
            r"deals\s+(\d+|X)\s+damage to each (creature|player and each creature|nonflying creature)|"
            r"return all (creatures|nonland permanents|permanents) to their owners' hands|"
            r"return each nonland permanent you don't control|"
            r"each player sacrifices (all|\d+|X)\b",
            re.IGNORECASE,
        )

        # 4. Draw Patterns
        self.re_draw_action = re.compile(
            r"\bdraws?\s+(a|\d+|two|three|four|five|X|that many|two additional|an additional|\d+ additional)?\s*cards?\b",
            re.IGNORECASE,
        )
        self.re_draw_engine = re.compile(
            r"\b(whenever|at the beginning of|as long as|when)\b.*?\bdraws?\s+(a|\d+|two|three|X|two additional|an additional)?\s*cards?\b",
            re.IGNORECASE,
        )
        self.re_cantrip = re.compile(
            r"^draw a card\.$|\.\s*Draw a card\.|draw a card",
            re.IGNORECASE,
        )

        # 5. Tutor Patterns
        self.re_tutor_general = re.compile(
            r"search your library for (?:a|up to \d+)?\s*(?:card|instant|sorcery|creature|artifact|enchantment|planeswalker|permanent)\b",
            re.IGNORECASE,
        )

    def extract_text_and_types(self, card_data: Dict[str, Any]) -> Tuple[str, str, float]:
        """
        Extracts concatenated oracle text, type line, and CMC from card data.
        Handles Modal Double-Faced Cards (MDFCs), split cards, and adventure cards.
        """
        if "card_faces" in card_data and card_data["card_faces"]:
            combined_text = " // ".join([face.get("oracle_text", "") for face in card_data["card_faces"]])
            type_line = card_data.get("type_line") or " // ".join([face.get("type_line", "") for face in card_data["card_faces"]])
            cmc = float(card_data.get("cmc", 0.0))
            return combined_text, type_line, cmc
        return card_data.get("oracle_text", ""), card_data.get("type_line", ""), float(card_data.get("cmc", 0.0))

    def classify(self, card_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Classifies an individual MTG card into roles.
        """
        oracle_text, type_line, cmc = self.extract_text_and_types(card_data)
        is_land = "Land" in type_line
        is_permanent = any(t in type_line for t in ["Creature", "Artifact", "Enchantment", "Planeswalker", "Battle"])

        result = {
            "is_ramp": False,
            "ramp_tier": None,  # "fast" (CMC <= 2) or "standard" (CMC >= 3)
            "is_targeted_removal": False,
            "is_board_wipe": False,
            "is_draw": False,
            "draw_type": None,  # "engine", "cantrip", "burst"
            "is_tutor": False,
            "tutor_type": None,  # "general", "land"
            "is_tapland": False,
        }

        # Tapland check (unconditional taplands vs shock/check/reveal lands with untap conditions)
        if is_land:
            oracle_lower = oracle_text.lower()
            if "enters the battlefield tapped" in oracle_lower and not any(
                cond in oracle_lower for cond in ["unless", "if you control", "as long as", "you may pay", "if you don't", "reveal a"]
            ):
                result["is_tapland"] = True

        # Ramp check (non-lands only - basic and utility lands tapping for mana are not nonland ramp)
        if not is_land:
            if self.re_mana_dork_rock.search(oracle_text) or self.re_land_fetch.search(oracle_text) or self.re_treasure.search(oracle_text):
                result["is_ramp"] = True
                result["ramp_tier"] = "fast" if cmc <= 2.0 else "standard"

        # Removal check (Check board wipe vs targeted removal)
        if self.re_board_wipe.search(oracle_text):
            result["is_board_wipe"] = True
        if self.re_targeted_removal.search(oracle_text):
            result["is_targeted_removal"] = True

        # Draw check
        if self.re_draw_action.search(oracle_text):
            result["is_draw"] = True
            if is_permanent and self.re_draw_engine.search(oracle_text):
                result["draw_type"] = "engine"
            elif re.search(r"\bdraws?\s+(two|three|four|five|[2-9]|\d{2,}|X|that many|two additional|three additional)\s+cards?\b", oracle_text, re.IGNORECASE):
                result["draw_type"] = "burst"
            else:
                result["draw_type"] = "cantrip"

        # Tutor check
        if self.re_land_fetch.search(oracle_text):
            result["is_tutor"] = True
            result["tutor_type"] = "land"
        elif self.re_tutor_general.search(oracle_text):
            result["is_tutor"] = True
            result["tutor_type"] = "general"

        return result
