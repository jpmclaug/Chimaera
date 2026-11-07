"""Timewalked-style Suggested Upgrades engine with collection binder matching.

Follows Timewalked.com's core methodology:
- Primary ranking by EDHREC inclusion percentage ('Popular with Commander')
- Boosted by Commander Spellbook 'One Card Away' near-combos (+25 score)
- Boosted by Timewalked Card Ratio targets (+15 short, +7 close)
- Greedily pairs each suggestion with a data-driven cut candidate (lowest inclusion % nonland / land-for-land)
- Splits output into 'Free Upgrades' (owned in binder / inventory) and 'Buy List'
- Flags cards that exceed Bracket 3 Game Changer threshold (max 3 Game Changers)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .api_clients import ScryfallClient, SpellbookClient
from .card_db import GAME_CHANGERS, STRONG_STAPLES, classify_card_roles

# Timewalked's exact DEFAULT_RATIO_TARGETS from deck-builder.js
DEFAULT_RATIO_TARGETS: dict[str, int] = {
    "card_advantage": 12,
    "ramp": 10,
    "protection": 8,
    "removal": 8,
    "recursion": 1,
    "tutor": 1,
    "finisher": 2,
    "lifegain": 0,
    "lands": 34,
}

RATIO_CATEGORY_LABELS: dict[str, str] = {
    "card_advantage": "Card advantage",
    "ramp": "Ramp",
    "protection": "Protection",
    "removal": "Removal & disruption",
    "recursion": "Recursion",
    "tutor": "Tutors",
    "finisher": "Finisher",
    "lifegain": "Lifegain",
    "lands": "Lands",
}


def ratio_state_for(count: int | None, target: int | None) -> str | None:
    """Returns 'met' (>= target), 'close' (>= 80% target), or 'short' (< 80%)."""
    if count is None or target is None or target <= 0:
        return "met" if (count is not None and count > 0) else None
    if count >= target:
        return "met"
    return "close" if count >= target * 0.8 else "short"


def classify_timewalked_categories(card: dict[str, Any]) -> set[str]:
    """Map card to Timewalked's 9 core ratio categories."""
    cats: set[str] = set()
    roles = classify_card_roles(card)
    type_line = card.get("type_line", "")
    oracle = (card.get("oracle_text") or "").lower()

    if "Land" in type_line:
        cats.add("lands")

    if "card_draw" in roles:
        cats.add("card_advantage")

    if "ramp" in roles or "fast_mana" in roles:
        cats.add("ramp")

    if "tutor" in roles:
        cats.add("tutor")

    if "finisher" in roles:
        cats.add("finisher")

    if "interaction" in roles or "free_interaction" in roles:
        cats.add("removal")

    # Protection cues
    if re.search(r"hexproof|indestructible|phase out|protection from|can't be countered|shroud|ward\b", oracle):
        cats.add("protection")
    if any(name in card.get("name", "") for name in ["Teferi's Protection", "Heroic Intervention", "Flawless Maneuver", "Boros Charm", "Clever Concealment", "Lightning Greaves", "Swiftfoot Boots"]):
        cats.add("protection")

    # Recursion cues
    if re.search(r"return .* from your graveyard|put .* from your graveyard|cast .* from your graveyard", oracle):
        cats.add("recursion")
    if any(name in card.get("name", "") for name in ["Eternal Witness", "Sun Titan", "Reanimate", "Animate Dead", "Regrowth", "Bala Ged Recovery"]):
        cats.add("recursion")

    # Lifegain cues
    if "you gain" in oracle and "life" in oracle:
        cats.add("lifegain")

    return cats


def compute_ratio_breakdown(
    resolved_cards: dict[str, dict[str, Any]],
    deck_counts: dict[str, int],
    targets: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute current counts and target status across all Timewalked ratio categories."""
    active_targets = targets or DEFAULT_RATIO_TARGETS
    counts: dict[str, int] = {k: 0 for k in active_targets}

    for name, qty in deck_counts.items():
        card = resolved_cards.get(name) or {"name": name, "type_line": "Spell"}
        cats = classify_timewalked_categories(card)
        for cat in cats:
            if cat in counts:
                counts[cat] += qty

    breakdown: dict[str, dict[str, Any]] = {}
    for cat, target in active_targets.items():
        cnt = counts.get(cat, 0)
        state = ratio_state_for(cnt, target)
        breakdown[cat] = {
            "key": cat,
            "label": RATIO_CATEGORY_LABELS.get(cat, cat.title()),
            "count": cnt,
            "target": target,
            "state": state,
        }
    return breakdown


@dataclass
class UpgradeSuggestion:
    card_in: str
    score: float
    inclusion_pct: float
    reasons: list[str]
    signals: list[str]  # ['edhrec', 'ratio', 'combo']
    categories: list[str]
    completes_combos: list[dict[str, Any]]
    card_out: str | None
    cut_reason: str
    cut_inclusion_pct: float | None
    is_owned: bool
    owned_qty: int
    binders: list[str]
    in_other_decks: list[str]
    price_usd: float | None
    image_uri: str
    type_line: str
    cmc: float
    is_game_changer: bool
    bracket_warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SuggestedUpgradeEngine:
    """Generates Timewalked-style suggested upgrades comparing against binder inventory."""

    def __init__(
        self,
        edhrec_fetcher: Callable[[str, Optional[str]], Optional[dict[str, Any]]],
        scryfall: Optional[ScryfallClient] = None,
        spellbook: Optional[SpellbookClient] = None,
    ) -> None:
        self.edhrec_fetcher = edhrec_fetcher
        self.scryfall = scryfall or ScryfallClient()
        self.spellbook = spellbook or SpellbookClient()

    @staticmethod
    def _clean_card_key(name: str) -> str:
        s = name.strip().lower()
        if " // " in s:
            s = s.split(" // ")[0].strip()
        # strip common punctuation
        return re.sub(r"['’,\-\s]+", "", s)

    def suggest(
        self,
        commanders: list[str],
        mainboard: dict[str, int],
        owned_index: dict[str, dict[str, Any]],
        allocations: Optional[dict[str, dict[str, Any]]] = None,
        on_paper_bracket: Optional[int] = None,
        game_changer_count: int = 0,
        dismissed_names: Optional[list[str]] = None,
        limit_per_tier: int = 30,
        pre_resolved_cards: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Main evaluation routine."""
        if pre_resolved_cards:
            self.scryfall.seed_cards(pre_resolved_cards)

        dismissed_set = {self._clean_card_key(n) for n in (dismissed_names or []) if n}

        # Identify existing deck cards
        deck_all_names = list(dict.fromkeys(commanders + list(mainboard.keys())))
        deck_resolved = self.scryfall.resolve_cards(deck_all_names)

        deck_keys: set[str] = set()
        for name in deck_all_names:
            deck_keys.add(self._clean_card_key(name))
            if " // " in name:
                deck_keys.add(self._clean_card_key(name.split(" // ")[0]))

        # 1. Compute deck ratios
        ratio_breakdown = compute_ratio_breakdown(deck_resolved, mainboard)

        # 2. Fetch EDHREC commander profile
        primary_cmdr = commanders[0] if commanders else ""
        partner_cmdr = commanders[1] if len(commanders) > 1 else None
        edh_data = self.edhrec_fetcher(primary_cmdr, partner_cmdr) or {}
        edh_synergies: dict[str, dict[str, Any]] = edh_data.get("card_synergies") or {}
        edh_total_decks = int(edh_data.get("num_decks") or 0)

        # Map clean keys to EDHREC inclusion data
        edh_by_clean: dict[str, dict[str, Any]] = {}
        for c_lower, item in edh_synergies.items():
            ck = self._clean_card_key(item.get("name") or c_lower)
            if ck not in edh_by_clean or item.get("inclusion_percent", 0) > edh_by_clean[ck].get("inclusion_percent", 0):
                edh_by_clean[ck] = item

        # 3. Find 'One Card Away' combos from Spellbook
        combo_results = self.spellbook.find_combos(commanders, list(mainboard.keys()))
        almost_combos = combo_results.get("almost_included") or []

        combos_missing_card: dict[str, list[dict[str, Any]]] = {}
        for ac in almost_combos:
            missing_list = ac.get("missing") or []
            if len(missing_list) == 1:
                missing_name = missing_list[0]
                ck = self._clean_card_key(missing_name)
                combos_missing_card.setdefault(ck, []).append(ac)

        # 4. Gather candidate cards
        candidates_dict: dict[str, dict[str, Any]] = {}

        # A) From EDHREC
        for ck, item in edh_by_clean.items():
            if ck in deck_keys or ck in dismissed_set:
                continue
            card_name = item.get("name") or ""
            if not card_name:
                continue
            # Skip basic lands
            if card_name in {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}:
                continue
            inc_pct = float(item.get("inclusion_percent") or 0.0)
            if inc_pct >= 3.0:  # Include meaningful staples & popular picks
                candidates_dict[ck] = {
                    "name": card_name,
                    "inclusion_percent": inc_pct,
                    "source": "edhrec",
                }

        # B) From Missing Combos (even if low EDHREC inclusion)
        for ck, combo_list in combos_missing_card.items():
            if ck in deck_keys or ck in dismissed_set:
                continue
            if ck not in candidates_dict:
                rep_name = combo_list[0].get("missing", [""])[0]
                candidates_dict[ck] = {
                    "name": rep_name,
                    "inclusion_percent": 0.0,
                    "source": "spellbook_combo",
                }

        # Resolve candidates metadata with Scryfall
        cand_names = [v["name"] for v in candidates_dict.values()]
        cand_resolved = self.scryfall.resolve_cards(cand_names)

        # 5. Score candidates
        scored_candidates: list[dict[str, Any]] = []
        for ck, cand in candidates_dict.items():
            card_name = cand["name"]
            card_obj = cand_resolved.get(card_name) or {"name": card_name, "type_line": "Card", "cmc": 3.0}
            legalities = card_obj.get("legalities") or {}
            if legalities.get("commander") not in ("legal", None):
                continue

            cats = classify_timewalked_categories(card_obj)
            inc_pct = float(cand.get("inclusion_percent") or 0.0)

            score = inc_pct
            reasons: list[str] = []
            signals: list[str] = ["edhrec"]

            if inc_pct > 0:
                reasons.append(f"{round(inc_pct)}% of {primary_cmdr or 'this commander'} decks")

            # Ratio gap boost
            best_ratio_boost = 0.0
            ratio_reason = ""
            for cat in cats:
                r_info = ratio_breakdown.get(cat)
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

            # Combo completion boost
            cm_combos = combos_missing_card.get(ck) or []
            if cm_combos:
                combo_boost = 25.0 + min(len(cm_combos) - 1, 2) * 10.0
                score += combo_boost
                signals.append("combo")
                partner_pieces = []
                for ac in cm_combos:
                    other_cards = [c for c in ac.get("cards", []) if self._clean_card_key(c) != ck]
                    if other_cards:
                        partner_pieces.append(other_cards[0])
                partner_summary = ", ".join(partner_pieces[:2]) if partner_pieces else "existing pieces"
                reasons.append(f"Completes combo with {partner_summary}")

            # Check Game Changer status & Bracket 3 guardrail
            is_gc = bool(card_obj.get("game_changer") or card_name in GAME_CHANGERS)
            bracket_warning = None
            if is_gc:
                current_gc = game_changer_count
                if on_paper_bracket is not None and on_paper_bracket <= 3 and current_gc >= 3:
                    bracket_warning = f"Adding this Game Changer would raise deck to Bracket 4 (exceeds 3 GC cap: {current_gc + 1} total)"
                elif on_paper_bracket == 2 and current_gc == 0:
                    bracket_warning = "Official Game Changer (will bump deck from Bracket 2 to Bracket 3)"

            scored_candidates.append({
                "clean_key": ck,
                "name": card_name,
                "card_obj": card_obj,
                "score": round(score, 1),
                "inclusion_pct": inc_pct,
                "reasons": reasons,
                "signals": signals,
                "categories": sorted(list(cats)),
                "completes_combos": cm_combos,
                "is_game_changer": is_gc,
                "bracket_warning": bracket_warning,
            })

        # Sort descending by score, then inclusion_pct
        scored_candidates.sort(key=lambda x: (x["score"], x["inclusion_pct"]), reverse=True)

        # 6. Build Cut Candidates Pool from Deck
        deck_cut_pool: list[dict[str, Any]] = []
        for name, qty in mainboard.items():
            if name in commanders:
                continue
            card_obj = deck_resolved.get(name) or {"name": name, "type_line": "Spell", "cmc": 3.0}
            type_line = card_obj.get("type_line", "")
            is_basic = name in {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
            is_gc = bool(card_obj.get("game_changer") or name in GAME_CHANGERS)
            ck = self._clean_card_key(name)
            is_part_of_combo = False  # avoid cutting active combo pieces
            inc_pct = edh_by_clean.get(ck, {}).get("inclusion_percent", 0.0)
            deck_cut_pool.append({
                "name": name,
                "clean_key": ck,
                "card_obj": card_obj,
                "type_line": type_line,
                "is_land": "Land" in type_line,
                "is_basic": is_basic,
                "is_game_changer": is_gc,
                "is_part_of_combo": is_part_of_combo,
                "inclusion_pct": float(inc_pct),
                "cmc": float(card_obj.get("cmc", 0.0)),
                "categories": classify_timewalked_categories(card_obj),
            })

        # 7. Match Cuts & Split into Free vs Buy
        used_cuts: set[str] = set()
        free_upgrades: list[UpgradeSuggestion] = []
        buy_upgrades: list[UpgradeSuggestion] = []

        total_free_value = 0.0
        total_buy_cost = 0.0

        for cand in scored_candidates:
            card_name = cand["name"]
            card_obj = cand["card_obj"]
            ck = cand["clean_key"]

            # Cut Selection Algorithm
            is_cand_land = "lands" in cand["categories"]
            matched_cut: Optional[dict[str, Any]] = None
            cut_reason = "Lowest synergy / meta inclusion in deck"

            eligible_cuts = [
                c for c in deck_cut_pool
                if c["clean_key"] not in used_cuts
                and not c["is_game_changer"]
                and not c["is_part_of_combo"]
            ]

            if is_cand_land:
                # Land for land: pick lowest inclusion land
                land_cuts = [c for c in eligible_cuts if c["is_land"]]
                if land_cuts:
                    # Nonbasics with low inclusion first, basics last
                    land_cuts.sort(key=lambda x: (x["is_basic"], x["inclusion_pct"]))
                    matched_cut = land_cuts[0]
                    cut_reason = f"Replaces land ({round(matched_cut['inclusion_pct'])}% inclusion)"
            else:
                # Nonland: try same category first if category stays healthy
                cand_cats = set(cand["categories"]) - {"lands"}
                same_cat_cuts = [
                    c for c in eligible_cuts
                    if not c["is_land"] and not c["is_basic"] and (cand_cats & c["categories"])
                ]
                if same_cat_cuts:
                    same_cat_cuts.sort(key=lambda x: (x["inclusion_pct"], -x["cmc"]))
                    matched_cut = same_cat_cuts[0]
                    intersect = list(cand_cats & matched_cut["categories"])[0]
                    lbl = RATIO_CATEGORY_LABELS.get(intersect, intersect.title())
                    cut_reason = f"Same-role swap: replaces {lbl} ({round(matched_cut['inclusion_pct'])}% inclusion)"
                else:
                    # Pick lowest inclusion nonland card
                    nonland_cuts = [c for c in eligible_cuts if not c["is_land"]]
                    if nonland_cuts:
                        nonland_cuts.sort(key=lambda x: (x["inclusion_pct"], -x["cmc"]))
                        matched_cut = nonland_cuts[0]
                        cut_reason = f"Lowest inclusion card in deck ({round(matched_cut['inclusion_pct'])}% inclusion)"

            if matched_cut:
                used_cuts.add(matched_cut["clean_key"])
                cut_name = matched_cut["name"]
                cut_pct = matched_cut["inclusion_pct"]
            else:
                cut_name = None
                cut_pct = None
                cut_reason = "No clean cut available (consider testing in maybeboard)"

            # Check Collection Ownership
            owned_data = owned_index.get(ck)
            is_owned = False
            owned_qty = 0
            binders: list[str] = []
            other_decks: list[str] = []

            if owned_data and owned_data.get("qty", 0) > 0:
                is_owned = True
                owned_qty = int(owned_data.get("qty", 0))
                binders = sorted(list(owned_data.get("binders") or []))

                # Check cross-deck allocations
                if allocations and ck in allocations:
                    for d in allocations[ck].get("decks", []):
                        if not d.get("is_current") and d.get("deck_name"):
                            other_decks.append(d["deck_name"])

            price = card_obj.get("price_usd")
            img = card_obj.get("image_uri") or ""

            suggestion = UpgradeSuggestion(
                card_in=card_name,
                score=cand["score"],
                inclusion_pct=cand["inclusion_pct"],
                reasons=cand["reasons"],
                signals=cand["signals"],
                categories=cand["categories"],
                completes_combos=cand["completes_combos"],
                card_out=cut_name,
                cut_reason=cut_reason,
                cut_inclusion_pct=cut_pct,
                is_owned=is_owned,
                owned_qty=owned_qty,
                binders=binders,
                in_other_decks=other_decks,
                price_usd=price,
                image_uri=img,
                type_line=card_obj.get("type_line", ""),
                cmc=float(card_obj.get("cmc", 0.0)),
                is_game_changer=cand["is_game_changer"],
                bracket_warning=cand["bracket_warning"],
            )

            if is_owned:
                if len(free_upgrades) < limit_per_tier:
                    free_upgrades.append(suggestion)
                total_free_value += float(price or 0.0)
            else:
                if len(buy_upgrades) < limit_per_tier:
                    buy_upgrades.append(suggestion)
                total_buy_cost += float(price or 0.0)

        return {
            "commander": primary_cmdr,
            "edhrec_decks": edh_total_decks,
            "ratios": ratio_breakdown,
            "near_combos_count": len(almost_combos),
            "free_upgrades": [u.to_dict() for u in free_upgrades],
            "buy_upgrades": [u.to_dict() for u in buy_upgrades],
            "free_count": len(free_upgrades),
            "buy_count": len(buy_upgrades),
            "free_value_usd": round(total_free_value, 2),
            "buy_total_usd": round(total_buy_cost, 2),
            "initial_display_count": 6,
        }
