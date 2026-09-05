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
    ) -> Dict[str, Any]:
        """
        Executes dual-tier upgrade evaluation enriched with EDHREC synergy,
        theme alignment, anti-salt filtering, and combo analysis.
        Returns:
        {
            "owned_swaps": list[dict],
            "shopping_list": {
                "budget": list[dict],      # < $3.00
                "moderate": list[dict],    # $3.00 - $15.00
                "high_impact": list[dict]  # > $15.00
            },
            "synergy_brackets": {
                "signature": list[dict],    # > 50% synergy
                "high_synergy": list[dict], # 25% - 50% synergy
                "standard": list[dict]      # < 25% synergy / staples
            },
            "combos": {
                "active": list[dict],
                "near": list[dict]
            },
            "all_shopping_cards": list[dict],
            "deck_color_identity": list[str],
            "owned_count": int,
            "shopping_count": int,
            "theme_applied": str | None,
            "anti_salt_applied": bool,
        }
        """
        # 1. Resolve deck attributes
        cards = deck.get_parsed_cards() if hasattr(deck, "get_parsed_cards") else (deck.get("cards") or [])
        deck_id = deck.id if hasattr(deck, "id") else deck.get("id")
        deck_name = deck.deck_name if hasattr(deck, "deck_name") else deck.get("deck_name", "Commander Deck")
        color_identity = set(deck.get_color_identity_list() if hasattr(deck, "get_color_identity_list") else deck.get("color_identity", []))

        # Extract EDHREC metadata & synergy maps if present
        edhrec_synergies: Dict[str, Dict[str, Any]] = (edhrec_data or {}).get("card_synergies", {})
        top_salt_map: Dict[str, float] = (edhrec_data or {}).get("top_salt_map", {})
        edhrec_combos: List[Dict[str, Any]] = (edhrec_data or {}).get("combos", [])

        # Build card lookup for current deck (all match keys: lowercase, unaccented, front-face)
        deck_cards_set: Set[str] = set()
        for c in cards:
            c_name = c.get("name", "").strip()
            if c_name:
                deck_cards_set.update(get_card_match_keys(c_name))

        # Extract cut candidates from current deck
        cut_candidates = self._identify_cut_candidates(cards, ai_analysis)

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

                if self._is_color_legal(card_in, color_identity, u.get("color_identity")) and self._is_format_legal(card_in):
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
                            "rationale": u.get("rationale") or f"Upgrade into {primary_copy.name} from your binder for enhanced synergy and curve efficiency.",
                            "is_owned": True,
                            "total_owned": total_owned,
                            "available_copies": avail,
                            "already_allocated": (avail <= 0 and total_owned > 0),
                            "allocated_in": [d["deck_name"] for d in alloc_info.get("decks", []) if not d.get("is_current")],
                        })
                        applied_card_in_names.add(card_in.lower())

        # B) Check EDHREC High Synergy & Top Cards against User Inventory
        edhrec_priority_pool = (edhrec_data or {}).get("high_synergy_cards", []) + (edhrec_data or {}).get("top_cards", [])
        for rec in edhrec_priority_pool:
            rec_name = rec.get("name", "").strip()
            rec_lower = rec_name.lower()
            if not rec_name or self._is_card_in_deck(rec_name, deck_cards_set) or rec_lower in applied_card_in_names:
                continue

            if not self._is_color_legal(rec_name, color_identity) or not self._is_format_legal(rec_name):
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
                    "rationale": f"High EDHREC synergy (+{syn_pct}% in this commander) owned in your binder. Replace {matched_cut['name'] if isinstance(matched_cut, dict) else matched_cut}.",
                    "is_owned": True,
                    "total_owned": total_owned,
                    "available_copies": avail,
                    "already_allocated": (avail <= 0 and total_owned > 0),
                    "allocated_in": [d["deck_name"] for d in alloc_info.get("decks", []) if not d.get("is_current")],
                })
                applied_card_in_names.add(rec_lower)

        # C) Check Curated Tactical Staples against Inventory
        for staple in CURATED_UPGRADES:
            s_name = staple["name"]
            s_name_lower = s_name.lower()
            if self._is_card_in_deck(s_name, deck_cards_set) or s_name_lower in applied_card_in_names:
                continue

            # Color and legality check
            if not self._is_staple_color_legal(staple.get("colors", []), color_identity):
                continue
            if not self._is_format_legal(s_name):
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
                    "rationale": f"Replace {matched_cut['name'] if isinstance(matched_cut, dict) else matched_cut} with {primary_copy.name} from your binder: {staple.get('rationale')}",
                    "is_owned": True,
                    "total_owned": total_owned,
                    "available_copies": avail,
                    "already_allocated": (avail <= 0 and total_owned > 0),
                    "allocated_in": [d["deck_name"] for d in alloc_info.get("decks", []) if not d.get("is_current")],
                })
                applied_card_in_names.add(s_name_lower)

        # D) Prioritize Owned Swaps by EDHREC Synergy % descending, then availability
        owned_swaps.sort(
            key=lambda x: (
                x.get("synergy") or 0.0,
                1 if not x.get("already_allocated") else 0,
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

                if self._is_color_legal(card_in, color_identity, u.get("color_identity")) and self._is_format_legal(card_in):
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

            if not self._is_color_legal(rec_name, color_identity) or not self._is_format_legal(rec_name):
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
        for staple in CURATED_UPGRADES:
            s_name = staple["name"]
            s_name_lower = s_name.lower()
            if (self._is_card_in_deck(s_name, deck_cards_set) or 
                self._find_owned_inventory_copies(s_name, owned_by_name) or 
                s_name_lower in shopping_names_applied):
                continue

            if not self._is_staple_color_legal(staple.get("colors", []), color_identity):
                continue
            if not self._is_format_legal(s_name):
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

        # Batch resolve prices and Scryfall metadata for unowned cards if missing
        missing_meta_names = [s["name"] for s in shopping_list_raw if s.get("price_usd") is None or not s.get("image_uri")]
        if missing_meta_names:
            try:
                scryfall_meta, _ = self.scryfall_provider.get_cards_collection(missing_meta_names)
                for s in shopping_list_raw:
                    meta = scryfall_meta.get(s["name"].lower(), {})
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
            except Exception as e:
                logger.error(f"Error resolving prices for shopping list: {e}")

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
        }

    def evaluate_combos(
        self,
        combos: List[Dict[str, Any]],
        deck_cards: List[Dict[str, Any]],
        user_inventory: List[UserInventoryCard],
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
    def _is_format_legal(card_name: str) -> bool:
        """Checks if card is legal in Commander (not on banned list)."""
        if not card_name:
            return True
        clean = strip_accents(card_name).strip().lower()
        if " // " in clean:
            clean = clean.split(" // ")[0].strip()
        return clean not in COMMANDER_BANNED_CARDS

    @staticmethod
    def _is_color_legal(card_name: str, deck_colors: Set[str], card_cid: Optional[List[str]] = None) -> bool:
        """Ensures a card's color identity is completely contained within deck's color identity."""
        if not deck_colors:
            # Colorless commander: card must have 0 colors
            if card_cid:
                return len([c for c in card_cid if c in ("W", "U", "B", "R", "G")]) == 0
            return True

        if card_cid is not None:
            return all(c in deck_colors for c in card_cid if c in ("W", "U", "B", "R", "G"))
        return True

    @staticmethod
    def _is_staple_color_legal(staple_colors: List[str], deck_colors: Set[str]) -> bool:
        """Verifies staple colored mana pips match deck's commander colors."""
        if not staple_colors:
            # Colorless card is legal in any deck
            return True
        return all(c in deck_colors for c in staple_colors)

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

            # Basic lands shouldn't be primary nonland cut candidates unless mana base swap
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

        # Sort so weakest / lowest rated / highest CMC cards are first
        candidates.sort(key=lambda x: (not x["is_ai_cut"], x["rating"], -x["cmc"]))
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
        if "land" in target_lower or "mana base" in target_lower:
            for c in cut_candidates:
                if _available(c) and "land" in (c.get("type_line") or "").lower():
                    used_cuts.add(c.get("name", "").strip().lower())
                    return c
        elif "ramp" in target_lower or "rock" in target_lower:
            for c in cut_candidates:
                if _available(c) and c.get("cmc", 0) >= 3 and ("artifact" in (c.get("type_line") or "").lower() or c["rating"] <= 6.0):
                    used_cuts.add(c.get("name", "").strip().lower())
                    return c
        elif "removal" in target_lower or "interaction" in target_lower:
            for c in cut_candidates:
                if _available(c) and c.get("cmc", 0) >= 3 and c["rating"] <= 6.5:
                    used_cuts.add(c.get("name", "").strip().lower())
                    return c

        # Next check any candidate not yet assigned
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
