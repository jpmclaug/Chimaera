"""Timewalked-style Commander Deck Classifier: 'On Paper' Bracket + 'Feels Like' Score with Itemized Receipts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .api_clients import HttpJsonClient, ScryfallClient, SpellbookClient
from .card_db import (
    BRACKET_NAMES,
    GAME_CHANGERS,
    STRONG_STAPLES,
    classify_card_roles,
)


@dataclass
class ScoreReceipt:
    """A single itemized modifier explaining how the 'Feels Like' score was nudged."""

    delta: float
    category: str
    explanation: str
    cards: list[str] = field(default_factory=list)


@dataclass
class DeckAnalysisResult:
    """Full output of the Timewalked-style Commander deck classifier."""

    deck_name: str
    commanders: list[str]
    total_cards: int
    on_paper_bracket: int
    on_paper_label: str
    on_paper_reasons: list[str]
    feels_like_score: float
    estimated_win_turn: int
    summary_verdict: str
    receipts: list[ScoreReceipt]
    game_changers: list[str]
    combos: list[dict[str, Any]]
    package_counts: dict[str, int]
    avg_cmc_nonland: float
    packages: dict[str, list[str]] = field(default_factory=dict)
    almost_included_combos_count: int = 0
    combo_source: str = "local_combo_db"
    land_count: int = 0
    card_details: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeckClassifier:
    """Evaluates a Commander decklist into 'On Paper' and 'Feels Like' scores."""

    def __init__(self, offline: bool = False) -> None:
        self.http = HttpJsonClient(offline=offline)
        self.scryfall = ScryfallClient(self.http)
        self.spellbook = SpellbookClient(self.http)

    def classify(
        self,
        commanders: list[str],
        mainboard: dict[str, int],
        deck_name: str = "Untitled Commander Deck",
        pre_resolved_cards: list[dict[str, Any]] | None = None,
    ) -> DeckAnalysisResult:
        if pre_resolved_cards:
            self.scryfall.seed_cards(pre_resolved_cards)

        all_card_names = list(dict.fromkeys(commanders + list(mainboard.keys())))
        resolved = self.scryfall.resolve_cards(all_card_names)
        combo_data = self.spellbook.find_combos(commanders, list(mainboard.keys()))
        included_combos = combo_data.get("included", [])

        # Bucket cards into functional roles
        buckets: dict[str, list[str]] = {
            "game_changer": [],
            "strong_card": [],
            "fast_mana": [],
            "ramp": [],
            "tutor": [],
            "card_draw": [],
            "interaction": [],
            "free_interaction": [],
            "finisher": [],
            "extra_turn": [],
            "mass_land_denial": [],
        }

        nonland_cmcs: list[float] = []
        land_count = 0
        total_cards = len(commanders) + sum(mainboard.values())
        card_details: dict[str, dict[str, Any]] = {}

        for name in all_card_names:
            qty = 1 if name in commanders else mainboard.get(name, 1)
            card_obj = resolved.get(name, {"name": name, "cmc": 3.0, "type_line": "Spell"})
            roles = classify_card_roles(card_obj)

            for role in roles:
                if role in buckets and name not in buckets[role]:
                    buckets[role].append(name)

            if "Land" in card_obj.get("type_line", ""):
                land_count += qty
            else:
                nonland_cmcs.extend([float(card_obj.get("cmc", 0.0))] * qty)

            weight = GAME_CHANGERS.get(name) or STRONG_STAPLES.get(name) or 0.0
            card_details[name] = {
                "name": card_obj.get("name") or name,
                "quantity": qty,
                "is_commander": name in commanders,
                "cmc": float(card_obj.get("cmc", 0.0) or 0.0),
                "type_line": card_obj.get("type_line", ""),
                "mana_cost": card_obj.get("mana_cost", ""),
                "image_uri": card_obj.get("image_uri", ""),
                "price_usd": card_obj.get("price_usd"),
                "game_changer": bool(card_obj.get("game_changer") or name in GAME_CHANGERS),
                "weight": round(float(weight), 2),
                "roles": sorted(list(roles)),
            }

        avg_cmc = sum(nonland_cmcs) / max(len(nonland_cmcs), 1)

        # ---------------------------------------------------------------------
        # 1. Calculate "On Paper" Official WotC Bracket (1 - 5)
        # ---------------------------------------------------------------------
        gc_list = buckets["game_changer"]
        mld_list = buckets["mass_land_denial"]
        extra_turns = buckets["extra_turn"]
        two_card_combos = [c for c in included_combos if len(c.get("cards", [])) <= 2]
        fast_combos = [
            c
            for c in two_card_combos
            if str(c.get("bracket_tag", "")) in ("4", "5")
            or int(c.get("min_turn", 4)) <= 3
        ]

        on_paper = 2
        paper_reasons: list[str] = []

        if mld_list:
            on_paper = max(on_paper, 4)
            paper_reasons.append(
                f"Mass Land Denial present ({', '.join(mld_list[:3])}) -> Bracket 4 minimum"
            )

        if len(gc_list) >= 4:
            on_paper = max(on_paper, 4)
            paper_reasons.append(
                f"{len(gc_list)} Game Changers ({', '.join(gc_list[:4])}) exceeds Bracket 3 cap (max 3)"
            )
        elif 1 <= len(gc_list) <= 3:
            on_paper = max(on_paper, 3)
            paper_reasons.append(
                f"{len(gc_list)} Game Changer(s) ({', '.join(gc_list)}) -> Bracket 3 minimum"
            )

        if fast_combos:
            on_paper = max(on_paper, 4)
            c_names = " + ".join(fast_combos[0]["cards"])
            paper_reasons.append(
                f"Early 2-card infinite combo ({c_names}) -> Bracket 4 minimum"
            )
        elif two_card_combos:
            on_paper = max(on_paper, 3)
            c_names = " + ".join(two_card_combos[0]["cards"])
            paper_reasons.append(
                f"Late-game 2-card combo ({c_names}) -> Bracket 3 minimum"
            )

        if len(extra_turns) >= 2:
            on_paper = max(on_paper, 4)
            paper_reasons.append(
                f"Multiple extra-turn spells ({', '.join(extra_turns[:3])}) risk turn chaining -> Bracket 4"
            )
        elif len(extra_turns) == 1 and on_paper < 3:
            on_paper = max(on_paper, 2)

        # cEDH (Bracket 5) on-paper signature check
        if (
            on_paper >= 4
            and len(gc_list) >= 6
            and len(buckets["fast_mana"]) >= 4
            and len(buckets["free_interaction"]) >= 3
            and fast_combos
        ):
            on_paper = 5
            paper_reasons.append(
                "Full cEDH signature: fast 2-card wincons + dense fast mana + free interaction suite -> Bracket 5"
            )

        if not paper_reasons:
            paper_reasons.append(
                "0 Game Changers, no 2-card combos, no Mass Land Denial -> Bracket 2 (Core) on paper"
            )

        # ---------------------------------------------------------------------
        # 2. Calculate "Feels Like" Score (2.0 baseline + itemized receipts)
        # ---------------------------------------------------------------------
        base_score = 2.0
        receipts: list[ScoreReceipt] = []

        # (A) Individual Strong Cards & Game Changers contribution
        card_power_sum = 0.0
        notable_strong: list[str] = []
        for name in all_card_names:
            if name in GAME_CHANGERS:
                card_power_sum += GAME_CHANGERS[name]
                notable_strong.append(name)
            elif name in STRONG_STAPLES:
                card_power_sum += STRONG_STAPLES[name]
                notable_strong.append(name)

        # Diminishing returns cap so 25 staples don't push a deck to 7.0
        strong_delta = round(min(card_power_sum, 1.35), 2)
        if strong_delta >= 0.05:
            sample = ", ".join(notable_strong[:3])
            receipts.append(
                ScoreReceipt(
                    delta=strong_delta,
                    category="strong_cards",
                    explanation=f"{len(notable_strong)} high-power cards, like {sample}",
                    cards=notable_strong,
                )
            )

        # (B) Combos & Wincon Speed
        if included_combos:
            combo_delta = 0.0
            combo_names = []
            for c in included_combos:
                combo_names.append(" + ".join(c.get("cards", [])))
                if len(c.get("cards", [])) <= 2:
                    combo_delta += 0.35 if str(c.get("bracket_tag")) == "5" else 0.25
                else:
                    combo_delta += 0.12
            combo_delta = round(min(combo_delta, 0.75), 2)
            receipts.append(
                ScoreReceipt(
                    delta=combo_delta,
                    category="combos",
                    explanation=f"{len(included_combos)} combo line(s) ({'; '.join(combo_names[:2])})",
                    cards=combo_names,
                )
            )

        # (C) Fast Mana burst potential
        fast_mana_cards = buckets["fast_mana"]
        if len(fast_mana_cards) >= 2:
            fm_delta = round(min(len(fast_mana_cards) * 0.06, 0.45), 2)
            receipts.append(
                ScoreReceipt(
                    delta=fm_delta,
                    category="fast_mana",
                    explanation=f"{len(fast_mana_cards)} fast mana accelerants, like {', '.join(fast_mana_cards[:3])}",
                    cards=fast_mana_cards,
                )
            )

        # (D) Tutor density & consistency
        tutors = buckets["tutor"]
        if tutors:
            tutor_delta = round(min(len(tutors) * 0.06, 0.40), 2)
            receipts.append(
                ScoreReceipt(
                    delta=tutor_delta,
                    category="tutors",
                    explanation=f"{len(tutors)} tutor(s) increasing consistency, like {', '.join(tutors[:3])}",
                    cards=tutors,
                )
            )

        # (E) Finishers & Closing Power
        finishers = buckets["finisher"]
        if finishers:
            fin_delta = round(min(len(finishers) * 0.04, 0.24), 2)
            receipts.append(
                ScoreReceipt(
                    delta=fin_delta,
                    category="finishers",
                    explanation=f"{len(finishers)} real finisher(s), like {', '.join(finishers[:2])}",
                    cards=finishers,
                )
            )

        # (F) Ramp & Mana Consistency
        ramp_cards = buckets["ramp"]
        if len(ramp_cards) >= 8:
            ramp_delta = round(min(0.05 + (len(ramp_cards) - 8) * 0.015, 0.18), 2)
            receipts.append(
                ScoreReceipt(
                    delta=ramp_delta,
                    category="ramp",
                    explanation=f"dense ramp package ({len(ramp_cards)} ramp pieces)",
                    cards=ramp_cards,
                )
            )
        elif len(ramp_cards) <= 3 and len(fast_mana_cards) <= 1:
            receipts.append(
                ScoreReceipt(
                    delta=-0.12,
                    category="ramp_deficit",
                    explanation=f"light mana acceleration ({len(ramp_cards)} ramp cards)",
                    cards=ramp_cards,
                )
            )

        # (G) Free Interaction Suite
        free_int = buckets["free_interaction"]
        if free_int:
            fi_delta = round(min(len(free_int) * 0.06, 0.30), 2)
            receipts.append(
                ScoreReceipt(
                    delta=fi_delta,
                    category="free_interaction",
                    explanation=f"{len(free_int)} zero-mana interaction spell(s), like {', '.join(free_int[:2])}",
                    cards=free_int,
                )
            )

        # (H) Mana Curve Efficiency adjustment
        if nonland_cmcs:
            if avg_cmc <= 2.15:
                receipts.append(
                    ScoreReceipt(
                        delta=0.22,
                        category="mana_curve",
                        explanation=f"razor-lean mana curve (avg MV {avg_cmc:.2f})",
                    )
                )
            elif avg_cmc <= 2.65:
                receipts.append(
                    ScoreReceipt(
                        delta=0.10,
                        category="mana_curve",
                        explanation=f"low, efficient mana curve (avg MV {avg_cmc:.2f})",
                    )
                )
            elif avg_cmc >= 3.75:
                receipts.append(
                    ScoreReceipt(
                        delta=-0.18,
                        category="mana_curve",
                        explanation=f"heavy mana curve slows deployment (avg MV {avg_cmc:.2f})",
                    )
                )

        raw_feels_like = base_score + sum(r.delta for r in receipts)
        feels_like = round(max(1.0, min(5.0, raw_feels_like)), 1)

        # Sort receipts by magnitude descending
        receipts.sort(key=lambda r: abs(r.delta), reverse=True)

        # ---------------------------------------------------------------------
        # 3. Estimate Win Turn & Generate Human Summary Verdict
        # ---------------------------------------------------------------------
        estimated_turn = max(3, min(11, round(10.5 - (feels_like - 1.5) * 2.1)))
        if fast_combos and len(fast_mana_cards) >= 4 and len(tutors) >= 4:
            estimated_turn = min(estimated_turn, 3)

        if feels_like >= on_paper + 0.5:
            verdict = (
                f"Bracket {on_paper} on paper, but plays closer to a {round(feels_like)} "
                f"({feels_like}). High synergy, ramp/tutors, or finishers punch above its paper rules."
            )
        elif feels_like <= on_paper - 0.5:
            verdict = (
                f"Technically Bracket {on_paper} on paper due to flagged cards, "
                f"but plays like a {feels_like}. Worth a quick Rule Zero note."
            )
        else:
            verdict = (
                f"Solid Bracket {on_paper} ({BRACKET_NAMES[on_paper]}), playing right around {feels_like} "
                f"and usually threatening a win around turn {estimated_turn}."
            )

        return DeckAnalysisResult(
            deck_name=deck_name,
            commanders=commanders,
            total_cards=total_cards,
            on_paper_bracket=on_paper,
            on_paper_label=BRACKET_NAMES.get(on_paper, "Core"),
            on_paper_reasons=paper_reasons,
            feels_like_score=feels_like,
            estimated_win_turn=estimated_turn,
            summary_verdict=verdict,
            receipts=receipts,
            game_changers=gc_list,
            combos=included_combos,
            package_counts={k: len(v) for k, v in buckets.items()},
            avg_cmc_nonland=round(avg_cmc, 2),
            packages=buckets,
            almost_included_combos_count=int(combo_data.get("almost_included_count", 0) or 0),
            combo_source=str(combo_data.get("source", "local_combo_db")),
            land_count=land_count,
            card_details=card_details,
        )
