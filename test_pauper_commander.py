"""
Comprehensive unit test suite for Pauper Commander (PDH) support.
Tests:
1. DeckAnalysis database model serialization & format properties.
2. DeckParser format propagation & auto-detection.
3. DeckAnalyzer rules evaluation (uncommon commander, 100% common 99, banlist).
4. DualTierUpgradeEngine strictly filtering upgrades to commons only.
5. Flask API endpoints for format switching & rules updates.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

from app import create_app
from models import db, User, DeckAnalysis, UserInventoryCard
from deck_parser import DeckParser
from deck_analyzer import DeckAnalyzer, PAUPER_COMMANDER_BANNED_CARDS, COMMANDER_BANNED_CARDS
from deck_upgrade_engine import DualTierUpgradeEngine, CURATED_PAUPER_UPGRADES, CURATED_UPGRADES


class MockInventoryCard:
    def __init__(self, name, rarity="common", colors=None, quantity=1):
        self.name = name
        self.rarity = rarity
        self.color_identity = colors or []
        self.quantity = quantity
        self.regular_price = 1.00
        self.price_usd = 1.00
        self.image_uri = ""
        self.set_code = "pdh"
        self.collector_number = "1"
        self.cmc = 2
        self.mana_cost = "{1}{U}"
        self.type_line = "Instant"
        self.foil = ""
        self.price_usd_foil = None
        self.condition = "Near Mint"
        self.binder_name = "Binder 1"
        self.oracle_text = ""


class TestPauperDeckModel(unittest.TestCase):
    """Tests DeckAnalysis model fields, format properties, and to_dict serialization."""

    def setUp(self):
        self.app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_default_format_is_commander(self):
        deck = DeckAnalysis(
            deck_name="Standard Ur-Dragon",
            commander_name="The Ur-Dragon",
            raw_decklist="1 The Ur-Dragon\n1 Sol Ring",
        )
        db.session.add(deck)
        db.session.commit()

        self.assertFalse(deck.is_pauper)
        self.assertFalse(deck.is_pauper_commander)
        self.assertEqual(deck.deck_format, "commander")

        serialized = deck.to_dict()
        self.assertFalse(serialized["is_pauper"])
        self.assertEqual(serialized["deck_format"], "commander")
        self.assertIn("rule_evaluation", serialized)

    def test_pauper_commander_flag_and_property(self):
        deck = DeckAnalysis(
            deck_name="Murmuring Mystic Control",
            commander_name="Murmuring Mystic",
            raw_decklist="1 Murmuring Mystic\n1 Preordain",
            is_pauper=True,
            deck_format="pauper_commander",
        )
        db.session.add(deck)
        db.session.commit()

        self.assertTrue(deck.is_pauper)
        self.assertTrue(deck.is_pauper_commander)
        self.assertEqual(deck.deck_format, "pauper_commander")

        serialized = deck.to_dict()
        self.assertTrue(serialized["is_pauper"])
        self.assertEqual(serialized["deck_format"], "pauper_commander")


class TestPauperDeckParser(unittest.TestCase):
    """Tests format flag passing and auto-detection in DeckParser."""

    def test_parse_with_explicit_is_pauper(self):
        decklist = """
// Commander
1 Murmuring Mystic

// Creatures
1 Archaeomancer
1 Peregrine Drake

// Instants
1 Counterspell
1 Brainstorm
"""
        parsed = DeckParser.parse(decklist, source_type="text", is_pauper=True)
        self.assertTrue(parsed["is_pauper"])
        self.assertEqual(parsed["deck_format"], "pauper_commander")

    def test_parse_auto_detect_from_format_header(self):
        decklist = """
// Format: Pauper Commander
// Commander
1 Murmuring Mystic

1 Counterspell
1 Ponder
"""
        parsed = DeckParser.parse(decklist, source_type="text")
        self.assertTrue(parsed["is_pauper"])
        self.assertEqual(parsed["deck_format"], "pauper_commander")

    def test_parse_auto_detect_from_pdh_tag(self):
        decklist = """
// Tags: PDH, Spellslinger
// Commander
1 Murmuring Mystic

1 Counterspell
1 Preordain
"""
        parsed = DeckParser.parse(decklist, source_type="text")
        self.assertTrue(parsed["is_pauper"])
        self.assertEqual(parsed["deck_format"], "pauper_commander")


class TestPauperRulesEngine(unittest.TestCase):
    """Tests DeckAnalyzer.evaluate_deck_rules for both EDH and PDH formats."""

    def setUp(self):
        self.analyzer = DeckAnalyzer()

    def test_valid_pauper_commander_deck(self):
        cards = [
            {
                "name": "Murmuring Mystic",
                "section": "commander",
                "type_line": "Creature — Human Wizard",
                "rarity": "uncommon",
                "color_identity": ["U"],
                "quantity": 1,
            },
            {
                "name": "Counterspell",
                "section": "mainboard",
                "type_line": "Instant",
                "rarity": "common",
                "legalities": {"paupercommander": "legal"},
                "color_identity": ["U"],
                "quantity": 1,
            },
            {
                "name": "Brainstorm",
                "section": "mainboard",
                "type_line": "Instant",
                "rarity": "common",
                "legalities": {"paupercommander": "legal"},
                "color_identity": ["U"],
                "quantity": 1,
            },
            {
                "name": "Island",
                "section": "mainboard",
                "type_line": "Basic Land — Island",
                "rarity": "common",
                "color_identity": ["U"],
                "quantity": 97,
            },
        ]

        result = self.analyzer.evaluate_deck_rules(
            cards=cards,
            commander_names=["Murmuring Mystic"],
            is_pauper=True,
            total_cards=100,
            deck_color_identity=["U"],
        )

        self.assertTrue(result["is_pauper"])
        self.assertEqual(result["format"], "pauper_commander")
        self.assertTrue(result["is_legal"])
        self.assertEqual(result["violations_count"], 0)
        self.assertEqual(result["rarity_counts"]["uncommon"], 1)
        self.assertEqual(result["rarity_counts"]["common"], 99)

    def test_pauper_commander_with_rare_commander_fails(self):
        cards = [
            {
                "name": "The Ur-Dragon",
                "section": "commander",
                "type_line": "Legendary Creature — Dragon Avatar",
                "rarity": "mythic",
                "color_identity": ["W", "U", "B", "R", "G"],
                "quantity": 1,
            },
            {
                "name": "Forest",
                "section": "mainboard",
                "type_line": "Basic Land — Forest",
                "rarity": "common",
                "color_identity": ["G"],
                "quantity": 99,
            },
        ]

        result = self.analyzer.evaluate_deck_rules(
            cards=cards,
            commander_names=["The Ur-Dragon"],
            is_pauper=True,
            total_cards=100,
            deck_color_identity=["W", "U", "B", "R", "G"],
        )

        self.assertFalse(result["is_legal"])
        self.assertGreater(result["violations_count"], 0)
        violation_messages = [v["message"] for v in result["violations"]]
        self.assertTrue(any("must be an Uncommon creature" in m for m in violation_messages))

    def test_pauper_commander_with_rare_library_cards_fails(self):
        cards = [
            {
                "name": "Murmuring Mystic",
                "section": "commander",
                "type_line": "Creature — Human Wizard",
                "rarity": "uncommon",
                "color_identity": ["U"],
                "quantity": 1,
            },
            {
                "name": "Rhystic Study",
                "section": "mainboard",
                "type_line": "Enchantment",
                "rarity": "rare",
                "legalities": {"paupercommander": "banned"},
                "color_identity": ["U"],
                "quantity": 1,
            },
            {
                "name": "Cyclonic Rift",
                "section": "mainboard",
                "type_line": "Instant",
                "rarity": "rare",
                "legalities": {"paupercommander": "not_legal"},
                "color_identity": ["U"],
                "quantity": 1,
            },
            {
                "name": "Island",
                "section": "mainboard",
                "type_line": "Basic Land — Island",
                "rarity": "common",
                "color_identity": ["U"],
                "quantity": 97,
            },
        ]

        result = self.analyzer.evaluate_deck_rules(
            cards=cards,
            commander_names=["Murmuring Mystic"],
            is_pauper=True,
            total_cards=100,
            deck_color_identity=["U"],
        )

        self.assertFalse(result["is_legal"])
        self.assertEqual(result["violations_count"], 2)
        self.assertIn("Rhystic Study", result["illegal_cards"])
        self.assertIn("Cyclonic Rift", result["illegal_cards"])

    def test_pauper_commander_banned_card(self):
        cards = [
            {
                "name": "Murmuring Mystic",
                "section": "commander",
                "type_line": "Creature — Human Wizard",
                "rarity": "uncommon",
                "color_identity": ["U"],
                "quantity": 1,
            },
            {
                "name": "Mystic Remora",
                "section": "mainboard",
                "type_line": "Enchantment",
                "rarity": "common",
                "legalities": {"paupercommander": "banned"},
                "color_identity": ["U"],
                "quantity": 1,
            },
            {
                "name": "Island",
                "section": "mainboard",
                "type_line": "Basic Land — Island",
                "rarity": "common",
                "color_identity": ["U"],
                "quantity": 98,
            },
        ]

        result = self.analyzer.evaluate_deck_rules(
            cards=cards,
            commander_names=["Murmuring Mystic"],
            is_pauper=True,
            total_cards=100,
            deck_color_identity=["U"],
        )

        self.assertFalse(result["is_legal"])
        self.assertIn("Mystic Remora", result["illegal_cards"])
        self.assertTrue(any("banned in Pauper Commander" in v["message"] for v in result["violations"]))


class TestPauperUpgradeEngine(unittest.TestCase):
    """Tests DualTierUpgradeEngine strictly recommending only common cards when is_pauper=True."""

    def setUp(self):
        self.engine = DualTierUpgradeEngine()

    def test_curated_pauper_pool_has_only_commons(self):
        """Verify the curated catalog for PDH contains only common cards."""
        for entry in CURATED_PAUPER_UPGRADES:
            card_name = entry.get("name")
            rarity = entry.get("rarity", "common").lower()
            self.assertEqual(rarity, "common", f"Card '{card_name}' in CURATED_PAUPER_UPGRADES is not common ({rarity})")

    def test_pauper_upgrades_exclude_rare_inventory_cards(self):
        """When is_pauper=True, owned rare cards must NOT be recommended as swaps."""
        deck = {
            "cards": [
                {"name": "Murmuring Mystic", "section": "commander", "type_line": "Creature", "rarity": "uncommon", "cmc": 4},
                {"name": "Cancel", "section": "mainboard", "type_line": "Instant", "cmc": 3, "rarity": "common"},
                {"name": "Island", "section": "mainboard", "type_line": "Basic Land", "quantity": 98, "rarity": "common", "cmc": 0},
            ],
            "color_identity": ["U"],
            "is_pauper": True,
            "deck_format": "pauper_commander",
        }

        # Inventory has a Rare card (Mana Drain) and a Common card (Counterspell)
        user_inventory = [
            MockInventoryCard("Mana Drain", rarity="rare", colors=["U"], quantity=1),
            MockInventoryCard("Counterspell", rarity="common", colors=["U"], quantity=1),
        ]

        upgrades = self.engine.generate_upgrades(
            deck=deck,
            user_inventory=user_inventory,
            allocations={},
            is_pauper=True,
        )

        owned_swaps = upgrades.get("owned_swaps", [])
        recommended_in_names = [s["card_in"] for s in owned_swaps]

        # Mana Drain must NEVER be recommended
        self.assertNotIn("Mana Drain", recommended_in_names)

        # All recommended cards in shopping list must not be known non-commons
        for category, card_list in upgrades.get("shopping_list", {}).items():
            for item in card_list:
                name = item.get("name", "")
                self.assertNotIn(name, ["Mana Drain", "Rhystic Study", "Cyclonic Rift", "Fierce Guardianship", "Demonic Tutor"])

    def test_pauper_combos_exclude_non_pauper_cards(self):
        """When is_pauper=True, combos containing rares (e.g. Thassa's Oracle) are filtered out."""
        combos = [
            {
                "cards": ["Thassa's Oracle", "Demonic Consultation"],
                "results": ["Win the game"],
            },
            {
                "cards": ["Peregrine Drake", "Archaeomancer", "Ghostly Flicker"],
                "results": ["Infinite mana"],
            },
        ]

        pauper_cache = {
            "peregrine drake": True,
            "archaeomancer": True,
            "ghostly flicker": True,
            "thassa's oracle": False,
            "demonic consultation": False,
        }

        combo_results = self.engine.evaluate_combos(
            combos=combos,
            deck_cards=[{"name": "Peregrine Drake"}, {"name": "Ghostly Flicker"}],
            user_inventory=[],
            is_pauper=True,
            pauper_legal_cache=pauper_cache,
        )

        # Thassa's Oracle combo should be stripped
        all_combos = combo_results.get("active", []) + combo_results.get("near", [])
        combo_names = [" + ".join(c.get("cards", [])) for c in all_combos]
        self.assertFalse(any("Thassa's Oracle" in c for c in combo_names))


class TestPauperApiEndpoints(unittest.TestCase):
    """Tests Flask API format switching and rules evaluation endpoints."""

    def setUp(self):
        self.app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Create test user
        self.user = User(email="pdh@example.com", name="Pauper Player", is_admin=False)
        db.session.add(self.user)
        db.session.commit()

        # Login
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_toggle_deck_format_endpoint(self):
        deck = DeckAnalysis(
            user_id=self.user.id,
            deck_name="Test Deck",
            commander_name="Murmuring Mystic",
            raw_decklist="// Commander\n1 Murmuring Mystic\n\n// Spells\n1 Brainstorm\n1 Counterspell",
            is_pauper=False,
            deck_format="commander",
        )
        db.session.add(deck)
        db.session.commit()

        # Toggle to Pauper Commander
        resp = self.client.post(f"/api/deck/{deck.id}/format", json={"is_pauper": True})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["is_pauper"])
        self.assertEqual(data["deck_format"], "pauper_commander")
        self.assertIn("rule_evaluation", data)
        self.assertTrue(data["rule_evaluation"]["is_pauper"])

        # Check DB updated
        reloaded = db.session.get(DeckAnalysis, deck.id)
        self.assertTrue(reloaded.is_pauper)
        self.assertEqual(reloaded.deck_format, "pauper_commander")

        # Toggle back to Commander
        resp2 = self.client.post(f"/api/deck/{deck.id}/format", json={"is_pauper": False})
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertFalse(data2["is_pauper"])
        self.assertEqual(data2["deck_format"], "commander")
        self.assertFalse(data2["rule_evaluation"]["is_pauper"])


if __name__ == "__main__":
    unittest.main()
