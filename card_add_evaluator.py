"""
Card Add Evaluator & Tactical Add Analysis Suite for Chimaera MTG.
Evaluates single cards, batch card lists, and Secret Lair drops against individual
Commander decks or the user's entire Commander fleet.
Cross-references color identity rules, Commander/Pauper banlists, EDHREC synergies,
deck deficits, and leverages Google Gemini for tactical rationales and cut recommendations.
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
from gemini_analyzer import (
    DEFAULT_MODEL,
    GEMINI_API_BASE,
    MODEL_FALLBACK_MAP,
    MODEL_TIER_SEQUENCE,
    GeminiAnalysisError,
    get_model_for_task,
)
from providers.edhrec import EDHRECProvider
from providers.scryfall import ScryfallProvider
from secret_lair_advisor import (
    SecretLairFinancialEvaluator,
    SecretLairGeminiAdvisor,
    SecretLairScraper,
)

logger = logging.getLogger(__name__)

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
    """

    def __init__(
        self,
        scryfall_provider: Optional[ScryfallProvider] = None,
        edhrec_provider: Optional[EDHRECProvider] = None,
    ):
        self.scryfall = scryfall_provider or ScryfallProvider()
        self.edhrec = edhrec_provider or EDHRECProvider()
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
    # Algorithmic Synergy Scoring & Cut Candidate Selection
    # -------------------------------------------------------------------------

    def build_card_profile(self, card_meta: dict) -> dict:
        """
        Computes deck-independent card data (clean names, classification, primary role) once,
        so it can be reused across every deck in a suite run.
        """
        card_name = card_meta.get("canonical_name") or card_meta.get("name", "")
        clean_name = strip_accents(card_name).lower().strip()
        clean_front = clean_name.split(" // ")[0].strip() if " // " in clean_name else clean_name
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

        return {
            "card_name": card_name,
            "clean_name": clean_name,
            "clean_front": clean_front,
            "is_land": "land" in type_lower,
            "classification": classification,
            "role": role,
        }

    def evaluate_card_algorithmic(
        self,
        card_meta: dict,
        deck: dict,
        cut_candidates: list[dict],
        used_cuts: Optional[set[str]] = None,
        edhrec_synergies: Optional[dict[str, dict]] = None,
        profile: Optional[dict] = None,
    ) -> dict:
        """
        Calculates functional role, synergy score (1-100 and 1.0-10.0 scale),
        fit verdict, deficit filling, and matching cut candidate.
        Pass a precomputed `profile` (from build_card_profile) to skip re-classification.
        """
        if profile is None:
            profile = self.build_card_profile(card_meta)
        clean_name = profile["clean_name"]
        clean_front = profile["clean_front"]
        classification = profile["classification"]
        role = profile["role"]

        # Check EDHREC synergy if available
        syn = 0.0
        syn_pct = 0.0
        inc_pct = 0.0
        if edhrec_synergies:
            syn_info = edhrec_synergies.get(clean_name) or edhrec_synergies.get(clean_front)
            if syn_info:
                syn = float(syn_info.get("synergy", 0.0))
                syn_pct = float(syn_info.get("synergy_percent", round(syn * 100.0, 1)))
                inc_pct = float(syn_info.get("inclusion_percent", 0.0))

        # Scoring heuristics
        score = 45.0
        reasons = []

        # 1. EDHREC synergy bonus
        if syn_pct > 0:
            score += min(syn_pct * 0.7, 30.0)
            reasons.append(f"+{syn_pct:.1f}% EDHREC synergy")
        elif syn_pct < -15.0:
            score -= 10.0

        if inc_pct >= 20.0:
            score += min(inc_pct * 0.25, 12.0)
            reasons.append(f"played in {inc_pct:.0f}% of decks")

        # 2. Deficit filling & curve efficiency
        deck_stats = deck.get("stats") or {}
        pacing = deck_stats.get("pacing") or {}
        velocity = deck_stats.get("velocity") or {}

        # Draw deficit
        if (pacing.get("draw_engine_count", 0) < 5 or pacing.get("draw_total", 0) < 8) and (
            classification.get("is_draw") or classification.get("draw_type") == "engine"
        ):
            score += 15.0
            reasons.append("fills deck's card draw deficit")

        # Ramp deficit
        if (velocity.get("fast_ramp_count", 0) < 5 or velocity.get("ramp_count", 0) < 9) and classification.get("is_ramp"):
            score += 15.0
            reasons.append("enhances early mana acceleration")

        # Removal deficit
        if (pacing.get("targeted_removal_count", 0) < 6) and classification.get("is_targeted_removal"):
            score += 12.0
            reasons.append("fills instant-speed interaction gap")

        # Board Wipe deficit
        if (pacing.get("board_wipe_count", 0) < 2) and classification.get("is_board_wipe"):
            score += 12.0
            reasons.append("provides essential board sweeper reset")

        # 3. Curated staple check
        staples = _staple_index(_is_pauper_deck(deck))
        staple_match = staples.get(clean_name) or staples.get(clean_front)
        if staple_match:
            score += 15.0
            reasons.append(f"premier format staple ({staple_match.get('category', 'Staple')})")

        # 4. Low CMC efficiency
        cmc = card_meta.get("cmc", 0.0)
        if cmc <= 2 and not profile["is_land"]:
            score += 8.0

        # Cap score between 1 and 100
        score = max(5.0, min(99.0, score))
        synergy_rating = round(score / 10.0, 1)

        # Fit Verdict
        if synergy_rating >= 9.0 or syn_pct >= 40.0:
            fit_verdict = "Essential Upgrade"
        elif synergy_rating >= 7.8 or syn_pct >= 18.0:
            fit_verdict = "High Synergy"
        elif classification.get("wincon_tags"):
            fit_verdict = "Alternative Win-Con"
        elif synergy_rating >= 6.0:
            fit_verdict = "Role Filler / Utility"
        else:
            fit_verdict = "Suboptimal / Redundant"

        # Find best cut candidate
        matched_cut = self.upgrade_engine._find_best_cut(
            cut_candidates=cut_candidates,
            target_role_or_category=role,
            used_cuts=used_cuts,
        )
        cut_name = matched_cut.get("name") if isinstance(matched_cut, dict) else str(matched_cut)
        cut_cmc = matched_cut.get("cmc") if isinstance(matched_cut, dict) else None
        cut_type = matched_cut.get("type_line") if isinstance(matched_cut, dict) else None

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
            "rationale": rationale,
            "suggested_cut": cut_name,
            "suggested_cut_cmc": cut_cmc,
            "suggested_cut_type": cut_type,
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
        gen_config = {"temperature": 0.2, "maxOutputTokens": 8192, "responseMimeType": "application/json"}
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

    def evaluate_cards_suite(
        self,
        cards: list[dict],
        decks: list[dict],
        use_gemini: bool = True,
        custom_instructions: str = "",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> dict:
        """
        Full evaluation suite for custom cards.
        Computes legality checks, algorithmic scoring, cut matching,
        and optionally enriches with Gemini AI strategic reasoning.
        Produces both Card Matrix View and Deck Breakdown View.
        """
        if not cards:
            return {"error": "No valid cards provided to evaluate."}
        if not decks:
            return {"error": "No Commander decks selected to evaluate against."}

        # Precompute deck cards sets and cut candidates per deck
        deck_cards_sets: dict[int, set[str]] = {}
        deck_cut_candidates: dict[int, list[dict]] = {}
        deck_synergies_cache: dict[int, dict] = {}

        deck_ci_cache: dict[int, set[str]] = {}

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

            # Identify cut candidates from existing deck cards
            cuts = self.upgrade_engine._identify_cut_candidates(d_cards, ai_analysis=d.get("analysis"))
            deck_cut_candidates[did] = cuts

        # Query EDHREC synergies once per unique commander (parallel, cached by provider)
        deck_synergies_cache = self._fetch_deck_synergies(decks)

        # Classify each candidate card once (deck-independent)
        card_profiles = [self.build_card_profile(card) for card in cards]

        # 1. Compute Algorithmic Baseline for Card Matrix & Deck Breakdowns
        card_matrix = []
        deck_breakdowns_map: dict[int, dict] = {}

        for d in decks:
            deck_breakdowns_map[d["id"]] = {
                "deck_id": d["id"],
                "deck_name": d["deck_name"],
                "commander_name": d.get("commander_name") or "Unspecified",
                "color_identity": sorted(deck_ci_cache[d["id"]]),
                "commander_art": d.get("commander_art"),
                "total_applicable_cards": 0,
                "applicable_cards": [],
            }

        # deck_id -> card names that are legal & not already in deck (sent to Gemini)
        eligible_pairs: dict[int, list[str]] = {}
        # (card_name_lower, deck_id) -> (matrix rec, breakdown entry) for O(1) AI overlay
        pair_index: dict[tuple[str, Any], tuple[dict, dict]] = {}

        for card, profile in zip(cards, card_profiles):
            c_name = card.get("canonical_name") or card.get("name", "")
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

                eligible_pairs.setdefault(did, []).append(c_name)

                # Algorithmic evaluation
                cuts = deck_cut_candidates[did]
                synergies = deck_synergies_cache.get(did, {})
                algo_res = self.evaluate_card_algorithmic(
                    card, d, cut_candidates=cuts, edhrec_synergies=synergies, profile=profile
                )

                rec = {
                    "deck_id": did,
                    "deck_name": d["deck_name"],
                    "commander_name": d.get("commander_name"),
                    "fit_verdict": algo_res["fit_verdict"],
                    "role": algo_res["role"],
                    "synergy_rating": algo_res["synergy_rating"],
                    "score": algo_res["score"],
                    "suggested_cut": algo_res["suggested_cut"],
                    "suggested_cut_cmc": algo_res["suggested_cut_cmc"],
                    "suggested_cut_type": algo_res["suggested_cut_type"],
                    "rationale": algo_res["rationale"],
                    "edhrec_synergy_percent": algo_res["edhrec_synergy_percent"],
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
                    "fit_verdict": algo_res["fit_verdict"],
                    "role": algo_res["role"],
                    "synergy_rating": algo_res["synergy_rating"],
                    "suggested_cut": algo_res["suggested_cut"],
                    "suggested_cut_cmc": algo_res["suggested_cut_cmc"],
                    "suggested_cut_type": algo_res["suggested_cut_type"],
                    "rationale": algo_res["rationale"],
                }
                deck_breakdowns_map[did]["applicable_cards"].append(app_card)
                pair_index[(c_name.lower(), did)] = (rec, app_card)

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
                new_commanders.append({
                    "card_name": c_name,
                    "colors": colors,
                    "type_line": card.get("type_line", ""),
                    "image_uri": card.get("image_uri", ""),
                    "archetype": "Commander Potential",
                    "rationale": f"{c_name} is a legendary creature that can helm a new Commander deck.",
                })

        result = {
            "mode": "cards",
            "executive_summary": f"Analyzed {len(cards)} candidate card(s) against {len(decks)} Commander deck(s).",
            "card_matrix": card_matrix,
            "deck_breakdowns": deck_breakdowns,
            "new_commander_opportunities": new_commanders,
            "evaluated_cards_count": len(cards),
            "evaluated_decks_count": len(decks),
            "_model_used": "algorithmic-fast-engine",
        }

        # 2. If Gemini is requested and API key is present, enrich with Gemini
        #    (skipped entirely when no card is a legal, new add for any deck — saves a full API call)
        if use_gemini and eligible_pairs:
            try:
                cut_pools = {
                    did: [c["name"] for c in cands if not c.get("is_basic")][:GEMINI_CUT_POOL_SIZE]
                    for did, cands in deck_cut_candidates.items()
                }
                gemini_res = self.evaluate_with_gemini(
                    cards=cards,
                    decks=decks,
                    custom_instructions=custom_instructions,
                    model=model,
                    api_key=api_key,
                    eligible_pairs=eligible_pairs,
                    cut_pools=cut_pools,
                )

                # Merge Gemini insights into algorithmic matrix
                result["executive_summary"] = gemini_res.get("executive_summary") or result["executive_summary"]
                result["_model_used"] = gemini_res.get("_model_used", model or "gemini-3.8-flash")
                if gemini_res.get("new_commander_opportunities"):
                    result["new_commander_opportunities"] = gemini_res["new_commander_opportunities"]

                # Overlay Gemini rationales, ratings, and cuts onto both views in one pass
                for ev in self._iter_gemini_evals(gemini_res):
                    c_low = str(ev.get("card") or ev.get("card_name") or "").strip().lower()
                    raw_did = ev.get("deck_id")
                    try:
                        did = int(raw_did)
                    except (TypeError, ValueError):
                        did = raw_did
                    hit = pair_index.get((c_low, did)) or pair_index.get((c_low, raw_did))
                    if not hit and " // " not in c_low:
                        # Gemini may return only the front face of a DFC/split card
                        hit = next(
                            (v for (n, d_id), v in pair_index.items() if d_id == did and n.split(" // ")[0] == c_low),
                            None,
                        )
                    if not hit:
                        continue
                    for target in hit:
                        if ev.get("fit_verdict"):
                            target["fit_verdict"] = ev["fit_verdict"]
                        if ev.get("role"):
                            target["role"] = ev["role"]
                        if ev.get("synergy_rating") is not None:
                            try:
                                target["synergy_rating"] = float(ev["synergy_rating"])
                            except (TypeError, ValueError):
                                pass
                        if ev.get("suggested_cut"):
                            target["suggested_cut"] = ev["suggested_cut"]
                        if ev.get("rationale"):
                            target["rationale"] = ev["rationale"]

                # Re-sort both views by updated synergy rating
                for c_entry in result["card_matrix"]:
                    c_entry["deck_recommendations"].sort(key=lambda x: -x["synergy_rating"])
                    c_entry["best_fit_deck"] = (
                        c_entry["deck_recommendations"][0]["deck_name"]
                        if c_entry["deck_recommendations"]
                        else None
                    )
                for db_info in result["deck_breakdowns"]:
                    db_info["applicable_cards"].sort(key=lambda x: -x["synergy_rating"])

            except Exception as e:
                logger.warning(f"Gemini enrichment failed; using algorithmic results: {e}")
                result["_gemini_warning"] = f"Gemini strategic enrichment skipped: {str(e)}"

        return result

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
    # Secret Lair Superdrop Integration
    # -------------------------------------------------------------------------

    def evaluate_secret_lair_suite(
        self,
        url_or_text: str,
        decks: list[dict],
        custom_instructions: str = "",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> dict:
        """
        Integrates Secret Lair Superdrop scraping, Scryfall EV calculation,
        and fleet synergy analysis. Generates EV rankings, bundle analysis,
        and feeds drop cards into the Card Matrix.
        """
        effective_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()

        # 1. Fetch & Parse announcement
        announcement = self.scraper.fetch_announcement(url_or_text)
        drops, bundles = self.scraper.parse_drops(announcement["text"], api_key=effective_key, html=announcement.get("html"))
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

        # 4. Dispatch to Gemini Fleet Synergy Advisor
        advisor = SecretLairGeminiAdvisor(api_key=effective_key, model=model)
        fleet_analysis = advisor.analyze_fleet_synergy(
            superdrop_title=announcement["title"],
            drops=enriched_drops,
            bundles=bundles,
            commander_decks=decks,
            custom_instructions=custom_instructions,
        )

        # 5. Build unified Card Matrix from drops
        card_matrix_result = self.evaluate_cards_suite(
            cards=all_drop_cards,
            decks=decks,
            use_gemini=False,  # Already analyzed via fleet_analysis
        )

        # Overlay fleet_analysis deck breakdowns into card matrix
        ai_deck_cards_map: dict[str, dict] = {}
        for db_entry in fleet_analysis.get("deck_breakdowns", []):
            did = db_entry.get("deck_id")
            for ac in db_entry.get("applicable_cards", []):
                key = (ac.get("card_name", "").lower(), did)
                ai_deck_cards_map[key] = ac

        for cm in card_matrix_result.get("card_matrix", []):
            c_low = cm["card_name"].lower()
            for rec in cm.get("deck_recommendations", []):
                ai_match = ai_deck_cards_map.get((c_low, rec["deck_id"]))
                if ai_match:
                    if ai_match.get("fit_verdict"):
                        rec["fit_verdict"] = ai_match["fit_verdict"]
                    if ai_match.get("role"):
                        rec["role"] = ai_match["role"]
                    if ai_match.get("synergy_rating") is not None:
                        rec["synergy_rating"] = float(ai_match["synergy_rating"])
                    if ai_match.get("suggested_cut"):
                        rec["suggested_cut"] = ai_match["suggested_cut"]
                    if ai_match.get("rationale"):
                        rec["rationale"] = ai_match["rationale"]

            cm["deck_recommendations"].sort(key=lambda x: -x["synergy_rating"])
            cm["best_fit_deck"] = cm["deck_recommendations"][0]["deck_name"] if cm["deck_recommendations"] else None

        return {
            "mode": "secret_lair",
            "title": announcement["title"],
            "banner_image": announcement.get("banner_image"),
            "source_url": announcement.get("source_url"),
            "drops": enriched_drops,
            "bundles": bundles,
            "fleet_analysis": fleet_analysis,
            "card_matrix": card_matrix_result.get("card_matrix", []),
            "deck_breakdowns": fleet_analysis.get("deck_breakdowns", []),
            "best_drops_to_buy": fleet_analysis.get("best_drops_to_buy", []),
            "bundle_analysis": fleet_analysis.get("bundle_analysis", {}),
            "new_commander_opportunities": fleet_analysis.get("new_commander_opportunities", []),
            "_model_used": fleet_analysis.get("_model_used", model or "gemini-3.8-flash"),
        }
