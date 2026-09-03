"""
Card classification engine for Magic: The Gathering cards.
Analyzes enriched card metadata from Scryfall to assign functional roles:
- Ramp (Fast: CMC <= 2, Standard: CMC >= 3)
- Targeted Removal & Board Wipes
- Draw Actions (Engine, Cantrip, Burst)
- Tutors (General, Land)
- Taplands
- Threat Type Coverage (Creatures, Artifacts, Enchantments, Planeswalkers, Graveyards, Lands, Spells/Stack)
- Protection vs. Counterspells
- Virtual Card Advantage (Impulse Draw, Graveyard Recursion)
- Engine Roles (Enablers vs. Payoffs)
- Typal / Kindred Synergies
- Win Condition Detection
- Mana Sinks (X-Spells, Activated Outlets, Utility Lands)
"""

import re
from typing import Dict, Any, Tuple, List, Optional


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
        self.re_treasure_creation = self.re_treasure

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
        self.re_targeted_destroy_exile = re.compile(
            r"\b(destroy|exile|counter|return)\s+target\b",
            re.IGNORECASE,
        )
        self.re_targeted_damage = re.compile(
            r"\bdeals?\s+(\d+|X|that much)\s+damage to target\b",
            re.IGNORECASE,
        )
        self.re_fight_bite = re.compile(
            r"target creature you control fights target|deals damage equal to its power to target",
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
        self.re_mass_removal = self.re_board_wipe
        self.re_damage_all = re.compile(
            r"deals\s+(\d+|X)\s+damage to each (creature|player and each creature|nonflying creature)",
            re.IGNORECASE,
        )
        self.re_mass_bounce = re.compile(
            r"return all (creatures|nonland permanents|permanents) to their owners' hands",
            re.IGNORECASE,
        )
        self.re_mass_sacrifice = re.compile(
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
        self.re_draw_triggers = self.re_draw_engine
        self.re_cantrip = re.compile(
            r"^draw a card\.$|\.\s*Draw a card\.|draw a card",
            re.IGNORECASE,
        )

        # 5. Tutor Patterns
        self.re_tutor_general = re.compile(
            r"search your library for (?:a|up to \d+)?\s*(?:card|instant|sorcery|creature|artifact|enchantment|planeswalker|permanent)\b",
            re.IGNORECASE,
        )

        # 6. Interaction & Threat Target Patterns
        self.re_counterspell = re.compile(
            r"\bcounter target (?:noncreature |creature |instant |sorcery )?spell\b|\bcounter target spell\b|\bcounter that spell\b",
            re.IGNORECASE,
        )
        self.re_hits_creature = re.compile(
            r"\btarget (?:attacking |blocking )?creature\b|\bcreatures?\b|\bany target\b|\b(each|all) creatures?\b|\btarget (?:nonland )?permanent\b",
            re.IGNORECASE,
        )
        self.re_hits_artifact = re.compile(
            r"\btarget artifact\b|\bartifacts?\b|\bany target\b|\b(each|all) artifacts?\b|\btarget (?:nonland )?permanent\b",
            re.IGNORECASE,
        )
        self.re_hits_enchantment = re.compile(
            r"\btarget enchantment\b|\benchantments?\b|\bany target\b|\b(each|all) enchantments?\b|\btarget (?:nonland )?permanent\b",
            re.IGNORECASE,
        )
        self.re_hits_planeswalker = re.compile(
            r"\btarget planeswalker\b|\bplaneswalkers?\b|\bany target\b|\b(each|all) planeswalkers?\b|\btarget (?:nonland )?permanent\b",
            re.IGNORECASE,
        )
        self.re_hits_graveyard = re.compile(
            r"\bexile (?:all cards from |target player's |all )?graveyards?\b|"
            r"\bexile target (?:card|creature|instant|sorcery) from a graveyard\b|"
            r"\bcan't leave (?:their |all )?graveyards\b|"
            r"\bgraveyard.*?(?:shuffled into|shuffle into)\b",
            re.IGNORECASE,
        )
        self.re_hits_land = re.compile(
            r"\btarget (?:nonbasic )?land\b|\bdestroy all lands\b|\btarget permanent\b",
            re.IGNORECASE,
        )
        self.re_hits_any_permanent = re.compile(
            r"\btarget (?:nonland )?permanent\b|\bany target\b|\bpermanent's owner\b",
            re.IGNORECASE,
        )

        # 7. Defensive Protection Patterns
        self.re_prot_hexproof = re.compile(
            r"\b(?:gains?|have|has)\s+(?:hexproof|shroud)\b|\bhexproof\b|\bshroud\b",
            re.IGNORECASE,
        )
        self.re_prot_indestructible = re.compile(
            r"\b(?:gains?|have|has|are)\b.*?\bindestructible\b|\bindestructible until end of turn\b|\bhas indestructible\b|\bgrant.*?indestructible\b|\bindestructible\b",
            re.IGNORECASE,
        )
        self.re_prot_phase_out = re.compile(
            r"\bphase(?:s)?\s+out\b",
            re.IGNORECASE,
        )
        self.re_prot_flicker_bounce = re.compile(
            r"\bexile target (?:creature|permanent) you control, then return\b|\breturn target (?:creature|permanent) you control to its owner's hand\b",
            re.IGNORECASE,
        )
        self.re_prot_ward_prot = re.compile(
            r"\bprotection from (?:all colors|each color|white|blue|black|red|green|everything|creatures)\b|\bward\s+\{[0-9X\w]+\}\b",
            re.IGNORECASE,
        )

        # 8. Virtual Card Advantage Patterns
        self.re_impulse_draw = re.compile(
            r"exile (?:the top\s+)?(?:\w+\s+)?cards? of your library.*?(?:you may (?:play|cast)|until (?:the end of|end of))|"
            r"exile (?:the top|\d+|that many|up to \d+|[\w\s]+) cards? of your library.*?(?:play|cast)|"
            r"\b(light up the stage|reckless impulse|wrenn's resolve|jeska's will|valakut exploration|professional face-breaker|laelia, the blade reforged)\b",
            re.IGNORECASE | re.DOTALL,
        )
        self.re_graveyard_recursion = re.compile(
            r"return .*? from (?:your|a) graveyard to (?:your hand|the battlefield)|"
            r"cast .*? from (?:your|a) graveyard|"
            r"put .*? from (?:your|a) graveyard onto the battlefield",
            re.IGNORECASE,
        )

        # 9. Engine Enabler vs. Payoff Patterns
        # Counters
        self.re_counters_enabler = re.compile(r"put (?:a|\d+|X) \+1/\+1 counter|proliferate|enters.*?with (?:a|\d+|X) \+1/\+1", re.IGNORECASE)
        self.re_counters_payoff = re.compile(r"whenever (?:a|\d+) \+1/\+1 counter|twice that many \+1/\+1|for each \+1/\+1 counter|modified creature|creatures with \+1/\+1 counters", re.IGNORECASE)
        # Sacrifice / Aristocrats
        self.re_sac_enabler = re.compile(r"sacrifice a (?:creature|permanent):|sacrifice another creature:|create (?:a|\d+|X) (?:[a-zA-Z]+ )?(?:creature |token creature )?tokens?", re.IGNORECASE)
        self.re_sac_payoff = re.compile(r"whenever (?:a|another) creature dies|whenever you sacrifice|each opponent loses \d+ life and you gain|blood artist|zulaport cutthroat|cruel celebrant|marionette apprentice", re.IGNORECASE)
        # Graveyard / Reanimation
        self.re_gy_enabler = re.compile(r"\bmill \d+\b|discard a card:|put (?:the top|\d+) cards? of your library into your graveyard|entomb|buried alive", re.IGNORECASE)
        self.re_gy_payoff = re.compile(r"return .*? from (?:your|a) graveyard to the battlefield|underworld breach|delve|dredge|flashback|reanimate", re.IGNORECASE)
        # Spellslinger
        self.re_spells_enabler = re.compile(r"^draw a card|cantrip|ritual|add \{(?:[WUBRGC0-9])+\}", re.IGNORECASE)
        self.re_spells_payoff = re.compile(r"whenever you cast an instant or sorcery|whenever you cast a noncreature spell|magecraft|storm|guttersnipe|archmage emeritus", re.IGNORECASE)
        # Blink
        self.re_blink_enabler = re.compile(r"exile (?:target|another) (?:creature|permanent) you control, then return|flicker", re.IGNORECASE)
        self.re_blink_payoff = re.compile(r"when (?:this creature|another creature|it) enters the battlefield,\s*(?:draw|create|deal|destroy|gain)", re.IGNORECASE)
        # Tokens
        self.re_tokens_enabler = re.compile(r"create (?:a|\d+|X) (?:[a-zA-Z]+ )?tokens?", re.IGNORECASE)
        self.re_tokens_payoff = re.compile(r"whenever (?:a|another) creature enters the battlefield under your control|twice that many tokens|doubling season|parallel lives|anointed procession|purphoros", re.IGNORECASE)

        # 10. Typal / Kindred Support Patterns
        self.re_typal_lord = re.compile(r"(?:other )?([a-zA-Z]+) (?:creatures? )?you control get \+[0-9]/\+[0-9]", re.IGNORECASE)
        self.re_typal_discount = re.compile(r"([a-zA-Z]+) spells? you cast cost \{?\d+\}? less", re.IGNORECASE)
        self.re_typal_trigger = re.compile(r"whenever (?:a|another) ([a-zA-Z]+) (?:enters the battlefield|you control dies|attacks)", re.IGNORECASE)
        self.re_typal_staples = re.compile(
            r"\b(changeling|kindred discovery|herald's horn|urza's incubator|vanquisher's banner|roaming throne|"
            r"morophon|door of destinies|coat of arms|cavern of souls|path of ancestry|realmwalker|patriarch's bidding|"
            r"metallic mimic|adaptive automaton|icon of ancestry)\b|choose a creature type",
            re.IGNORECASE,
        )

        # 11. Win Condition Patterns
        self.re_wincon_overrun = re.compile(
            r"creatures you control get \+[X\d]+/\+[X\d]+|"
            r"gain trample.*?until end of turn|"
            r"\b(craterhoof behemoth|moonshaker cavalry|triumph of the hordes|overwhelming stampede|beastmaster ascension|akroma's will|finale of devastation)\b",
            re.IGNORECASE,
        )
        self.re_wincon_drain = re.compile(
            r"\b(blood artist|zulaport cutthroat|marionette apprentice|syr konrad, the grim|cruel celebrant|"
            r"bastion of remembrance|torment of hailfire|exsanguinate|meathook massacre|gray merchant of asphodel)\b",
            re.IGNORECASE,
        )
        self.re_wincon_combo_alt = re.compile(
            r"you win the game|target opponent loses the game|"
            r"\b(thassa's oracle|laboratory maniac|jace, wielder of mysteries|approach of the second sun|"
            r"revel in riches|halo fountain|simic ascendancy|felidar sovereign|dramatic reversal|isochron scepter|"
            r"dualcaster mage|twinflame|walking ballista|heliod, sun-crowned|exquisite blood|sanguine bond|"
            r"kiki-jiki, mirror breaker|zealous conscripts|pitiless plunderer)\b",
            re.IGNORECASE,
        )
        self.re_wincon_burn_storm = re.compile(
            r"\b(aetherflux reservoir|grapeshot|tendrils of agony|guttersnipe|storm-kiln artist|fiery emancipation)\b",
            re.IGNORECASE,
        )

        # 12. Mana Sink Patterns
        self.re_x_cost = re.compile(r"\{X\}", re.IGNORECASE)
        self.re_activated_mana_ability = re.compile(
            r"(?:^|\n)(?:\{[0-9WUBRGCX\/]+\})+\s*(?:,\s*(?:\{T\}|[^{:\n]+?))?\s*:\s*(?!.*?add\s+\{)(?!.*?[Ee]quip\b).*",
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
        return card_data.get("oracle_text", "") or "", card_data.get("type_line", "") or "", float(card_data.get("cmc", 0.0))

    def extract_subtypes(self, type_line: str) -> List[str]:
        """Extracts creature and permanent subtypes from a type line."""
        if not type_line or "—" not in type_line:
            return []
        parts = type_line.split("—")
        if len(parts) < 2:
            return []
        subtypes_part = parts[1].strip()
        tokens = [s.strip() for s in subtypes_part.split() if s.strip()]
        excluded = {"//", "Legendary", "Basic", "Snow", "World", "Tribal", "Kindred"}
        return [t for t in tokens if t not in excluded]

    def classify_card(self, card_data: Dict[str, Any]) -> Dict[str, Any]:
        """Classifies an enriched Scryfall card JSON object into functional roles."""
        return self.classify(card_data)

    def classify(self, card_data: Dict[str, Any]) -> Dict[str, Any]:
        """Classifies an individual MTG card into comprehensive roles and telemetry categories."""
        oracle_text, type_line, cmc = self.extract_text_and_types(card_data)
        oracle_lower = oracle_text.lower()
        type_line_lower = type_line.lower()
        is_land = "land" in type_line_lower
        is_creature = "creature" in type_line_lower
        is_artifact = "artifact" in type_line_lower
        is_enchantment = "enchantment" in type_line_lower
        is_planeswalker = "planeswalker" in type_line_lower
        is_instant = "instant" in type_line_lower
        is_sorcery = "sorcery" in type_line_lower
        is_permanent = is_creature or is_artifact or is_enchantment or is_planeswalker or "battle" in type_line_lower

        mana_cost = card_data.get("mana_cost", "")
        if not mana_cost and "card_faces" in card_data and card_data["card_faces"]:
            mana_cost = " // ".join([face.get("mana_cost", "") for face in card_data["card_faces"] if face.get("mana_cost")])

        tags: List[str] = []
        threat_targets: List[str] = []
        protection_types: List[str] = []
        engine_enablers: List[str] = []
        engine_payoffs: List[str] = []
        wincon_tags: List[str] = []
        subtypes = self.extract_subtypes(type_line)

        result = {
            "is_ramp": False,
            "ramp_tier": None,       # "fast" (CMC <= 2) or "standard" (CMC >= 3)
            "ramp_type": None,       # "dork_or_rock", "land_fetch", "treasure"
            "is_targeted_removal": False,
            "is_board_wipe": False,
            "is_draw": False,
            "is_card_draw": False,
            "draw_type": None,       # "engine", "cantrip", "burst"
            "is_tutor": False,
            "tutor_type": None,      # "general", "land", "land_search"
            "is_tapland": False,
            "is_counterspell": False,
            "threat_targets": threat_targets,
            "is_protection": False,
            "protection_types": protection_types,
            "is_impulse_draw": False,
            "is_recursion": False,
            "is_mana_sink": False,
            "mana_sink_type": None,  # "x_spell", "activated_ability", "utility_land", "repeatable_spell"
            "engine_enabler": engine_enablers,
            "engine_payoff": engine_payoffs,
            "wincon_tags": wincon_tags,
            "creature_subtypes": subtypes,
            "tags": tags,
        }

        # 1. Tapland check
        if is_land:
            if "enters the battlefield tapped" in oracle_lower and not any(
                cond in oracle_lower for cond in ["unless", "if you control", "as long as", "you may pay", "if you don't", "reveal a"]
            ):
                result["is_tapland"] = True
                tags.append("Tapland")

        # 2. Ramp check (non-lands only)
        if not is_land:
            if self.re_mana_dork_rock.search(oracle_text):
                result["is_ramp"] = True
                result["ramp_type"] = "dork_or_rock"
                result["ramp_tier"] = "fast" if cmc <= 2.0 else "standard"
                tags.append("Ramp")
            elif self.re_land_fetch.search(oracle_text):
                result["is_ramp"] = True
                result["ramp_type"] = "land_fetch"
                result["ramp_tier"] = "fast" if cmc <= 2.0 else "standard"
                tags.append("Ramp")
            elif self.re_treasure.search(oracle_text):
                result["is_ramp"] = True
                result["ramp_type"] = "treasure"
                result["ramp_tier"] = "fast" if cmc <= 2.0 else "standard"
                tags.append("Ramp")

        # 3. Board Wipe check
        if self.re_board_wipe.search(oracle_text):
            result["is_board_wipe"] = True
            tags.append("Board Wipe")

        # 4. Targeted Removal & Counterspell check
        if self.re_counterspell.search(oracle_text):
            result["is_counterspell"] = True
            result["is_targeted_removal"] = True
            threat_targets.append("spell")
            tags.append("Counterspell")
            tags.append("Targeted Removal")
        elif self.re_targeted_removal.search(oracle_text):
            result["is_targeted_removal"] = True
            tags.append("Targeted Removal")

        # 5. Threat Targets Categorization (for all removal / wipes / interaction)
        is_interaction = result["is_targeted_removal"] or result["is_board_wipe"] or "deal" in oracle_lower or "destroy" in oracle_lower or "exile" in oracle_lower
        if is_interaction:
            if self.re_hits_any_permanent.search(oracle_text):
                for target_type in ["creatures", "artifacts", "enchantments", "planeswalkers"]:
                    if target_type not in threat_targets:
                        threat_targets.append(target_type)
                if "target permanent" in oracle_lower and "nonland" not in oracle_lower:
                    if "lands" not in threat_targets:
                        threat_targets.append("lands")
            else:
                if self.re_hits_creature.search(oracle_text) and "creatures" not in threat_targets:
                    threat_targets.append("creatures")
                if self.re_hits_artifact.search(oracle_text) and "artifacts" not in threat_targets:
                    threat_targets.append("artifacts")
                if self.re_hits_enchantment.search(oracle_text) and "enchantments" not in threat_targets:
                    threat_targets.append("enchantments")
                if self.re_hits_planeswalker.search(oracle_text) and "planeswalkers" not in threat_targets:
                    threat_targets.append("planeswalkers")
                if self.re_hits_land.search(oracle_text) and "lands" not in threat_targets:
                    threat_targets.append("lands")

        if self.re_hits_graveyard.search(oracle_text):
            threat_targets.append("graveyards")
            tags.append("Graveyard Hate")

        # 6. Defensive Protection check
        if self.re_prot_hexproof.search(oracle_text) or "you have hexproof" in oracle_lower:
            result["is_protection"] = True
            protection_types.append("hexproof_shroud")
        if self.re_prot_indestructible.search(oracle_text):
            result["is_protection"] = True
            protection_types.append("indestructible")
        if self.re_prot_phase_out.search(oracle_text):
            result["is_protection"] = True
            protection_types.append("phase_out")
        if self.re_prot_flicker_bounce.search(oracle_text):
            result["is_protection"] = True
            protection_types.append("flicker_bounce")
        if self.re_prot_ward_prot.search(oracle_text):
            result["is_protection"] = True
            protection_types.append("ward_prot")
        if result["is_protection"]:
            tags.append("Protection")

        # 7. Draw check (Pure Draw)
        if self.re_draw_action.search(oracle_text):
            result["is_draw"] = True
            result["is_card_draw"] = True
            tags.append("Card Draw")
            if is_permanent and self.re_draw_engine.search(oracle_text):
                result["draw_type"] = "engine"
            elif re.search(r"\bdraws?\s+(two|three|four|five|[2-9]|\d{2,}|X|that many|two additional|three additional)\s+cards?\b", oracle_text, re.IGNORECASE):
                result["draw_type"] = "burst"
            else:
                result["draw_type"] = "cantrip"

        # 8. Virtual Card Advantage (Impulse Draw & Graveyard Recursion)
        if self.re_impulse_draw.search(oracle_text):
            result["is_impulse_draw"] = True
            tags.append("Impulse Draw")
        if self.re_graveyard_recursion.search(oracle_text):
            result["is_recursion"] = True
            tags.append("Recursion")

        # 9. Tutor check
        if self.re_land_fetch.search(oracle_text):
            result["is_tutor"] = True
            result["tutor_type"] = "land"
            tags.append("Tutor")
        elif self.re_tutor_general.search(oracle_text):
            result["is_tutor"] = True
            result["tutor_type"] = "general"
            tags.append("Tutor")

        # 10. Engine Enabler vs Payoff
        if self.re_counters_enabler.search(oracle_text):
            engine_enablers.append("counters")
        if self.re_counters_payoff.search(oracle_text):
            engine_payoffs.append("counters")

        if self.re_sac_enabler.search(oracle_text):
            engine_enablers.append("sacrifice")
        if self.re_sac_payoff.search(oracle_text):
            engine_payoffs.append("sacrifice")

        if self.re_gy_enabler.search(oracle_text):
            engine_enablers.append("graveyard")
        if self.re_gy_payoff.search(oracle_text):
            engine_payoffs.append("graveyard")

        if is_instant or is_sorcery:
            if cmc <= 2.0 and (result["is_draw"] or result["is_ramp"]):
                engine_enablers.append("spellslinger")
        if self.re_spells_payoff.search(oracle_text):
            engine_payoffs.append("spellslinger")

        if self.re_blink_enabler.search(oracle_text):
            engine_enablers.append("blink")
        if self.re_blink_payoff.search(oracle_text) and is_permanent:
            engine_payoffs.append("blink")

        if self.re_tokens_enabler.search(oracle_text):
            engine_enablers.append("tokens")
        if self.re_tokens_payoff.search(oracle_text):
            engine_payoffs.append("tokens")

        # 11. Win Condition Detection
        if self.re_wincon_overrun.search(oracle_text):
            wincon_tags.append("combat_overrun")
        if self.re_wincon_drain.search(oracle_text):
            wincon_tags.append("aristocrats_drain")
        if self.re_wincon_combo_alt.search(oracle_text):
            wincon_tags.append("infinite_combo")
        if self.re_wincon_burn_storm.search(oracle_text):
            wincon_tags.append("spellslinger_burn")
        # Voltron: high equipment / aura count or massive single-target commander buff
        card_name_lower = card_data.get("name", "").lower()
        if "equipment" in type_line_lower or "aura" in type_line_lower:
            if any(k in oracle_lower for k in ["equipped creature gets", "enchanted creature gets", "double strike", "unblockable", "commander's plate", "blackblade reforged"]):
                wincon_tags.append("commander_voltron")
        if cmc >= 7 and (is_creature or "eldrazi" in type_line_lower):
            wincon_tags.append("big_mana")

        # 12. Mana Sinks
        if self.re_x_cost.search(mana_cost) or ("{X}" in oracle_text and (is_instant or is_sorcery or is_permanent)):
            result["is_mana_sink"] = True
            result["mana_sink_type"] = "x_spell"
            tags.append("Mana Sink")
        elif is_land and self.re_activated_mana_ability.search(oracle_text):
            # Utility land with mana activation that isn't just mana production
            result["is_mana_sink"] = True
            result["mana_sink_type"] = "utility_land"
            tags.append("Mana Sink")
        elif is_permanent and not is_land:
            # Activated ability costing mana
            if self.re_activated_mana_ability.search(oracle_text) and "equip" not in oracle_lower:
                result["is_mana_sink"] = True
                result["mana_sink_type"] = "activated_ability"
                tags.append("Mana Sink")
        elif "buyback" in oracle_lower or "flashback" in oracle_lower:
            result["is_mana_sink"] = True
            result["mana_sink_type"] = "repeatable_spell"
            tags.append("Mana Sink")

        result["tags"] = sorted(list(set(tags)))
        return result
