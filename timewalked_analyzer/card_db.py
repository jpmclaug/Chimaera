"""Curated card knowledge base and Oracle-text functional tagger for Commander analysis."""

from __future__ import annotations

import re
from typing import Any

BRACKET_NAMES: dict[int, str] = {
    1: "Exhibition",
    2: "Core",
    3: "Upgraded",
    4: "Optimized",
    5: "cEDH",
}

# Official Wizards of the Coast Commander Game Changers & high-power staples
# Value represents individual "Feels Like" power contribution weight.
GAME_CHANGERS: dict[str, float] = {
    # Fast Mana & Lands
    "Ancient Tomb": 0.14,
    "Chrome Mox": 0.16,
    "Gaea's Cradle": 0.18,
    "Grim Monolith": 0.15,
    "Lion's Eye Diamond": 0.18,
    "Mana Vault": 0.15,
    "Mox Diamond": 0.16,
    "Serra's Sanctum": 0.13,
    "The Tabernacle at Pendrell Vale": 0.15,
    "Glacial Chasm": 0.10,
    "Field of the Dead": 0.10,
    # Tutors
    "Demonic Tutor": 0.14,
    "Vampiric Tutor": 0.15,
    "Imperial Seal": 0.14,
    "Enlightened Tutor": 0.11,
    "Mystical Tutor": 0.11,
    "Worldly Tutor": 0.10,
    "Gamble": 0.09,
    "Gifts Ungiven": 0.12,
    "Intuition": 0.13,
    "Survival of the Fittest": 0.14,
    # Draw & Value Engines
    "Rhystic Study": 0.15,
    "Smothering Tithe": 0.13,
    "The One Ring": 0.15,
    "Mystic Remora": 0.12,
    "Necropotence": 0.15,
    "Ad Nauseam": 0.18,
    "Bolas's Citadel": 0.13,
    "Consecrated Sphinx": 0.10,
    "Seedborn Muse": 0.10,
    "Jeska's Will": 0.12,
    # Free Interaction & Sweeps
    "Cyclonic Rift": 0.12,
    "Fierce Guardianship": 0.13,
    "Force of Will": 0.14,
    "Force of Negation": 0.11,
    "Pact of Negation": 0.11,
    "Deflecting Swat": 0.12,
    "Deadly Rollick": 0.10,
    "Teferi's Protection": 0.10,
    # Wincons, Combos & Extra Turns
    "Thassa's Oracle": 0.18,
    "Underworld Breach": 0.18,
    "Food Chain": 0.15,
    "Expropriate": 0.12,
    "Time Warp": 0.10,
    "Coalition Victory": 0.08,
    "Biorhythm": 0.08,
    "Panoptic Mirror": 0.08,
    "Sway of the Stars": 0.08,
    # Stax & Oppressive Lock Pieces
    "Drannith Magistrate": 0.12,
    "Opposition Agent": 0.13,
    "Orcish Bowmasters": 0.13,
    "Grand Arbiter Augustin IV": 0.10,
    "Tergrid, God of Fright": 0.11,
    "Vorinclex, Voice of Hunger": 0.10,
    "Jin-Gitaxias, Core Augur": 0.10,
    "Narset, Parter of Veils": 0.10,
    "Notion Thief": 0.10,
    "Humility": 0.10,
    "Aura Shards": 0.09,
    "Braids, Cabal Minion": 0.10,
    # High-Ceiling Commanders / Engines
    "Urza, Lord High Artificer": 0.15,
    "Kinnan, Bonder Prodigy": 0.16,
    "Yuriko, the Tiger's Shadow": 0.15,
    "Winota, Joiner of Forces": 0.15,
    "Rofellos, Llanowar Emissary": 0.14,
}

# Strong non-Game-Changer staples that nudge the "Feels Like" score
STRONG_STAPLES: dict[str, float] = {
    "Sol Ring": 0.06,
    "Mana Drain": 0.09,
    "Mental Misstep": 0.08,
    "Swan Song": 0.06,
    "Flusterstorm": 0.08,
    "Silence": 0.07,
    "Esper Sentinel": 0.08,
    "Sylvan Library": 0.08,
    "Sensei's Divining Top": 0.07,
    "Carpet of Flowers": 0.08,
    "Lotus Petal": 0.08,
    "Mox Opal": 0.12,
    "Mox Amber": 0.08,
    "Simian Spirit Guide": 0.07,
    "Elvish Spirit Guide": 0.07,
    "Dark Ritual": 0.07,
    "Cabal Ritual": 0.06,
    "Culling the Weak": 0.07,
    "Entomb": 0.09,
    "Reanimate": 0.08,
    "Animate Dead": 0.06,
    "Finale of Devastation": 0.09,
    "Chord of Calling": 0.07,
    "Eldritch Evolution": 0.08,
    "Neoform": 0.08,
    "Birthing Pod": 0.08,
    "Diabolic Intent": 0.09,
    "Imperial Recruiter": 0.07,
    "Recruiter of the Guard": 0.07,
    "Ranger-Captain of Eos": 0.09,
    "Urza's Saga": 0.09,
    "Boseiju, Who Endures": 0.07,
    "Otawara, Soaring City": 0.06,
    "Fetch Land": 0.03,
    "Nissa, Who Shakes the World": 0.05,
    "Teferi, Time Raveler": 0.07,
    "Collector Ouphe": 0.06,
    "Blood Moon": 0.09,
    "Magus of the Moon": 0.08,
    "Back to Basics": 0.09,
    "Winter Orb": 0.10,
    "Static Orb": 0.10,
    "Stasis": 0.10,
    "Armageddon": 0.11,
    "Ravages of War": 0.11,
    "Obliterate": 0.10,
    "Jokulhaups": 0.10,
    "Decree of Annihilation": 0.10,
}

# Known finishers & haymakers
FINISHERS: set[str] = {
    "Craterhoof Behemoth",
    "Triumph of the Hordes",
    "Finale of Devastation",
    "Akroma's Will",
    "Torment of Hailfire",
    "Exsanguinate",
    "Genesis Wave",
    "Insurrection",
    "Moonshaker Cavalry",
    "Blightsteel Colossus",
    "Aetherflux Reservoir",
    "Laboratory Maniac",
    "Jace, Wielder of Mysteries",
    "Thassa's Oracle",
    "Approach of the Second Sun",
    "Rise of the Dark Realms",
    "Overwhelming Stampede",
}

# Known Mass Land Denial (MLD) cards
MASS_LAND_DENIAL: set[str] = {
    "Armageddon",
    "Ravages of War",
    "Catastrophe",
    "Global Ruin",
    "Sunder",
    "Ruination",
    "Blood Moon",
    "Magus of the Moon",
    "Back to Basics",
    "Winter Orb",
    "Static Orb",
    "Stasis",
    "Hokori, Dust Drinker",
    "Rising Waters",
    "Vorinclex, Voice of Hunger",
    "Obliterate",
    "Jokulhaups",
    "Decree of Annihilation",
    "Impending Disaster",
    "Wake of Destruction",
    "Contamination",
    "Infernal Darkness",
    "Hall of Gemstone",
}

# Known Extra Turn cards
EXTRA_TURN_CARDS: set[str] = {
    "Time Warp",
    "Temporal Manipulation",
    "Capture of Jingzhou",
    "Expropriate",
    "Nexus of Fate",
    "Time Stretch",
    "Walk the Aeons",
    "Temporal Mastery",
    "Temporal Trespass",
    "Part the Waterveil",
    "Alrund's Epiphany",
    "Karn's Temporal Sundering",
    "Beacon of Tomorrows",
    "Notorious Throng",
    "Time Sieve",
    "Savor the Moment",
    "Final Fortune",
    "Last Chance",
    "Warrior's Oath",
}

# Curated local combo database (used alongside or as fallback for Commander Spellbook API)
KNOWN_COMBOS: list[dict[str, Any]] = [
    {
        "id": "thoracle-consult",
        "cards": ["Thassa's Oracle", "Demonic Consultation"],
        "produces": ["Win the game"],
        "bracket_tag": "5",
        "min_turn": 2,
    },
    {
        "id": "thoracle-pact",
        "cards": ["Thassa's Oracle", "Tainted Pact"],
        "produces": ["Win the game"],
        "bracket_tag": "5",
        "min_turn": 2,
    },
    {
        "id": "scepter-reversal",
        "cards": ["Isochron Scepter", "Dramatic Reversal"],
        "produces": ["Infinite mana", "Infinite untaps", "Infinite storm"],
        "bracket_tag": "4",
        "min_turn": 3,
    },
    {
        "id": "breach-led-freeze",
        "cards": ["Underworld Breach", "Lion's Eye Diamond", "Brain Freeze"],
        "produces": ["Infinite mill", "Infinite mana", "Infinite storm"],
        "bracket_tag": "5",
        "min_turn": 2,
    },
    {
        "id": "heliod-ballista",
        "cards": ["Heliod, Sun-Crowned", "Walking Ballista"],
        "produces": ["Infinite damage", "Infinite lifegain"],
        "bracket_tag": "4",
        "min_turn": 4,
    },
    {
        "id": "exquisite-sanguine",
        "cards": ["Exquisite Blood", "Sanguine Bond"],
        "produces": ["Infinite lifeloss", "Infinite lifegain"],
        "bracket_tag": "3",
        "min_turn": 5,
    },
    {
        "id": "dualcaster-twinflame",
        "cards": ["Dualcaster Mage", "Twinflame"],
        "produces": ["Infinite hasty tokens", "Win the game"],
        "bracket_tag": "4",
        "min_turn": 3,
    },
    {
        "id": "kiki-conscripts",
        "cards": ["Kiki-Jiki, Mirror Breaker", "Zealous Conscripts"],
        "produces": ["Infinite hasty tokens", "Win the game"],
        "bracket_tag": "4",
        "min_turn": 5,
    },
    {
        "id": "niv-curiosity",
        "cards": ["Niv-Mizzet, Parun", "Curiosity"],
        "produces": ["Near-infinite damage", "Near-infinite card draw"],
        "bracket_tag": "4",
        "min_turn": 4,
    },
    {
        "id": "basalt-rings",
        "cards": ["Basalt Monolith", "Rings of Brighthearth"],
        "produces": ["Infinite colorless mana"],
        "bracket_tag": "4",
        "min_turn": 4,
    },
    {
        "id": "kinnan-basalt",
        "cards": ["Kinnan, Bonder Prodigy", "Basalt Monolith"],
        "produces": ["Infinite colorless mana"],
        "bracket_tag": "4",
        "min_turn": 2,
    },
    {
        "id": "food-chain-squee",
        "cards": ["Food Chain", "Squee, the Immortal"],
        "produces": ["Infinite creature mana"],
        "bracket_tag": "4",
        "min_turn": 3,
    },
]


def classify_card_roles(card: dict[str, Any]) -> set[str]:
    """Classify a card into functional Commander roles using name, type_line, cmc, and oracle_text."""
    roles: set[str] = set()
    name = card.get("name", "")
    type_line = card.get("type_line", "")
    oracle = (card.get("oracle_text") or "").lower()
    cmc = float(card.get("cmc", 0.0))

    if card.get("game_changer") or name in GAME_CHANGERS:
        roles.add("game_changer")

    if name in STRONG_STAPLES or name in GAME_CHANGERS:
        roles.add("strong_card")

    if name in FINISHERS or (
        "creatures you control get +" in oracle and cmc >= 5
    ) or "you win the game" in oracle:
        roles.add("finisher")

    if name in MASS_LAND_DENIAL or re.search(
        r"destroy all lands|lands don't untap|nonbasic lands are mountains", oracle
    ):
        roles.add("mass_land_denial")

    if name in EXTRA_TURN_CARDS or "extra turn after this one" in oracle:
        roles.add("extra_turn")

    # Fast mana: 0-1 MV nonland mana producers or net-positive rituals/rocks
    if "Land" not in type_line:
        if name in {
            "Sol Ring",
            "Mana Vault",
            "Grim Monolith",
            "Chrome Mox",
            "Mox Diamond",
            "Mox Opal",
            "Mox Amber",
            "Lotus Petal",
            "Lion's Eye Diamond",
            "Dark Ritual",
            "Cabal Ritual",
            "Jeska's Will",
            "Simian Spirit Guide",
            "Elvish Spirit Guide",
        } or (cmc <= 1 and "add {" in oracle and "Artifact" in type_line):
            roles.add("fast_mana")

        # Ramp: mana dorks, rocks, land search
        if (
            "search your library for a" in oracle
            and ("land" in oracle or "forest" in oracle or "plains" in oracle)
            and "onto the battlefield" in oracle
        ) or (
            ("Artifact" in type_line or "Creature" in type_line)
            and cmc <= 3
            and ("{t}: add " in oracle or "add one mana of any" in oracle)
        ):
            roles.add("ramp")

    # Tutors (excluding basic land ramp)
    if (
        "search your library for a card" in oracle
        or "search your library for an artifact" in oracle
        or "search your library for a creature" in oracle
        or "search your library for an instant" in oracle
        or "search your library for an enchantment" in oracle
        or name in {"Demonic Tutor", "Vampiric Tutor", "Imperial Seal", "Mystical Tutor", "Enlightened Tutor", "Worldly Tutor", "Gamble", "Entomb", "Gifts Ungiven", "Intuition", "Survival of the Fittest", "Finale of Devastation", "Chord of Calling", "Eldritch Evolution", "Neoform", "Diabolic Intent"}
    ):
        roles.add("tutor")

    # Card draw / advantage
    if "draw a card" in oracle or "draw two cards" in oracle or "draw cards" in oracle or "draw x cards" in oracle:
        roles.add("card_draw")

    # Interaction (counterspells & targeted removal)
    if (
        "counter target" in oracle
        or "destroy target" in oracle
        or "exile target" in oracle
        or "deals " in oracle and "damage to any target" in oracle
    ):
        roles.add("interaction")

    # Free interaction
    if name in {
        "Fierce Guardianship",
        "Force of Will",
        "Force of Negation",
        "Pact of Negation",
        "Deflecting Swat",
        "Deadly Rollick",
        "Mental Misstep",
        "Mindbreak Trap",
        "Subtlety",
        "Solitude",
        "Grief",
        "Endurance",
        "Fury",
    } or ("rather than pay this spell's mana cost" in oracle and "counter target" in oracle):
        roles.add("free_interaction")

    return roles
