"""
Dual-Tier Deck Upgrade Engine for Chimaera MTG.
Cross-references Commander color identity, archetype tags, mana curves, and synergy targets
against user's collection inventory and market acquisitions.
Generates two distinct upgrade categories:
1. 'In Your Binder' (Zero Cost Owned Swaps with 1-click Apply Swap)
2. 'Buy / Wishlist' (Market Acquisitions organized by budget brackets with ManaBox export)
"""

import io
import csv
import logging
import re
from typing import Dict, Any, List, Optional, Set, Tuple
from card_classifier import MTGCardClassifier
from models import UserInventoryCard, DeckAnalysis
from providers.scryfall import ScryfallProvider
from card_utils import get_card_match_keys, strip_accents, fix_mojibake

logger = logging.getLogger(__name__)

# Official Commander (EDH) Banned Cards List
COMMANDER_BANNED_CARDS: Set[str] = {
    "ancestral recall", "balance", "biorhythm", "black lotus", "braids, cabal minion",
    "channel", "chaos orb", "coalition victory", "dockside extortionist", "emrakul, the aeons torn",
    "erayo, soratami ascendant", "falling star", "fastbond", "flash", "gifts ungiven",
    "golos, tireless pilgrim", "griselbrand", "hullbreacher", "iona, shield of emeria",
    "jeweled lotus", "karakas", "leovold, emissary of trest", "library of alexandria",
    "limited resources", "lutri, the spellchaser", "mana crypt", "mox emerald",
    "mox jet", "mox pearl", "mox ruby", "mox sapphire", "nadu, winged wisdom",
    "panoptic mirror", "primeval titan", "prophet of kruphix", "recurring nightmare",
    "rofeellos, llanowar emissary", "shahrazad", "sundering titan", "sway of the stars",
    "sylvan primordial", "time vault", "time walk", "tinker", "tolarian academy",
    "trade secrets", "upheaval", "yawgmoth's bargain",
}

# Official Pauper Commander (PDH) Banned Cards List (PDH Home Base)
PAUPER_COMMANDER_BANNED_CARDS: Set[str] = {
    "mystic remora", "rhystic study", "stone-throwing devils", "pradesh gypsies",
}

# Curated tactical Commander upgrade staples catalog
CURATED_UPGRADES: List[Dict[str, Any]] = [
    # Top-tier Universal Interaction / Removal
    {
        "name": "Swords to Plowshares", "role": "Spot Removal", "cmc": 1, "colors": ["W"],
        "category": "Targeted Removal", "rating": 9.8,
        "rationale": "Premier 1-CMC unconditional instant-speed creature exile."
    },
    {
        "name": "Path to Exile", "role": "Spot Removal", "cmc": 1, "colors": ["W"],
        "category": "Targeted Removal", "rating": 9.2,
        "rationale": "Efficient 1-CMC instant-speed exile removal."
    },
    {
        "name": "Generous Gift", "role": "Spot Removal", "cmc": 3, "colors": ["W"],
        "category": "Targeted Removal", "rating": 9.0,
        "rationale": "Instant-speed destroy any permanent flexibility."
    },
    {
        "name": "Counterspell", "role": "Protection / Counterspell", "cmc": 2, "colors": ["U"],
        "category": "Interaction", "rating": 9.2,
        "rationale": "Unconditional 2-CMC hard counter at instant speed."
    },
    {
        "name": "Swan Song", "role": "Protection / Counterspell", "cmc": 1, "colors": ["U"],
        "category": "Interaction", "rating": 9.4,
        "rationale": "Elite 1-CMC protection countering instants, sorceries, and enchantments."
    },
    {
        "name": "Cyclonic Rift", "role": "Board Wipe", "cmc": 2, "colors": ["U"],
        "category": "Board Wipe", "rating": 9.9,
        "rationale": "One-sided instant-speed board bounce that frequently closes out games."
    },
    {
        "name": "Pongify", "role": "Spot Removal", "cmc": 1, "colors": ["U"],
        "category": "Targeted Removal", "rating": 8.8,
        "rationale": "High-velocity 1-CMC creature removal in Blue."
    },
    {
        "name": "Infernal Grasp", "role": "Spot Removal", "cmc": 2, "colors": ["B"],
        "category": "Targeted Removal", "rating": 9.1,
        "rationale": "Unconditional 2-CMC instant creature destruction with negligible life loss."
    },
    {
        "name": "Deadly Rollick", "role": "Spot Removal", "cmc": 4, "colors": ["B"],
        "category": "Targeted Removal", "rating": 9.7,
        "rationale": "Free instant-speed exile removal when your Commander is in play."
    },
    {
        "name": "Toxic Deluge", "role": "Board Wipe", "cmc": 3, "colors": ["B"],
        "category": "Board Wipe", "rating": 9.8,
        "rationale": "Unbeatable 3-CMC board wipe bypassing indestructible and hexproof."
    },
    {
        "name": "Chaos Warp", "role": "Spot Removal", "cmc": 3, "colors": ["R"],
        "category": "Targeted Removal", "rating": 9.2,
        "rationale": "Unconditional catch-all instant-speed removal hitting any permanent."
    },
    {
        "name": "Blasphemous Act", "role": "Board Wipe", "cmc": 9, "colors": ["R"],
        "category": "Board Wipe", "rating": 9.5,
        "rationale": "Near-guaranteed 1-CMC board wipe dealing 13 damage to all creatures."
    },
    {
        "name": "Beast Within", "role": "Spot Removal", "cmc": 3, "colors": ["G"],
        "category": "Targeted Removal", "rating": 9.5,
        "rationale": "Premier green instant-speed catch-all destroying any permanent."
    },
    {
        "name": "Nature's Claim", "role": "Spot Removal", "cmc": 1, "colors": ["G"],
        "category": "Targeted Removal", "rating": 9.0,
        "rationale": "High-efficiency 1-CMC instant destroying artifact or enchantment."
    },
    {
        "name": "Heroic Intervention", "role": "Protection / Counterspell", "cmc": 2, "colors": ["G"],
        "category": "Protection", "rating": 9.4,
        "rationale": "Complete 2-CMC board protection granting hexproof and indestructible."
    },
    {
        "name": "Anguished Unmaking", "role": "Spot Removal", "cmc": 3, "colors": ["W", "B"],
        "category": "Targeted Removal", "rating": 9.3,
        "rationale": "Instant-speed nonland permanent exile."
    },
    {
        "name": "Assassin's Trophy", "role": "Spot Removal", "cmc": 2, "colors": ["B", "G"],
        "category": "Targeted Removal", "rating": 9.4,
        "rationale": "2-CMC unconditional permanent destruction at instant speed."
    },

    # Fast Mana & Elite Acceleration
    {
        "name": "Sol Ring", "role": "Ramp", "cmc": 1, "colors": [],
        "category": "Fast Ramp", "rating": 10.0,
        "rationale": "The defining staple of Commander; accelerates mana curve by +2 immediately."
    },
    {
        "name": "Arcane Signet", "role": "Ramp", "cmc": 2, "colors": [],
        "category": "Mana Rock", "rating": 9.8,
        "rationale": "Optimal 2-CMC artifact producing any color of your commander."
    },
    {
        "name": "Fellwar Stone", "role": "Ramp", "cmc": 2, "colors": [],
        "category": "Mana Rock", "rating": 9.0,
        "rationale": "Reliable 2-CMC rock producing colored mana in multiplayer pods."
    },
    {
        "name": "Talisman of Progress", "role": "Ramp", "cmc": 2, "colors": ["W", "U"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Dominance", "role": "Ramp", "cmc": 2, "colors": ["U", "B"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Indulgence", "role": "Ramp", "cmc": 2, "colors": ["B", "R"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Impulse", "role": "Ramp", "cmc": 2, "colors": ["R", "G"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Unity", "role": "Ramp", "cmc": 2, "colors": ["W", "G"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Hierarchy", "role": "Ramp", "cmc": 2, "colors": ["W", "B"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Creativity", "role": "Ramp", "cmc": 2, "colors": ["U", "R"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Resilience", "role": "Ramp", "cmc": 2, "colors": ["B", "G"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Conviction", "role": "Ramp", "cmc": 2, "colors": ["W", "R"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Talisman of Curiosity", "role": "Ramp", "cmc": 2, "colors": ["U", "G"],
        "category": "Mana Rock", "rating": 9.2,
        "rationale": "Untapped 2-CMC dual colored mana rock."
    },
    {
        "name": "Birds of Paradise", "role": "Ramp", "cmc": 1, "colors": ["G"],
        "category": "Fast Ramp", "rating": 9.5,
        "rationale": "1-CMC dork fixing all 5 colors with flying."
    },
    {
        "name": "Three Visits", "role": "Ramp", "cmc": 2, "colors": ["G"],
        "category": "Ramp", "rating": 9.3,
        "rationale": "2-CMC ramp fetching untapped dual/triome forest lands."
    },
    {
        "name": "Nature's Lore", "role": "Ramp", "cmc": 2, "colors": ["G"],
        "category": "Ramp", "rating": 9.3,
        "rationale": "2-CMC ramp fetching untapped dual/triome forest lands."
    },
    {
        "name": "Farseek", "role": "Ramp", "cmc": 2, "colors": ["G"],
        "category": "Ramp", "rating": 9.1,
        "rationale": "2-CMC ramp fetching shocklands and dual typed lands."
    },

    # Premium Lands
    {
        "name": "Command Tower", "role": "Land", "cmc": 0, "colors": [],
        "category": "Mana Base", "rating": 10.0,
        "rationale": "Untapped land producing all commander colors with zero downside."
    },
    {
        "name": "Exotic Orchard", "role": "Land", "cmc": 0, "colors": [],
        "category": "Mana Base", "rating": 9.0,
        "rationale": "Untapped multi-color fixing based on opponents' lands."
    },
    {
        "name": "Mana Confluence", "role": "Land", "cmc": 0, "colors": [],
        "category": "Mana Base", "rating": 9.3,
        "rationale": "Untapped unconditional 5-color land fixing."
    },
    {
        "name": "City of Brass", "role": "Land", "cmc": 0, "colors": [],
        "category": "Mana Base", "rating": 9.3,
        "rationale": "Untapped unconditional 5-color land fixing."
    },

    # Card Advantage & Tutors
    {
        "name": "Rhystic Study", "role": "Card Advantage", "cmc": 3, "colors": ["U"],
        "category": "Card Draw", "rating": 9.9,
        "rationale": "Unmatched continuous card draw engine taxing opponent spells."
    },
    {
        "name": "Mystic Remora", "role": "Card Advantage", "cmc": 1, "colors": ["U"],
        "category": "Card Draw", "rating": 9.6,
        "rationale": "1-CMC explosive early-game card draw punishing noncreature spells."
    },
    {
        "name": "Esper Sentinel", "role": "Card Advantage", "cmc": 1, "colors": ["W"],
        "category": "Card Draw", "rating": 9.7,
        "rationale": "1-CMC creature draw engine taxing opponents on noncreature casts."
    },
    {
        "name": "Black Market Connections", "role": "Card Advantage", "cmc": 3, "colors": ["B"],
        "category": "Card Advantage", "rating": 9.4,
        "rationale": "Repeatable card draw, treasure ramp, and shapeshifter tokens every turn."
    },
    {
        "name": "Phyrexian Arena", "role": "Card Advantage", "cmc": 3, "colors": ["B"],
        "category": "Card Draw", "rating": 8.5,
        "rationale": "Guaranteed extra card draw every upkeep for 1 life."
    },
    {
        "name": "Sylvan Library", "role": "Card Advantage", "cmc": 2, "colors": ["G"],
        "category": "Card Draw", "rating": 9.5,
        "rationale": "Draw up to 2 additional cards per turn or curate topdecks for free."
    },
    {
        "name": "Demonic Tutor", "role": "Tutor", "cmc": 2, "colors": ["B"],
        "category": "Tutor", "rating": 10.0,
        "rationale": "2-CMC unconditional search for any card in library directly to hand."
    },
    {
        "name": "Vampiric Tutor", "role": "Tutor", "cmc": 1, "colors": ["B"],
        "category": "Tutor", "rating": 9.8,
        "rationale": "1-CMC instant-speed topdeck tutor for any card."
    },
    {
        "name": "Worldly Tutor", "role": "Tutor", "cmc": 1, "colors": ["G"],
        "category": "Tutor", "rating": 9.1,
        "rationale": "1-CMC instant-speed tutor for key combo or win-condition creatures."
    },
    {
        "name": "Enlightened Tutor", "role": "Tutor", "cmc": 1, "colors": ["W"],
        "category": "Tutor", "rating": 9.3,
        "rationale": "1-CMC instant-speed search for game-winning artifact or enchantment."
    },
    {
        "name": "Mystical Tutor", "role": "Tutor", "cmc": 1, "colors": ["U"],
        "category": "Tutor", "rating": 9.2,
        "rationale": "1-CMC instant-speed search for board wipes, protection, or extra turns."
    },
    {
        "name": "Skullclamp", "role": "Card Advantage", "cmc": 1, "colors": [],
        "category": "Card Draw", "rating": 9.7,
        "rationale": "Insane draw engine turning tokens and small utility creatures into +2 cards."
    },
]

# Curated tactical Pauper Commander (PDH) upgrade staples catalog (all printed at common)
CURATED_PAUPER_UPGRADES: List[Dict[str, Any]] = [
    # White Staples
    {"name": "Ephemerate", "role": "Protection / Flicker", "cmc": 1, "colors": ["W"], "category": "Synergy", "rating": 9.7, "rationale": "Premier 1-CMC instant-speed blink with Rebound for double ETB value."},
    {"name": "Thraben Inspector", "role": "Card Advantage", "cmc": 1, "colors": ["W"], "category": "Card Draw", "rating": 9.1, "rationale": "1-CMC creature generating an investigate Clue token for smooth early velocity."},
    {"name": "Spirited Companion", "role": "Card Advantage", "cmc": 2, "colors": ["W"], "category": "Card Draw", "rating": 9.0, "rationale": "2-CMC cantripping creature providing immediate card replacement on ETB."},
    {"name": "Journey to Nowhere", "role": "Spot Removal", "cmc": 2, "colors": ["W"], "category": "Targeted Removal", "rating": 9.2, "rationale": "Clean 2-CMC unconditional creature exile enchantment."},
    {"name": "Oblivion Ring", "role": "Spot Removal", "cmc": 3, "colors": ["W"], "category": "Targeted Removal", "rating": 9.0, "rationale": "Catch-all 3-CMC exile answer hitting any nonland permanent."},
    {"name": "Prismatic Strands", "role": "Protection / Counterspell", "cmc": 3, "colors": ["W"], "category": "Protection", "rating": 9.5, "rationale": "Damage prevention combat trick with free flashback tapping an untapped white creature."},
    {"name": "Dawn Charm", "role": "Protection / Counterspell", "cmc": 2, "colors": ["W"], "category": "Protection", "rating": 9.1, "rationale": "Modal 2-CMC protection: prevent combat damage, regenerate, or counter spell targeting you."},
    {"name": "Late to Dinner", "role": "Recursion", "cmc": 4, "colors": ["W"], "category": "Synergy", "rating": 8.9, "rationale": "Unconditional creature reanimation from graveyard that creates a Food token."},

    # Blue Staples
    {"name": "Counterspell", "role": "Protection / Counterspell", "cmc": 2, "colors": ["U"], "category": "Interaction", "rating": 9.9, "rationale": "Unconditional 2-CMC hard counter at instant speed."},
    {"name": "Brainstorm", "role": "Card Advantage", "cmc": 1, "colors": ["U"], "category": "Card Draw", "rating": 9.5, "rationale": "Instant 1-CMC draw 3 cards with library sculpting."},
    {"name": "Ponder", "role": "Card Advantage", "cmc": 1, "colors": ["U"], "category": "Card Draw", "rating": 9.6, "rationale": "Premier 1-CMC cantrip with shuffle option and scry 3 depth."},
    {"name": "Preordain", "role": "Card Advantage", "cmc": 1, "colors": ["U"], "category": "Card Draw", "rating": 9.6, "rationale": "Top tier 1-CMC card selection with Scry 2."},
    {"name": "Mulldrifter", "role": "Card Advantage", "cmc": 5, "colors": ["U"], "category": "Card Draw", "rating": 9.7, "rationale": "Iconic evoke/draw 2 engine easily looped with blink and graveyard recursion."},
    {"name": "Snap", "role": "Spot Removal", "cmc": 2, "colors": ["U"], "category": "Targeted Removal", "rating": 9.3, "rationale": "Free instant-speed bounce that untaps 2 lands."},
    {"name": "Frantic Search", "role": "Card Advantage", "cmc": 3, "colors": ["U"], "category": "Card Draw", "rating": 9.4, "rationale": "Free loot spell sculpting hand while untapping 3 lands."},
    {"name": "Peregrine Drake", "role": "Ramp / Combo", "cmc": 5, "colors": ["U"], "category": "Finisher", "rating": 9.6, "rationale": "Untaps 5 lands on ETB, serving as a huge tempo play or infinite mana combo engine."},
    {"name": "Murmuring Mystic", "role": "Finisher / Win-Con", "cmc": 4, "colors": ["U"], "category": "Synergy", "rating": 9.3, "rationale": "Spellslinger engine flooding the board with 1/1 flying Bird illusion tokens."},

    # Black Staples
    {"name": "Cast Down", "role": "Spot Removal", "cmc": 2, "colors": ["B"], "category": "Targeted Removal", "rating": 9.6, "rationale": "Premier 2-CMC unconditional instant removal hitting any nonlegendary creature."},
    {"name": "Snuff Out", "role": "Spot Removal", "cmc": 4, "colors": ["B"], "category": "Targeted Removal", "rating": 9.8, "rationale": "Free instant-speed creature removal castable by paying 4 life."},
    {"name": "Deadly Dispute", "role": "Card Advantage", "cmc": 2, "colors": ["B"], "category": "Card Draw", "rating": 9.7, "rationale": "Top tier 2-CMC instant drawing 2 cards and ramping with a Treasure token."},
    {"name": "Village Rites", "role": "Card Advantage", "cmc": 1, "colors": ["B"], "category": "Card Draw", "rating": 9.3, "rationale": "Ultra-efficient 1-CMC instant turning tokens or chump blockers into 2 cards."},
    {"name": "Night's Whisper", "role": "Card Advantage", "cmc": 2, "colors": ["B"], "category": "Card Draw", "rating": 9.4, "rationale": "Unconditional 2-CMC draw 2 cards at sorcery speed for 2 life."},
    {"name": "Sign in Blood", "role": "Card Advantage", "cmc": 2, "colors": ["B"], "category": "Card Draw", "rating": 9.2, "rationale": "Consistent 2-CMC draw 2 with burn versatility against opponents."},
    {"name": "Defile", "role": "Spot Removal", "cmc": 1, "colors": ["B"], "category": "Targeted Removal", "rating": 9.1, "rationale": "1-CMC instant removal scaling with Swamp count."},
    {"name": "Crypt Rats", "role": "Board Wipe", "cmc": 3, "colors": ["B"], "category": "Board Wipe", "rating": 9.5, "rationale": "Repeatable Pestilence board sweeper on a creature body."},
    {"name": "Gray Merchant of Asphodel", "role": "Finisher / Win-Con", "cmc": 5, "colors": ["B"], "category": "Finisher", "rating": 9.7, "rationale": "Massive life drain finisher scaling with black devotion."},

    # Red Staples
    {"name": "Lightning Bolt", "role": "Spot Removal", "cmc": 1, "colors": ["R"], "category": "Targeted Removal", "rating": 9.7, "rationale": "Gold standard 1-CMC 3 damage instant removal."},
    {"name": "Abrade", "role": "Spot Removal", "cmc": 2, "colors": ["R"], "category": "Targeted Removal", "rating": 9.5, "rationale": "Modal flexibility dealing 3 damage to a creature or destroying an artifact."},
    {"name": "Faithless Looting", "role": "Card Advantage", "cmc": 1, "colors": ["R"], "category": "Card Draw", "rating": 9.4, "rationale": "High-velocity hand sculpting and graveyard filling with flashback."},
    {"name": "Thrill of Possibility", "role": "Card Advantage", "cmc": 2, "colors": ["R"], "category": "Card Draw", "rating": 8.9, "rationale": "Instant-speed card cycling and graveyard stocking."},
    {"name": "Cast into the Fire", "role": "Spot Removal", "cmc": 2, "colors": ["R"], "category": "Targeted Removal", "rating": 9.3, "rationale": "Modal exile for 1-toughness creatures or problematic artifacts."},
    {"name": "Pyroblast", "role": "Protection / Counterspell", "cmc": 1, "colors": ["R"], "category": "Interaction", "rating": 9.5, "rationale": "Premier 1-CMC red counterspell and removal against blue spells and permanents."},
    {"name": "Red Elemental Blast", "role": "Protection / Counterspell", "cmc": 1, "colors": ["R"], "category": "Interaction", "rating": 9.5, "rationale": "Crucial 1-CMC interaction countering blue spells and destroying blue permanents."},
    {"name": "Guttersnipe", "role": "Finisher / Win-Con", "cmc": 3, "colors": ["R"], "category": "Finisher", "rating": 9.3, "rationale": "Premier spellslinger win condition burning every opponent for 2 damage per instant/sorcery."},

    # Green Staples
    {"name": "Llanowar Elves", "role": "Ramp", "cmc": 1, "colors": ["G"], "category": "Ramp", "rating": 9.5, "rationale": "Essential 1-CMC mana dork powering out early turn 2 plays."},
    {"name": "Elvish Mystic", "role": "Ramp", "cmc": 1, "colors": ["G"], "category": "Ramp", "rating": 9.4, "rationale": "Core 1-CMC mana acceleration for green mana curves."},
    {"name": "Fyndhorn Elves", "role": "Ramp", "cmc": 1, "colors": ["G"], "category": "Ramp", "rating": 9.4, "rationale": "Redundant 1-CMC mana acceleration."},
    {"name": "Sakura-Tribe Elder", "role": "Ramp", "cmc": 2, "colors": ["G"], "category": "Ramp", "rating": 9.6, "rationale": "Rampant Growth on a chump-blocking body."},
    {"name": "Rampant Growth", "role": "Ramp", "cmc": 2, "colors": ["G"], "category": "Ramp", "rating": 9.3, "rationale": "2-CMC basic land ramp directly to the battlefield."},
    {"name": "Cultivate", "role": "Ramp", "cmc": 3, "colors": ["G"], "category": "Ramp", "rating": 9.5, "rationale": "Premier 3-CMC land ramp fixing colors and smoothing curve."},
    {"name": "Kodama's Reach", "role": "Ramp", "cmc": 3, "colors": ["G"], "category": "Ramp", "rating": 9.5, "rationale": "Essential 3-CMC double land search and color fixing."},
    {"name": "Nature's Claim", "role": "Spot Removal", "cmc": 1, "colors": ["G"], "category": "Targeted Removal", "rating": 9.4, "rationale": "Ultra-efficient 1-CMC instant artifact/enchantment destruction."},
    {"name": "Return to Nature", "role": "Spot Removal", "cmc": 2, "colors": ["G"], "category": "Targeted Removal", "rating": 9.1, "rationale": "Modal Disenchant with bonus instant graveyard exile."},

    # Colorless / Artifacts
    {"name": "Arcane Signet", "role": "Ramp", "cmc": 2, "colors": [], "category": "Ramp", "rating": 9.8, "rationale": "Untapped mana rock producing all commander colors."},
    {"name": "Mind Stone", "role": "Ramp", "cmc": 2, "colors": [], "category": "Ramp", "rating": 9.3, "rationale": "2-CMC ramp rock that cycles into a fresh card in the late game."},
    {"name": "Commander's Sphere", "role": "Ramp", "cmc": 3, "colors": [], "category": "Ramp", "rating": 9.0, "rationale": "3-CMC any-color mana rock with free emergency card draw."},
    {"name": "Wayfarer's Bauble", "role": "Ramp", "cmc": 1, "colors": [], "category": "Ramp", "rating": 9.2, "rationale": "Land ramp for non-green decks directly to the battlefield."},
    {"name": "Ashnod's Altar", "role": "Ramp / Sac Outlet", "cmc": 3, "colors": [], "category": "Ramp", "rating": 9.6, "rationale": "Sacrifice outlet producing 2 colorless mana per creature."},
    {"name": "Bonder's Ornament", "role": "Ramp / Card Advantage", "cmc": 3, "colors": [], "category": "Ramp", "rating": 9.2, "rationale": "Mana rock offering repeatable multiplayer card advantage."},
    {"name": "Pristine Talisman", "role": "Ramp", "cmc": 3, "colors": [], "category": "Ramp", "rating": 8.9, "rationale": "Taps for mana while gaining 1 life every turn."},
    {"name": "Thought Vessel", "role": "Ramp", "cmc": 2, "colors": [], "category": "Ramp", "rating": 9.2, "rationale": "2-CMC mana rock granting unlimited hand size."},

    # Lands
    {"name": "Command Tower", "role": "Land", "cmc": 0, "colors": [], "category": "Mana Base", "rating": 9.9, "rationale": "Enters untapped and produces all commander identity colors."},
    {"name": "Path of Ancestry", "role": "Land", "cmc": 0, "colors": [], "category": "Mana Base", "rating": 9.3, "rationale": "Any-color mana land offering scry on typal creature casts."},
    {"name": "Ash Barrens", "role": "Land", "cmc": 0, "colors": [], "category": "Mana Base", "rating": 9.4, "rationale": "1-mana instant basic landcycling and fixing."},
    {"name": "Evolving Wilds", "role": "Land", "cmc": 0, "colors": [], "category": "Mana Base", "rating": 9.1, "rationale": "Color-fixing land finding any basic needed."},
    {"name": "Terramorphic Expanse", "role": "Land", "cmc": 0, "colors": [], "category": "Mana Base", "rating": 9.1, "rationale": "Essential color fixing for budget and pauper mana bases."},
    {"name": "Guildless Commons", "role": "Land", "cmc": 0, "colors": [], "category": "Mana Base", "rating": 9.0, "rationale": "Colorless bounce land tapping for {C}{C}."},
]


class DualTierUpgradeEngine:
    """
    Evaluates deck composition and user inventory to produce:
    1. Owned Swaps ('In Your Binder'): Cards owned by the user, zero cost, legal, in-color.
    2. Shopping List ('To Buy'): Market recommendations by budget bracket (<$3, $3-$15, >$15).
    """

    def __init__(self, scryfall_provider: Optional[ScryfallProvider] = None):
        self.scryfall_provider = scryfall_provider or ScryfallProvider()
        self.classifier = MTGCardClassifier()

    def generate_upgrades(
        self,
        deck: Any,
        user_inventory: List[UserInventoryCard],
        allocations: Dict[str, Dict[str, Any]],
        ai_analysis: Optional[Dict[str, Any]] = None,
        edhrec_data: Optional[Dict[str, Any]] = None,
        theme: Optional[str] = None,
        anti_salt: bool = False,
        max_salt: float = 1.5,
        is_pauper: Optional[bool] = None,
        deck_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes dual-tier upgrade evaluation enriched with EDHREC synergy,
        theme alignment, anti-salt filtering, strategic deck deficit alignment,
        inventory binder deep scan, and combo analysis.
        """
        # 1. Resolve deck attributes
        cards = deck.get_parsed_cards() if hasattr(deck, "get_parsed_cards") else (deck.get("cards") or [])
        deck_id = deck.id if hasattr(deck, "id") else deck.get("id")
        deck_name = deck.deck_name if hasattr(deck, "deck_name") else deck.get("deck_name", "Commander Deck")
        color_identity = self._resolve_deck_color_identity(deck, cards)

        # Resolve deck strategic engine and deficits
        deck_strategy = self._extract_deck_strategy_and_deficits(cards, ai_analysis=ai_analysis, deck_stats=deck_stats)

        # Resolve whether this deck is evaluated as Pauper Commander
        if is_pauper is None:
            is_pauper = bool(
                getattr(deck, "is_pauper_commander", False)
                or getattr(deck, "is_pauper", False)
                or (isinstance(deck, dict) and (deck.get("is_pauper") or deck.get("deck_format") == "pauper_commander"))
            )

        # Extract EDHREC metadata & synergy maps if present
        edhrec_synergies: Dict[str, Dict[str, Any]] = (edhrec_data or {}).get("card_synergies", {})
        top_salt_map: Dict[str, float] = (edhrec_data or {}).get("top_salt_map", {})
        edhrec_combos: List[Dict[str, Any]] = (edhrec_data or {}).get("combos", [])
        edhrec_priority_pool = (edhrec_data or {}).get("high_synergy_cards", []) + (edhrec_data or {}).get("top_cards", [])

        # Build card lookup for current deck (all match keys: lowercase, unaccented, front-face)
        deck_cards_set: Set[str] = set()
        for c in cards:
            c_name = c.get("name", "").strip()
            if c_name:
                deck_cards_set.update(get_card_match_keys(c_name))

        # Extract cut candidates from current deck
        cut_candidates = self._identify_cut_candidates(cards, ai_analysis)

        # Build Color Identity cache (cid_cache) & Pauper Commander legality cache
        cid_cache: Dict[str, List[str]] = {}
        pauper_legal_cache: Dict[str, bool] = {}

        # 1. Curated staples
        for staple in CURATED_UPGRADES + CURATED_PAUPER_UPGRADES:
            s_colors = staple.get("colors", [])
            for k in get_card_match_keys(staple["name"]):
                cid_cache[k] = s_colors
                if is_pauper and staple in CURATED_PAUPER_UPGRADES:
                    pauper_legal_cache[k] = True

        # 2. Basic lands
        basic_land_cids = {
            "plains": ["W"], "snow-covered plains": ["W"],
            "island": ["U"], "snow-covered island": ["U"],
            "swamp": ["B"], "snow-covered swamp": ["B"],
            "mountain": ["R"], "snow-covered mountain": ["R"],
            "forest": ["G"], "snow-covered forest": ["G"],
            "wastes": [],
        }
        for b_name, b_cid in basic_land_cids.items():
            for k in get_card_match_keys(b_name):
                cid_cache[k] = b_cid
                if is_pauper:
                    pauper_legal_cache[k] = True

        # 3. Known Commander / Pauper banned cards
        if is_pauper:
            for banned in PAUPER_COMMANDER_BANNED_CARDS.union(COMMANDER_BANNED_CARDS):
                for k in get_card_match_keys(banned):
                    pauper_legal_cache[k] = False

        # 4. Cards in current deck
        for c in cards:
            c_name = c.get("name", "").strip()
            c_cid = c.get("color_identity")
            if c_name and c_cid is not None:
                for k in get_card_match_keys(c_name):
                    cid_cache[k] = c_cid

        # 5. User inventory cards
        for ic in user_inventory:
            cid_list = None
            if hasattr(ic, "color_identity") and ic.color_identity is not None:
                if isinstance(ic.color_identity, list):
                    cid_list = ic.color_identity
                elif isinstance(ic.color_identity, str):
                    cid_list = [c.strip() for c in ic.color_identity.split(",") if c.strip()]
            elif hasattr(ic, "get_color_identity_list") and getattr(ic, "color_identity", None) is not None:
                cid_list = ic.get_color_identity_list()

            if cid_list is not None:
                for k in get_card_match_keys(ic.name):
                    cid_cache[k] = cid_list

            if is_pauper:
                ic_rarity = (getattr(ic, "rarity", "") or "").lower()
                if ic_rarity == "common":
                    clean = strip_accents(ic.name).strip().lower()
                    if clean not in PAUPER_COMMANDER_BANNED_CARDS and clean not in COMMANDER_BANNED_CARDS:
                        for k in get_card_match_keys(ic.name):
                            if k not in pauper_legal_cache:
                                pauper_legal_cache[k] = True

        # 6. Collect candidates needing Scryfall resolution
        candidates_to_validate: Set[str] = set()
        if ai_analysis and "upgrades" in ai_analysis and isinstance(ai_analysis["upgrades"], list):
            for u in ai_analysis["upgrades"]:
                c_in = u.get("card_in", "").strip()
                if c_in:
                    ai_cid = u.get("color_identity")
                    if ai_cid is not None:
                        for k in get_card_match_keys(c_in):
                            cid_cache[k] = ai_cid
                    needs_pauper = is_pauper and not any(k in pauper_legal_cache for k in get_card_match_keys(c_in))
                    needs_cid = not any(k in cid_cache for k in get_card_match_keys(c_in))
                    if needs_pauper or needs_cid:
                        candidates_to_validate.add(c_in)

        for rec in edhrec_priority_pool:
            r_name = rec.get("name", "").strip()
            if r_name:
                needs_pauper = is_pauper and not any(k in pauper_legal_cache for k in get_card_match_keys(r_name))
                needs_cid = not any(k in cid_cache for k in get_card_match_keys(r_name))
                if needs_pauper or needs_cid:
                    candidates_to_validate.add(r_name)

        for combo in edhrec_combos:
            for piece in combo.get("pieces", []):
                p_name = piece.strip() if isinstance(piece, str) else ""
                if p_name:
                    needs_pauper = is_pauper and not any(k in pauper_legal_cache for k in get_card_match_keys(p_name))
                    needs_cid = not any(k in cid_cache for k in get_card_match_keys(p_name))
                    if needs_pauper or needs_cid:
                        candidates_to_validate.add(p_name)

        # Only add inventory cards to ad-hoc Scryfall validation if they match EDHREC synergies
        # for this commander, preventing synchronous collection-wide API hammering.
        for ic in user_inventory:
            clean = strip_accents(ic.name).strip().lower()
            clean_front = clean.split(" // ")[0].strip() if " // " in clean else clean
            if clean in edhrec_synergies or clean_front in edhrec_synergies:
                needs_pauper = is_pauper and not any(k in pauper_legal_cache for k in get_card_match_keys(ic.name))
                needs_cid = not any(k in cid_cache for k in get_card_match_keys(ic.name))
                if needs_pauper or needs_cid:
                    candidates_to_validate.add(ic.name)

        if candidates_to_validate:
            # Strictly cap at 75 cards (exactly 1 Scryfall batch) to guarantee <300ms execution
            candidates_list = list(candidates_to_validate)[:75]
            try:
                scryfall_meta, _ = self.scryfall_provider.get_cards_collection(candidates_list)
                for name_query in candidates_list:
                    q_low = name_query.lower().strip()
                    meta = scryfall_meta.get(q_low)
                    if not meta:
                        clean_q = strip_accents(name_query).strip().lower()
                        meta = scryfall_meta.get(clean_q)
                    if meta:
                        scry_cid = meta.get("color_identity", [])
                        for k in get_card_match_keys(meta.get("name", name_query)):
                            cid_cache[k] = scry_cid
                            if is_pauper:
                                pauper_legal_cache[k] = ScryfallProvider.is_pauper_legal(meta)

                        # In-memory enrichment for matched inventory cards
                        for ic in user_inventory:
                            if ic.name.lower() == q_low or strip_accents(ic.name).lower() == q_low:
                                if not getattr(ic, "color_identity", None) and scry_cid:
                                    ic.color_identity = ",".join(scry_cid)
                                if not getattr(ic, "type_line", None) and meta.get("type_line"):
                                    ic.type_line = meta.get("type_line")
                                if not getattr(ic, "mana_cost", None) and meta.get("mana_cost"):
                                    ic.mana_cost = meta.get("mana_cost")
                                if (getattr(ic, "cmc", None) is None or ic.cmc == 0) and meta.get("cmc") is not None:
                                    ic.cmc = float(meta["cmc"])
                                if not getattr(ic, "image_uri", None) and (meta.get("image_uri") or meta.get("small_image_uri")):
                                    ic.image_uri = meta.get("image_uri") or meta.get("small_image_uri")
                    else:
                        if is_pauper:
                            pauper_legal_cache[q_low] = False
            except Exception as e:
                logger.error(f"Error validating card metadata with Scryfall: {e}")

        # Choose curated staples pool based on format
        staples_pool = CURATED_PAUPER_UPGRADES if is_pauper else CURATED_UPGRADES

        # 2. Build Inventory Map & Owned Upgrades (indexed by all match keys)
        owned_by_name: Dict[str, List[UserInventoryCard]] = {}
        for ic in user_inventory:
            for k in get_card_match_keys(ic.name):
                owned_by_name.setdefault(k, []).append(ic)

        owned_swaps: List[Dict[str, Any]] = []
        applied_card_in_names: Set[str] = set()
        assigned_cuts: Set[str] = set()

        def _get_edhrec_info(card_name: str) -> Tuple[float, float, float, Optional[float]]:
            c_low = card_name.lower().strip()
            syn_info = edhrec_synergies.get(c_low)
            if not syn_info and " // " in c_low:
                syn_info = edhrec_synergies.get(c_low.split(" // ")[0].strip())
            syn = syn_info.get("synergy", 0.0) if syn_info else 0.0
            syn_pct = syn_info.get("synergy_percent", 0.0) if syn_info else round(syn * 100.0, 1)
            inc_pct = syn_info.get("inclusion_percent", 0.0) if syn_info else 0.0
            salt = top_salt_map.get(c_low)
            if salt is None and " // " in c_low:
                salt = top_salt_map.get(c_low.split(" // ")[0].strip())
            return syn, syn_pct, inc_pct, salt

        # A) Check AI suggested upgrades first if present
        if ai_analysis and "upgrades" in ai_analysis and isinstance(ai_analysis["upgrades"], list):
            for u in ai_analysis["upgrades"]:
                card_in = u.get("card_in", "").strip()
                card_out = u.get("card_out", "").strip()
                if not card_in or self._is_card_in_deck(card_in, deck_cards_set) or card_in.lower() in applied_card_in_names:
                    continue

                if self._is_color_legal(card_in, color_identity, u.get("color_identity"), cid_cache=cid_cache, mana_cost=u.get("card_in_mana")) and self._is_format_legal(card_in, is_pauper=is_pauper, pauper_legal_cache=pauper_legal_cache):
                    owned_copies = self._find_owned_inventory_copies(card_in, owned_by_name)
                    if owned_copies:
                        primary_copy = owned_copies[0]
                        alloc_key = card_in.lower()
                        if alloc_key not in allocations and " // " in alloc_key:
                            alloc_key = alloc_key.split(" // ")[0].strip()
                        alloc_info = allocations.get(alloc_key, {"total_allocated": 0, "other_allocated": 0, "decks": []})

                        total_owned = sum(c.quantity for c in owned_copies)
                        other_allocated = alloc_info.get("other_allocated", 0)
                        avail = max(0, total_owned - other_allocated)

                        if card_out and self._is_card_in_deck(card_out, deck_cards_set) and card_out.lower() not in assigned_cuts:
                            matched_cut = card_out
                            assigned_cuts.add(card_out.lower())
                        else:
                            matched_cut = self._find_best_cut(cut_candidates, u.get("category", "General"), used_cuts=assigned_cuts)

                        syn, syn_pct, inc_pct, salt = _get_edhrec_info(primary_copy.name)

                        owned_swaps.append({
                            "card_in": primary_copy.name,
                            "card_in_image": primary_copy.image_uri,
                            "card_in_mana": primary_copy.mana_cost or "",
                            "card_in_cmc": primary_copy.cmc or 0,
                            "card_in_type": primary_copy.type_line or "",
                            "card_in_set": primary_copy.set_code or "",
                            "card_in_foil": primary_copy.foil or "normal",
                            "card_in_condition": primary_copy.condition or "Near Mint",
                            "card_in_price": primary_copy.price_usd,
                            "card_out": matched_cut["name"] if isinstance(matched_cut, dict) else matched_cut,
                            "card_out_cmc": matched_cut.get("cmc") if isinstance(matched_cut, dict) else None,
                            "card_out_type": matched_cut.get("type_line") if isinstance(matched_cut, dict) else None,
                            "category": u.get("category", "Tactical Upgrade"),
                            "estimated_impact": u.get("estimated_impact", "High"),
                            "synergy": syn,
                            "synergy_percent": syn_pct,
                            "inclusion_percent": inc_pct,
                            "salt_score": salt,
                            "strategic_score": round(45.0 + (syn * 50.0), 1),
                            "rationale": u.get("rationale") or f"Upgrade into {primary_copy.name} from your binder for enhanced synergy and curve efficiency.",
                            "is_owned": True,
                            "total_owned": total_owned,
                            "available_copies": avail,
                            "already_allocated": (avail <= 0 and total_owned > 0),
                            "allocated_in": [d["deck_name"] for d in alloc_info.get("decks", []) if not d.get("is_current")],
                        })
                        applied_card_in_names.add(card_in.lower())

        # B) Check EDHREC High Synergy & Top Cards against User Inventory
        for rec in edhrec_priority_pool:
            rec_name = rec.get("name", "").strip()
            rec_lower = rec_name.lower()
            if not rec_name or self._is_card_in_deck(rec_name, deck_cards_set) or rec_lower in applied_card_in_names:
                continue

            if not self._is_color_legal(rec_name, color_identity, cid_cache=cid_cache) or not self._is_format_legal(rec_name, is_pauper=is_pauper, pauper_legal_cache=pauper_legal_cache):
                continue

            owned_copies = self._find_owned_inventory_copies(rec_name, owned_by_name)
            if owned_copies:
                primary_copy = owned_copies[0]
                alloc_key = rec_lower
                if alloc_key not in allocations and " // " in alloc_key:
                    alloc_key = alloc_key.split(" // ")[0].strip()
                alloc_info = allocations.get(alloc_key, {"total_allocated": 0, "other_allocated": 0, "decks": []})

                total_owned = sum(c.quantity for c in owned_copies)
                other_allocated = alloc_info.get("other_allocated", 0)
                avail = max(0, total_owned - other_allocated)

                matched_cut = self._find_best_cut(cut_candidates, primary_copy.type_line or "Synergy", used_cuts=assigned_cuts)
                syn, syn_pct, inc_pct, salt = _get_edhrec_info(primary_copy.name)

                owned_swaps.append({
                    "card_in": primary_copy.name,
                    "card_in_image": primary_copy.image_uri,
                    "card_in_mana": primary_copy.mana_cost or "",
                    "card_in_cmc": primary_copy.cmc or 0,
                    "card_in_type": primary_copy.type_line or "Card",
                    "card_in_set": primary_copy.set_code or "",
                    "card_in_foil": primary_copy.foil or "normal",
                    "card_in_condition": primary_copy.condition or "Near Mint",
                    "card_in_price": primary_copy.price_usd,
                    "card_out": matched_cut["name"] if isinstance(matched_cut, dict) else matched_cut,
                    "card_out_cmc": matched_cut.get("cmc") if isinstance(matched_cut, dict) else None,
                    "card_out_type": matched_cut.get("type_line") if isinstance(matched_cut, dict) else None,
                    "category": "Signature Synergy" if syn >= 0.50 else ("High Synergy" if syn >= 0.25 else "EDHREC Upgrade"),
                    "estimated_impact": "High" if syn >= 0.25 else "Medium",
                    "synergy": syn,
                    "synergy_percent": syn_pct,
                    "inclusion_percent": inc_pct,
                    "salt_score": salt,
                    "strategic_score": round(40.0 + (syn * 60.0), 1),
                    "rationale": f"High EDHREC synergy (+{syn_pct}% in this commander) owned in your binder. Replace {matched_cut['name'] if isinstance(matched_cut, dict) else matched_cut}.",
                    "is_owned": True,
                    "total_owned": total_owned,
                    "available_copies": avail,
                    "already_allocated": (avail <= 0 and total_owned > 0),
                    "allocated_in": [d["deck_name"] for d in alloc_info.get("decks", []) if not d.get("is_current")],
                })
                applied_card_in_names.add(rec_lower)

        # C) Check Curated Tactical Staples against Inventory
        for staple in staples_pool:
            s_name = staple["name"]
            s_name_lower = s_name.lower()
            if self._is_card_in_deck(s_name, deck_cards_set) or s_name_lower in applied_card_in_names:
                continue

            # Color and legality check
            if not self._is_staple_color_legal(staple.get("colors", []), color_identity):
                continue
            if not self._is_format_legal(s_name, is_pauper=is_pauper, pauper_legal_cache=pauper_legal_cache):
                continue

            owned_copies = self._find_owned_inventory_copies(s_name, owned_by_name)
            if owned_copies:
                primary_copy = owned_copies[0]
                alloc_key = s_name_lower
                if alloc_key not in allocations and " // " in alloc_key:
                    alloc_key = alloc_key.split(" // ")[0].strip()
                alloc_info = allocations.get(alloc_key, {"total_allocated": 0, "other_allocated": 0, "decks": []})

                total_owned = sum(c.quantity for c in owned_copies)
                other_allocated = alloc_info.get("other_allocated", 0)
                avail = max(0, total_owned - other_allocated)

                matched_cut = self._find_best_cut(cut_candidates, staple.get("role", "Utility"), used_cuts=assigned_cuts)
                syn, syn_pct, inc_pct, salt = _get_edhrec_info(primary_copy.name)

                owned_swaps.append({
                    "card_in": primary_copy.name,
                    "card_in_image": primary_copy.image_uri,
                    "card_in_mana": primary_copy.mana_cost or staple.get("cmc"),
                    "card_in_cmc": primary_copy.cmc if primary_copy.cmc is not None else staple.get("cmc", 0),
                    "card_in_type": primary_copy.type_line or staple.get("role", "Card"),
                    "card_in_set": primary_copy.set_code or "",
                    "card_in_foil": primary_copy.foil or "normal",
                    "card_in_condition": primary_copy.condition or "Near Mint",
                    "card_in_price": primary_copy.price_usd,
                    "card_out": matched_cut["name"] if isinstance(matched_cut, dict) else matched_cut,
                    "card_out_cmc": matched_cut.get("cmc") if isinstance(matched_cut, dict) else None,
                    "card_out_type": matched_cut.get("type_line") if isinstance(matched_cut, dict) else None,
                    "category": staple.get("category", "Power"),
                    "estimated_impact": "High",
                    "synergy": syn,
                    "synergy_percent": syn_pct,
                    "inclusion_percent": inc_pct,
                    "salt_score": salt,
                    "strategic_score": round(35.0 + float(staple.get("rating", 9.0)) * 2.0, 1),
                    "rationale": f"Replace {matched_cut['name'] if isinstance(matched_cut, dict) else matched_cut} with {primary_copy.name} from your binder: {staple.get('rationale')}",
                    "is_owned": True,
                    "total_owned": total_owned,
                    "available_copies": avail,
                    "already_allocated": (avail <= 0 and total_owned > 0),
                    "allocated_in": [d["deck_name"] for d in alloc_info.get("decks", []) if not d.get("is_current")],
                })
                applied_card_in_names.add(s_name_lower)

        # D) Deep Scan User's Entire Binder for High-Buff and Strategy Upgrades
        binder_recommendations = self._scan_binder_for_upgrades(
            user_inventory=user_inventory,
            deck_cards_set=deck_cards_set,
            color_identity=color_identity,
            cut_candidates=cut_candidates,
            assigned_cuts=assigned_cuts,
            allocations=allocations,
            applied_card_in_names=applied_card_in_names,
            edhrec_synergies=edhrec_synergies,
            top_salt_map=top_salt_map,
            deck_strategy=deck_strategy,
            is_pauper=is_pauper,
            pauper_legal_cache=pauper_legal_cache,
            cid_cache=cid_cache,
        )
        owned_swaps.extend(binder_recommendations)

        # Final pass: enforce strict color legality on owned swaps
        owned_swaps = [
            s for s in owned_swaps
            if self._is_color_legal(s["card_in"], color_identity, cid_cache=cid_cache, mana_cost=s.get("card_in_mana"))
        ]

        # E) Prioritize Owned Swaps:
        # 1. Available copies first (not allocated to other active decks)
        # 2. Highest strategic score descending
        # 3. Synergy descending, total owned descending
        owned_swaps.sort(
            key=lambda x: (
                0 if x.get("already_allocated") else 1,
                x.get("strategic_score", (x.get("synergy") or 0.0) * 100.0),
                x.get("synergy") or 0.0,
                x.get("total_owned") or 0,
            ),
            reverse=True,
        )

        # 3. Build Shopping List ("To Buy / Wishlist")
        shopping_list_raw: List[Dict[str, Any]] = []
        shopping_names_applied: Set[str] = set()

        # A) Add unowned AI upgrades
        if ai_analysis and "upgrades" in ai_analysis and isinstance(ai_analysis["upgrades"], list):
            for u in ai_analysis["upgrades"]:
                card_in = u.get("card_in", "").strip()
                if not card_in or self._is_card_in_deck(card_in, deck_cards_set) or self._find_owned_inventory_copies(card_in, owned_by_name) or card_in.lower() in shopping_names_applied:
                    continue

                if self._is_color_legal(card_in, color_identity, u.get("color_identity"), cid_cache=cid_cache, mana_cost=u.get("card_in_mana")) and self._is_format_legal(card_in, is_pauper=is_pauper, pauper_legal_cache=pauper_legal_cache):
                    matched_cut = u.get("card_out") or self._find_best_cut(cut_candidates, u.get("category", "General"), used_cuts=assigned_cuts)
                    price_val = None
                    try:
                        if u.get("card_in_price"):
                            price_val = float(u["card_in_price"])
                    except Exception:
                        pass

                    syn, syn_pct, inc_pct, salt = _get_edhrec_info(card_in)
                    if anti_salt and salt is not None and salt >= max_salt:
                        continue

                    shopping_list_raw.append({
                        "name": card_in,
                        "card_out": matched_cut["name"] if isinstance(matched_cut, dict) else matched_cut,
                        "category": u.get("category", "Tactical Upgrade"),
                        "estimated_impact": u.get("estimated_impact", "High"),
                        "synergy": syn,
                        "synergy_percent": syn_pct,
                        "inclusion_percent": inc_pct,
                        "salt_score": salt,
                        "is_salty": bool(salt is not None and salt >= max_salt),
                        "rationale": u.get("rationale") or f"Recommended upgrade to increase deck velocity and synergy.",
                        "price_usd": price_val,
                        "image_uri": u.get("card_in_image"),
                        "tcgplayer_url": u.get("card_in_tcg"),
                        "mana_cost": u.get("card_in_mana"),
                        "type_line": u.get("card_in_type"),
                        "is_owned": False,
                    })
                    shopping_names_applied.add(card_in.lower())

        # B) Add unowned EDHREC High Synergy & Top Cards
        for rec in edhrec_priority_pool:
            rec_name = rec.get("name", "").strip()
            rec_lower = rec_name.lower()
            if (not rec_name or 
                self._is_card_in_deck(rec_name, deck_cards_set) or 
                self._find_owned_inventory_copies(rec_name, owned_by_name) or 
                rec_lower in shopping_names_applied):
                continue

            if not self._is_color_legal(rec_name, color_identity, cid_cache=cid_cache) or not self._is_format_legal(rec_name, is_pauper=is_pauper, pauper_legal_cache=pauper_legal_cache):
                continue

            syn, syn_pct, inc_pct, salt = _get_edhrec_info(rec_name)
            if anti_salt and salt is not None and salt >= max_salt:
                continue

            matched_cut = self._find_best_cut(cut_candidates, "Synergy Card", used_cuts=assigned_cuts)
            cat = "Signature Card" if syn >= 0.50 else ("High Synergy" if syn >= 0.25 else "EDHREC Recommendation")

            shopping_list_raw.append({
                "name": rec_name,
                "card_out": matched_cut["name"] if isinstance(matched_cut, dict) else matched_cut,
                "category": cat,
                "estimated_impact": "High" if syn >= 0.25 else "Medium",
                "synergy": syn,
                "synergy_percent": syn_pct,
                "inclusion_percent": inc_pct,
                "salt_score": salt,
                "is_salty": bool(salt is not None and salt >= max_salt),
                "rationale": f"Key synergy recommendation for this commander (+{syn_pct}% synergy, {inc_pct}% deck inclusion).",
                "price_usd": None,
                "image_uri": None,
                "tcgplayer_url": None,
                "mana_cost": "",
                "type_line": "Card",
                "is_owned": False,
            })
            shopping_names_applied.add(rec_lower)

        # C) Add unowned Curated Staples
        for staple in staples_pool:
            s_name = staple["name"]
            s_name_lower = s_name.lower()
            if (self._is_card_in_deck(s_name, deck_cards_set) or 
                self._find_owned_inventory_copies(s_name, owned_by_name) or 
                s_name_lower in shopping_names_applied):
                continue

            if not self._is_staple_color_legal(staple.get("colors", []), color_identity):
                continue
            if not self._is_format_legal(s_name, is_pauper=is_pauper, pauper_legal_cache=pauper_legal_cache):
                continue

            syn, syn_pct, inc_pct, salt = _get_edhrec_info(s_name)
            if anti_salt and salt is not None and salt >= max_salt:
                continue

            matched_cut = self._find_best_cut(cut_candidates, staple.get("role", "Utility"), used_cuts=assigned_cuts)

            shopping_list_raw.append({
                "name": s_name,
                "card_out": matched_cut["name"] if isinstance(matched_cut, dict) else matched_cut,
                "category": staple.get("category", "Staple Upgrade"),
                "estimated_impact": "High" if staple.get("rating", 0) >= 9.2 else "Medium",
                "synergy": syn,
                "synergy_percent": syn_pct,
                "inclusion_percent": inc_pct,
                "salt_score": salt,
                "is_salty": bool(salt is not None and salt >= max_salt),
                "rationale": staple.get("rationale", ""),
                "price_usd": None,
                "image_uri": None,
                "tcgplayer_url": None,
                "mana_cost": f"{{{staple['cmc']}}}" if staple.get("cmc") is not None else "",
                "type_line": staple.get("role", "Card"),
                "is_owned": False,
            })
            shopping_names_applied.add(s_name_lower)

        # Batch resolve prices and Scryfall metadata for unowned cards if missing (capped at 1 batch of 75)
        missing_meta_names = [s["name"] for s in shopping_list_raw if s.get("price_usd") is None or not s.get("image_uri")]
        if missing_meta_names:
            try:
                scryfall_meta, _ = self.scryfall_provider.get_cards_collection(missing_meta_names[:75])
                for s in shopping_list_raw:
                    meta = scryfall_meta.get(s["name"].lower(), {})
                    if not meta:
                        clean_n = strip_accents(s["name"]).strip().lower()
                        meta = scryfall_meta.get(clean_n, {})
                    if meta:
                        if s.get("price_usd") is None and meta.get("prices", {}).get("usd"):
                            try:
                                s["price_usd"] = float(meta["prices"]["usd"])
                            except Exception:
                                pass
                        if not s.get("image_uri"):
                            s["image_uri"] = meta.get("image_uri") or meta.get("small_image_uri")
                        if not s.get("tcgplayer_url"):
                            s["tcgplayer_url"] = meta.get("tcgplayer_url")
                        if not s.get("mana_cost") and meta.get("mana_cost"):
                            s["mana_cost"] = meta["mana_cost"]
                        if (not s.get("type_line") or s.get("type_line") == "Card") and meta.get("type_line"):
                            s["type_line"] = meta["type_line"]
                        if meta.get("color_identity") is not None:
                            for k in get_card_match_keys(meta.get("name", s["name"])):
                                cid_cache[k] = meta["color_identity"]
            except Exception as e:
                logger.error(f"Error resolving prices for shopping list: {e}")

        # Final pass: enforce strict color legality on shopping list
        shopping_list_raw = [
            s for s in shopping_list_raw
            if self._is_color_legal(s["name"], color_identity, cid_cache=cid_cache, mana_cost=s.get("mana_cost"))
        ]

        # Segregate Shopping List into Budget Brackets (sorted by synergy descending, then price)
        budget_bracket: List[Dict[str, Any]] = []      # < $3.00
        moderate_bracket: List[Dict[str, Any]] = []    # $3.00 - $15.00
        high_impact_bracket: List[Dict[str, Any]] = [] # > $15.00

        for s in shopping_list_raw:
            price = s.get("price_usd")
            if price is None:
                moderate_bracket.append(s)
            elif price < 3.0:
                budget_bracket.append(s)
            elif price <= 15.0:
                moderate_bracket.append(s)
            else:
                high_impact_bracket.append(s)

        # Segregate Shopping List into Synergy Brackets
        signature_bracket = [s for s in shopping_list_raw if (s.get("synergy") or 0.0) >= 0.50]
        high_syn_bracket = [s for s in shopping_list_raw if 0.25 <= (s.get("synergy") or 0.0) < 0.50]
        standard_bracket = [s for s in shopping_list_raw if (s.get("synergy") or 0.0) < 0.25]

        # Evaluate Known Combos from EDHREC / Commander Spellbook
        combo_results = self.evaluate_combos(
            combos=edhrec_combos,
            deck_cards=cards,
            user_inventory=user_inventory,
            is_pauper=is_pauper,
            pauper_legal_cache=pauper_legal_cache,
        )

        return {
            "owned_swaps": owned_swaps,
            "shopping_list": {
                "budget": sorted(budget_bracket, key=lambda x: (-(x.get("synergy") or 0.0), x.get("price_usd") or 0)),
                "moderate": sorted(moderate_bracket, key=lambda x: (-(x.get("synergy") or 0.0), x.get("price_usd") or 0)),
                "high_impact": sorted(high_impact_bracket, key=lambda x: (-(x.get("synergy") or 0.0), -(x.get("price_usd") or 0))),
            },
            "synergy_brackets": {
                "signature": sorted(signature_bracket, key=lambda x: (-(x.get("synergy") or 0.0))),
                "high_synergy": sorted(high_syn_bracket, key=lambda x: (-(x.get("synergy") or 0.0))),
                "standard": sorted(standard_bracket, key=lambda x: (-(x.get("synergy") or 0.0))),
            },
            "combos": combo_results,
            "all_shopping_cards": sorted(shopping_list_raw, key=lambda x: (-(x.get("synergy") or 0.0), x.get("price_usd") or 0)),
            "deck_color_identity": sorted(list(color_identity)),
            "owned_count": len(owned_swaps),
            "shopping_count": len(shopping_list_raw),
            "theme_applied": theme,
            "anti_salt_applied": anti_salt,
            "is_pauper": is_pauper,
            "deck_format": "pauper_commander" if is_pauper else "commander",
            "deck_strategy": deck_strategy,
        }

    def evaluate_combos(
        self,
        combos: List[Dict[str, Any]],
        deck_cards: List[Dict[str, Any]],
        user_inventory: List[UserInventoryCard],
        is_pauper: bool = False,
        pauper_legal_cache: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Evaluates EDHREC / Commander Spellbook combos against active deck cards and user inventory.
        Categorizes combos into:
        - active: 100% of pieces present in active deck.
        - near: missing 1 or 2 pieces, flagging if missing piece is in user's binder!
        """
        if not combos:
            return {"active": [], "near": []}

        deck_cards_set: Set[str] = set()
        for c in deck_cards:
            name = c.get("name", "").strip().lower()
            if name:
                deck_cards_set.add(name)
                if " // " in name:
                    deck_cards_set.add(name.split(" // ")[0].strip().lower())

        owned_by_name: Dict[str, List[UserInventoryCard]] = {}
        for ic in user_inventory:
            k = ic.name.strip().lower()
            owned_by_name.setdefault(k, []).append(ic)
            if " // " in ic.name:
                owned_by_name.setdefault(ic.name.split(" // ")[0].strip().lower(), []).append(ic)

        active_combos: List[Dict[str, Any]] = []
        near_combos: List[Dict[str, Any]] = []

        for combo in combos:
            pieces = combo.get("pieces", [])
            if len(pieces) < 2:
                continue

            # In Pauper Commander, every combo piece must be pauper legal
            if is_pauper:
                if not all(self._is_format_legal(p, is_pauper=True, pauper_legal_cache=pauper_legal_cache) for p in pieces):
                    continue

            in_deck: List[str] = []
            missing: List[Dict[str, Any]] = []

            for p in pieces:
                if self._is_card_in_deck(p, deck_cards_set):
                    in_deck.append(p)
                else:
                    is_in_binder = bool(self._find_owned_inventory_copies(p, owned_by_name))
                    missing.append({
                        "name": p,
                        "in_binder": is_in_binder,
                    })

            combo_entry = {
                "name": combo.get("name", " + ".join(pieces)),
                "pieces": pieces,
                "url": combo.get("url", ""),
                "in_deck_pieces": in_deck,
                "missing_pieces": missing,
                "missing_count": len(missing),
            }

            if len(missing) == 0:
                active_combos.append(combo_entry)
            elif len(missing) in (1, 2) and len(in_deck) >= 1:
                near_combos.append(combo_entry)

        return {
            "active": active_combos,
            "near": near_combos,
        }


    @staticmethod
    def _is_card_in_deck(card_name: str, deck_cards_set: Set[str]) -> bool:
        """Checks if a card (or either face of a DFC, with or without accents) is already in the deck."""
        if not card_name:
            return False
        return bool(get_card_match_keys(card_name).intersection(deck_cards_set))

    @staticmethod
    def _find_owned_inventory_copies(card_name: str, owned_by_name: Dict[str, List[UserInventoryCard]]) -> Optional[List[UserInventoryCard]]:
        """Looks up owned inventory copies matching full name, DFC front face, or unaccented variant."""
        if not card_name:
            return None
        for k in get_card_match_keys(card_name):
            if k in owned_by_name:
                return owned_by_name[k]
        return None

    @staticmethod
    def _is_format_legal(card_name: str, is_pauper: bool = False, pauper_legal_cache: Optional[Dict[str, bool]] = None) -> bool:
        """Checks if card is legal in Commander (or Pauper Commander if is_pauper is True)."""
        if not card_name:
            return True
        clean = strip_accents(card_name).strip().lower()
        if " // " in clean:
            clean = clean.split(" // ")[0].strip()
        if clean in COMMANDER_BANNED_CARDS:
            return False
        if is_pauper:
            if clean in PAUPER_COMMANDER_BANNED_CARDS:
                return False
            if pauper_legal_cache is not None:
                for k in get_card_match_keys(card_name):
                    if k in pauper_legal_cache:
                        return pauper_legal_cache[k]
                return False
        return True

    @staticmethod
    def _resolve_deck_color_identity(deck: Any, cards: List[Dict[str, Any]]) -> Set[str]:
        """
        Authoritatively resolves the commander's color identity for this deck:
        1. Checks designated commander cards in cards list (section=='commander' or name matching commander_name)
        2. Unions partner commander color identities if present
        3. Falls back to deck.get_color_identity_list() or deck.color_identity
        """
        cmdr_names = []
        if hasattr(deck, "commander_name") and deck.commander_name:
            cmdr_names = [c.strip().lower() for c in deck.commander_name.split(",") if c.strip()]
        elif isinstance(deck, dict):
            c_val = deck.get("commander") or deck.get("commander_name")
            if isinstance(c_val, list):
                cmdr_names = [str(c).strip().lower() for c in c_val if str(c).strip()]
            elif isinstance(c_val, str) and c_val.strip():
                cmdr_names = [c.strip().lower() for c in c_val.split(",") if c.strip()]

        cmdr_colors: Set[str] = set()
        for c in cards:
            c_name = c.get("name", "").strip().lower()
            clean_front = c_name.split(" // ")[0].strip() if " // " in c_name else c_name
            is_cmdr_sec = (c.get("section") or "").lower() in ("commander", "command zone")
            is_cmdr_match = any(cn == c_name or cn == clean_front for cn in cmdr_names)
            if is_cmdr_sec or is_cmdr_match:
                c_cid = c.get("color_identity") or []
                for col in c_cid:
                    if col and str(col).upper() in ("W", "U", "B", "R", "G"):
                        cmdr_colors.add(str(col).upper())
                if c.get("mana_cost"):
                    for col in re.findall(r"[WUBRG]", re.sub(r"[^WUBRG/]", "", str(c["mana_cost"]).upper())):
                        cmdr_colors.add(col)

        if cmdr_colors:
            return cmdr_colors

        # Fallback 1: deck model or dict
        if hasattr(deck, "get_color_identity_list"):
            cid_list = deck.get_color_identity_list()
            if cid_list:
                return {c.upper() for c in cid_list if c and c.upper() in ("W", "U", "B", "R", "G")}
        if isinstance(deck, dict) and "color_identity" in deck:
            cid_val = deck["color_identity"]
            if isinstance(cid_val, str) and cid_val.strip():
                return {c.strip().upper() for c in cid_val.split(",") if c.strip().upper() in ("W", "U", "B", "R", "G")}
            if isinstance(cid_val, (list, set)) and len(cid_val) > 0:
                return {str(c).upper() for c in cid_val if str(c).upper() in ("W", "U", "B", "R", "G")}

        # Fallback 2: infer from basic lands or known colored cards in deck
        inferred_colors: Set[str] = set()
        basic_map = {
            "plains": "W", "snow-covered plains": "W",
            "island": "U", "snow-covered island": "U",
            "swamp": "B", "snow-covered swamp": "B",
            "mountain": "R", "snow-covered mountain": "R",
            "forest": "G", "snow-covered forest": "G",
        }
        for c in cards:
            c_name = c.get("name", "").strip().lower()
            if c_name in basic_map:
                inferred_colors.add(basic_map[c_name])
            for col in (c.get("color_identity") or []):
                if col and str(col).upper() in ("W", "U", "B", "R", "G"):
                    inferred_colors.add(str(col).upper())

        if inferred_colors:
            return inferred_colors

        return set()

    @classmethod
    def _is_color_legal(
        cls,
        card_name: str,
        deck_colors: Set[str],
        card_cid: Optional[List[str]] = None,
        cid_cache: Optional[Dict[str, List[str]]] = None,
        mana_cost: Optional[str] = None,
    ) -> bool:
        """
        Strictly ensures a card's color identity is completely contained within the deck's color identity.
        If card_cid is not provided, looks up in cid_cache, curated catalog, or mana_cost.
        If color identity cannot be confirmed, rejects for safety.
        """
        if not card_name:
            return False

        clean_deck_colors = {c.upper() for c in deck_colors if c and c.upper() in ("W", "U", "B", "R", "G")}

        # 5-color deck: every card in Magic is legal in Commander
        if clean_deck_colors == {"W", "U", "B", "R", "G"}:
            return True

        if card_cid is None and cid_cache:
            for k in get_card_match_keys(card_name):
                if k in cid_cache:
                    card_cid = cid_cache[k]
                    break

        if card_cid is None:
            clean_name = strip_accents(card_name).strip().lower()
            for s in CURATED_UPGRADES + CURATED_PAUPER_UPGRADES:
                if strip_accents(s["name"]).lower() == clean_name:
                    card_cid = s.get("colors", [])
                    break

        if card_cid is None and mana_cost:
            extracted = set(re.findall(r"[WUBRG]", re.sub(r"[^WUBRG/]", "", str(mana_cost).upper())))
            if extracted:
                card_cid = list(extracted)

        # If still None, reject for safety - never let unverified colors bypass
        if card_cid is None:
            return False

        pips = {c.upper() for c in card_cid if c and c.upper() in ("W", "U", "B", "R", "G")}

        if not deck_colors:
            # Colorless deck: card must have 0 colored mana symbols
            return len(pips) == 0

        return pips.issubset(clean_deck_colors)

    @staticmethod
    def _is_staple_color_legal(staple_colors: List[str], deck_colors: Set[str]) -> bool:
        """Verifies staple colored mana pips match deck's commander colors."""
        if not staple_colors:
            # Colorless card is legal in any deck
            return True
        if not deck_colors:
            # Colorless deck: cards with colored mana symbols are illegal
            return False
        clean_deck_colors = {c.upper() for c in deck_colors if c and c.upper() in ("W", "U", "B", "R", "G")}
        return all(c.upper() in clean_deck_colors for c in staple_colors)

    def _extract_deck_strategy_and_deficits(
        self,
        cards: List[Dict[str, Any]],
        ai_analysis: Optional[Dict[str, Any]] = None,
        deck_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyzes deck composition and telemetry to identify what the deck is doing
        (dominant engines, creature typal types, core synergies) and where its deficits lie.
        """
        engine_counts: Dict[str, int] = {
            "tokens": 0,
            "counters": 0,
            "sacrifice": 0,
            "graveyard": 0,
            "spellslinger": 0,
            "blink": 0,
        }
        subtype_counts: Dict[str, int] = {}
        total_draw = 0
        total_ramp = 0
        total_removal = 0
        total_wipes = 0
        has_poison = False
        nonland_count = 0
        nonland_cmc_sum = 0.0

        for c in cards:
            qty = int(c.get("quantity", 1))
            tl = (c.get("type_line") or "").lower()
            oracle = (c.get("oracle_text") or "").lower()
            cmc = float(c.get("cmc", 0.0))

            if "land" not in tl:
                nonland_count += qty
                nonland_cmc_sum += cmc * qty

            if any(k in oracle for k in ["poison counter", "toxic ", "infect", "deathtouch"]):
                has_poison = True

            classification = c.get("classification")
            if not classification:
                classification = self.classifier.classify(c)

            for en in classification.get("engine_enabler", []):
                if en in engine_counts:
                    engine_counts[en] += qty
            for po in classification.get("engine_payoff", []):
                if po in engine_counts:
                    engine_counts[po] += qty

            if "creature" in tl:
                for st in classification.get("creature_subtypes", []):
                    st_cap = st.capitalize()
                    subtype_counts[st_cap] = subtype_counts.get(st_cap, 0) + qty

            if classification.get("is_draw"):
                total_draw += qty
            if classification.get("is_ramp"):
                total_ramp += qty
            if classification.get("is_targeted_removal"):
                total_removal += qty
            if classification.get("is_board_wipe"):
                total_wipes += qty

        if deck_stats:
            total_draw = max(total_draw, deck_stats.get("total_draw_count", total_draw))
            total_ramp = max(total_ramp, deck_stats.get("total_ramp_count", total_ramp))
            total_removal = max(total_removal, deck_stats.get("targeted_removal_count", total_removal))
            total_wipes = max(total_wipes, deck_stats.get("board_wipe_count", total_wipes))

        dominant_engine = None
        max_engine_count = 0
        for eng, count in engine_counts.items():
            if count > max_engine_count and count >= 3:
                max_engine_count = count
                dominant_engine = eng

        primary_type = None
        is_typal = False
        for st, count in sorted(subtype_counts.items(), key=lambda x: x[1], reverse=True):
            if count >= 6 and st not in ["Human", "Warrior", "Soldier"]:
                primary_type = st
                is_typal = True
                break
            elif count >= 8:
                primary_type = st
                is_typal = True
                break

        amv = round(nonland_cmc_sum / nonland_count, 2) if nonland_count > 0 else 3.0
        if deck_stats and deck_stats.get("nonland_amv"):
            amv = float(deck_stats["nonland_amv"])

        engine_labels = {
            "tokens": "Token Swarm",
            "counters": "+1/+1 Counters",
            "sacrifice": "Aristocrats / Sacrifice",
            "graveyard": "Graveyard Recursion",
            "spellslinger": "Spellslinger",
            "blink": "Blink / Flicker",
        }

        return {
            "dominant_engine": dominant_engine,
            "engine_label": engine_labels.get(dominant_engine, ""),
            "primary_type": primary_type,
            "is_typal": is_typal,
            "draw_deficit": total_draw < 8,
            "ramp_deficit": total_ramp < 8 or amv > 3.4,
            "removal_deficit": total_removal < 7,
            "wipe_deficit": total_wipes < 2,
            "has_poison": has_poison,
            "amv": amv,
            "archetype": (ai_analysis or {}).get("archetype") or (deck_stats or {}).get("archetype") or "Midrange",
        }

    def _scan_binder_for_upgrades(
        self,
        user_inventory: List[UserInventoryCard],
        deck_cards_set: Set[str],
        color_identity: Set[str],
        cut_candidates: List[Dict[str, Any]],
        assigned_cuts: Set[str],
        allocations: Dict[str, Dict[str, Any]],
        applied_card_in_names: Set[str],
        edhrec_synergies: Dict[str, Dict[str, Any]],
        top_salt_map: Dict[str, float],
        deck_strategy: Dict[str, Any],
        is_pauper: bool = False,
        pauper_legal_cache: Optional[Dict[str, bool]] = None,
        cid_cache: Optional[Dict[str, List[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scans the user's entire inventory to discover the strongest buff candidates matching
        the deck's color identity, strategy/engines, and deficit needs.
        """
        binder_swaps = []
        curated_names_set = {strip_accents(s["name"]).lower() for s in CURATED_UPGRADES + CURATED_PAUPER_UPGRADES}

        inventory_by_name: Dict[str, List[UserInventoryCard]] = {}
        for ic in user_inventory:
            clean = strip_accents(ic.name).strip().lower()
            inventory_by_name.setdefault(clean, []).append(ic)

        for clean_name, copies in inventory_by_name.items():
            if not clean_name or clean_name in applied_card_in_names:
                continue
            primary_copy = copies[0]
            card_name = primary_copy.name

            if self._is_card_in_deck(card_name, deck_cards_set):
                continue

            if not self._is_format_legal(card_name, is_pauper=is_pauper, pauper_legal_cache=pauper_legal_cache):
                continue

            ic_cid = None
            if hasattr(primary_copy, "color_identity") and primary_copy.color_identity is not None:
                if isinstance(primary_copy.color_identity, list):
                    ic_cid = primary_copy.color_identity
                elif isinstance(primary_copy.color_identity, str):
                    ic_cid = [c.strip() for c in primary_copy.color_identity.split(",") if c.strip()]
            elif hasattr(primary_copy, "get_color_identity_list") and getattr(primary_copy, "color_identity", None) is not None:
                ic_cid = primary_copy.get_color_identity_list()

            if not self._is_color_legal(card_name, color_identity, card_cid=ic_cid, cid_cache=cid_cache, mana_cost=primary_copy.mana_cost):
                continue

            type_line_lower = (primary_copy.type_line or "").lower()
            if "basic" in type_line_lower and "land" in type_line_lower:
                continue

            card_dict = {
                "name": card_name,
                "type_line": primary_copy.type_line or "",
                "oracle_text": primary_copy.oracle_text or "",
                "cmc": primary_copy.cmc or 0,
                "mana_cost": primary_copy.mana_cost or "",
            }
            classification = self.classifier.classify(card_dict)

            score = 0.0
            reasons = []
            category = "Binder Upgrade"

            # 1. EDHREC synergy and inclusion
            syn_info = edhrec_synergies.get(clean_name)
            if not syn_info and " // " in clean_name:
                syn_info = edhrec_synergies.get(clean_name.split(" // ")[0].strip())
            syn = syn_info.get("synergy", 0.0) if syn_info else 0.0
            syn_pct = syn_info.get("synergy_percent", 0.0) if syn_info else round(syn * 100.0, 1)
            inc_pct = syn_info.get("inclusion_percent", 0.0) if syn_info else 0.0
            salt = top_salt_map.get(clean_name)

            if syn_pct > 0:
                score += (syn_pct * 0.7)
                reasons.append(f"+{syn_pct}% EDHREC synergy")
            if inc_pct > 0:
                score += min(inc_pct * 0.3, 15.0)
                if inc_pct >= 20:
                    reasons.append(f"played in {inc_pct}% of decks")

            if syn >= 0.50:
                category = "Signature Synergy"
            elif syn >= 0.25:
                category = "High Synergy"

            # 2. Engine & Archetype alignment
            dominant_engine = deck_strategy.get("dominant_engine")
            if dominant_engine:
                card_enablers = classification.get("engine_enabler", [])
                card_payoffs = classification.get("engine_payoff", [])
                if dominant_engine in card_enablers or dominant_engine in card_payoffs:
                    score += 35.0
                    engine_label = deck_strategy.get("engine_label", dominant_engine.title())
                    reasons.append(f"synergizes with deck's {engine_label} engine")
                    if category == "Binder Upgrade":
                        category = f"Engine Synergy ({engine_label})"

            primary_type = deck_strategy.get("primary_type")
            if primary_type and deck_strategy.get("is_typal"):
                card_subtypes = classification.get("creature_subtypes", [])
                oracle_lower = (primary_copy.oracle_text or "").lower()
                if primary_type.lower() in [s.lower() for s in card_subtypes]:
                    score += 30.0
                    reasons.append(f"creature type {primary_type}")
                    if category == "Binder Upgrade":
                        category = f"Typal Buff ({primary_type})"
                elif re.search(rf"\b{re.escape(primary_type.lower())}\b", oracle_lower):
                    score += 25.0
                    reasons.append(f"kindred support for {primary_type}")
                    if category == "Binder Upgrade":
                        category = f"Typal Support ({primary_type})"

            if deck_strategy.get("has_poison") and any(k in (primary_copy.oracle_text or "").lower() for k in ["deathtouch", "toxic", "poison", "proliferate"]):
                score += 30.0
                reasons.append("triggers commander poison / counter win conditions")
                if category == "Binder Upgrade":
                    category = "Commander Synergy"

            # 3. Deficit filling
            if deck_strategy.get("draw_deficit") and (classification.get("is_draw") or classification.get("draw_type") == "engine"):
                score += 25.0
                reasons.append("fills deck's card draw deficit")
                if category == "Binder Upgrade":
                    category = "Card Advantage Engine"

            if deck_strategy.get("ramp_deficit") and classification.get("is_ramp") and (primary_copy.cmc or 0) <= 2:
                score += 22.0
                reasons.append("efficient low-cost ramp acceleration")
                if category == "Binder Upgrade":
                    category = "Fast Ramp"

            if deck_strategy.get("removal_deficit") and classification.get("is_targeted_removal"):
                score += 20.0
                reasons.append("efficient interaction to answer threats")
                if category == "Binder Upgrade":
                    category = "Targeted Removal"

            if deck_strategy.get("wipe_deficit") and classification.get("is_board_wipe"):
                score += 22.0
                reasons.append("board sweeper protection")
                if category == "Binder Upgrade":
                    category = "Board Wipe"

            if classification.get("wincon_tags"):
                score += 28.0
                reasons.append("high-impact game-ending finisher")
                if category == "Binder Upgrade":
                    category = "Finisher / Win-Con"

            if clean_name in curated_names_set:
                score += 20.0
                reasons.append("top-tier Commander staple")
                if category == "Binder Upgrade":
                    category = "Power Staple"

            price = primary_copy.price_usd or 0.0
            if price >= 15.0:
                score += 15.0
                reasons.append("high-impact card in collection")
            elif price >= 5.0:
                score += 8.0

            if score < 18.0 and syn < 0.20:
                continue

            alloc_key = clean_name
            if alloc_key not in allocations and " // " in alloc_key:
                alloc_key = alloc_key.split(" // ")[0].strip()
            alloc_info = allocations.get(alloc_key, {"total_allocated": 0, "other_allocated": 0, "decks": []})
            total_owned = sum(c.quantity for c in copies)
            other_allocated = alloc_info.get("other_allocated", 0)
            avail = max(0, total_owned - other_allocated)

            matched_cut = self._find_best_cut(cut_candidates, category, used_cuts=assigned_cuts)
            cut_name = matched_cut["name"] if isinstance(matched_cut, dict) else matched_cut

            rationale_text = f"Upgrade into {card_name} from your binder: {', '.join(reasons[:2])}. Replaces {cut_name}." if reasons else f"Recommended upgrade from your binder into {card_name}, replacing {cut_name}."

            impact = "High" if (score >= 35 or syn >= 0.25) else "Medium"

            binder_swaps.append({
                "card_in": card_name,
                "card_in_image": primary_copy.image_uri,
                "card_in_mana": primary_copy.mana_cost or "",
                "card_in_cmc": primary_copy.cmc or 0,
                "card_in_type": primary_copy.type_line or "Card",
                "card_in_set": primary_copy.set_code or "",
                "card_in_foil": primary_copy.foil or "normal",
                "card_in_condition": primary_copy.condition or "Near Mint",
                "card_in_price": primary_copy.price_usd,
                "card_out": cut_name,
                "card_out_cmc": matched_cut.get("cmc") if isinstance(matched_cut, dict) else None,
                "card_out_type": matched_cut.get("type_line") if isinstance(matched_cut, dict) else None,
                "category": category,
                "estimated_impact": impact,
                "synergy": syn,
                "synergy_percent": syn_pct,
                "inclusion_percent": inc_pct,
                "salt_score": salt,
                "strategic_score": round(score, 1),
                "rationale": rationale_text,
                "is_owned": True,
                "total_owned": total_owned,
                "available_copies": avail,
                "already_allocated": (avail <= 0 and total_owned > 0),
                "allocated_in": [d["deck_name"] for d in alloc_info.get("decks", []) if not d.get("is_current")],
            })
            applied_card_in_names.add(clean_name)

        return binder_swaps

    def _identify_cut_candidates(self, cards: List[Dict[str, Any]], ai_analysis: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Identifies the weakest slotted cards in the deck to suggest as cuts."""
        candidates = []
        ai_cuts = set()
        if ai_analysis and "cut_recommendations" in ai_analysis:
            for c in ai_analysis["cut_recommendations"]:
                c_name = c.get("card_name", "").strip().lower()
                if c_name:
                    ai_cuts.add(c_name)

        ai_ratings_map = {}
        if ai_analysis and "card_ratings" in ai_analysis:
            for r in ai_analysis["card_ratings"]:
                r_name = r.get("card_name", "").strip().lower()
                if r_name:
                    try:
                        ai_ratings_map[r_name] = float(r.get("rating", 7.0))
                    except Exception:
                        pass

        for c in cards:
            c_name = c.get("name", "").strip()
            if not c_name:
                continue
            section = (c.get("section") or "mainboard").lower()
            if section in ("commander", "command zone"):
                continue  # Never suggest cutting the commander

            name_lower = c_name.lower()
            type_line = (c.get("type_line") or "").lower()
            cmc = float(c.get("cmc", 0))

            is_basic = "basic" in type_line and "land" in type_line
            rating = ai_ratings_map.get(name_lower, 7.0)
            is_ai_cut = (name_lower in ai_cuts)

            candidates.append({
                "name": c_name,
                "cmc": cmc,
                "type_line": c.get("type_line", ""),
                "is_basic": is_basic,
                "rating": rating,
                "is_ai_cut": is_ai_cut,
            })

        # Sort so weakest / lowest rated non-basic cards are prioritized as cuts
        candidates.sort(key=lambda x: (not x["is_ai_cut"], x["is_basic"], x["rating"], -x["cmc"]))
        return candidates

    def _find_best_cut(
        self,
        cut_candidates: List[Dict[str, Any]],
        target_role_or_category: str,
        used_cuts: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """Finds matching card to cut based on role or picks lowest rated candidate not yet assigned."""
        if not cut_candidates:
            return {"name": "Suboptimal Slotted Card", "cmc": 3, "type_line": "Card"}

        if used_cuts is None:
            used_cuts = set()

        def _available(cand):
            return cand.get("name", "").strip().lower() not in used_cuts

        target_lower = (target_role_or_category or "").lower()

        # 1. Lands
        if "land" in target_lower or "mana base" in target_lower:
            for c in cut_candidates:
                if _available(c) and "land" in (c.get("type_line") or "").lower():
                    used_cuts.add(c.get("name", "").strip().lower())
                    return c

        # 2. Ramp / Rocks
        elif any(k in target_lower for k in ["ramp", "rock", "velocity"]):
            for c in cut_candidates:
                if _available(c) and not c.get("is_basic") and (
                    (c.get("cmc", 0) >= 3 and "artifact" in (c.get("type_line") or "").lower())
                    or c.get("rating", 7.0) <= 6.0
                ):
                    used_cuts.add(c.get("name", "").strip().lower())
                    return c

        # 3. Card Draw / Advantage
        elif any(k in target_lower for k in ["draw", "card advantage", "cantrip"]):
            for c in cut_candidates:
                if _available(c) and not c.get("is_basic") and (
                    c.get("rating", 7.0) <= 6.0 or (c.get("cmc", 0) >= 4 and "creature" in (c.get("type_line") or "").lower())
                ):
                    used_cuts.add(c.get("name", "").strip().lower())
                    return c

        # 4. Removal / Interaction
        elif any(k in target_lower for k in ["removal", "interaction", "counterspell", "protection"]):
            for c in cut_candidates:
                if _available(c) and not c.get("is_basic") and (
                    (c.get("cmc", 0) >= 3 and c.get("rating", 7.0) <= 6.5) or c.get("rating", 7.0) <= 5.5
                ):
                    used_cuts.add(c.get("name", "").strip().lower())
                    return c

        # 5. Finishers / High Impact
        elif any(k in target_lower for k in ["finisher", "win-con", "overrun"]):
            for c in cut_candidates:
                if _available(c) and not c.get("is_basic") and c.get("cmc", 0) >= 4 and c.get("rating", 7.0) <= 6.5:
                    used_cuts.add(c.get("name", "").strip().lower())
                    return c

        # Next check any non-basic candidate not yet assigned
        for c in cut_candidates:
            if _available(c) and not c.get("is_basic"):
                used_cuts.add(c.get("name", "").strip().lower())
                return c

        # If only basics left, check any candidate
        for c in cut_candidates:
            if _available(c):
                used_cuts.add(c.get("name", "").strip().lower())
                return c

        # Fallback if all cut candidates have been allocated at least once
        c = cut_candidates[0]
        used_cuts.add(c.get("name", "").strip().lower())
        return c

    @staticmethod
    def generate_manabox_wishlist_export(acquisitions: List[Dict[str, Any]], format_type: str = "csv") -> str:
        """
        Generates formatted ManaBox Wishlist export (CSV or plain text) for external purchases.
        """
        if format_type.lower() == "text":
            lines = []
            for item in acquisitions:
                lines.append(f"1 {item.get('name')}")
            return "\n".join(lines)

        # Standard ManaBox Import CSV format
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "Quantity", "Foil", "Condition", "Language", "Binder Name"])
        for item in acquisitions:
            writer.writerow([
                item.get("name"),
                1,
                "normal",
                "Near Mint",
                "en",
                "Wishlist",
            ])
        return output.getvalue()
