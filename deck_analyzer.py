"""
Deck Analytics Engine for MTG Commander Decks.
Calculates advanced statistical metrics:
- Nonland Average Mana Value (AMV) & CMC Histogram
- Mana Pip vs. Source Breakdown and Balance Ratios
- Velocity (Fast vs. Standard Ramp, Tapland Penalty Index)
- Pacing & Interaction Distribution (Instant Speed Ratio, Targeted Removal, Board Wipes, Removal Efficiency)
- Card Advantage (Draw Engines, Burst Draw, Cantrips, Tutors)
- Archetype classification heuristics
"""

import re
from typing import Dict, Any, List, Optional
from card_classifier import MTGCardClassifier


class DeckAnalyzer:
    """Evaluates MTG Commander deck payloads and computes advanced mathematical and statistical telemetry."""

    COLOR_KEYS = ["W", "U", "B", "R", "G", "C"]

    def __init__(self):
        self.classifier = MTGCardClassifier()

    def parse_mana_pips(self, mana_cost: str) -> Dict[str, float]:
        """
        Parses mana_cost string (e.g., '{1}{U}{U}{B}', '{2/W}', '{W/U}', '{B/P}')
        and returns colored pip counts for W, U, B, R, G, C.
        """
        pips = {c: 0.0 for c in self.COLOR_KEYS}
        if not mana_cost:
            return pips

        symbols = re.findall(r"\{([^}]+)\}", mana_cost)
        for sym in symbols:
            upper_sym = sym.upper()
            if "/" in upper_sym:
                parts = upper_sym.split("/")
                if "P" in parts:
                    for part in parts:
                        if part in pips:
                            pips[part] += 1.0
                else:
                    weight = 1.0 / len(parts)
                    for part in parts:
                        if part in pips:
                            pips[part] += weight
            elif upper_sym in pips:
                pips[upper_sym] += 1.0

        return pips

    def extract_mana_sources(self, card_data: Dict[str, Any]) -> List[str]:
        """
        Extracts produced colored mana sources (W, U, B, R, G, C) from card data.
        Inspects produced_mana, type_line, and oracle_text.
        """
        sources = set()
        type_line = card_data.get("type_line", "")
        oracle_text, _, _ = self.classifier.extract_text_and_types(card_data)

        # 1. Scryfall produced_mana array
        if "produced_mana" in card_data and card_data["produced_mana"]:
            for m in card_data["produced_mana"]:
                m_upper = str(m).upper()
                if m_upper in self.COLOR_KEYS:
                    sources.add(m_upper)

        # 2. Basic Land types in type line
        tl_lower = type_line.lower()
        if "plains" in tl_lower:
            sources.add("W")
        if "island" in tl_lower:
            sources.add("U")
        if "swamp" in tl_lower:
            sources.add("B")
        if "mountain" in tl_lower:
            sources.add("R")
        if "forest" in tl_lower:
            sources.add("G")
        if "wastes" in tl_lower:
            sources.add("C")

        # 3. Any-color land / artifact patterns (Command Tower, City of Brass, Arcane Signet, Birds of Paradise)
        card_name = card_data.get("name", "").lower()
        if any(c in card_name for c in ["command tower", "mana confluence", "city of brass", "reflecting pool", "exotic orchard", "arcane signet", "fellwar stone", "birds of paradise", "chromatic lantern"]):
            # Add all colors from deck color identity or standard 5 colors
            cid = card_data.get("color_identity", [])
            if cid:
                for c in cid:
                    if c in self.COLOR_KEYS:
                        sources.add(c)
            else:
                for c in ["W", "U", "B", "R", "G"]:
                    sources.add(c)

        # 4. Oracle text fallback regex
        add_matches = re.findall(r"add\s+((?:\{[WUBRGC0-9X\/\s]+\}|mana\s+of\s+any|one\s+mana))", oracle_text, re.IGNORECASE)
        for m in add_matches:
            if "any" in m.lower() or "one mana" in m.lower():
                for c in ["W", "U", "B", "R", "G"]:
                    sources.add(c)
            else:
                for sym in re.findall(r"\{([WUBRGC])\}", m, re.IGNORECASE):
                    sources.add(sym.upper())

        return sorted(list(sources))

    def analyze(self, deck_data: Any) -> Dict[str, Any]:
        """
        Executes full statistical and analytical evaluation across deck payload.
        """
        if isinstance(deck_data, dict):
            raw_cards = deck_data.get("cards", []) or deck_data.get("cards_data", [])
        elif isinstance(deck_data, list):
            raw_cards = deck_data
        else:
            raw_cards = []
        enriched_cards = []

        total_cards_count = 0
        total_deck_value = 0.0
        nonland_count = 0
        total_nonland_cmc = 0.0
        total_cmc = 0.0

        # Histograms & breakdowns
        type_counts = {
            "Creatures": 0,
            "Instants": 0,
            "Sorceries": 0,
            "Artifacts": 0,
            "Enchantments": 0,
            "Planeswalkers": 0,
            "Lands": 0,
            "Battles": 0,
            "Other": 0,
        }
        cmc_curve = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
        color_identity_set = set()

        # Mana pips and sources
        total_pips = {c: 0 for c in self.COLOR_KEYS}
        total_sources = {c: 0 for c in self.COLOR_KEYS}

        # Role and interaction counters
        instant_speed_count = 0
        fast_ramp_count = 0
        standard_ramp_count = 0
        taplands_count = 0
        targeted_removal_count = 0
        targeted_removal_cmc_sum = 0.0
        board_wipe_count = 0
        draw_engine_count = 0
        draw_burst_count = 0
        draw_cantrip_count = 0
        tutor_general_count = 0
        tutor_land_count = 0

        for card in raw_cards:
            qty = int(card.get("quantity", 1))
            total_cards_count += qty

            # Classification
            classification = card.get("classification")
            if not classification:
                classification = self.classifier.classify(card)

            card_obj = dict(card)
            card_obj["classification"] = classification
            card_obj["quantity"] = qty

            cmc_val = float(card.get("cmc", 0.0))
            type_line = (card.get("type_line") or "").strip()
            type_line_lower = type_line.lower()
            is_land = "land" in type_line_lower

            # Pricing
            price_val = card.get("price_usd")
            if price_val is not None:
                try:
                    total_deck_value += float(price_val) * qty
                except (ValueError, TypeError):
                    pass

            # Color identity
            for col in card.get("color_identity", []):
                color_identity_set.add(col.upper())

            # Type line aggregation
            if "creature" in type_line_lower:
                type_counts["Creatures"] += qty
            elif "instant" in type_line_lower:
                type_counts["Instants"] += qty
            elif "sorcery" in type_line_lower:
                type_counts["Sorceries"] += qty
            elif "artifact" in type_line_lower:
                type_counts["Artifacts"] += qty
            elif "enchantment" in type_line_lower:
                type_counts["Enchantments"] += qty
            elif "planeswalker" in type_line_lower:
                type_counts["Planeswalkers"] += qty
            elif "land" in type_line_lower:
                type_counts["Lands"] += qty
            elif "battle" in type_line_lower:
                type_counts["Battles"] += qty
            else:
                type_counts["Other"] += qty

            # CMC Tracking
            total_cmc += (cmc_val * qty)
            if not is_land:
                nonland_count += qty
                total_nonland_cmc += (cmc_val * qty)
                cmc_key = "7+" if cmc_val >= 7 else str(int(cmc_val))
                cmc_curve[cmc_key] = cmc_curve.get(cmc_key, 0) + qty

            # Pips tracking
            mana_cost = card.get("mana_cost", "")
            if not mana_cost and "card_faces" in card and card["card_faces"]:
                mana_cost = " // ".join([face.get("mana_cost", "") for face in card["card_faces"] if face.get("mana_cost")])
            card_pips = self.parse_mana_pips(mana_cost)
            for c, cnt in card_pips.items():
                total_pips[c] += (cnt * qty)

            # Sources tracking (lands and mana-producing nonlands)
            card_sources = self.extract_mana_sources(card)
            card_obj["produced_mana_sources"] = card_sources
            if card_sources:
                for c in card_sources:
                    total_sources[c] += qty

            # Role counters
            if not is_land:
                # Instant speed nonland spells (type Instant or keyword Flash)
                keywords = [k.lower() for k in card.get("keywords", [])]
                oracle_text, _, _ = self.classifier.extract_text_and_types(card)
                is_instant_speed = "instant" in type_line_lower or "flash" in type_line_lower or "flash" in keywords or re.search(r"\bflash\b", oracle_text, re.IGNORECASE) is not None
                if is_instant_speed:
                    instant_speed_count += qty

                # Ramp
                if classification.get("is_ramp"):
                    if classification.get("ramp_tier") == "fast" or cmc_val <= 2.0:
                        fast_ramp_count += qty
                    else:
                        standard_ramp_count += qty

            # Land Tapland
            if is_land and classification.get("is_tapland"):
                taplands_count += qty

            # Removal
            if classification.get("is_targeted_removal"):
                targeted_removal_count += qty
                targeted_removal_cmc_sum += (cmc_val * qty)
            elif classification.get("is_board_wipe"):
                board_wipe_count += qty

            # Draw
            if classification.get("is_draw"):
                draw_type = classification.get("draw_type")
                if draw_type == "engine":
                    draw_engine_count += qty
                elif draw_type == "cantrip":
                    draw_cantrip_count += qty
                else:
                    draw_burst_count += qty

            # Tutor
            if classification.get("is_tutor"):
                if classification.get("tutor_type") == "general":
                    tutor_general_count += qty
                elif classification.get("tutor_type") == "land":
                    tutor_land_count += qty

            enriched_cards.append(card_obj)

        # Mathematical metric computations
        nonland_amv = round(total_nonland_cmc / nonland_count, 2) if nonland_count > 0 else 0.0
        avg_cmc = nonland_amv

        total_pips_count = sum(total_pips.values())
        total_sources_count = sum(total_sources.values())

        pip_breakdown = {}
        for c in self.COLOR_KEYS:
            c_pips = total_pips[c]
            c_sources = total_sources[c]

            pip_demand_pct = round((c_pips / total_pips_count) * 100, 1) if total_pips_count > 0 else 0.0
            source_supply_pct = round((c_sources / total_sources_count) * 100, 1) if total_sources_count > 0 else 0.0

            if total_sources_count > 0 and total_pips_count > 0 and c_pips > 0:
                balance_ratio = round((c_sources / total_sources_count) / (c_pips / total_pips_count), 2)
            elif c_pips == 0 and c_sources > 0:
                balance_ratio = 2.0  # Surplus
            elif c_pips == 0 and c_sources == 0:
                balance_ratio = 1.0  # Inactive / balanced
            else:
                balance_ratio = 0.0  # Deficit

            status = "balanced"
            if c_pips == 0 and c_sources == 0:
                status = "none"
            elif balance_ratio > 1.2:
                status = "surplus"
            elif balance_ratio < 0.8:
                status = "deficit"

            pip_breakdown[c] = {
                "pips": c_pips,
                "sources": c_sources,
                "pip_demand_pct": pip_demand_pct,
                "source_supply_pct": source_supply_pct,
                "balance_ratio": balance_ratio,
                "status": status,
            }

        instant_speed_ratio = round((instant_speed_count / nonland_count) * 100, 1) if nonland_count > 0 else 0.0
        total_lands_count = type_counts["Lands"]
        tapland_penalty_index = round((taplands_count / total_lands_count) * 100, 1) if total_lands_count > 0 else 0.0
        removal_mana_efficiency = round(targeted_removal_cmc_sum / targeted_removal_count, 2) if targeted_removal_count > 0 else 0.0
        total_ramp_count = fast_ramp_count + standard_ramp_count
        total_draw_count = draw_engine_count + draw_burst_count + draw_cantrip_count
        total_tutor_count = tutor_general_count + tutor_land_count

        # Heuristic Archetype Classification
        archetype = "Midrange / Engine"
        if nonland_amv < 2.7 and fast_ramp_count >= 8 and instant_speed_ratio >= 25.0:
            archetype = "Aggro / Fast Combo"
        elif 2.7 <= nonland_amv <= 3.4 and draw_engine_count >= 5 and targeted_removal_count >= 5:
            archetype = "Midrange / Engine"
        elif nonland_amv > 3.4 and total_ramp_count >= 12 and board_wipe_count >= 3:
            archetype = "Battlecruiser / Big Mana"
        elif nonland_amv < 2.8:
            archetype = "Aggro / Fast Combo"
        elif nonland_amv > 3.4:
            archetype = "Battlecruiser / Big Mana"
        elif instant_speed_ratio >= 25.0 or (targeted_removal_count + board_wipe_count >= 10):
            archetype = "Control / Interactive"
        elif draw_engine_count >= 4:
            archetype = "Midrange / Engine"

        # AMV Rating color code
        amv_color = "emerald" if nonland_amv <= 2.8 else ("amber" if nonland_amv <= 3.4 else "rose")

        stats = {
            "total_value": round(total_deck_value, 2),
            "avg_cmc": nonland_amv,
            "nonland_amv": nonland_amv,
            "amv_color": amv_color,
            "total_cards": total_cards_count,
            "nonland_count": nonland_count,
            "land_count": total_lands_count,
            "type_counts": type_counts,
            "cmc_curve": cmc_curve,
            "color_identity": sorted(list(color_identity_set)),
            "pip_breakdown": pip_breakdown,
            "total_pips": total_pips,
            "total_sources": total_sources,
            "instant_speed_count": instant_speed_count,
            "instant_speed_ratio": instant_speed_ratio,
            "fast_ramp_count": fast_ramp_count,
            "standard_ramp_count": standard_ramp_count,
            "total_ramp_count": total_ramp_count,
            "taplands_count": taplands_count,
            "tapland_penalty_index": tapland_penalty_index,
            "targeted_removal_count": targeted_removal_count,
            "board_wipe_count": board_wipe_count,
            "removal_mana_efficiency": removal_mana_efficiency,
            "draw_engine_count": draw_engine_count,
            "draw_burst_count": draw_burst_count,
            "draw_cantrip_count": draw_cantrip_count,
            "total_draw_count": total_draw_count,
            "tutor_general_count": tutor_general_count,
            "tutor_land_count": tutor_land_count,
            "total_tutor_count": total_tutor_count,
            "archetype": archetype,
        }

        result_dict = {
            "deck_name": deck_data.get("deck_name", "Commander Deck") if isinstance(deck_data, dict) else "Commander Deck",
            "commander": deck_data.get("commander", []) if isinstance(deck_data, dict) else [],
            "commander_art": deck_data.get("commander_art") if isinstance(deck_data, dict) else None,
            "total_cards": total_cards_count,
            "cards": enriched_cards,
            "stats": stats,
            "source_type": deck_data.get("source_type", "text") if isinstance(deck_data, dict) else "text",
            "raw_text": deck_data.get("raw_text", "") if isinstance(deck_data, dict) else "",
        }
        for k, v in stats.items():
            result_dict[k] = v
        return result_dict
