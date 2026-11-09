"""
Card Add Evaluator & Tactical Add Analysis Suite for Chimaera MTG.
Evaluates single cards, batch card lists, and Secret Lair drops against individual
Commander decks or the user's entire Commander fleet.
Cross-references color identity rules, Commander/Pauper banlists, EDHREC synergies,
deck deficits, Timewalked ratio targets, Commander Spellbook combos, and WotC brackets
for tactical rationales and cut recommendations.
"""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from card_classifier import MTGCardClassifier
from card_utils import (
    card_names_match,
    fix_mojibake,
    get_card_match_keys,
    normalize_card_name,
    strip_accents,
)
from deck_parser import DeckParser
from deck_upgrade_engine import (
    COMMANDER_BANNED_CARDS,
    CURATED_PAUPER_UPGRADES,
    CURATED_UPGRADES,
    PAUPER_COMMANDER_BANNED_CARDS,
    DualTierUpgradeEngine,
)
from gemini_analyzer import GEMINI_API_BASE, get_model_for_task
from providers.edhrec import EDHRECProvider
from providers.scryfall import ScryfallProvider
from secret_lair_advisor import (
    SecretLairFinancialEvaluator,
    SecretLairScraper,
)
from tcgplayer_parser import TCGPlayerPurchaseParser
from timewalked_analyzer import (
    BRACKET_NAMES as TIMEWALKED_BRACKET_NAMES,
    DEFAULT_RATIO_TARGETS as TIMEWALKED_RATIO_TARGETS,
    DeckClassifier as TimewalkedDeckClassifier,
    GAME_CHANGERS as TIMEWALKED_GAME_CHANGERS,
    MASS_LAND_DENIAL as TIMEWALKED_MASS_LAND_DENIAL,
    STRONG_STAPLES as TIMEWALKED_STRONG_STAPLES,
    classify_timewalked_categories,
    compute_ratio_breakdown,
)
from timewalked_analyzer.api_clients import SpellbookClient
from timewalked_analyzer.card_db import classify_card_roles as timewalked_classify_card_roles

logger = logging.getLogger(__name__)

_LOWER_GAME_CHANGERS = {k.strip().lower() for k in TIMEWALKED_GAME_CHANGERS}
_LOWER_MASS_LAND_DENIAL = {k.strip().lower() for k in TIMEWALKED_MASS_LAND_DENIAL}
_LOWER_STRONG_STAPLES = {k.strip().lower() for k in TIMEWALKED_STRONG_STAPLES}

# Lazily-built {lowercase name: staple} indices for O(1) curated staple lookups.
_STAPLE_INDEX: dict[bool, dict[str, dict]] = {}

# Max non-basic cut candidates per deck sent to Gemini (keeps prompt small).
GEMINI_CUT_POOL_SIZE = 12


def _staple_index(is_pauper: bool) -> dict[str, dict]:
    idx = _STAPLE_INDEX.get(is_pauper)
    if idx is None:
        idx = {}
        for s in (CURATED_PAUPER_UPGRADES if is_pauper else CURATED_UPGRADES):
            idx.setdefault(s["name"].lower(), s)  # first match wins (same as prior next())
        _STAPLE_INDEX[is_pauper] = idx
    return idx


def _is_pauper_deck(deck: dict) -> bool:
    return bool(deck.get("is_pauper") or str(deck.get("deck_format", "")).lower() == "pauper_commander")


class CardAddEvaluator:
    """
    Evaluates candidate cards (single, bulk, or Secret Lair drops) against
    Commander decks to determine optimal fit, synergy ratings, and cuts.
    Powered by the Timewalked methodology (EDHREC inclusion/synergy, Commander
    Spellbook combos, 9-category deck ratios, WotC bracket guardrails, and data-driven cuts).
    """

    def __init__(
        self,
        scryfall_provider: Optional[ScryfallProvider] = None,
        edhrec_provider: Optional[EDHRECProvider] = None,
        spellbook_client: Optional[SpellbookClient] = None,
    ):
        self.scryfall = scryfall_provider or ScryfallProvider()
        self.edhrec = edhrec_provider or EDHRECProvider()
        self.spellbook = spellbook_client or SpellbookClient()
        self.classifier = MTGCardClassifier()
        self.upgrade_engine = DualTierUpgradeEngine(scryfall_provider=self.scryfall)
        self.scraper = SecretLairScraper()
        self.financial_evaluator = SecretLairFinancialEvaluator(self.scryfall)

    # -------------------------------------------------------------------------
    # Input Parsing & Extraction
    # -------------------------------------------------------------------------

    def parse_card_input(self, raw_input: str | list[str]) -> list[dict]:
        """
        Parses raw text, CSV, semicolon/newline separated cards, or a Python list.
        Handles MTG formats like '1x Sol Ring', '1 Rhystic Study', 'Cyclonic Rift (RTR) 35'.
        Returns normalized list of dicts:
        [{"name": str, "quantity": int, "raw_line": str}]
        """
        if not raw_input:
            return []

        lines: list[str] = []
        if isinstance(raw_input, list):
            for item in raw_input:
                if isinstance(item, str):
                    lines.extend(item.splitlines())
                elif isinstance(item, dict) and item.get("name"):
                    lines.append(item["name"])
        else:
            norm = str(raw_input).replace("\r\n", "\n").replace("\r", "\n")
            for line in norm.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Support semicolon-separated names on one line
                if ";" in line and not line.startswith("http"):
                    parts = line.split(";")
                    for p in parts:
                        if p.strip():
                            lines.append(p.strip())
                else:
                    lines.append(line)

        cards: list[dict] = []
        seen_names: set[str] = set()

        for line in lines:
            line_str = fix_mojibake(line.strip())
            if not line_str or line_str.startswith("#") or line_str.startswith("//"):
                continue

            # Check if CSV row (e.g. ManaBox CSV: "Name,Quantity,Binder Name...")
            if "," in line_str and not line_str.startswith('"') and ("name" in line_str.lower() or "quantity" in line_str.lower()):
                continue  # Header row

            # Check if TCGplayer purchase manifest header or row
            if line_str.lower().startswith("qty\tdescription") or line_str.lower().startswith("quantity\t"):
                continue
            if TCGPlayerPurchaseParser.is_likely_tcgplayer_manifest(line_str):
                tcg_item = TCGPlayerPurchaseParser.parse_line(line_str)
                if tcg_item and tcg_item.card_name:
                    canonical = DeckParser._clean_card_name(tcg_item.card_name)
                    match_key = canonical.lower()
                    if match_key in seen_names:
                        for c in cards:
                            if c["name"].lower() == match_key:
                                c["quantity"] += tcg_item.quantity
                                break
                    else:
                        seen_names.add(match_key)
                        cards.append({
                            "name": canonical,
                            "quantity": tcg_item.quantity,
                            "raw_line": line_str,
                        })
                    continue

            qty = 1
            clean_name = line_str

            # Parse quantity prefix like '1x ', '2 ', '4X '
            qty_match = re.match(r"^(\d+)\s*[xX]?\s+(.+)$", line_str)
            if qty_match:
                try:
                    qty = int(qty_match.group(1))
                    clean_name = qty_match.group(2).strip()
                except Exception:
                    qty = 1

            # Strip set code/collector number suffixes like (M21) 123 or [CMR]
            clean_name = re.sub(r"\s*[\(\[][A-Za-z0-9_]{3,6}[\)\]]\s*[\w\d]*.*$", "", clean_name).strip()
            # Strip trailing collector number e.g. #123
            clean_name = re.sub(r"\s*#\d+.*$", "", clean_name).strip()
            # Strip quotes
            clean_name = clean_name.strip("\"'").strip()

            if not clean_name:
                continue

            # Canonical clean card name
            canonical = DeckParser._clean_card_name(clean_name)
            match_key = canonical.lower()

            if match_key in seen_names:
                # Update quantity for existing entry
                for c in cards:
                    if c["name"].lower() == match_key:
                        c["quantity"] += qty
                        break
            else:
                seen_names.add(match_key)
                cards.append({
                    "name": canonical,
                    "quantity": qty,
                    "raw_line": line_str,
                })

        return cards

    # -------------------------------------------------------------------------
    # Scryfall Enrichment
    # -------------------------------------------------------------------------

    def enrich_cards(self, cards: list[dict]) -> list[dict]:
        """
        Batches unique card names to Scryfall for live market pricing,
        colors, color identity, CMC, mana cost, oracle text, legalities, and art.
        """
        if not cards:
            return []

        unique_names = list({c["name"] for c in cards if c.get("name")})
        # Disable the built-in named fallback so each unresolved name is looked up exactly once below
        # (previously up to 20 names were re-queried twice).
        scryfall_map, not_found = self.scryfall.get_cards_collection(unique_names, fallback_named=False)

        # For any not resolved via collection batch, try single named lookup
        for name in dict.fromkeys(not_found):
            single = self.scryfall.get_card_named(name)
            if single:
                self.scryfall._index_card_in_map(scryfall_map, single, extra_names=[name])

        enriched: list[dict] = []
        for c in cards:
            c_name = c["name"]
            meta = scryfall_map.get(c_name.lower())
            if not meta:
                clean_unaccent = strip_accents(c_name).lower()
                meta = scryfall_map.get(clean_unaccent)
            if not meta and " // " in c_name:
                meta = scryfall_map.get(c_name.split(" // ")[0].strip().lower())

            meta = meta or {}

            prices = meta.get("prices", {})
            p_usd = prices.get("usd")
            p_usd_foil = prices.get("usd_foil") or p_usd
            val_nonfoil = float(p_usd) if p_usd is not None else 0.0
            val_foil = float(p_usd_foil) if p_usd_foil is not None else val_nonfoil

            if val_nonfoil == 0.0 and meta:
                try:
                    cheap = self.scryfall.get_cheapest_tcgplayer_price(c_name)
                    if cheap and cheap.get("price", 0) > 0:
                        val_nonfoil = float(cheap["price"])
                        val_foil = val_nonfoil
                except Exception:
                    pass

            item = dict(c)
            item.update({
                "canonical_name": meta.get("name") or c_name,
                "scryfall_id": meta.get("id"),
                "mana_cost": meta.get("mana_cost", ""),
                "cmc": meta.get("cmc", 0.0),
                "type_line": meta.get("type_line", "Unknown"),
                "colors": meta.get("colors", []),
                "color_identity": meta.get("color_identity", []),
                "oracle_text": meta.get("oracle_text", ""),
                "rarity": meta.get("rarity", ""),
                "set_code": meta.get("set_code", ""),
                "set_name": meta.get("set_name", ""),
                "collector_number": meta.get("collector_number", ""),
                "image_uri": meta.get("image_uri") or meta.get("small_image_uri") or "",
                "small_image_uri": meta.get("small_image_uri") or meta.get("image_uri") or "",
                "art_crop_uri": meta.get("art_crop_uri") or meta.get("image_uri") or "",
                "price_usd": round(val_nonfoil, 2) if val_nonfoil > 0 else None,
                "price_usd_foil": round(val_foil, 2) if val_foil > 0 else None,
                "tcgplayer_url": meta.get("tcgplayer_url", ""),
                "legalities": meta.get("legalities", {}),
            })
            enriched.append(item)

        return enriched

    # -------------------------------------------------------------------------
    # Legality, Duplicate & Compatibility Checks
    # -------------------------------------------------------------------------

    @staticmethod
    def normalize_color_identity(ci_raw: Any) -> set[str]:
        """Normalizes color identity input into a set of single uppercase letters {'W','U','B','R','G'}."""
        if not ci_raw:
            return set()
        if isinstance(ci_raw, (list, tuple, set)):
            res = set()
            for x in ci_raw:
                s = str(x).strip().upper()
                for ch in s:
                    if ch in ("W", "U", "B", "R", "G"):
                        res.add(ch)
            return res
        if isinstance(ci_raw, str):
            res = set()
            for ch in ci_raw.upper():
                if ch in ("W", "U", "B", "R", "G"):
                    res.add(ch)
            return res
        return set()

    def check_card_deck_compatibility(
        self,
        card_meta: dict,
        deck: dict,
        deck_cards_set: Optional[set[str]] = None,
        deck_ci: Optional[set[str]] = None,
    ) -> dict:
        """
        Evaluates color legality, format legality, and duplicate presence
        for a card against a specific Commander deck.
        """
        card_name = card_meta.get("canonical_name") or card_meta.get("name", "")
        clean_name = strip_accents(card_name).lower().strip()
        clean_front = clean_name.split(" // ")[0].strip() if " // " in clean_name else clean_name

        # 1. Color Identity check
        if deck_ci is None:
            deck_ci = self.normalize_color_identity(deck.get("color_identity"))
        card_ci = self.normalize_color_identity(card_meta.get("color_identity"))
        is_color_legal = card_ci.issubset(deck_ci)

        illegal_colors = sorted(list(card_ci - deck_ci))

        # 2. Format Legality check
        is_pauper = _is_pauper_deck(deck)
        is_format_banned = False
        ban_reason = None

        if clean_name in COMMANDER_BANNED_CARDS or clean_front in COMMANDER_BANNED_CARDS:
            is_format_banned = True
            ban_reason = "Banned in Commander (EDH)"
        elif is_pauper and (clean_name in PAUPER_COMMANDER_BANNED_CARDS or clean_front in PAUPER_COMMANDER_BANNED_CARDS):
            is_format_banned = True
            ban_reason = "Banned in Pauper Commander (PDH)"
        elif is_pauper and not ScryfallProvider.is_pauper_legal(card_meta):
            is_format_banned = True
            ban_reason = "Not legal in Pauper Commander (non-common printing)"

        # 3. Duplicate check (already in deck)
        if deck_cards_set is None:
            deck_cards_set = set()
            for dc in deck.get("cards", []):
                dc_name = dc.get("name", "").strip().lower()
                if dc_name:
                    deck_cards_set.add(dc_name)
                    if " // " in dc_name:
                        deck_cards_set.add(dc_name.split(" // ")[0].strip())

        type_line = (card_meta.get("type_line") or "").lower()
        is_basic_land = "basic" in type_line and "land" in type_line
        is_already_in_deck = (not is_basic_land) and (clean_name in deck_cards_set or clean_front in deck_cards_set)

        # Legality verdict
        is_legal = is_color_legal and not is_format_banned

        status_flags = []
        if not is_color_legal:
            status_flags.append(f"Illegal Colors: [{','.join(illegal_colors)}]")
        if is_format_banned:
            status_flags.append(ban_reason or "Format Banned")
        if is_already_in_deck:
            status_flags.append("Already in Deck")

        return {
            "is_legal": is_legal,
            "is_color_legal": is_color_legal,
            "illegal_colors": illegal_colors,
            "is_format_banned": is_format_banned,
            "ban_reason": ban_reason,
            "is_already_in_deck": is_already_in_deck,
            "status_summary": " | ".join(status_flags) if status_flags else "Legal Add",
        }

    # -------------------------------------------------------------------------
    # Algorithmic & Timewalked Synergy Scoring & Cut Candidate Selection
    # -------------------------------------------------------------------------

    @staticmethod
    def _clean_card_key(name: str) -> str:
        s = name.strip().lower()
        if " // " in s:
            s = s.split(" // ")[0].strip()
        return re.sub(r"['’,\-\s]+", "", s)

    def build_card_profile(self, card_meta: dict) -> dict:
        """
        Computes deck-independent card data (clean names, classification, primary role,
        Timewalked categories, and Game Changer status) once, so it can be reused
        across every deck in a suite run.
        """
        card_name = card_meta.get("canonical_name") or card_meta.get("name", "")
        clean_name = strip_accents(card_name).lower().strip()
        clean_front = clean_name.split(" // ")[0].strip() if " // " in clean_name else clean_name
        clean_key = self._clean_card_key(card_name)
        type_lower = (card_meta.get("type_line") or "").lower()

        # Classify functional role
        card_dict = {
            "name": card_name,
            "type_line": card_meta.get("type_line", ""),
            "oracle_text": card_meta.get("oracle_text", ""),
            "cmc": card_meta.get("cmc", 0.0),
            "mana_cost": card_meta.get("mana_cost", ""),
        }
        classification = self.classifier.classify(card_dict)
        tw_categories = classify_timewalked_categories(card_dict)
        tw_roles = timewalked_classify_card_roles(card_dict)

        # Primary Role assignment
        role = "Utility / Support"
        if classification.get("is_board_wipe"):
            role = "Board Wipe"
        elif classification.get("is_targeted_removal"):
            role = "Spot Removal"
        elif classification.get("is_fast_ramp"):
            role = "Fast Ramp (<=2 CMC)"
        elif classification.get("is_ramp"):
            role = "Ramp / Acceleration"
        elif classification.get("draw_type") == "engine" or classification.get("is_draw_engine"):
            role = "Card Advantage Engine"
        elif classification.get("is_burst_draw"):
            role = "Burst Card Draw"
        elif classification.get("is_cantrip"):
            role = "Cantrip / Velocity"
        elif classification.get("is_tutor"):
            role = "Tutor / Search"
        elif classification.get("is_protection") or classification.get("is_counterspell"):
            role = "Protection / Counterspell"
        elif classification.get("wincon_tags"):
            role = "Finisher / Win-Con"
        elif "land" in type_lower:
            role = "Mana Base / Land"

        is_game_changer = bool(
            card_meta.get("game_changer")
            or card_name in TIMEWALKED_GAME_CHANGERS
            or clean_name in _LOWER_GAME_CHANGERS
            or clean_front in _LOWER_GAME_CHANGERS
            or "game_changer" in tw_roles
        )
        is_mass_land_denial = bool(
            card_name in TIMEWALKED_MASS_LAND_DENIAL
            or clean_name in _LOWER_MASS_LAND_DENIAL
            or clean_front in _LOWER_MASS_LAND_DENIAL
            or "mass_land_denial" in tw_roles
        )
        is_strong_staple = bool(
            card_name in TIMEWALKED_STRONG_STAPLES
            or clean_name in _LOWER_STRONG_STAPLES
            or clean_front in _LOWER_STRONG_STAPLES
            or "strong_card" in tw_roles
        )

        return {
            "card_name": card_name,
            "clean_name": clean_name,
            "clean_front": clean_front,
            "clean_key": clean_key,
            "is_land": "land" in type_lower,
            "classification": classification,
            "role": role,
            "tw_categories": tw_categories,
            "tw_roles": tw_roles,
            "is_game_changer": is_game_changer,
            "is_mass_land_denial": is_mass_land_denial,
            "is_strong_staple": is_strong_staple,
        }

    def _build_timewalked_deck_context(
        self,
        deck: dict,
        edhrec_synergies: Optional[dict[str, dict]] = None,
        offline: bool = False,
    ) -> dict:
        """
        Prepares Timewalked context for a deck:
        - Ratio breakdown (9 categories)
        - On-paper bracket & feels-like score
        - Included & almost-included combos from Commander Spellbook
        - Timewalked cut pool with inclusion rates and protected combo pieces
        """
        raw_cards = deck.get("cards", []) or []
        deck_counts: dict[str, int] = {}
        deck_resolved: dict[str, dict[str, Any]] = {}
        commanders: list[str] = []

        cmdr_raw = deck.get("commander_name") or ""
        if cmdr_raw:
            commanders = [c.strip() for c in re.split(r"[,/]+", cmdr_raw) if c.strip()]

        for c in raw_cards:
            c_name = c.get("name", "").strip()
            if not c_name:
                continue
            qty = max(1, int(c.get("quantity", 1) or 1))
            sec = (c.get("section") or "mainboard").lower()
            if sec in ("commander", "commanders"):
                if c_name not in commanders:
                    commanders.append(c_name)
            else:
                deck_counts[c_name] = deck_counts.get(c_name, 0) + qty

            deck_resolved[c_name] = {
                "name": c_name,
                "type_line": c.get("type_line", ""),
                "oracle_text": c.get("oracle_text", ""),
                "cmc": float(c.get("cmc", 0.0) or 0.0),
                "mana_cost": c.get("mana_cost", ""),
            }

        # 1. 9-Category Ratio Breakdown
        ratios = compute_ratio_breakdown(deck_resolved, deck_counts)

        # 2. Timewalked Classifier (Brackets, Feels Like, Spellbook Combos)
        tw_classifier = TimewalkedDeckClassifier(offline=offline)
        analysis_res = tw_classifier.classify(
            commanders=commanders,
            mainboard=deck_counts,
            deck_name=deck.get("deck_name", "Commander Deck"),
            pre_resolved_cards=raw_cards,
        )

        on_paper_bracket = analysis_res.on_paper_bracket
        on_paper_label = analysis_res.on_paper_label
        feels_like_score = analysis_res.feels_like_score
        game_changers_count = len(analysis_res.game_changers)
        included_combos = analysis_res.combos

        # Find 1-card-away combos from Spellbook
        combo_results = tw_classifier.spellbook.find_combos(commanders, list(deck_counts.keys()))
        almost_combos = combo_results.get("almost_included") or []
        combos_missing_card: dict[str, list[dict[str, Any]]] = {}
        for ac in almost_combos:
            missing_list = ac.get("missing") or []
            if len(missing_list) == 1:
                missing_name = missing_list[0]
                ck = self._clean_card_key(missing_name)
                combos_missing_card.setdefault(ck, []).append(ac)

        # Protect cards in active combos
        active_combo_cards: set[str] = set()
        for ic in included_combos:
            for cn in ic.get("cards", []):
                active_combo_cards.add(self._clean_card_key(cn))

        # 3. Build Cut Pool
        edh_by_clean: dict[str, dict] = {}
        if edhrec_synergies:
            for c_lower, item in edhrec_synergies.items():
                ck = self._clean_card_key(item.get("name") or c_lower)
                if ck not in edh_by_clean or item.get("inclusion_percent", 0) > edh_by_clean[ck].get("inclusion_percent", 0):
                    edh_by_clean[ck] = item

        cut_pool: list[dict[str, Any]] = []
        for name, qty in deck_counts.items():
            if name in commanders:
                continue
            card_obj = deck_resolved.get(name) or {"name": name, "type_line": "Spell", "cmc": 3.0}
            t_line = card_obj.get("type_line", "")
            is_basic = name in {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
            ck = self._clean_card_key(name)
            is_gc = bool(card_obj.get("game_changer") or name in TIMEWALKED_GAME_CHANGERS)
            is_part_of_combo = ck in active_combo_cards
            inc_pct = float(edh_by_clean.get(ck, {}).get("inclusion_percent", 0.0) or 0.0)
            cats = classify_timewalked_categories(card_obj)

            cut_pool.append({
                "name": name,
                "clean_key": ck,
                "card_obj": card_obj,
                "type_line": t_line,
                "is_land": "land" in t_line.lower(),
                "is_basic": is_basic,
                "is_game_changer": is_gc,
                "is_part_of_combo": is_part_of_combo,
                "inclusion_pct": inc_pct,
                "cmc": float(card_obj.get("cmc", 0.0)),
                "categories": cats,
            })

        return {
            "commanders": commanders,
            "ratios": ratios,
            "on_paper_bracket": on_paper_bracket,
            "on_paper_label": on_paper_label,
            "feels_like_score": feels_like_score,
            "game_changers_count": game_changers_count,
            "included_combos": included_combos,
            "combos_missing_card": combos_missing_card,
            "cut_pool": cut_pool,
            "edh_by_clean": edh_by_clean,
        }

    def evaluate_card_algorithmic(
        self,
        card_meta: dict,
        deck: dict,
        cut_candidates: list[dict],
        used_cuts: Optional[set[str]] = None,
        edhrec_synergies: Optional[dict[str, dict]] = None,
        profile: Optional[dict] = None,
        tw_context: Optional[dict] = None,
        timewalked_context: Optional[dict] = None,
        deck_context: Optional[dict] = None,
    ) -> dict:
        """
        Calculates functional role, synergy score (1-100 and 1.0-10.0 scale),
        fit verdict, deficit filling, and matching cut candidate using the
        Timewalked methodology.
        """
        if tw_context is None:
            tw_context = timewalked_context if timewalked_context is not None else deck_context
        if profile is None:
            profile = self.build_card_profile(card_meta)
        clean_name = profile["clean_name"]
        clean_front = profile["clean_front"]
        clean_key = profile["clean_key"]
        classification = profile["classification"]
        role = profile["role"]
        tw_cats = profile["tw_categories"]
        is_gc = profile["is_game_changer"]

        # Check EDHREC synergy and inclusion
        syn = 0.0
        syn_pct = 0.0
        inc_pct = 0.0
        if edhrec_synergies:
            syn_info = (
                edhrec_synergies.get(clean_name)
                or edhrec_synergies.get(clean_front)
                or (tw_context.get("edh_by_clean", {}).get(clean_key) if tw_context else None)
            )
            if syn_info:
                syn = float(syn_info.get("synergy", 0.0) or 0.0)
                syn_pct = float(syn_info.get("synergy_percent", round(syn * 100.0, 1)) or 0.0)
                inc_pct = float(syn_info.get("inclusion_percent", 0.0) or 0.0)

        # Baseline score calculation
        if inc_pct > 0.0:
            score = inc_pct
        else:
            score = 45.0  # Default baseline for cards not in EDHREC top samples

        reasons: list[str] = []
        signals: list[str] = []

        cmdr_display = (tw_context and tw_context.get("commanders") and tw_context["commanders"][0]) or deck.get("commander_name") or "this commander"

        # 1. EDHREC Signals
        if inc_pct > 0.0:
            signals.append("edhrec")
            reasons.append(f"{round(inc_pct)}% of {cmdr_display} decks")
            if syn_pct > 15.0:
                score += min(syn_pct * 0.4, 20.0)
                reasons.append(f"+{syn_pct:.1f}% EDHREC synergy")
        elif syn_pct > 0.0:
            signals.append("edhrec")
            score += min(syn_pct * 0.7, 30.0)
            reasons.append(f"+{syn_pct:.1f}% EDHREC synergy")

        # 2. Timewalked 9-Category Ratio Targets Boost
        if tw_context and tw_context.get("ratios"):
            ratios = tw_context["ratios"]
            best_ratio_boost = 0.0
            ratio_reason = ""
            for cat in tw_cats:
                r_info = ratios.get(cat)
                if not r_info:
                    continue
                state = r_info.get("state")
                cnt = r_info.get("count", 0)
                tgt = r_info.get("target", 0)
                lbl = r_info.get("label", cat.title())

                if state == "short" and best_ratio_boost < 15.0:
                    best_ratio_boost = 15.0
                    ratio_reason = f"Fills {lbl} ({cnt}/{tgt})"
                elif state == "close" and best_ratio_boost < 7.0:
                    best_ratio_boost = 7.0
                    ratio_reason = f"Boosts {lbl} ({cnt}/{tgt})"

            if best_ratio_boost > 0:
                score += best_ratio_boost
                signals.append("ratio")
                reasons.append(ratio_reason)

        # 3. Commander Spellbook 1-Card-Away Combo Boost
        cm_combos = []
        if tw_context:
            if tw_context.get("combos_missing_card"):
                cm_combos = tw_context["combos_missing_card"].get(clean_key, [])
            elif tw_context.get("almost_included_combos"):
                for ac in tw_context["almost_included_combos"]:
                    missing = ac.get("missing_cards") or ac.get("missing") or []
                    if any(self._clean_card_key(m) == clean_key for m in missing):
                        cm_combos.append(ac)
        completes_combos = []
        if cm_combos:
            combo_boost = 25.0 + min(len(cm_combos) - 1, 2) * 10.0
            score += combo_boost
            signals.append("combo")
            completes_combos = cm_combos
            partner_pieces = []
            for ac in cm_combos:
                other_cards = [c for c in ac.get("cards", []) if self._clean_card_key(c) != clean_key]
                if other_cards:
                    partner_pieces.append(other_cards[0])
            partner_summary = ", ".join(partner_pieces[:2]) if partner_pieces else "existing pieces"
            reasons.append(f"Completes combo with {partner_summary}")

        # 4. Curated staple & Game Changer check
        staples = _staple_index(_is_pauper_deck(deck))
        staple_match = staples.get(clean_name) or staples.get(clean_front)
        if staple_match:
            score += 15.0
            reasons.append(f"premier staple ({staple_match.get('category', 'Staple')})")

        if is_gc:
            score += 10.0
            reasons.append("Official Game Changer")

        # 5. Low CMC efficiency
        cmc = card_meta.get("cmc", 0.0)
        if cmc <= 2 and not profile["is_land"]:
            score += 6.0

        # Bracket Guardrail Warning
        bracket_warning = None
        if profile.get("is_mass_land_denial"):
            bracket_warning = "Mass Land Denial (automatically places deck in Bracket 4)"
        elif tw_context and is_gc:
            cur_gc = tw_context.get("game_changers_count", 0)
            p_bracket = tw_context.get("on_paper_bracket", 2)
            if p_bracket <= 3 and cur_gc >= 3:
                bracket_warning = f"Adding this Game Changer raises deck to Bracket 4 (exceeds 3 GC cap: {cur_gc + 1} total)"
            elif p_bracket == 2 and cur_gc == 0:
                bracket_warning = "Official Game Changer (bumps deck from Bracket 2 to Bracket 3)"

        # Cap score between 5.0 and 99.0
        score = max(5.0, min(99.0, score))
        synergy_rating = round(score / 10.0, 1)

        # Fit Verdict
        if synergy_rating >= 9.0 or syn_pct >= 40.0 or inc_pct >= 60.0 or completes_combos:
            fit_verdict = "Essential Upgrade"
        elif synergy_rating >= 7.8 or syn_pct >= 18.0 or inc_pct >= 30.0:
            fit_verdict = "High Synergy"
        elif classification.get("wincon_tags"):
            fit_verdict = "Alternative Win-Con"
        elif synergy_rating >= 6.0 or inc_pct >= 10.0:
            fit_verdict = "Role Filler / Utility"
        else:
            fit_verdict = "Suboptimal / Redundant"

        # Find best cut candidate using Timewalked cut pool or fallback engine cut
        cut_name = None
        cut_cmc = None
        cut_type = None
        cut_reason = "Lowest synergy / meta inclusion in deck"

        cut_pool = tw_context.get("cut_pool") if tw_context else None
        if cut_pool:
            is_cand_land = "lands" in tw_cats or profile["is_land"]
            eligible_cuts = [
                c for c in cut_pool
                if (used_cuts is None or c.get("clean_key") not in used_cuts)
                and not c.get("is_game_changer", False)
                and not c.get("is_part_of_combo", False)
            ]

            matched_cut = None
            if is_cand_land:
                land_cuts = [c for c in eligible_cuts if c.get("is_land")]
                if land_cuts:
                    land_cuts.sort(key=lambda x: (x.get("is_basic", False), x.get("inclusion_pct", 0.0)))
                    matched_cut = land_cuts[0]
                    cut_reason = f"Replaces land ({round(matched_cut.get('inclusion_pct', 0.0))}% inclusion)"
            else:
                cand_nonland_cats = set(tw_cats) - {"lands"}
                same_cat_cuts = [
                    c for c in eligible_cuts
                    if not c.get("is_land") and not c.get("is_basic") and (cand_nonland_cats & set(c.get("categories", [])))
                ]
                if same_cat_cuts:
                    same_cat_cuts.sort(key=lambda x: (x.get("inclusion_pct", 0.0), -float(x.get("cmc", 0.0))))
                    matched_cut = same_cat_cuts[0]
                    intersect = list(cand_nonland_cats & set(matched_cut.get("categories", [])))[0]
                    lbl = intersect.replace("_", " ").title()
                    cut_reason = f"Same-role swap: replaces {lbl} ({round(matched_cut.get('inclusion_pct', 0.0))}% inclusion)"
                else:
                    nonland_cuts = [c for c in eligible_cuts if not c.get("is_land") and not c.get("is_basic")]
                    if nonland_cuts:
                        nonland_cuts.sort(key=lambda x: (x.get("inclusion_pct", 0.0), -float(x.get("cmc", 0.0))))
                        matched_cut = nonland_cuts[0]
                        cut_reason = f"Lowest inclusion card in deck ({round(matched_cut.get('inclusion_pct', 0.0))}% inclusion)"

            if matched_cut:
                if used_cuts is not None and matched_cut.get("clean_key"):
                    used_cuts.add(matched_cut["clean_key"])
                cut_name = matched_cut.get("name")
                cut_cmc = matched_cut.get("cmc")
                cut_type = matched_cut.get("type_line")
        else:
            matched_cut = self.upgrade_engine._find_best_cut(
                cut_candidates=cut_candidates,
                target_role_or_category=role,
                used_cuts=used_cuts,
            )
            cut_name = matched_cut.get("name") if isinstance(matched_cut, dict) else str(matched_cut)
            cut_cmc = matched_cut.get("cmc") if isinstance(matched_cut, dict) else None
            cut_type = matched_cut.get("type_line") if isinstance(matched_cut, dict) else None
            cut_reason = f"Role replacement for {role}"

        # Build concise rationale
        if reasons:
            rationale = f"Strong addition: {', '.join(reasons[:2])}. Replaces {cut_name}."
        else:
            rationale = f"Solid tactical fit for {role}. Recommended replacement for {cut_name}."

        return {
            "role": role,
            "score": round(score, 1),
            "synergy_rating": synergy_rating,
            "fit_verdict": fit_verdict,
            "reasons": reasons,
            "signals": signals,
            "rationale": rationale,
            "suggested_cut": cut_name,
            "suggested_cut_cmc": cut_cmc,
            "suggested_cut_type": cut_type,
            "cut_reason": cut_reason,
            "bracket_warning": bracket_warning,
            "completes_combos": completes_combos,
            "is_game_changer": is_gc,
            "is_mass_land_denial": profile.get("is_mass_land_denial", False),
            "categories": sorted(list(tw_cats)),
            "edhrec_synergy_percent": syn_pct if syn_pct != 0.0 else None,
            "edhrec_inclusion_percent": inc_pct if inc_pct != 0.0 else None,
        }

    # -------------------------------------------------------------------------
    # Gemini Strategic Intelligence (Deep Reasoning)
    # -------------------------------------------------------------------------

    def evaluate_with_gemini(
        self,
        cards: list[dict],
        decks: list[dict],
        custom_instructions: str = "",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        eligible_pairs: Optional[dict[Any, list[str]]] = None,
        cut_pools: Optional[dict[Any, list[str]]] = None,
    ) -> dict:
        """
        Sends candidate cards and target Commander decks to Gemini.
        Returns tactical fleet fit matrix, cuts, and new commander opportunities.

        Token-lean: only legal, not-already-in-deck (card, deck) pairs are sent
        (`eligible_pairs`: deck_id -> card names), each deck ships a short weakest-card
        cut pool instead of a 40-card sample, and the response is a flat `evals` list
        (no duplicated deck_breakdowns section).
        """
        effective_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not effective_key:
            raise GeminiAnalysisError("Gemini API Key is required for AI tactical analysis.")

        raw_model = model or os.getenv("GEMINI_DEFAULT_MODEL", DEFAULT_MODEL)
        if not raw_model or str(raw_model).strip().lower() in ("auto", "default", "none", ""):
            target_model_base = "gemini-3.8-flash" if (len(cards) > 5 or len(decks) > 3) else get_model_for_task("card_evaluation")
        else:
            target_model_base = get_model_for_task("card_evaluation", preferred_model=raw_model)

        # Pre-filter pairs locally (color identity / bans / duplicates) if caller didn't supply them,
        # so Gemini never spends tokens on pairings that can't be played.
        if eligible_pairs is None:
            eligible_pairs = {}
            for d in decks:
                d_ci = self.normalize_color_identity(d.get("color_identity"))
                names = []
                for c in cards:
                    compat = self.check_card_deck_compatibility(c, d, deck_ci=d_ci)
                    if compat["is_legal"] and not compat["is_already_in_deck"]:
                        names.append(c.get("canonical_name") or c.get("name", ""))
                if names:
                    eligible_pairs[d.get("id")] = names

        if cut_pools is None:
            cut_pools = {}
            for d in decks:
                cands = self.upgrade_engine._identify_cut_candidates(d.get("cards", []), ai_analysis=d.get("analysis"))
                cut_pools[d.get("id")] = [c["name"] for c in cands if not c.get("is_basic")][:GEMINI_CUT_POOL_SIZE]

        if not any(eligible_pairs.values()):
            raise GeminiAnalysisError("No legal card/deck pairings to evaluate.")

        # Only send cards that appear in at least one eligible pairing
        needed = {n.lower() for names in eligible_pairs.values() for n in names}
        card_lines = []
        legendary_names = []
        for c in cards:
            c_name = c.get("canonical_name") or c.get("name", "")
            t_line = c.get("type_line") or ""
            t_low = t_line.lower()
            is_legend = "legendary" in t_low and "creature" in t_low
            if is_legend:
                legendary_names.append(c_name)
            if c_name.lower() not in needed and not is_legend:
                continue
            ci = "".join(c.get("color_identity") or []) or "C"
            card_lines.append(f"{c_name}|{c.get('mana_cost') or '-'}|{t_line or '?'}|{ci}")

        deck_blocks = []
        for d in decks:
            did = d.get("id")
            names = eligible_pairs.get(did)
            if not names:
                continue
            ci = "".join(sorted(self.normalize_color_identity(d.get("color_identity")))) or "C"
            arch = d.get("archetype") or "Synergy"
            pool = cut_pools.get(did) or []
            deck_blocks.append(
                f"[{did}] {d.get('commander_name') or 'Unknown'} ({ci}, {arch})\n"
                f" rate: {'; '.join(names)}\n"
                f" cuts: {'; '.join(pool) if pool else 'any weak card'}"
            )

        system_instruction = (
            "You are an expert MTG Commander deckbuilding advisor. Rate each listed card for each deck it is "
            "listed under. Pairings are already legal and not duplicates. suggested_cut must come from that deck's "
            "cuts list. Keep rationale under 25 words, citing the commander's synergy. Output raw JSON only."
        )

        new_cmdr_schema = (
            ',"new_commander_opportunities":[{"card_name":str,"colors":[str],"archetype":str,"rationale":str}]'
            if legendary_names
            else ""
        )
        user_prompt = (
            "CARDS (name|cost|type|CI):\n" + "\n".join(card_lines) + "\n\n"
            "DECKS ([id] commander (CI, archetype)):\n" + "\n".join(deck_blocks) + "\n\n"
            + (f"USER DIRECTIVES: {custom_instructions}\n\n" if custom_instructions else "")
            + "fit_verdict is one of: Essential Upgrade, High Synergy, Alternative Win-Con, Role Filler / Utility, "
            "Suboptimal / Redundant. synergy_rating is 1.0-10.0.\n"
            + (f"Also assess these legends as new commanders: {'; '.join(legendary_names)}.\n" if legendary_names else "")
            + 'Schema: {"executive_summary":str (2-3 sentences),"evals":[{"card":str,"deck_id":int,"fit_verdict":str,'
            '"role":str,"synergy_rating":num,"suggested_cut":str,"rationale":str}]' + new_cmdr_schema + "}"
        )

        clean_key = effective_key.strip()
        target_model = target_model_base
        url = f"{GEMINI_API_BASE}/{target_model}:generateContent?key={clean_key}"
        gen_config = {"maxOutputTokens": 8192, "responseMimeType": "application/json"}
        if "gemini-3" in target_model:
            gen_config["thinkingConfig"] = {"thinkingLevel": "low"}

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "generationConfig": gen_config,
        }

        max_retries = 2
        last_err = ""
        last_status = 0

        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff_sec = 1.0 * attempt
                logger.info(f"Retrying Card Add Gemini call ({target_model}) attempt {attempt + 1}/{max_retries + 1} after {backoff_sec}s delay...")
                time.sleep(backoff_sec)

            try:
                resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=90)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        last_err = "Empty candidates"
                        last_status = 200
                        continue
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        last_err = "Empty parts"
                        last_status = 200
                        continue

                    raw_text = parts[0].get("text", "").strip()
                    parsed = self._clean_and_parse_json(raw_text)
                    parsed["_model_used"] = target_model
                    parsed["_analyzed_at"] = datetime.now(timezone.utc).isoformat()
                    return parsed
                else:
                    last_status = resp.status_code
                    last_err = resp.text[:200]
                    logger.warning(f"Card Add model '{target_model}' failed (HTTP {resp.status_code}): {last_err}")
                    if resp.status_code in (429, 500, 503) and attempt < max_retries:
                        continue
                    break
            except Exception as e:
                last_err = str(e)
                last_status = 0
                logger.warning(f"Card Add model '{target_model}' exception: {e}")
                if attempt < max_retries:
                    continue
                break

        sc_str = f"HTTP {last_status}: " if last_status else ""
        raise GeminiAnalysisError(f"Card Add evaluation failed on model '{target_model}': {sc_str}{last_err}")

    def _clean_and_parse_json(self, raw: str) -> dict:
        """Strips markdown code fences and safely extracts JSON."""
        clean = raw.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s*```$", "", clean)
        clean = clean.strip()

        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            start = clean.find("{")
            end = clean.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(clean[start : end + 1])
                except Exception:
                    pass
            raise GeminiAnalysisError(f"Could not parse Gemini JSON response. Snippet: {raw[:250]}")

    # -------------------------------------------------------------------------
    # Unified Suite Evaluator (Algorithmic + Gemini Integration)
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Unified Suite Evaluator (Timewalked Methodology Engine)
    # -------------------------------------------------------------------------

    def evaluate_cards_suite(
        self,
        cards: list[dict],
        decks: list[dict],
        use_gemini: bool = False,
        custom_instructions: str = "",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        offline: bool = False,
        user_inventory_index: Optional[dict[str, dict]] = None,
    ) -> dict:
        """
        Full evaluation suite for custom cards powered by the Timewalked methodology.
        Computes legality checks, Timewalked ratio targets & gap filling,
        Commander Spellbook 1-card-away combo completions, WotC bracket guardrails,
        data-driven cuts, and collection binder inventory matching.
        Produces both Card Matrix View and Deck Breakdown View.
        """
        if not cards:
            return {"error": "No valid cards provided to evaluate."}
        if not decks:
            return {"error": "No Commander decks selected to evaluate against."}

        # Query EDHREC synergies once per unique commander (parallel, cached by provider)
        deck_synergies_cache = self._fetch_deck_synergies(decks) if not offline else {}

        # Precompute Timewalked context, deck card sets, and cut pools per deck
        deck_cards_sets: dict[int, set[str]] = {}
        deck_ci_cache: dict[int, set[str]] = {}
        deck_tw_contexts: dict[int, dict] = {}
        deck_cut_candidates: dict[int, list[dict]] = {}

        for d in decks:
            did = d["id"]
            d_cards = d.get("cards", [])
            d_set = set()
            for c in d_cards:
                c_name = c.get("name", "").strip().lower()
                if c_name:
                    d_set.add(c_name)
                    if " // " in c_name:
                        d_set.add(c_name.split(" // ")[0].strip())
            deck_cards_sets[did] = d_set
            deck_ci_cache[did] = self.normalize_color_identity(d.get("color_identity"))

            # Build Timewalked deck context (ratios, combos, brackets, cut pool)
            edh_syn = deck_synergies_cache.get(did, {})
            tw_ctx = self._build_timewalked_deck_context(d, edhrec_synergies=edh_syn, offline=offline)
            deck_tw_contexts[did] = tw_ctx

            # Identify cut candidates for backward compatibility
            cuts = self.upgrade_engine._identify_cut_candidates(d_cards, ai_analysis=d.get("analysis"))
            deck_cut_candidates[did] = cuts

        # Classify each candidate card once (deck-independent)
        card_profiles = [self.build_card_profile(card) for card in cards]

        # 1. Compute Timewalked Card Matrix & Deck Breakdowns
        card_matrix = []
        deck_breakdowns_map: dict[int, dict] = {}

        for d in decks:
            did = d["id"]
            tw_ctx = deck_tw_contexts[did]
            deck_breakdowns_map[did] = {
                "deck_id": did,
                "deck_name": d["deck_name"],
                "commander_name": d.get("commander_name") or "Unspecified",
                "color_identity": sorted(deck_ci_cache[did]),
                "commander_art": d.get("commander_art"),
                "on_paper_bracket": tw_ctx.get("on_paper_bracket", 2),
                "on_paper_label": tw_ctx.get("on_paper_label", "Core"),
                "feels_like_score": tw_ctx.get("feels_like_score", 2.0),
                "ratios": tw_ctx.get("ratios", {}),
                "total_applicable_cards": 0,
                "applicable_cards": [],
            }

        for card, profile in zip(cards, card_profiles):
            c_name = card.get("canonical_name") or card.get("name", "")
            clean_k = profile["clean_key"]

            # Binder matching
            is_owned = False
            owned_qty = 0
            binders = []
            if user_inventory_index:
                owned_data = (
                    user_inventory_index.get(clean_k)
                    or user_inventory_index.get(c_name.lower().strip())
                    or user_inventory_index.get(self._clean_card_key(c_name))
                )
                if owned_data and (owned_data.get("qty", 0) > 0 or owned_data.get("quantity", 0) > 0 or owned_data.get("id")):
                    is_owned = True
                    owned_qty = int(owned_data.get("qty") or owned_data.get("quantity") or 1)
                    raw_b = owned_data.get("binders") or ([owned_data.get("binder_name")] if owned_data.get("binder_name") else [])
                    binders = sorted(list({b for b in raw_b if b}))

            card_entry = {
                "card_name": c_name,
                "mana_cost": card.get("mana_cost", ""),
                "cmc": card.get("cmc", 0.0),
                "type_line": card.get("type_line", ""),
                "color_identity": card.get("color_identity", []),
                "price_usd": card.get("price_usd"),
                "price_usd_foil": card.get("price_usd_foil"),
                "image_uri": card.get("image_uri", ""),
                "art_crop_uri": card.get("art_crop_uri", ""),
                "scryfall_id": card.get("scryfall_id"),
                "tcgplayer_url": card.get("tcgplayer_url", ""),
                "is_game_changer": profile["is_game_changer"],
                "categories": sorted(list(profile["tw_categories"])),
                "is_owned": is_owned,
                "in_binder": is_owned,
                "owned_qty": owned_qty,
                "binders": binders,
                "binder_name": binders[0] if binders else None,
                "compatible_decks_count": 0,
                "deck_recommendations": [],
                "incompatible_decks": [],
            }

            for d in decks:
                did = d["id"]
                deck_cards_set = deck_cards_sets[did]
                compat = self.check_card_deck_compatibility(
                    card, d, deck_cards_set=deck_cards_set, deck_ci=deck_ci_cache[did]
                )

                if not compat["is_legal"]:
                    card_entry["incompatible_decks"].append({
                        "deck_id": did,
                        "deck_name": d["deck_name"],
                        "reason": compat["status_summary"],
                    })
                    continue

                if compat["is_already_in_deck"]:
                    card_entry["incompatible_decks"].append({
                        "deck_id": did,
                        "deck_name": d["deck_name"],
                        "reason": "Already in Deck",
                    })
                    continue

                # Timewalked evaluation
                cuts = deck_cut_candidates[did]
                synergies = deck_synergies_cache.get(did, {})
                tw_ctx = deck_tw_contexts[did]
                tw_res = self.evaluate_card_algorithmic(
                    card,
                    d,
                    cut_candidates=cuts,
                    edhrec_synergies=synergies,
                    profile=profile,
                    tw_context=tw_ctx,
                )

                rec = {
                    "deck_id": did,
                    "deck_name": d["deck_name"],
                    "commander_name": d.get("commander_name"),
                    "fit_verdict": tw_res["fit_verdict"],
                    "role": tw_res["role"],
                    "synergy_rating": tw_res["synergy_rating"],
                    "score": tw_res["score"],
                    "suggested_cut": tw_res["suggested_cut"],
                    "suggested_cut_cmc": tw_res["suggested_cut_cmc"],
                    "suggested_cut_type": tw_res["suggested_cut_type"],
                    "cut_reason": tw_res.get("cut_reason", ""),
                    "bracket_warning": tw_res.get("bracket_warning"),
                    "reasons": tw_res.get("reasons", []),
                    "signals": tw_res.get("signals", []),
                    "completes_combos": tw_res.get("completes_combos", []),
                    "is_game_changer": tw_res.get("is_game_changer", False),
                    "rationale": tw_res["rationale"],
                    "edhrec_synergy_percent": tw_res["edhrec_synergy_percent"],
                    "edhrec_inclusion_percent": tw_res["edhrec_inclusion_percent"],
                    "is_already_in_deck": False,
                }

                card_entry["deck_recommendations"].append(rec)

                # Add to deck breakdown
                app_card = {
                    "card_name": c_name,
                    "mana_cost": card.get("mana_cost", ""),
                    "cmc": card.get("cmc", 0.0),
                    "type_line": card.get("type_line", ""),
                    "image_uri": card.get("image_uri", ""),
                    "price_usd": card.get("price_usd"),
                    "scryfall_id": card.get("scryfall_id"),
                    "fit_verdict": tw_res["fit_verdict"],
                    "role": tw_res["role"],
                    "synergy_rating": tw_res["synergy_rating"],
                    "suggested_cut": tw_res["suggested_cut"],
                    "suggested_cut_cmc": tw_res["suggested_cut_cmc"],
                    "suggested_cut_type": tw_res["suggested_cut_type"],
                    "cut_reason": tw_res.get("cut_reason", ""),
                    "bracket_warning": tw_res.get("bracket_warning"),
                    "reasons": tw_res.get("reasons", []),
                    "signals": tw_res.get("signals", []),
                    "completes_combos": tw_res.get("completes_combos", []),
                    "is_game_changer": tw_res.get("is_game_changer", False),
                    "rationale": tw_res["rationale"],
                }
                deck_breakdowns_map[did]["applicable_cards"].append(app_card)

            # Sort deck recommendations by synergy score descending
            card_entry["deck_recommendations"].sort(key=lambda x: -x["synergy_rating"])
            card_entry["compatible_decks_count"] = len(card_entry["deck_recommendations"])
            card_entry["best_fit_deck"] = (
                card_entry["deck_recommendations"][0]["deck_name"]
                if card_entry["deck_recommendations"]
                else None
            )

            card_matrix.append(card_entry)

        # Update totals in deck breakdowns and sort applicable cards
        deck_breakdowns = []
        for did, db_info in deck_breakdowns_map.items():
            db_info["total_applicable_cards"] = len(db_info["applicable_cards"])
            db_info["applicable_cards"].sort(key=lambda x: -x["synergy_rating"])
            deck_breakdowns.append(db_info)

        # Check new commander opportunities
        new_commanders = []
        for card in cards:
            t_line = (card.get("type_line") or "").lower()
            if "legendary" in t_line and "creature" in t_line:
                c_name = card.get("canonical_name") or card.get("name", "")
                colors = card.get("color_identity", [])
                is_gc_cmdr = bool(card.get("game_changer") or c_name in TIMEWALKED_GAME_CHANGERS)
                new_commanders.append({
                    "card_name": c_name,
                    "colors": colors,
                    "type_line": card.get("type_line", ""),
                    "image_uri": card.get("image_uri", ""),
                    "archetype": "High-Ceiling Commander" if is_gc_cmdr else "Commander Potential",
                    "is_game_changer": is_gc_cmdr,
                    "rationale": f"{c_name} is an official Game Changer legendary creature with extreme power ceiling."
                    if is_gc_cmdr
                    else f"{c_name} is a legendary creature that can helm a new Commander deck.",
                })

        # Summary telemetry
        essential_count = sum(
            1
            for cm in card_matrix
            for r in cm.get("deck_recommendations", [])
            if r.get("fit_verdict") == "Essential Upgrade"
        )
        combo_count = sum(
            1
            for cm in card_matrix
            for r in cm.get("deck_recommendations", [])
            if r.get("completes_combos")
        )
        exec_summary = (
            f"Evaluated {len(cards)} candidate card(s) against {len(decks)} Commander deck(s) using Timewalked methodology. "
            f"Identified {essential_count} Essential Upgrade match(es)"
            + (f" and {combo_count} combo completion(s)." if combo_count > 0 else ".")
        )

        return {
            "mode": "cards",
            "executive_summary": exec_summary,
            "card_matrix": card_matrix,
            "deck_breakdowns": deck_breakdowns,
            "new_commander_opportunities": new_commanders,
            "evaluated_cards_count": len(cards),
            "evaluated_decks_count": len(decks),
            "_model_used": "timewalked-methodology",
        }

    @staticmethod
    def _iter_gemini_evals(gemini_res: dict):
        """Yields flat eval dicts from the compact `evals` schema (or legacy nested `card_matrix`)."""
        evals = gemini_res.get("evals")
        if isinstance(evals, list):
            yield from (e for e in evals if isinstance(e, dict))
            return
        for cm in gemini_res.get("card_matrix", []) or []:
            for r in cm.get("deck_recommendations", []) or []:
                yield {**r, "card": cm.get("card_name", "")}

    def _fetch_deck_synergies(self, decks: list[dict]) -> dict[Any, dict]:
        """
        Fetches EDHREC card synergies once per unique commander, in parallel.
        Returns {deck_id: {card_name_lower: synergy_info}}.
        """
        cmdr_by_deck = {
            d["id"]: (d.get("commander_name") or "").strip()
            for d in decks
            if (d.get("commander_name") or "").strip()
        }
        unique_cmdrs = list(dict.fromkeys(cmdr_by_deck.values()))
        if not unique_cmdrs:
            return {}

        # EDHREC provider's DB cache needs a Flask app context inside worker threads
        app_obj = None
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                app_obj = current_app._get_current_object()
        except Exception:
            app_obj = None

        def _fetch(cmdr: str) -> dict:
            try:
                if app_obj is not None:
                    with app_obj.app_context():
                        data = self.edhrec.get_commander_data(cmdr)
                else:
                    data = self.edhrec.get_commander_data(cmdr)
                return (data or {}).get("card_synergies") or {}
            except Exception as e:
                logger.debug(f"EDHREC synergy lookup failed for {cmdr}: {e}")
                return {}

        if len(unique_cmdrs) == 1:
            results = {unique_cmdrs[0]: _fetch(unique_cmdrs[0])}
        else:
            with ThreadPoolExecutor(max_workers=min(6, len(unique_cmdrs))) as ex:
                results = dict(zip(unique_cmdrs, ex.map(_fetch, unique_cmdrs)))

        return {did: results.get(cmdr, {}) for did, cmdr in cmdr_by_deck.items()}

    # -------------------------------------------------------------------------
    # Secret Lair Superdrop Integration (Timewalked Deterministic Methodology)
    # -------------------------------------------------------------------------

    def evaluate_secret_lair_suite(
        self,
        url_or_text: str = "",
        decks: list[dict] = None,
        use_gemini: bool = False,
        custom_instructions: str = "",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        offline: bool = False,
        user_inventory_index: Optional[dict[str, dict]] = None,
        drops: Optional[list[dict]] = None,
    ) -> dict:
        """
        Integrates Secret Lair Superdrop scraping, Scryfall EV calculation,
        and Timewalked fleet synergy analysis. Generates EV rankings, bundle analysis,
        and feeds drop cards into the Card Matrix.
        """
        effective_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        bundles = []

        # 1. Fetch & Parse announcement (or use pre-parsed drops)
        if drops is None:
            announcement = self.scraper.fetch_announcement(url_or_text)
            parsed_drops, bundles = self.scraper.parse_drops(
                announcement["text"],
                api_key=effective_key if use_gemini else None,
                html=announcement.get("html"),
            )
            title = announcement.get("title", "Secret Lair Superdrop")
            banner_img = announcement.get("banner_image")
            src_url = announcement.get("source_url", url_or_text)
            drops = parsed_drops
        else:
            title = "Secret Lair Evaluation"
            banner_img = None
            src_url = url_or_text or ""

        if not drops:
            raise ValueError("No Secret Lair drops or cards could be parsed from the provided announcement.")

        # 2. Enrich drops with Scryfall EV & market singles valuations
        enriched_drops = self.financial_evaluator.enrich_drops_with_scryfall(drops)

        # 3. Flatten drop cards for Card Matrix
        all_drop_cards = []
        for drop in enriched_drops:
            d_name = drop.get("drop_name", "Drop")
            for c in drop.get("cards", []):
                card_item = dict(c)
                card_item["from_drop"] = d_name
                all_drop_cards.append(card_item)

        # 4. Build unified Card Matrix from drops using Timewalked methodology
        card_matrix_result = self.evaluate_cards_suite(
            cards=all_drop_cards,
            decks=decks,
            use_gemini=False,
            offline=offline,
            user_inventory_index=user_inventory_index,
        )

        # 5. Deterministic Timewalked Drop Ranking and Bundle Analysis
        # Count high-synergy / essential upgrades per drop across the fleet
        drop_upgrade_counts: dict[str, dict] = {}
        for drop in enriched_drops:
            d_name = drop.get("drop_name", "Drop")
            drop_upgrade_counts[d_name] = {
                "drop_name": d_name,
                "essential_count": 0,
                "high_count": 0,
                "target_decks": set(),
                "cards_matching": [],
            }

        for cm in card_matrix_result.get("card_matrix", []):
            d_origin = cm.get("from_drop")
            if not d_origin or d_origin not in drop_upgrade_counts:
                # Fallback matching
                for d_cand in drop_upgrade_counts:
                    if any(
                        self._clean_card_key(c.get("canonical_name", "")) == self._clean_card_key(cm.get("card_name", ""))
                        for c in next((d.get("cards", []) for d in enriched_drops if d.get("drop_name") == d_cand), [])
                    ):
                        d_origin = d_cand
                        break

            if d_origin and d_origin in drop_upgrade_counts:
                info = drop_upgrade_counts[d_origin]
                for rec in cm.get("deck_recommendations", []):
                    if rec.get("fit_verdict") == "Essential Upgrade":
                        info["essential_count"] += 1
                        info["target_decks"].add(rec.get("deck_name"))
                        info["cards_matching"].append(cm.get("card_name"))
                    elif rec.get("fit_verdict") == "High Synergy":
                        info["high_count"] += 1
                        info["target_decks"].add(rec.get("deck_name"))
                        info["cards_matching"].append(cm.get("card_name"))

        ranked_drops = []
        for drop in enriched_drops:
            d_name = drop.get("drop_name", "Drop")
            upg = drop_upgrade_counts.get(d_name, {})
            ev_ratio = drop.get("ev_ratio_nonfoil") or 1.0
            ess = upg.get("essential_count", 0)
            high = upg.get("high_count", 0)

            # Combined score: EV financial ratio + tactical fleet upgrades
            fleet_score = round((ev_ratio * 35.0) + (ess * 18.0) + (high * 8.0), 1)

            tier = "Pass / Low Priority"
            if ess >= 2 or (ev_ratio >= 1.25 and ess >= 1):
                tier = "Must Buy / High Priority"
            elif ess >= 1 or high >= 2 or ev_ratio >= 1.1:
                tier = "Consider Buying"
            elif high >= 1:
                tier = "Role Filler"

            deck_list_str = ", ".join(sorted(list(upg.get("target_decks", [])))[:2])
            summary_rationale = (
                f"EV {ev_ratio:.2f}x retail with {ess} Essential and {high} High Synergy fleet upgrades"
                + (f" for {deck_list_str}." if deck_list_str else ".")
            )

            ranked_drops.append({
                "drop_name": d_name,
                "recommendation_tier": tier,
                "fleet_synergy_score": fleet_score,
                "ev_ratio": ev_ratio,
                "summary_rationale": summary_rationale,
                "target_decks": sorted(list(upg.get("target_decks", []))),
            })

        ranked_drops.sort(key=lambda x: -x["fleet_synergy_score"])
        for idx, r in enumerate(ranked_drops, 1):
            r["rank"] = idx

        # Bundle evaluation
        bundle_analysis = {}
        if bundles:
            best_bundle = bundles[0]
            b_name = best_bundle.get("bundle_name", "Superdrop Bundle")
            total_singles = sum(d.get("singles_value_nonfoil", 0.0) for d in enriched_drops)
            b_price = best_bundle.get("price_nonfoil") or best_bundle.get("price") or 119.99
            b_discount_val = round(total_singles - b_price, 2)
            b_verdict = "Buy Bundle" if total_singles > b_price else "Buy Selected Singles"
            bundle_analysis = {
                "verdict": b_verdict,
                "bundle_name": b_name,
                "singles_total_value": round(total_singles, 2),
                "bundle_price": b_price,
                "savings_vs_singles": b_discount_val,
                "rationale": f"Total fleet singles value of ${total_singles:.2f} vs bundle retail of ${b_price:.2f}. "
                f"Result: {b_verdict}.",
            }

        return {
            "mode": "secret_lair",
            "title": title,
            "banner_image": banner_img,
            "source_url": src_url,
            "drops": enriched_drops,
            "bundles": bundles,
            "card_matrix": card_matrix_result.get("card_matrix", []),
            "deck_breakdowns": card_matrix_result.get("deck_breakdowns", []),
            "best_drops_to_buy": ranked_drops,
            "bundle_analysis": bundle_analysis,
            "new_commander_opportunities": card_matrix_result.get("new_commander_opportunities", []),
            "_model_used": "timewalked-methodology",
        }
