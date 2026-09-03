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

import math
import random
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

    @staticmethod
    def _hypergeom(k: int, N: int, K: int, n: int) -> float:
        """Calculates hypergeometric probability P(X = k) for N total, K successes, n draws."""
        if k < max(0, n - (N - K)) or k > min(n, K) or N <= 0 or n <= 0:
            return 0.0
        try:
            return (math.comb(K, k) * math.comb(N - K, n - k)) / math.comb(N, n)
        except Exception:
            return 0.0

    def compute_land_drop_probabilities(self, land_count: int, cheap_cantrip_count: int = 0, total_cards: int = 99) -> Dict[str, Any]:
        """
        Calculates exact hypergeometric percentage chances of hitting land drops on turns 1, 2, 3, and 4.
        In multiplayer Commander, the starting player draws on Turn 1 (Rule 103.8a).
        Computes both raw probability and effective probability factoring the 1 Free Mulligan (Rule 103.5c)
        and cantrip selection.
        """
        N = max(total_cards, 60)
        K = max(0, min(land_count, N))
        
        raw_probs = {}
        eff_probs = {}
        
        # Free mulligan probability: hand 1 kept if 2 <= lands <= 5
        p_keep = sum(self._hypergeom(l, N, K, 7) for l in range(2, 6))
        
        for turn in [1, 2, 3, 4]:
            # Turn T cards seen = 7 opening + T draw steps (Rule 103.8a)
            n = min(7 + turn, N)
            
            # Raw: P(X >= turn) in n cards
            p_raw = sum(self._hypergeom(k, N, K, n) for k in range(turn, min(n, K) + 1))
            
            # Effective with 1 free mulligan:
            p_kept_and_hit = 0.0
            for l in range(2, 6):
                p_l = self._hypergeom(l, N, K, 7)
                needed = max(0, turn - l)
                if needed == 0:
                    p_draw_needed = 1.0
                elif N - 7 >= turn and K - l >= 0:
                    p_draw_needed = sum(
                        self._hypergeom(d, N - 7, K - l, turn)
                        for d in range(needed, min(turn, K - l) + 1)
                    )
                else:
                    p_draw_needed = 0.0
                p_kept_and_hit += (p_l * p_draw_needed)
            
            p_eff = p_kept_and_hit + ((1.0 - p_keep) * p_raw)
            
            # Cantrip boost: if deck has cheap cantrips (CMC <= 2), selection boost for turns 2-4
            if cheap_cantrip_count > 0 and turn >= 2:
                cantrip_dig = min(0.06, cheap_cantrip_count * 0.012)
                p_eff = min(1.0, p_eff + cantrip_dig * (1.0 - p_eff))
                
            raw_probs[f"turn_{turn}"] = round(p_raw * 100, 1)
            eff_probs[f"turn_{turn}"] = round(p_eff * 100, 1)
            
        return {
            "turn_1": raw_probs["turn_1"],
            "turn_2": raw_probs["turn_2"],
            "turn_3": raw_probs["turn_3"],
            "turn_4": raw_probs["turn_4"],
            "effective_with_mulligan": eff_probs,
            "cheap_cantrip_count": cheap_cantrip_count,
        }

    def simulate_opening_hand_keepability(self, cards: List[Dict[str, Any]], sample_size: int = 5000, iterations: Optional[int] = None) -> Dict[str, Any]:
        """
        Simulates 5,000 opening 7-card hands to score how frequently an opening hand
        contains a workable balance (2-4 lands + early playable spell before turn 3).
        Computes natural keep rate, effective keep rate with 1 free mulligan, and land distributions.
        """
        if iterations is not None:
            sample_size = iterations
        deck_pool = []
        for c in cards:
            if c.get("section") == "commander":
                continue
            qty = int(c.get("quantity", 1))
            tl = (c.get("type_line") or "").lower()
            is_land = "land" in tl
            cmc = float(c.get("cmc", 0.0))
            classification = c.get("classification") or {}
            is_ramp = bool(classification.get("is_ramp"))
            is_draw = bool(classification.get("is_draw") or classification.get("is_card_draw"))
            is_cheap = (not is_land) and (cmc <= 2.0 or is_ramp or (cmc <= 3.0 and is_draw))
            
            card_tuple = (is_land, is_cheap, cmc)
            for _ in range(qty):
                deck_pool.append(card_tuple)
                
        if len(deck_pool) < 7:
            return {
                "natural_keep_rate": 0.0,
                "effective_keep_rate": 0.0,
                "avg_lands_in_hand": 0.0,
                "breakdown": {"optimal": 0.0, "workable": 0.0, "risky": 0.0, "unkeepable": 100.0},
                "land_distribution": {str(i): 0.0 for i in range(8)},
            }
            
        rng = random.Random(42)
        optimal_cnt = 0
        workable_cnt = 0
        risky_cnt = 0
        unkeepable_cnt = 0
        total_lands_drawn = 0
        land_dist = {i: 0 for i in range(8)}
        
        for _ in range(sample_size):
            hand = rng.sample(deck_pool, 7)
            lands = sum(1 for is_land, _, _ in hand if is_land)
            early_spells = sum(1 for is_land, is_cheap, _ in hand if (not is_land) and is_cheap)
            
            total_lands_drawn += lands
            if lands <= 7:
                land_dist[lands] += 1
            else:
                land_dist[7] += 1
            
            if (3 <= lands <= 4) and early_spells >= 1:
                optimal_cnt += 1
            elif (2 <= lands <= 5) and early_spells >= 1:
                workable_cnt += 1
            elif (lands == 2 and early_spells == 0) or (lands == 5 and early_spells == 0):
                risky_cnt += 1
            else:
                unkeepable_cnt += 1
                
        natural_keep_rate = round(((optimal_cnt + workable_cnt) / sample_size) * 100, 1)
        natural_frac = (optimal_cnt + workable_cnt) / sample_size
        effective_keep_rate = round((1.0 - (1.0 - natural_frac) ** 2) * 100, 1)
        avg_lands = round(total_lands_drawn / sample_size, 2)
        
        return {
            "natural_keep_rate": natural_keep_rate,
            "effective_keep_rate": effective_keep_rate,
            "avg_lands_in_hand": avg_lands,
            "breakdown": {
                "optimal": round((optimal_cnt / sample_size) * 100, 1),
                "workable": round((workable_cnt / sample_size) * 100, 1),
                "risky": round((risky_cnt / sample_size) * 100, 1),
                "unkeepable": round((unkeepable_cnt / sample_size) * 100, 1),
            },
            "land_distribution": {str(i): round((cnt / sample_size) * 100, 1) for i, cnt in land_dist.items()},
        }

    def simulate_earliest_commander_cast(
        self,
        commander_card: Optional[Dict[str, Any]],
        cards: List[Dict[str, Any]],
        sample_size: int = 2000,
        iterations: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Identifies the mathematical median turn the commander actually enters play
        when factoring in ramp density, color fixing, and casting cost.
        """
        if iterations is not None:
            sample_size = iterations
            
        if not commander_card:
            for c in cards:
                if c.get("section") == "commander":
                    commander_card = c
                    break
        
        if not commander_card:
            return {
                "median_cast_turn": 4,
                "avg_cast_turn": 4.0,
                "earliest_possible_turn": 3,
                "turn_distribution": {"turn_2": 0.0, "turn_3": 25.0, "turn_4": 50.0, "turn_5": 20.0, "turn_6_plus": 5.0},
                "color_bottleneck": "None",
            }
            
        cmdr_cmc = float(commander_card.get("cmc", 4.0))
        mana_cost = commander_card.get("mana_cost", "")
        pips = self.parse_mana_pips(mana_cost)
        req_pips = {k: int(math.ceil(v)) for k, v in pips.items() if v > 0}
        
        if cmdr_cmc <= 1.0:
            return {
                "median_cast_turn": 1,
                "avg_cast_turn": 1.0,
                "earliest_possible_turn": 1,
                "turn_distribution": {"turn_2": 100.0, "turn_3": 0.0, "turn_4": 0.0, "turn_5": 0.0, "turn_6_plus": 0.0},
                "color_bottleneck": "None",
            }
            
        library_pool = []
        for c in cards:
            if commander_card and c.get("name") == commander_card.get("name") and c.get("section") == "commander":
                continue
            qty = int(c.get("quantity", 1))
            tl = (c.get("type_line") or "").lower()
            is_land = "land" in tl
            cmc = float(c.get("cmc", 0.0))
            classification = c.get("classification") or {}
            is_ramp = bool(classification.get("is_ramp"))
            ramp_tier = classification.get("ramp_tier") or ("fast" if cmc <= 2.0 else "standard")
            sources = c.get("produced_mana_sources") or self.extract_mana_sources(c)
            
            c_name_lower = c.get("name", "").lower()
            mana_out = 2 if any(k in c_name_lower for k in ["sol ring", "mana vault", "mana crypt", "ancient tomb"]) else 1
            
            item = {
                "is_land": is_land,
                "is_ramp": is_ramp,
                "ramp_tier": ramp_tier,
                "cmc": cmc,
                "mana_out": mana_out,
                "sources": set(sources),
            }
            for _ in range(qty):
                library_pool.append(item)
                
        if len(library_pool) < 15:
            return {
                "median_cast_turn": int(math.ceil(cmdr_cmc)),
                "avg_cast_turn": float(cmdr_cmc),
                "earliest_possible_turn": max(2, int(math.ceil(cmdr_cmc)) - 1),
                "turn_distribution": {"turn_2": 5.0, "turn_3": 20.0, "turn_4": 50.0, "turn_5": 20.0, "turn_6_plus": 5.0},
                "color_bottleneck": "None",
            }
            
        rng = random.Random(42)
        cast_turns = []
        color_bottleneck_counts = {k: 0 for k in req_pips.keys()}
        
        for _ in range(sample_size):
            hand = rng.sample(library_pool, 7)
            remaining_lib = list(library_pool)
            for h in hand:
                remaining_lib.remove(h)
            rng.shuffle(remaining_lib)
            
            active_mana_capacity = 0
            sources_available = set()
            cast_turn = 10
            
            for turn in range(1, 11):
                if remaining_lib:
                    hand.append(remaining_lib.pop())
                    
                land_in_hand = next((c for c in hand if c["is_land"]), None)
                if land_in_hand:
                    hand.remove(land_in_hand)
                    active_mana_capacity += 1
                    sources_available.update(land_in_hand["sources"])
                    
                has_cmc = (active_mana_capacity >= cmdr_cmc)
                missing_color = None
                for col, count in req_pips.items():
                    if col not in sources_available and count > 0:
                        missing_color = col
                        break
                        
                if has_cmc and not missing_color:
                    cast_turn = turn
                    break
                elif has_cmc and missing_color:
                    color_bottleneck_counts[missing_color] += 1
                    
                ramp_candidates = [c for c in hand if c["is_ramp"] and c["cmc"] <= active_mana_capacity]
                if ramp_candidates:
                    best_ramp = min(ramp_candidates, key=lambda x: x["cmc"])
                    hand.remove(best_ramp)
                    active_mana_capacity += best_ramp["mana_out"]
                    sources_available.update(best_ramp["sources"])
                    
            cast_turns.append(cast_turn)
            
        cast_turns.sort()
        median_turn = cast_turns[len(cast_turns) // 2]
        avg_turn = round(sum(cast_turns) / len(cast_turns), 2)
        earliest_turn = min(cast_turns)
        
        dist = {"turn_2": 0, "turn_3": 0, "turn_4": 0, "turn_5": 0, "turn_6_plus": 0}
        for t in cast_turns:
            if t <= 2:
                dist["turn_2"] += 1
            elif t == 3:
                dist["turn_3"] += 1
            elif t == 4:
                dist["turn_4"] += 1
            elif t == 5:
                dist["turn_5"] += 1
            else:
                dist["turn_6_plus"] += 1
                
        turn_dist_pct = {k: round((v / sample_size) * 100, 1) for k, v in dist.items()}
        
        top_bottleneck = "None"
        if color_bottleneck_counts:
            top_col, cnt = max(color_bottleneck_counts.items(), key=lambda x: x[1])
            if cnt > (sample_size * 0.08):
                top_bottleneck = top_col
                
        return {
            "median_cast_turn": median_turn,
            "avg_cast_turn": avg_turn,
            "earliest_possible_turn": earliest_turn,
            "turn_distribution": turn_dist_pct,
            "color_bottleneck": top_bottleneck,
        }

    def analyze_threat_coverage(self, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Categorizes interaction by what permanent types it actually hits:
        Creatures, Artifacts, Enchantments, Planeswalkers, Graveyards, Lands, Spells on stack.
        Tracks Instant-speed vs. Sorcery-speed breakdown and flags coverage gaps.
        """
        categories = {
            "creatures": {"total": 0, "instant": 0, "sorcery": 0, "cards": []},
            "artifacts": {"total": 0, "instant": 0, "sorcery": 0, "cards": []},
            "enchantments": {"total": 0, "instant": 0, "sorcery": 0, "cards": []},
            "planeswalkers": {"total": 0, "instant": 0, "sorcery": 0, "cards": []},
            "graveyards": {"total": 0, "instant": 0, "sorcery": 0, "cards": []},
            "lands": {"total": 0, "instant": 0, "sorcery": 0, "cards": []},
            "spells": {"total": 0, "instant": 0, "sorcery": 0, "cards": []},
        }
        
        for c in cards:
            qty = int(c.get("quantity", 1))
            name = c.get("name", "")
            tl = (c.get("type_line") or "").lower()
            keywords = [k.lower() for k in c.get("keywords", [])]
            oracle = (c.get("oracle_text") or "").lower()
            is_instant = "instant" in tl or "flash" in tl or "flash" in keywords or re.search(r"\bflash\b", oracle) is not None
            
            classification = c.get("classification") or {}
            targets = list(classification.get("threat_targets", []))
            
            if classification.get("is_counterspell") and "spells" not in targets:
                targets.append("spells")
                
            for t in targets:
                t_key = t.lower()
                if t_key == "spell":
                    t_key = "spells"
                if t_key in categories:
                    categories[t_key]["total"] += qty
                    if is_instant:
                        categories[t_key]["instant"] += qty
                    else:
                        categories[t_key]["sorcery"] += qty
                    if name not in categories[t_key]["cards"]:
                        categories[t_key]["cards"].append(name)
                        
        vulnerabilities = []
        if categories["artifacts"]["instant"] == 0:
            vulnerabilities.append("No instant-speed answers for problem artifacts.")
        if categories["enchantments"]["instant"] == 0:
            vulnerabilities.append("No instant-speed answers for problem enchantments.")
        if categories["graveyards"]["total"] == 0:
            vulnerabilities.append("No dedicated graveyard interaction/hate.")
        if categories["creatures"]["total"] < 4:
            vulnerabilities.append("Critically low creature removal density (<4 answers).")
            
        return {
            "categories": categories,
            "vulnerabilities": vulnerabilities,
        }

    def analyze_counter_vs_protection(self, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Differentiates between offensive stack control (Counterspells) and
        defensive protection (hexproof, indestructible, phase-out, flicker, ward).
        """
        counterspells = []
        protection_cards = []
        protection_breakdown = {
            "hexproof_shroud": 0,
            "indestructible": 0,
            "phase_out": 0,
            "flicker_bounce": 0,
            "ward_prot": 0,
        }
        
        for c in cards:
            qty = int(c.get("quantity", 1))
            name = c.get("name", "")
            classification = c.get("classification") or {}
            
            if classification.get("is_counterspell") or "Counterspell" in classification.get("tags", []):
                for _ in range(qty):
                    counterspells.append(name)
                    
            if classification.get("is_protection") or "Protection" in classification.get("tags", []):
                for _ in range(qty):
                    protection_cards.append(name)
                for p_type in classification.get("protection_types", []):
                    if p_type in protection_breakdown:
                        protection_breakdown[p_type] += qty
                        
        cnt_count = len(counterspells)
        prot_count = len(protection_cards)
        
        ratio = round(cnt_count / prot_count, 2) if prot_count > 0 else (float(cnt_count) if cnt_count > 0 else 1.0)
        
        if cnt_count >= 5 and prot_count <= 2:
            stance = "Offensive Stack Control (Disruptive)"
        elif prot_count >= 5 and cnt_count <= 2:
            stance = "Defensive / Voltron Fortress (Board Protection)"
        elif cnt_count >= 3 and prot_count >= 3:
            stance = "Balanced Offense & Defense"
        elif cnt_count == 0 and prot_count == 0:
            stance = "Unprotected (Zero Stack / Board Protection)"
        elif prot_count <= 1:
            stance = "Exposed (Minimal Commander Protection)"
        else:
            stance = "Moderate Defense"
            
        return {
            "counterspell_count": cnt_count,
            "counterspells": sorted(list(set(counterspells))),
            "protection_count": prot_count,
            "protection_cards": sorted(list(set(protection_cards))),
            "protection_breakdown": protection_breakdown,
            "ratio": ratio,
            "stance": stance,
        }

    def compute_instant_mana_holdout(self, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculates the average mana required to hold up the instant-speed interaction package.
        """
        holdout_cards = []
        total_cmc = 0.0
        cmc_breakdown = {"0": 0, "1": 0, "2": 0, "3": 0, "4+": 0}
        
        for c in cards:
            qty = int(c.get("quantity", 1))
            name = c.get("name", "")
            tl = (c.get("type_line") or "").lower()
            if "land" in tl:
                continue
            keywords = [k.lower() for k in c.get("keywords", [])]
            oracle = (c.get("oracle_text") or "").lower()
            is_instant = "instant" in tl or "flash" in tl or "flash" in keywords or re.search(r"\bflash\b", oracle) is not None
            
            classification = c.get("classification") or {}
            is_interaction = (
                classification.get("is_targeted_removal")
                or classification.get("is_counterspell")
                or classification.get("is_board_wipe")
                or classification.get("is_protection")
            )
            
            if is_instant and is_interaction:
                raw_cmc = float(c.get("cmc", 0.0))
                effective_cmc = raw_cmc
                if "rather than pay" in oracle or "without paying" in oracle or raw_cmc == 0:
                    effective_cmc = 0.0
                for _ in range(qty):
                    holdout_cards.append({"name": name, "cmc": effective_cmc})
                    total_cmc += effective_cmc
                    if effective_cmc == 0:
                        cmc_breakdown["0"] += 1
                    elif effective_cmc == 1:
                        cmc_breakdown["1"] += 1
                    elif effective_cmc == 2:
                        cmc_breakdown["2"] += 1
                    elif effective_cmc == 3:
                        cmc_breakdown["3"] += 1
                    else:
                        cmc_breakdown["4+"] += 1
                        
        count = len(holdout_cards)
        avg_cmc = round(total_cmc / count, 2) if count > 0 else 0.0
        
        if avg_cmc < 2.0 and count > 0:
            rating = "Lean / Tempo (Pass with <= 2 open mana)"
            rating_color = "emerald"
        elif avg_cmc <= 2.8:
            rating = "Moderate (Standard Commander pacing)"
            rating_color = "cyan"
        else:
            rating = "Heavy / Clunky (Demands 3+ open mana, telegraphing answers)"
            rating_color = "rose"
            
        return {
            "avg_holdout_cmc": avg_cmc,
            "instant_interaction_count": count,
            "cmc_breakdown": cmc_breakdown,
            "rating": rating,
            "rating_color": rating_color,
            "cards": holdout_cards[:10],
        }

    def analyze_enabler_payoff_ratio(self, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        For archetype-heavy decks (counters, tribal, sacrifice, tokens, spells, blink),
        measures cards that initiate an engine (enablers) vs. cards that reward doing it (payoffs).
        """
        theme_counts = {
            "counters": {"enablers": [], "payoffs": []},
            "sacrifice": {"enablers": [], "payoffs": []},
            "graveyard": {"enablers": [], "payoffs": []},
            "spellslinger": {"enablers": [], "payoffs": []},
            "tokens": {"enablers": [], "payoffs": []},
            "blink": {"enablers": [], "payoffs": []},
        }
        
        for c in cards:
            name = c.get("name", "")
            qty = int(c.get("quantity", 1))
            classification = c.get("classification") or {}
            
            for en in classification.get("engine_enabler", []):
                if en in theme_counts:
                    for _ in range(qty):
                        theme_counts[en]["enablers"].append(name)
            for po in classification.get("engine_payoff", []):
                if po in theme_counts:
                    for _ in range(qty):
                        theme_counts[po]["payoffs"].append(name)
                        
        dominant_theme = None
        max_total = 0
        for th, data in theme_counts.items():
            total = len(data["enablers"]) + len(data["payoffs"])
            if total > max_total and total >= 3:
                max_total = total
                dominant_theme = th
                
        theme_display_names = {
            "counters": "+1/+1 Counters Engine",
            "sacrifice": "Aristocrats / Sacrifice Engine",
            "graveyard": "Graveyard / Reanimation Engine",
            "spellslinger": "Spellslinger / Storm Engine",
            "tokens": "Token Swarm Engine",
            "blink": "Blink / ETB Flicker Engine",
        }
        
        if dominant_theme:
            data = theme_counts[dominant_theme]
            en_cnt = len(data["enablers"])
            po_cnt = len(data["payoffs"])
            ratio = round(en_cnt / po_cnt, 2) if po_cnt > 0 else float(en_cnt)
            
            if 1.4 <= ratio <= 2.6:
                health = "Optimal Engine Balance (1.5–2.5:1) — fuel and rewards in harmony"
                status_color = "emerald"
            elif ratio < 1.2:
                health = "Payoff-Heavy Bottleneck (<1.2:1) — risk of dead cards with no fuel"
                status_color = "amber"
            elif ratio > 3.0:
                health = "Enabler-Heavy Low Impact (>3:1) — high setup, low closing rewards"
                status_color = "cyan"
            else:
                health = "Balanced Engine"
                status_color = "emerald"
                
            return {
                "theme": theme_display_names.get(dominant_theme, dominant_theme),
                "enabler_count": en_cnt,
                "payoff_count": po_cnt,
                "ratio": ratio,
                "health": health,
                "status_color": status_color,
                "enablers": sorted(list(set(data["enablers"])))[:8],
                "payoffs": sorted(list(set(data["payoffs"])))[:8],
            }
            
        return {
            "theme": "Goodstuff / Not Engine-Dependent",
            "enabler_count": 0,
            "payoff_count": 0,
            "ratio": 1.0,
            "health": "Balanced (Relies on raw card quality rather than narrow synergies)",
            "status_color": "slate",
            "enablers": [],
            "payoffs": [],
        }

    def analyze_typal_density(self, cards: List[Dict[str, Any]], commander_names: List[str]) -> Dict[str, Any]:
        """
        Tracks typal / kindred density: matching creature counts, type-specific lords/discounts,
        and kindred triggers.
        """
        subtype_counts: Dict[str, int] = {}
        total_creatures = 0
        matching_cards: Dict[str, List[str]] = {}
        
        for c in cards:
            tl = (c.get("type_line") or "").lower()
            if "creature" not in tl:
                continue
            qty = int(c.get("quantity", 1))
            total_creatures += qty
            name = c.get("name", "")
            
            classification = c.get("classification") or {}
            subtypes = classification.get("creature_subtypes") or self.classifier.extract_subtypes(c.get("type_line", ""))
            
            for st in subtypes:
                st_clean = st.capitalize()
                subtype_counts[st_clean] = subtype_counts.get(st_clean, 0) + qty
                if st_clean not in matching_cards:
                    matching_cards[st_clean] = []
                matching_cards[st_clean].append(name)
                
        primary_type = "None"
        cmdr_types = []
        for c in cards:
            if c.get("name") in commander_names or c.get("section") == "commander":
                cmdr_subtypes = self.classifier.extract_subtypes(c.get("type_line", ""))
                cmdr_types.extend([s.capitalize() for s in cmdr_subtypes])
                
        for ct in cmdr_types:
            if subtype_counts.get(ct, 0) >= 3:
                primary_type = ct
                break
                
        if primary_type == "None" and subtype_counts:
            sorted_subtypes = sorted(subtype_counts.items(), key=lambda x: x[1], reverse=True)
            for st, cnt in sorted_subtypes:
                if cnt >= 5 and st not in ["Human", "Warrior", "Soldier", "Wizard", "Cleric", "Rogue"] or cnt >= 8:
                    primary_type = st
                    break
            if primary_type == "None" and sorted_subtypes and sorted_subtypes[0][1] >= 6:
                primary_type = sorted_subtypes[0][0]
                
        if primary_type != "None":
            matching_cnt = subtype_counts.get(primary_type, 0)
            matching_pct = round((matching_cnt / total_creatures) * 100, 1) if total_creatures > 0 else 0.0
            
            kindred_support = []
            re_type_mention = re.compile(rf"\b{primary_type}\b", re.IGNORECASE)
            for c in cards:
                oracle = c.get("oracle_text", "")
                name = c.get("name", "")
                if self.classifier.re_typal_staples.search(name) or self.classifier.re_typal_staples.search(oracle):
                    if name not in kindred_support:
                        kindred_support.append(name)
                elif re_type_mention.search(oracle) and "creature" not in (c.get("type_line") or "").lower():
                    if name not in kindred_support:
                        kindred_support.append(name)
                        
            total_typal_deck_pct = round(((matching_cnt + len(kindred_support)) / len(cards)) * 100, 1) if cards else 0.0
            is_typal = matching_cnt >= 10 or (matching_cnt >= 6 and len(kindred_support) >= 1) or (total_creatures > 0 and (matching_cnt / total_creatures) >= 0.50 and matching_cnt >= 3)
            
            return {
                "is_typal_deck": is_typal,
                "primary_type": primary_type,
                "matching_creatures_count": matching_cnt,
                "total_creatures": total_creatures,
                "matching_creatures_pct": matching_pct,
                "kindred_support_count": len(kindred_support),
                "kindred_support_cards": kindred_support[:8],
                "total_typal_deck_pct": total_typal_deck_pct,
            }
            
        return {
            "is_typal_deck": False,
            "primary_type": "None",
            "matching_creatures_count": 0,
            "total_creatures": total_creatures,
            "matching_creatures_pct": 0.0,
            "kindred_support_count": 0,
            "kindred_support_cards": [],
            "total_typal_deck_pct": 0.0,
        }

    def compute_virtual_card_advantage(self, cards: List[Dict[str, Any]], draw_stats: Dict[str, Any]) -> Dict[str, Any]:
        """
        Quantifies recursion, impulse draw, and tutors alongside pure draw,
        reflecting true access to resources.
        """
        impulse_cards = []
        recursion_cards = []
        
        for c in cards:
            qty = int(c.get("quantity", 1))
            name = c.get("name", "")
            classification = c.get("classification") or {}
            
            if classification.get("is_impulse_draw") or "Impulse Draw" in classification.get("tags", []):
                for _ in range(qty):
                    impulse_cards.append(name)
            if classification.get("is_recursion") or "Recursion" in classification.get("tags", []):
                for _ in range(qty):
                    recursion_cards.append(name)
                    
        pure_draw = draw_stats.get("pure_draw", 0)
        impulse_cnt = len(impulse_cards)
        recursion_cnt = len(recursion_cards)
        tutors_cnt = draw_stats.get("tutors", 0)
        
        total_virtual = pure_draw + impulse_cnt + recursion_cnt + tutors_cnt
        
        if total_virtual >= 16:
            rating = "Deep Resource Reserve (Virtually impossible to run out of gas)"
            color = "emerald"
        elif total_virtual >= 11:
            rating = "Strong Access (Consistent resource velocity across turns)"
            color = "cyan"
        elif total_virtual >= 7:
            rating = "Moderate Access (Functional, but vulnerable to heavy attrition)"
            color = "amber"
        else:
            rating = "Fragile Card Advantage (At severe risk of stalling late game)"
            color = "rose"
            
        return {
            "pure_draw": pure_draw,
            "impulse_draw": impulse_cnt,
            "recursion": recursion_cnt,
            "tutors": tutors_cnt,
            "total_virtual_advantage": total_virtual,
            "resource_depth_rating": rating,
            "rating_color": color,
            "impulse_cards": sorted(list(set(impulse_cards)))[:6],
            "recursion_cards": sorted(list(set(recursion_cards)))[:6],
        }

    def classify_win_conditions(
        self,
        cards: List[Dict[str, Any]],
        commander_names: List[str],
        archetype: str,
    ) -> Dict[str, Any]:
        """
        Labels the primary and secondary paths to victory (Combat Damage / Overrun,
        Commander Voltron, Aristocrats / Drain, Infinite Combo, Spellslinger Burn).
        """
        scores = {
            "Combat Damage / Overrun": {"score": 0, "cards": []},
            "Commander Damage / Voltron": {"score": 0, "cards": []},
            "Aristocrats / Life Drain": {"score": 0, "cards": []},
            "Infinite Combo / Alt Win": {"score": 0, "cards": []},
            "Spellslinger / Storm Burn": {"score": 0, "cards": []},
            "Big Mana / Stompy": {"score": 0, "cards": []},
        }
        
        for c in cards:
            name = c.get("name", "")
            qty = int(c.get("quantity", 1))
            classification = c.get("classification") or {}
            win_tags = classification.get("wincon_tags", [])
            
            if "combat_overrun" in win_tags:
                scores["Combat Damage / Overrun"]["score"] += (3 * qty)
                scores["Combat Damage / Overrun"]["cards"].append(name)
            if "commander_voltron" in win_tags:
                scores["Commander Damage / Voltron"]["score"] += (2 * qty)
                scores["Commander Damage / Voltron"]["cards"].append(name)
            if "aristocrats_drain" in win_tags:
                scores["Aristocrats / Life Drain"]["score"] += (3 * qty)
                scores["Aristocrats / Life Drain"]["cards"].append(name)
            if "infinite_combo" in win_tags:
                scores["Infinite Combo / Alt Win"]["score"] += (4 * qty)
                scores["Infinite Combo / Alt Win"]["cards"].append(name)
            if "spellslinger_burn" in win_tags:
                scores["Spellslinger / Storm Burn"]["score"] += (3 * qty)
                scores["Spellslinger / Storm Burn"]["cards"].append(name)
            if "big_mana" in win_tags:
                scores["Big Mana / Stompy"]["score"] += (2 * qty)
                scores["Big Mana / Stompy"]["cards"].append(name)
                
        equipment_count = sum(1 for c in cards if "equipment" in (c.get("type_line") or "").lower())
        if equipment_count >= 6:
            scores["Commander Damage / Voltron"]["score"] += (equipment_count * 2)
            scores["Commander Damage / Voltron"]["cards"].append(f"{equipment_count} Equipment Arsenal")
            
        sorted_wincons = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
        
        primary_name, primary_data = sorted_wincons[0]
        secondary_name, secondary_data = sorted_wincons[1]
        
        if primary_data["score"] == 0:
            primary_name = "Combat Damage & Board Presence"
            clock = "Turns 8–10 (Standard Combat Attrition)"
        elif primary_name in ["Infinite Combo / Alt Win", "Spellslinger / Storm Burn"]:
            clock = "Turns 4–6 (Accelerated Combo Clock)"
        elif primary_name in ["Commander Damage / Voltron", "Combat Damage / Overrun"]:
            clock = "Turns 6–8 (Aggressive Combat Clock)"
        else:
            clock = "Turns 7–9 (Midrange Engine Clock)"
            
        return {
            "primary_wincon": {
                "name": primary_name,
                "confidence": "High" if primary_data["score"] >= 6 else "Moderate",
                "key_cards": sorted(list(set(primary_data["cards"])))[:6],
            },
            "secondary_wincon": {
                "name": secondary_name if secondary_data["score"] >= 2 else "Combat Damage Fallback",
                "confidence": "Secondary" if secondary_data["score"] >= 2 else "Incidental",
                "key_cards": sorted(list(set(secondary_data["cards"])))[:4],
            },
            "clock_estimate": clock,
        }

    def analyze_mana_sinks(self, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Counts repeatable outlets for excess mana in the late game
        (activated abilities, X-spells, utility land activations).
        """
        sinks = []
        sink_type_counts = {"x_spell": 0, "activated_ability": 0, "utility_land": 0, "repeatable_spell": 0}
        
        for c in cards:
            qty = int(c.get("quantity", 1))
            name = c.get("name", "")
            classification = c.get("classification") or {}
            
            if classification.get("is_mana_sink"):
                sink_type = classification.get("mana_sink_type") or "activated_ability"
                sink_type_counts[sink_type] = sink_type_counts.get(sink_type, 0) + qty
                for _ in range(qty):
                    sinks.append({
                        "name": name,
                        "type": sink_type.replace("_", " ").title(),
                        "cmc": float(c.get("cmc", 0.0)),
                    })
                    
        total = len(sinks)
        if total >= 5:
            resilience = "Abundant (5+ Outlets) — Immune to late-game flood"
            resilience_color = "emerald"
        elif total >= 2:
            resilience = "Sufficient (2–4 Outlets) — Reliable excess mana conversion"
            resilience_color = "cyan"
        else:
            resilience = "Starved (0–1 Outlets) — Danger of flooding with nothing to cast"
            resilience_color = "rose"
            
        return {
            "total_sinks": total,
            "type_breakdown": sink_type_counts,
            "late_game_resilience": resilience,
            "resilience_color": resilience_color,
            "top_sinks": sinks[:10],
        }

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
        dork_or_rock_count = 0
        land_fetch_count = 0
        treasure_count = 0
        taplands_count = 0
        targeted_removal_count = 0
        targeted_removal_cmc_sum = 0.0
        board_wipe_count = 0
        draw_engine_count = 0
        draw_burst_count = 0
        draw_cantrip_count = 0
        tutor_general_count = 0
        tutor_land_count = 0
        tag_counts = {"Ramp": 0, "Targeted Removal": 0, "Board Wipe": 0, "Card Draw": 0, "Tutor": 0, "Tapland": 0}

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
            for t in classification.get("tags", []):
                tag_counts[t] = tag_counts.get(t, 0) + qty

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

                    ramp_type = classification.get("ramp_type")
                    if ramp_type == "dork_or_rock":
                        dork_or_rock_count += qty
                    elif ramp_type == "land_fetch":
                        land_fetch_count += qty
                    elif ramp_type == "treasure":
                        treasure_count += qty

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
            if classification.get("is_draw") or classification.get("is_card_draw"):
                draw_type = classification.get("draw_type")
                if draw_type == "engine":
                    draw_engine_count += qty
                elif draw_type == "cantrip":
                    draw_cantrip_count += qty
                else:
                    draw_burst_count += qty

            # Tutor
            if classification.get("is_tutor"):
                tutor_type = classification.get("tutor_type")
                if tutor_type == "general":
                    tutor_general_count += qty
                elif tutor_type in ["land", "land_search"]:
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

        # Commander Extraction
        cmdr_names = []
        if isinstance(deck_data, dict):
            cmdr_names = deck_data.get("commander", []) or []
        cmdr_card = next((c for c in enriched_cards if c.get("section") == "commander" or c.get("name") in cmdr_names), None)

        # Advanced Tactical & Statistical Metrics (11-Metric Suite)
        land_drop_probabilities = self.compute_land_drop_probabilities(
            land_count=total_lands_count,
            cheap_cantrip_count=draw_cantrip_count,
            total_cards=total_cards_count or 99,
        )
        opening_hand_keepability = self.simulate_opening_hand_keepability(enriched_cards)
        earliest_commander_cast = self.simulate_earliest_commander_cast(cmdr_card, enriched_cards)
        threat_coverage = self.analyze_threat_coverage(enriched_cards)
        counter_vs_protection = self.analyze_counter_vs_protection(enriched_cards)
        instant_mana_holdout = self.compute_instant_mana_holdout(enriched_cards)
        enabler_payoff = self.analyze_enabler_payoff_ratio(enriched_cards)
        typal_density = self.analyze_typal_density(enriched_cards, cmdr_names)
        virtual_card_advantage = self.compute_virtual_card_advantage(
            enriched_cards,
            {
                "pure_draw": total_draw_count,
                "tutors": total_tutor_count,
            },
        )
        win_conditions = self.classify_win_conditions(enriched_cards, cmdr_names, archetype)
        mana_sinks = self.analyze_mana_sinks(enriched_cards)

        stats = {
            "total_value": round(total_deck_value, 2),
            "avg_cmc": nonland_amv,
            "nonland_amv": nonland_amv,
            "amv_color": amv_color,
            "total_cards": total_cards_count,
            "nonland_count": nonland_count,
            "land_count": total_lands_count,
            "type_counts": type_counts,
            "tag_counts": tag_counts,
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
            "dork_or_rock_count": dork_or_rock_count,
            "land_fetch_count": land_fetch_count,
            "treasure_count": treasure_count,
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
            "ramp_type_breakdown": {
                "dork_or_rock": dork_or_rock_count,
                "land_fetch": land_fetch_count,
                "treasure": treasure_count,
                "fast": fast_ramp_count,
                "standard": standard_ramp_count,
                "total": total_ramp_count,
            },
            "draw_type_breakdown": {
                "engine": draw_engine_count,
                "burst": draw_burst_count,
                "cantrip": draw_cantrip_count,
                "total": total_draw_count,
            },
            "tutor_type_breakdown": {
                "general": tutor_general_count,
                "land": tutor_land_count,
                "total": total_tutor_count,
            },
            "removal_type_breakdown": {
                "targeted": targeted_removal_count,
                "board_wipe": board_wipe_count,
                "total": targeted_removal_count + board_wipe_count,
            },
            # 11-Metric Telemetry Suite
            "land_drop_probabilities": land_drop_probabilities,
            "opening_hand_keepability": opening_hand_keepability,
            "earliest_commander_cast": earliest_commander_cast,
            "threat_coverage": threat_coverage,
            "counter_vs_protection": counter_vs_protection,
            "instant_mana_holdout": instant_mana_holdout,
            "enabler_payoff": enabler_payoff,
            "typal_density": typal_density,
            "virtual_card_advantage": virtual_card_advantage,
            "win_conditions": win_conditions,
            "mana_sinks": mana_sinks,
            # Flattened Convenience Stats
            "land_drop_turn_3_pct": land_drop_probabilities.get("turn_3", 0.0),
            "effective_keepability_rate": opening_hand_keepability.get("effective_keep_rate", 0.0),
            "natural_keepability_rate": opening_hand_keepability.get("natural_keep_rate", 0.0),
            "median_commander_cast_turn": earliest_commander_cast.get("median_cast_turn", 4),
            "avg_instant_holdout": instant_mana_holdout.get("avg_holdout_cmc", 0.0),
            "total_mana_sinks": mana_sinks.get("total_sinks", 0),
            "primary_wincon": win_conditions.get("primary_wincon", {}).get("name", "Combat Damage"),
            "is_typal_deck": typal_density.get("is_typal_deck", False),
            "primary_creature_type": typal_density.get("primary_type", "None"),
            "enabler_to_payoff_ratio": enabler_payoff.get("ratio", 1.0),
            "total_virtual_advantage": virtual_card_advantage.get("total_virtual_advantage", 0),
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
