"""
Deck Comparison Engine for MTG Commander Decks.
Compares two analyzed deck payloads (Deck A and Deck B) and computes:
- Statistical Delta Matrix (Delta AMV, Fast Ramp, Instant Speed, Tapland Penalty, Removal Efficiency, Value)
- Categorized Profiles (Interaction, Card Advantage, Velocity, Mana Base)
- Archetype classification heuristics (Aggro/Fast Combo, Midrange/Engine, Battlecruiser/Big Mana)
- Shared staples and unique cards matrix
"""

from typing import Dict, Any, List, Optional
from deck_analyzer import DeckAnalyzer


class DeckComparator:
    """Computes side-by-side comparative matrices and differential profiles between Commander decks."""

    def __init__(self):
        self.analyzer = DeckAnalyzer()

    def determine_archetype(self, stats: Dict[str, Any]) -> str:
        """
        Determines the heuristic archetype based on AMV, ramp velocity, draw engines, and interaction density.
        - Aggro / Fast Combo: AMV < 2.7, Fast Ramp >= 8, Instant Speed >= 25%
        - Midrange / Engine: AMV 2.7 - 3.4, Draw Engines >= 5, Balanced Removal
        - Battlecruiser / Big Mana: AMV > 3.4, Ramp >= 12, Board Wipes >= 3
        """
        amv = float(stats.get("nonland_amv") or stats.get("avg_cmc") or 0.0)
        fast_ramp = int(stats.get("fast_ramp_count") or 0)
        total_ramp = int(stats.get("total_ramp_count") or 0)
        instant_speed = float(stats.get("instant_speed_ratio") or 0.0)
        draw_engines = int(stats.get("draw_engine_count") or 0)
        targeted_removal = int(stats.get("targeted_removal_count") or 0)
        board_wipes = int(stats.get("board_wipe_count") or 0)

        if amv < 2.7 and fast_ramp >= 8 and instant_speed >= 25.0:
            return "Aggro / Fast Combo"
        elif 2.7 <= amv <= 3.4 and draw_engines >= 5 and (targeted_removal >= 5 or (targeted_removal + board_wipes) >= 6):
            return "Midrange / Engine"
        elif amv > 3.4 and total_ramp >= 12 and board_wipes >= 3:
            return "Battlecruiser / Big Mana"
        elif amv < 2.75:
            return "Aggro / Fast Combo"
        elif amv > 3.4:
            return "Battlecruiser / Big Mana"
        elif instant_speed >= 25.0 or (targeted_removal + board_wipes >= 10):
            return "Control / Interactive"
        elif draw_engines >= 4:
            return "Midrange / Engine"
        return "Midrange / Engine"

    def compare(self, deck_a_payload: Dict[str, Any], deck_b_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compares two deck payloads and returns the comprehensive delta matrix and side-by-side profile.
        """
        # Ensure decks are fully analyzed
        if isinstance(deck_a_payload, dict) and "stats" in deck_a_payload and deck_a_payload["stats"]:
            deck_a = deck_a_payload
            stats_a = deck_a["stats"]
        else:
            stats_a = self.analyzer.analyze(deck_a_payload)
            deck_a = {"stats": stats_a, "cards_data": deck_a_payload if isinstance(deck_a_payload, list) else deck_a_payload.get("cards", [])}

        if isinstance(deck_b_payload, dict) and "stats" in deck_b_payload and deck_b_payload["stats"]:
            deck_b = deck_b_payload
            stats_b = deck_b["stats"]
        else:
            stats_b = self.analyzer.analyze(deck_b_payload)
            deck_b = {"stats": stats_b, "cards_data": deck_b_payload if isinstance(deck_b_payload, list) else deck_b_payload.get("cards", [])}

        # Ensure archetype determination
        archetype_a = stats_a.get("archetype") or self.determine_archetype(stats_a)
        archetype_b = stats_b.get("archetype") or self.determine_archetype(stats_b)
        stats_a["archetype"] = archetype_a
        stats_b["archetype"] = archetype_b

        # 1. Delta Matrix (A - B)
        amv_a = float(stats_a.get("nonland_amv", stats_a.get("avg_cmc", 0.0)))
        amv_b = float(stats_b.get("nonland_amv", stats_b.get("avg_cmc", 0.0)))
        delta_amv = round(amv_a - amv_b, 2)

        fast_ramp_a = int(stats_a.get("fast_ramp_count", 0))
        fast_ramp_b = int(stats_b.get("fast_ramp_count", 0))
        delta_fast_ramp = fast_ramp_a - fast_ramp_b

        instant_speed_a = float(stats_a.get("instant_speed_ratio", 0.0))
        instant_speed_b = float(stats_b.get("instant_speed_ratio", 0.0))
        delta_instant_speed = round(instant_speed_a - instant_speed_b, 1)

        tapland_penalty_a = float(stats_a.get("tapland_penalty_index", 0.0))
        tapland_penalty_b = float(stats_b.get("tapland_penalty_index", 0.0))
        delta_tapland_penalty = round(tapland_penalty_a - tapland_penalty_b, 1)

        removal_eff_a = float(stats_a.get("removal_mana_efficiency", 0.0))
        removal_eff_b = float(stats_b.get("removal_mana_efficiency", 0.0))
        delta_removal_efficiency = round(removal_eff_a - removal_eff_b, 2)

        val_a = float(stats_a.get("total_value", 0.0))
        val_b = float(stats_b.get("total_value", 0.0))
        delta_value = round(val_a - val_b, 2)

        draw_engines_a = int(stats_a.get("draw_engine_count", 0))
        draw_engines_b = int(stats_b.get("draw_engine_count", 0))
        delta_draw_engines = draw_engines_a - draw_engines_b

        draw_burst_a = int(stats_a.get("draw_burst_count", 0))
        draw_burst_b = int(stats_b.get("draw_burst_count", 0))
        delta_draw_burst = draw_burst_a - draw_burst_b

        draw_cantrip_a = int(stats_a.get("draw_cantrip_count", 0))
        draw_cantrip_b = int(stats_b.get("draw_cantrip_count", 0))
        delta_draw_cantrip = draw_cantrip_a - draw_cantrip_b

        tutors_a = int(stats_a.get("tutor_general_count", 0))
        tutors_b = int(stats_b.get("tutor_general_count", 0))
        delta_tutors = tutors_a - tutors_b

        tutors_land_a = int(stats_a.get("tutor_land_count", 0))
        tutors_land_b = int(stats_b.get("tutor_land_count", 0))
        delta_tutors_land = tutors_land_a - tutors_land_b

        dork_rock_a = int(stats_a.get("dork_or_rock_count", 0))
        dork_rock_b = int(stats_b.get("dork_or_rock_count", 0))
        delta_dork_rock = dork_rock_a - dork_rock_b

        land_fetch_a = int(stats_a.get("land_fetch_count", 0))
        land_fetch_b = int(stats_b.get("land_fetch_count", 0))
        delta_land_fetch = land_fetch_a - land_fetch_b

        treasure_a = int(stats_a.get("treasure_count", 0))
        treasure_b = int(stats_b.get("treasure_count", 0))
        delta_treasure = treasure_a - treasure_b

        targeted_removal_a = int(stats_a.get("targeted_removal_count", 0))
        targeted_removal_b = int(stats_b.get("targeted_removal_count", 0))
        delta_targeted_removal = targeted_removal_a - targeted_removal_b

        board_wipes_a = int(stats_a.get("board_wipe_count", 0))
        board_wipes_b = int(stats_b.get("board_wipe_count", 0))
        delta_board_wipes = board_wipes_a - board_wipes_b

        # Advanced Telemetry Metrics
        keep_a = float(stats_a.get("effective_keepability_rate") or (stats_a.get("opening_hand_keepability", {}).get("effective_keep_rate", 0.0)))
        keep_b = float(stats_b.get("effective_keepability_rate") or (stats_b.get("opening_hand_keepability", {}).get("effective_keep_rate", 0.0)))
        delta_keep = round(keep_a - keep_b, 1)

        cast_a = int(stats_a.get("median_commander_cast_turn") or (stats_a.get("earliest_commander_cast", {}).get("median_cast_turn", 4)))
        cast_b = int(stats_b.get("median_commander_cast_turn") or (stats_b.get("earliest_commander_cast", {}).get("median_cast_turn", 4)))
        delta_cast = cast_a - cast_b

        holdout_a = float(stats_a.get("avg_instant_holdout") or (stats_a.get("instant_mana_holdout", {}).get("avg_holdout_cmc", 0.0)))
        holdout_b = float(stats_b.get("avg_instant_holdout") or (stats_b.get("instant_mana_holdout", {}).get("avg_holdout_cmc", 0.0)))
        delta_holdout = round(holdout_a - holdout_b, 2)

        sinks_a = int(stats_a.get("total_mana_sinks") or (stats_a.get("mana_sinks", {}).get("total_sinks", 0)))
        sinks_b = int(stats_b.get("total_mana_sinks") or (stats_b.get("mana_sinks", {}).get("total_sinks", 0)))
        delta_sinks = sinks_a - sinks_b

        virt_a = int(stats_a.get("total_virtual_advantage") or (stats_a.get("virtual_card_advantage", {}).get("total_virtual_advantage", 0)))
        virt_b = int(stats_b.get("total_virtual_advantage") or (stats_b.get("virtual_card_advantage", {}).get("total_virtual_advantage", 0)))
        delta_virt = virt_a - virt_b

        land3_a = float(stats_a.get("land_drop_turn_3_pct") or (stats_a.get("land_drop_probabilities", {}).get("turn_3", 0.0)))
        land3_b = float(stats_b.get("land_drop_turn_3_pct") or (stats_b.get("land_drop_probabilities", {}).get("turn_3", 0.0)))
        delta_land3 = round(land3_a - land3_b, 1)

        def get_adv(delta, lower_is_better=False):
            if delta == 0:
                return "Tie"
            if lower_is_better:
                return "deck_a" if delta < 0 else "deck_b"
            return "deck_a" if delta > 0 else "deck_b"

        delta_matrix = {
            "nonland_amv": {
                "metric": "Nonland AMV",
                "deck_a": amv_a,
                "deck_b": amv_b,
                "delta": delta_amv,
                "unit": "CMC",
                "advantage": get_adv(delta_amv, lower_is_better=True),
                "better": "lower" if delta_amv < 0 else ("higher" if delta_amv > 0 else "equal"),
            },
            "amv": {
                "metric": "Nonland AMV",
                "deck_a": amv_a,
                "deck_b": amv_b,
                "delta": delta_amv,
                "unit": "CMC",
                "advantage": get_adv(delta_amv, lower_is_better=True),
                "better": "lower" if delta_amv < 0 else ("higher" if delta_amv > 0 else "equal"),
            },
            "fast_ramp": {
                "metric": "Fast Ramp (CMC <= 2)",
                "deck_a": fast_ramp_a,
                "deck_b": fast_ramp_b,
                "delta": delta_fast_ramp,
                "unit": "cards",
                "advantage": get_adv(delta_fast_ramp, lower_is_better=False),
                "better": "higher" if delta_fast_ramp > 0 else ("lower" if delta_fast_ramp < 0 else "equal"),
            },
            "dork_or_rock_ramp": {
                "metric": "Mana Dorks & Rocks",
                "deck_a": dork_rock_a,
                "deck_b": dork_rock_b,
                "delta": delta_dork_rock,
                "unit": "cards",
                "advantage": get_adv(delta_dork_rock, lower_is_better=False),
                "better": "higher" if delta_dork_rock > 0 else ("lower" if delta_dork_rock < 0 else "equal"),
            },
            "land_fetch_ramp": {
                "metric": "Land Ramp Spells",
                "deck_a": land_fetch_a,
                "deck_b": land_fetch_b,
                "delta": delta_land_fetch,
                "unit": "cards",
                "advantage": get_adv(delta_land_fetch, lower_is_better=False),
                "better": "higher" if delta_land_fetch > 0 else ("lower" if delta_land_fetch < 0 else "equal"),
            },
            "treasure_ramp": {
                "metric": "Treasure Creation",
                "deck_a": treasure_a,
                "deck_b": treasure_b,
                "delta": delta_treasure,
                "unit": "cards",
                "advantage": get_adv(delta_treasure, lower_is_better=False),
                "better": "higher" if delta_treasure > 0 else ("lower" if delta_treasure < 0 else "equal"),
            },
            "instant_speed_ratio": {
                "metric": "Instant Speed Ratio",
                "deck_a": instant_speed_a,
                "deck_b": instant_speed_b,
                "delta": delta_instant_speed,
                "unit": "%",
                "advantage": get_adv(delta_instant_speed, lower_is_better=False),
                "better": "higher" if delta_instant_speed > 0 else ("lower" if delta_instant_speed < 0 else "equal"),
            },
            "instant_speed": {
                "metric": "Instant Speed Ratio",
                "deck_a": instant_speed_a,
                "deck_b": instant_speed_b,
                "delta": delta_instant_speed,
                "unit": "%",
                "advantage": get_adv(delta_instant_speed, lower_is_better=False),
                "better": "higher" if delta_instant_speed > 0 else ("lower" if delta_instant_speed < 0 else "equal"),
            },
            "tapland_penalty_index": {
                "metric": "Tapland Penalty Index",
                "deck_a": tapland_penalty_a,
                "deck_b": tapland_penalty_b,
                "delta": delta_tapland_penalty,
                "unit": "%",
                "advantage": get_adv(delta_tapland_penalty, lower_is_better=True),
                "better": "lower" if delta_tapland_penalty < 0 else ("higher" if delta_tapland_penalty > 0 else "equal"),
            },
            "tapland_penalty": {
                "metric": "Tapland Penalty Index",
                "deck_a": tapland_penalty_a,
                "deck_b": tapland_penalty_b,
                "delta": delta_tapland_penalty,
                "unit": "%",
                "advantage": get_adv(delta_tapland_penalty, lower_is_better=True),
                "better": "lower" if delta_tapland_penalty < 0 else ("higher" if delta_tapland_penalty > 0 else "equal"),
            },
            "targeted_removal": {
                "metric": "Targeted Removal",
                "deck_a": targeted_removal_a,
                "deck_b": targeted_removal_b,
                "delta": delta_targeted_removal,
                "unit": "cards",
                "advantage": get_adv(delta_targeted_removal, lower_is_better=False),
                "better": "higher" if delta_targeted_removal > 0 else ("lower" if delta_targeted_removal < 0 else "equal"),
            },
            "board_wipes": {
                "metric": "Board Wipes",
                "deck_a": board_wipes_a,
                "deck_b": board_wipes_b,
                "delta": delta_board_wipes,
                "unit": "cards",
                "advantage": get_adv(delta_board_wipes, lower_is_better=False),
                "better": "higher" if delta_board_wipes > 0 else ("lower" if delta_board_wipes < 0 else "equal"),
            },
            "removal_mana_efficiency": {
                "metric": "Removal Mana Efficiency",
                "deck_a": removal_eff_a,
                "deck_b": removal_eff_b,
                "delta": delta_removal_efficiency,
                "unit": "Avg CMC",
                "advantage": get_adv(delta_removal_efficiency, lower_is_better=True),
                "better": "lower" if delta_removal_efficiency < 0 else ("higher" if delta_removal_efficiency > 0 else "equal"),
            },
            "removal_efficiency": {
                "metric": "Removal Mana Efficiency",
                "deck_a": removal_eff_a,
                "deck_b": removal_eff_b,
                "delta": delta_removal_efficiency,
                "unit": "Avg CMC",
                "advantage": get_adv(delta_removal_efficiency, lower_is_better=True),
                "better": "lower" if delta_removal_efficiency < 0 else ("higher" if delta_removal_efficiency > 0 else "equal"),
            },
            "portfolio_value": {
                "metric": "Market Portfolio Value",
                "deck_a": val_a,
                "deck_b": val_b,
                "delta": delta_value,
                "unit": "USD",
                "advantage": get_adv(delta_value, lower_is_better=False),
                "better": "higher" if delta_value > 0 else ("lower" if delta_value < 0 else "equal"),
            },
            "total_value": {
                "metric": "Market Portfolio Value",
                "deck_a": val_a,
                "deck_b": val_b,
                "delta": delta_value,
                "unit": "USD",
                "advantage": get_adv(delta_value, lower_is_better=False),
                "better": "higher" if delta_value > 0 else ("lower" if delta_value < 0 else "equal"),
            },
            "draw_engines": {
                "metric": "Draw Engines",
                "deck_a": draw_engines_a,
                "deck_b": draw_engines_b,
                "delta": delta_draw_engines,
                "unit": "cards",
                "advantage": get_adv(delta_draw_engines, lower_is_better=False),
                "better": "higher" if delta_draw_engines > 0 else ("lower" if delta_draw_engines < 0 else "equal"),
            },
            "burst_draw": {
                "metric": "Burst Draw Spells",
                "deck_a": draw_burst_a,
                "deck_b": draw_burst_b,
                "delta": delta_draw_burst,
                "unit": "cards",
                "advantage": get_adv(delta_draw_burst, lower_is_better=False),
                "better": "higher" if delta_draw_burst > 0 else ("lower" if delta_draw_burst < 0 else "equal"),
            },
            "cantrips": {
                "metric": "Cantrips",
                "deck_a": draw_cantrip_a,
                "deck_b": draw_cantrip_b,
                "delta": delta_draw_cantrip,
                "unit": "cards",
                "advantage": get_adv(delta_draw_cantrip, lower_is_better=False),
                "better": "higher" if delta_draw_cantrip > 0 else ("lower" if delta_draw_cantrip < 0 else "equal"),
            },
            "tutors_general": {
                "metric": "General Tutors",
                "deck_a": tutors_a,
                "deck_b": tutors_b,
                "delta": delta_tutors,
                "unit": "cards",
                "advantage": get_adv(delta_tutors, lower_is_better=False),
                "better": "higher" if delta_tutors > 0 else ("lower" if delta_tutors < 0 else "equal"),
            },
            "tutors_land": {
                "metric": "Land / Ramp Tutors",
                "deck_a": tutors_land_a,
                "deck_b": tutors_land_b,
                "delta": delta_tutors_land,
                "unit": "cards",
                "advantage": get_adv(delta_tutors_land, lower_is_better=False),
                "better": "higher" if delta_tutors_land > 0 else ("lower" if delta_tutors_land < 0 else "equal"),
            },
            "turn_3_land_pct": {
                "metric": "Turn 3 Land Drop %",
                "deck_a": land3_a,
                "deck_b": land3_b,
                "delta": delta_land3,
                "unit": "%",
                "advantage": get_adv(delta_land3, lower_is_better=False),
                "better": "higher" if delta_land3 > 0 else ("lower" if delta_land3 < 0 else "equal"),
            },
            "keepability_rate": {
                "metric": "Effective Hand Keepability",
                "deck_a": keep_a,
                "deck_b": keep_b,
                "delta": delta_keep,
                "unit": "%",
                "advantage": get_adv(delta_keep, lower_is_better=False),
                "better": "higher" if delta_keep > 0 else ("lower" if delta_keep < 0 else "equal"),
            },
            "commander_cast_turn": {
                "metric": "Median Commander Cast Turn",
                "deck_a": cast_a,
                "deck_b": cast_b,
                "delta": delta_cast,
                "unit": "Turn",
                "advantage": get_adv(delta_cast, lower_is_better=True),
                "better": "lower" if delta_cast < 0 else ("higher" if delta_cast > 0 else "equal"),
            },
            "instant_holdout": {
                "metric": "Instant Mana Holdout",
                "deck_a": holdout_a,
                "deck_b": holdout_b,
                "delta": delta_holdout,
                "unit": "CMC",
                "advantage": get_adv(delta_holdout, lower_is_better=True),
                "better": "lower" if delta_holdout < 0 else ("higher" if delta_holdout > 0 else "equal"),
            },
            "mana_sinks": {
                "metric": "Late-Game Mana Sinks",
                "deck_a": sinks_a,
                "deck_b": sinks_b,
                "delta": delta_sinks,
                "unit": "outlets",
                "advantage": get_adv(delta_sinks, lower_is_better=False),
                "better": "higher" if delta_sinks > 0 else ("lower" if delta_sinks < 0 else "equal"),
            },
            "virtual_card_advantage": {
                "metric": "Virtual Card Advantage",
                "deck_a": virt_a,
                "deck_b": virt_b,
                "delta": delta_virt,
                "unit": "cards",
                "advantage": get_adv(delta_virt, lower_is_better=False),
                "better": "higher" if delta_virt > 0 else ("lower" if delta_virt < 0 else "equal"),
            },
        }

        # 2. Interaction Profile
        total_interaction_a = targeted_removal_a + board_wipes_a
        total_interaction_b = targeted_removal_b + board_wipes_b
        interaction_leader = "deck_a" if total_interaction_a > total_interaction_b else ("deck_b" if total_interaction_b > total_interaction_a else "tie")

        interaction_profile = {
            "targeted_removal": {
                "deck_a": targeted_removal_a,
                "deck_b": targeted_removal_b,
                "delta": delta_targeted_removal,
            },
            "board_wipes": {
                "deck_a": board_wipes_a,
                "deck_b": board_wipes_b,
                "delta": delta_board_wipes,
            },
            "total_interaction": {
                "deck_a": total_interaction_a,
                "deck_b": total_interaction_b,
                "delta": total_interaction_a - total_interaction_b,
            },
            "removal_mana_efficiency": {
                "deck_a": removal_eff_a,
                "deck_b": removal_eff_b,
                "delta": delta_removal_efficiency,
            },
            "instant_speed_ratio": {
                "deck_a": instant_speed_a,
                "deck_b": instant_speed_b,
                "delta": delta_instant_speed,
            },
            "interaction_leader": interaction_leader,
        }

        # 3. Card Advantage Profile
        adv_leader = "deck_a" if draw_engines_a > draw_engines_b else ("deck_b" if draw_engines_b > draw_engines_a else "tie")
        card_advantage_profile = {
            "engine_draw": {
                "deck_a": draw_engines_a,
                "deck_b": draw_engines_b,
                "delta": delta_draw_engines,
            },
            "burst_draw": {
                "deck_a": draw_burst_a,
                "deck_b": draw_burst_b,
                "delta": delta_draw_burst,
            },
            "cantrips": {
                "deck_a": draw_cantrip_a,
                "deck_b": draw_cantrip_b,
                "delta": delta_draw_cantrip,
            },
            "total_draw": {
                "deck_a": int(stats_a.get("total_draw_count", 0)),
                "deck_b": int(stats_b.get("total_draw_count", 0)),
                "delta": int(stats_a.get("total_draw_count", 0)) - int(stats_b.get("total_draw_count", 0)),
            },
            "tutors_general": {
                "deck_a": tutors_a,
                "deck_b": tutors_b,
                "delta": delta_tutors,
            },
            "tutors_land": {
                "deck_a": tutors_land_a,
                "deck_b": tutors_land_b,
                "delta": delta_tutors_land,
            },
            "total_tutors": {
                "deck_a": tutors_a + tutors_land_a,
                "deck_b": tutors_b + tutors_land_b,
                "delta": (tutors_a + tutors_land_a) - (tutors_b + tutors_land_b),
            },
            "advantage_leader": adv_leader,
        }

        # 4. Velocity Profile
        vel_leader = "deck_a" if fast_ramp_a > fast_ramp_b or (fast_ramp_a == fast_ramp_b and amv_a < amv_b) else ("deck_b" if fast_ramp_b > fast_ramp_a or amv_b < amv_a else "tie")
        velocity_profile = {
            "fast_ramp": {
                "deck_a": fast_ramp_a,
                "deck_b": fast_ramp_b,
                "delta": delta_fast_ramp,
            },
            "standard_ramp": {
                "deck_a": int(stats_a.get("standard_ramp_count", 0)),
                "deck_b": int(stats_b.get("standard_ramp_count", 0)),
                "delta": int(stats_a.get("standard_ramp_count", 0)) - int(stats_b.get("standard_ramp_count", 0)),
            },
            "dork_or_rock_ramp": {
                "deck_a": dork_rock_a,
                "deck_b": dork_rock_b,
                "delta": delta_dork_rock,
            },
            "land_fetch_ramp": {
                "deck_a": land_fetch_a,
                "deck_b": land_fetch_b,
                "delta": delta_land_fetch,
            },
            "treasure_ramp": {
                "deck_a": treasure_a,
                "deck_b": treasure_b,
                "delta": delta_treasure,
            },
            "total_ramp": {
                "deck_a": int(stats_a.get("total_ramp_count", 0)),
                "deck_b": int(stats_b.get("total_ramp_count", 0)),
                "delta": int(stats_a.get("total_ramp_count", 0)) - int(stats_b.get("total_ramp_count", 0)),
            },
            "tapland_penalty_index": {
                "deck_a": tapland_penalty_a,
                "deck_b": tapland_penalty_b,
                "delta": delta_tapland_penalty,
            },
            "velocity_leader": vel_leader,
        }

        # 5. Shared and Unique Cards Analysis
        cards_a = deck_a.get("cards", []) or deck_a.get("cards_data", [])
        cards_b = deck_b.get("cards", []) or deck_b.get("cards_data", [])

        set_a = {c.get("name", "").strip().lower(): c for c in cards_a if c.get("name")}
        set_b = {c.get("name", "").strip().lower(): c for c in cards_b if c.get("name")}

        shared_keys = set(set_a.keys()).intersection(set(set_b.keys()))
        unique_a_keys = set(set_a.keys()) - set(set_b.keys())
        unique_b_keys = set(set_b.keys()) - set(set_a.keys())

        shared_cards = [set_a[k] for k in sorted(list(shared_keys))]
        unique_cards_a = [set_a[k] for k in sorted(list(unique_a_keys))]
        unique_cards_b = [set_b[k] for k in sorted(list(unique_b_keys))]

        return {
            "success": True,
            "deck_a": deck_a,
            "deck_b": deck_b,
            "archetype_a": archetype_a,
            "archetype_b": archetype_b,
            "delta_matrix": delta_matrix,
            "interaction_profile": interaction_profile,
            "card_advantage_profile": card_advantage_profile,
            "velocity_profile": velocity_profile,
            "shared_cards": shared_cards,
            "shared_staples": shared_cards,
            "shared_count": len(shared_cards),
            "unique_deck_a": {
                "count": len(unique_cards_a),
                "cards": unique_cards_a,
            },
            "unique_deck_b": {
                "count": len(unique_cards_b),
                "cards": unique_cards_b,
            },
        }
