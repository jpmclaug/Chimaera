"""
Unit tests for Unicode accent normalization, mojibake repair, and flexible MTG card matching.
"""

import unittest
from unittest.mock import MagicMock, patch

from card_utils import (
    fix_mojibake,
    strip_accents,
    normalize_card_name,
    get_card_match_keys,
    card_names_match,
)
from providers.edhrec import EDHRECProvider
from providers.scryfall import ScryfallProvider
from deck_parser import DeckParser
from inventory_manager import InventoryManager
from deck_upgrade_engine import DualTierUpgradeEngine


class TestCardUtils(unittest.TestCase):
    """Tests for card_utils normalization, mojibake repair, and matching."""

    def test_fix_mojibake(self):
        # UTF-8 encoded 'ó' decoded as Latin-1 gives 'Ã³'
        self.assertEqual(fix_mojibake("Gl\u00c3\u00b3in the Mighty // Easy Pickings"), "Glóin the Mighty // Easy Pickings")
        self.assertEqual(fix_mojibake("Mj\u00c3\u00b6lnir, Hammer of Thor"), "Mjölnir, Hammer of Thor")
        self.assertEqual(fix_mojibake("D\u00c3\u00a1in Ironfoot"), "Dáin Ironfoot")
        self.assertEqual(fix_mojibake("The Balrog, Flame of Ud\u00c3\u00bbn"), "The Balrog, Flame of Udûn")
        # Regular text remains untouched
        self.assertEqual(fix_mojibake("Sol Ring"), "Sol Ring")
        self.assertEqual(fix_mojibake(""), "")
        self.assertEqual(fix_mojibake(None), "")

    def test_strip_accents(self):
        self.assertEqual(strip_accents("Glóin the Mighty"), "Gloin the Mighty")
        self.assertEqual(strip_accents("Mjölnir, Hammer of Thor"), "Mjolnir, Hammer of Thor")
        self.assertEqual(strip_accents("Dáin Ironfoot"), "Dain Ironfoot")
        self.assertEqual(strip_accents("Fíli and Kíli, Joyous"), "Fili and Kili, Joyous")
        self.assertEqual(strip_accents("Lothlórien Lookout"), "Lothlorien Lookout")
        self.assertEqual(strip_accents("The Balrog, Flame of Udûn"), "The Balrog, Flame of Udun")
        # Mojibake string stripped of accents
        self.assertEqual(strip_accents("Gl\u00c3\u00b3in the Mighty"), "Gloin the Mighty")

    def test_normalize_card_name(self):
        # Curly apostrophes and quotes
        self.assertEqual(normalize_card_name("Thorin, King of Durin\u2019s Folk"), "Thorin, King of Durin's Folk")
        # Foil tag and set code
        self.assertEqual(normalize_card_name("Glóin the Mighty *F*"), "Glóin the Mighty")
        # DFC / Adventure slashes
        self.assertEqual(normalize_card_name("Glóin the Mighty/Easy Pickings"), "Glóin the Mighty // Easy Pickings")
        self.assertEqual(normalize_card_name("Delver of Secrets // Insectile Aberration"), "Delver of Secrets // Insectile Aberration")
        # With strip_diacritics
        self.assertEqual(normalize_card_name("Glóin the Mighty", strip_diacritics=True), "Gloin the Mighty")

    def test_get_card_match_keys(self):
        keys = get_card_match_keys("Glóin the Mighty // Easy Pickings")
        self.assertIn("glóin the mighty // easy pickings", keys)
        self.assertIn("gloin the mighty // easy pickings", keys)
        self.assertIn("glóin the mighty", keys)
        self.assertIn("gloin the mighty", keys)

    def test_card_names_match(self):
        # Accent variations
        self.assertTrue(card_names_match("Glóin the Mighty", "gloin the mighty"))
        self.assertTrue(card_names_match("Gloin the Mighty", "Glóin the Mighty"))
        # Front face vs full adventure name
        self.assertTrue(card_names_match("Glóin the Mighty // Easy Pickings", "gloin the mighty"))
        self.assertTrue(card_names_match("gloin the mighty", "Glóin the Mighty // Easy Pickings"))
        self.assertTrue(card_names_match("Glóin the Mighty // Easy Pickings", "Gloin the Mighty // Easy Pickings"))
        # Mojibake vs clean
        self.assertTrue(card_names_match("Gl\u00c3\u00b3in the Mighty // Easy Pickings", "gloin the mighty"))
        # Non-matching
        self.assertFalse(card_names_match("Glóin the Mighty", "Gimli, Counter of Kills"))
        self.assertFalse(card_names_match(None, "Sol Ring"))


class TestEDHRECAccentSlug(unittest.TestCase):
    """Tests for EDHREC slug generation with accented names."""

    def test_edhrec_slug_with_accents(self):
        self.assertEqual(EDHRECProvider.normalize_slug("Glóin, Dwarf Emissary"), "gloin-dwarf-emissary")
        self.assertEqual(EDHRECProvider.normalize_slug("Glóin the Mighty // Easy Pickings"), "gloin-the-mighty")
        self.assertEqual(EDHRECProvider.normalize_slug("Gl\u00c3\u00b3in the Mighty"), "gloin-the-mighty")
        self.assertEqual(EDHRECProvider.normalize_slug("The Balrog, Flame of Udûn"), "the-balrog-flame-of-udun")
        self.assertEqual(EDHRECProvider.normalize_slug("Mjölnir, Hammer of Thor"), "mjolnir-hammer-of-thor")


class TestDeckParserAccents(unittest.TestCase):
    """Tests for text decklist parser handling accented lines."""

    def test_parse_text_with_accents_and_set_tags(self):
        raw = """
1 Glóin the Mighty // Easy Pickings (HOB) 99 *F*
4 Dáin Ironfoot (LTC) 123
1 Mjölnir, Hammer of Thor
1 Fíli the Pathfinder
"""
        result = DeckParser.parse_text(raw)
        cards = result["cards"]
        self.assertEqual(len(cards), 4)

        gloin = next(c for c in cards if "easy pickings" in c["name"].lower())
        self.assertEqual(gloin["name"], "Glóin the Mighty // Easy Pickings")
        self.assertEqual(gloin["quantity"], 1)
        self.assertEqual(gloin["set_code"], "HOB")
        self.assertEqual(gloin["collector_number"], "99")

        dain = next(c for c in cards if "dain" in strip_accents(c["name"]).lower())
        self.assertEqual(dain["name"], "Dáin Ironfoot")
        self.assertEqual(dain["quantity"], 4)
        self.assertEqual(dain["set_code"], "LTC")
        self.assertEqual(dain["collector_number"], "123")


class TestScryfallAccentResolution(unittest.TestCase):
    """Tests for ScryfallProvider multi-key indexing and fallback lookup."""

    def test_index_card_in_map_keys(self):
        scryfall = ScryfallProvider()
        found_map = {}
        formatted = {
            "name": "Glóin the Mighty // Easy Pickings",
            "mana_cost": "{3}{R} // {2}{R}",
            "type_line": "Legendary Creature — Dwarf Warrior // Sorcery — Adventure",
        }
        scryfall._index_card_in_map(found_map, formatted, extra_names=["Gl\u00c3\u00b3in the Mighty // Easy Pickings"])

        # Check all possible lookup keys find the card
        self.assertIn("glóin the mighty // easy pickings", found_map)
        self.assertIn("gloin the mighty // easy pickings", found_map)
        self.assertIn("glóin the mighty", found_map)
        self.assertIn("gloin the mighty", found_map)

    @patch("requests.Session.get")
    def test_get_card_named_fallback(self, mock_get):
        # Simulate exact lookup with accent failing, but unaccented lookup succeeding
        def side_effect(url, params=None, **kwargs):
            mock_resp = MagicMock()
            if params and params.get("exact") == "Gloin the Mighty":
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "name": "Glóin the Mighty // Easy Pickings",
                    "mana_cost": "{3}{R}",
                    "type_line": "Legendary Creature — Dwarf Warrior",
                    "card_faces": [],
                }
            elif params and params.get("exact") == "Glóin the Mighty":
                mock_resp.status_code = 404
                mock_resp.json.return_value = {"details": "Not found"}
            else:
                mock_resp.status_code = 404
                mock_resp.json.return_value = {"details": "Not found"}
            return mock_resp

        mock_get.side_effect = side_effect
        scryfall = ScryfallProvider()
        card = scryfall.get_card_named("Glóin the Mighty")
        self.assertIsNotNone(card)
        self.assertEqual(card["name"], "Glóin the Mighty // Easy Pickings")


class TestUpgradeEngineAccentMatching(unittest.TestCase):
    """Tests for DualTierUpgradeEngine matching cards in deck and inventory with accents."""

    def test_is_card_in_deck_accent_insensitive(self):
        deck_cards = [
            {"name": "Glóin the Mighty // Easy Pickings", "quantity": 1}
        ]
        deck_cards_set = set()
        for c in deck_cards:
            deck_cards_set.update(get_card_match_keys(c["name"]))

        # Check various query representations
        self.assertTrue(DualTierUpgradeEngine._is_card_in_deck("gloin the mighty", deck_cards_set))
        self.assertTrue(DualTierUpgradeEngine._is_card_in_deck("Glóin the Mighty", deck_cards_set))
        self.assertTrue(DualTierUpgradeEngine._is_card_in_deck("Gloin the Mighty // Easy Pickings", deck_cards_set))
        self.assertTrue(DualTierUpgradeEngine._is_card_in_deck("Glóin the Mighty // Easy Pickings", deck_cards_set))
        self.assertFalse(DualTierUpgradeEngine._is_card_in_deck("Gimli, Mournful Avenger", deck_cards_set))


if __name__ == "__main__":
    unittest.main()
