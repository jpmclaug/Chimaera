"""
Test suite for Commander Deck Analyzer with Google Gemini integration.
Tests deck parser (ManaBox, Moxfield, Archidekt, CSV, text), Scryfall enrichment,
Gemini analyzer prompts & parsing, database models, and API endpoints.
"""

import json
import unittest
from unittest.mock import patch, MagicMock
from app import create_app
from models import db, User, DeckAnalysis, SystemSetting
from deck_parser import DeckParser, DeckParseError
from gemini_analyzer import GeminiAnalyzer, GeminiAnalysisError


class TestDeckParser(unittest.TestCase):
    """Tests deck parsing across various MTG formats and external sources."""

    def test_parse_standard_text_decklist(self):
        sample_text = """
// Commander
1 The Ur-Dragon

// Creatures
1 Utvara Hellkite (RTR) 110
1 Dragonlord Dromoka *F*
1 Miirym, Sentinel Wyrm (CLB) 284

// Artifacts
1 Sol Ring
1 Arcane Signet
1 Chromatic Lantern

// Lands
1 Command Tower
1 Haven of the Spirit Dragon
5 Forest
        """
        parsed = DeckParser.parse(sample_text, source_type="text")
        self.assertEqual(parsed["deck_name"], "Commander Deck")
        self.assertIn("The Ur-Dragon", parsed["commander"])
        self.assertGreater(len(parsed["cards"]), 5)
        
        # Check specific card parsing
        card_names = [c["name"] for c in parsed["cards"]]
        self.assertIn("The Ur-Dragon", card_names)
        self.assertIn("Utvara Hellkite", card_names)
        self.assertIn("Dragonlord Dromoka", card_names)
        self.assertIn("Sol Ring", card_names)
        self.assertIn("Forest", card_names)

        # Check section assignment
        cmdr_cards = [c for c in parsed["cards"] if c["section"] == "commander"]
        self.assertEqual(len(cmdr_cards), 1)
        self.assertEqual(cmdr_cards[0]["name"], "The Ur-Dragon")

    def test_parse_csv_format(self):
        sample_csv = """Name,Quantity,Binder Name,Set code,Set name,Card number,Condition,Foil,Rarity
"The Ur-Dragon","1","Commander","c17","Commander 2017","48","Near Mint","foil","Mythic"
"Utvara Hellkite","1","Main","rtr","Return to Ravnica","110","Near Mint","normal","Mythic"
"Sol Ring","1","Main","c17","Commander 2017","223","Near Mint","normal","Uncommon"
"Command Tower","1","Main","c17","Commander 2017","245","Near Mint","normal","Common"
"""
        parsed = DeckParser.parse(sample_csv, source_type="csv")
        self.assertIn("The Ur-Dragon", parsed["commander"])
        self.assertEqual(len(parsed["cards"]), 4)
        self.assertEqual(parsed["cards"][0]["set_code"], "C17")

    @patch("requests.get")
    def test_parse_manabox_url(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <!DOCTYPE html>
        <html>
        <head><title>My Dragon Brew - ManaBox</title></head>
        <body>
            <h1>My Dragon Brew</h1>
            <script type="application/json">
            {
                "name": "My Dragon Brew",
                "commander": [{"name": "The Ur-Dragon", "quantity": 1}],
                "cards": [
                    {"name": "The Ur-Dragon", "quantity": 1, "is_commander": true},
                    {"name": "Utvara Hellkite", "quantity": 1},
                    {"name": "Sol Ring", "quantity": 1}
                ]
            }
            </script>
        </body>
        </html>
        """
        mock_get.return_value = mock_resp

        parsed = DeckParser.parse_manabox_url("https://manabox.app/decks/test-123")
        self.assertEqual(parsed["deck_name"], "My Dragon Brew")
        self.assertIn("The Ur-Dragon", parsed["commander"])
        self.assertEqual(len(parsed["cards"]), 3)

    @patch("requests.get")
    def test_parse_moxfield_url(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "name": "Atraxa Superfriends",
            "commanders": {
                "Atraxa, Praetors' Voice": {"quantity": 1, "card": {"set": "2xm", "cn": "198"}}
            },
            "mainboard": {
                "Doubling Season": {"quantity": 1, "card": {"set": "2xm", "cn": "158"}},
                "Sol Ring": {"quantity": 1, "card": {"set": "c17", "cn": "223"}}
            }
        }
        mock_get.return_value = mock_resp

        parsed = DeckParser.parse_moxfield_url("https://www.moxfield.com/decks/sample123")
        self.assertEqual(parsed["deck_name"], "Atraxa Superfriends")
        self.assertIn("Atraxa, Praetors' Voice", parsed["commander"])
        self.assertEqual(len(parsed["cards"]), 3)


class TestGeminiAnalyzer(unittest.TestCase):
    """Tests Gemini analysis prompt formatting, JSON response parsing, and error recovery."""

    def test_clean_and_parse_json_with_code_fences(self):
        analyzer = GeminiAnalyzer(api_key="test_key")
        raw_output = """```json
{
  "deck_name": "Dragon Storm",
  "commander": ["The Ur-Dragon"],
  "estimated_power_level": 8.0,
  "power_bracket": "Optimized (7-8)",
  "overall_summary": "Aggressive high-synergy tribal deck.",
  "card_ratings": [
    {
      "card_name": "The Ur-Dragon",
      "rating": 9.5,
      "role": "Commander",
      "purpose": "Provides cost reduction and card advantage."
    }
  ]
}
```"""
        parsed = analyzer._clean_and_parse_json(raw_output)
        self.assertEqual(parsed["deck_name"], "Dragon Storm")
        self.assertEqual(parsed["estimated_power_level"], 8.0)
        self.assertEqual(len(parsed["card_ratings"]), 1)
        self.assertEqual(parsed["card_ratings"][0]["rating"], 9.5)

    @patch("requests.post")
    def test_analyze_deck_mocked(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "deck_name": "Dragon Storm",
                                    "commander": ["The Ur-Dragon"],
                                    "estimated_power_level": 7.5,
                                    "power_bracket": "Optimized (7-8)",
                                    "overall_summary": "High power dragon deck with strong ramp.",
                                    "mana_base_analysis": "Good 5-color land base.",
                                    "win_conditions": [
                                        {
                                            "title": "Dragon Swarm Overwhelm",
                                            "type": "Combat",
                                            "description": "Cast large dragons with haste.",
                                            "key_cards": ["Utvara Hellkite", "Dragon Tempest"]
                                        }
                                    ],
                                    "card_ratings": [
                                        {
                                            "card_name": "The Ur-Dragon",
                                            "rating": 9.5,
                                            "role": "Commander",
                                            "purpose": "Eminence discount enables fast dragon deployments.",
                                            "verdict": "Core Staple"
                                        },
                                        {
                                            "card_name": "Sol Ring",
                                            "rating": 10.0,
                                            "role": "Ramp",
                                            "purpose": "Accelerates commander casting by 2 turns.",
                                            "verdict": "Core Staple"
                                        }
                                    ],
                                    "upgrades": [
                                        {
                                            "card_in": "Mana Crypt",
                                            "card_out": "Forest",
                                            "category": "Power",
                                            "rationale": "Massive speed boost",
                                            "estimated_impact": "High"
                                        }
                                    ],
                                    "cut_recommendations": []
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        analyzer = GeminiAnalyzer(api_key="valid_test_key", model="gemini-3.7-flash")
        deck_data = {
            "deck_name": "Dragon Storm",
            "commander": ["The Ur-Dragon"],
            "cards": [
                {"name": "The Ur-Dragon", "quantity": 1, "section": "commander"},
                {"name": "Sol Ring", "quantity": 1, "section": "mainboard"}
            ]
        }
        result = analyzer.analyze_deck(deck_data)
        self.assertEqual(result["deck_name"], "Dragon Storm")
        self.assertEqual(result["estimated_power_level"], 7.5)
        self.assertEqual(len(result["card_ratings"]), 2)
        self.assertEqual(result["card_ratings"][0]["rating"], 9.5)
        self.assertEqual(len(result["upgrades"]), 1)


class TestDeckAnalyzerRoutes(unittest.TestCase):
    """Tests web routes and API endpoints for Deck Analyzer."""

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-secret-key",
            "GEMINI_API_KEY": "test-gemini-key",
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            # Create test user
            user = User(
                email="commander_tester@chimera.local",
                name="Commander Tester",
                is_admin=True,
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id

    def test_deck_analyzer_page_loads(self):
        self._login()
        resp = self.client.get("/deck-analyzer")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Commander Deck Vault", resp.data)

    def test_api_deck_parse_text(self):
        self._login()
        payload = {
            "source": "// Commander\n1 The Ur-Dragon\n// Main\n1 Sol Ring\n1 Command Tower",
            "source_type": "text",
        }
        resp = self.client.post("/api/deck/parse", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["cards"]), 3)
        self.assertIn("The Ur-Dragon", data["commander"])

    @patch("gemini_analyzer.GeminiAnalyzer.analyze_deck")
    def test_api_deck_analyze_and_save(self, mock_analyze):
        mock_analyze.return_value = {
            "deck_name": "Dragon Surge",
            "commander": ["The Ur-Dragon"],
            "estimated_power_level": 8.0,
            "power_bracket": "Optimized (7-8)",
            "overall_summary": "Excellent dragon synergy.",
            "mana_base_analysis": "Solid fixing.",
            "win_conditions": [
                {"title": "Dragon Beatdown", "type": "Combat", "description": "Overwhelm with flying dragons."}
            ],
            "card_ratings": [
                {"card_name": "The Ur-Dragon", "rating": 9.5, "role": "Commander", "purpose": "Discount engine."}
            ],
            "upgrades": [
                {"card_in": "Miirym, Sentinel Wyrm", "card_out": "Forest", "category": "Synergy", "rationale": "Clones dragons."}
            ],
            "cut_recommendations": []
        }

        self._login()
        payload = {
            "source": "// Commander\n1 The Ur-Dragon\n1 Sol Ring",
            "source_type": "text",
            "save": True,
        }
        resp = self.client.post("/api/deck/analyze", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIsNotNone(data["analysis_id"])

        # Verify saved in database
        with self.app.app_context():
            saved = db.session.get(DeckAnalysis, data["analysis_id"])
            self.assertIsNotNone(saved)
            self.assertEqual(saved.deck_name, "Dragon Surge")
            self.assertEqual(saved.power_level, 8.0)

    def test_api_deck_save_without_ai(self):
        self._login()
        payload = {
            "source": "// Commander\n1 The Ur-Dragon\n// Main\n1 Sol Ring\n1 Command Tower",
            "source_type": "text",
        }
        resp = self.client.post("/api/deck/save", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("deck", data)
        self.assertEqual(data["deck"]["deck_name"], "Commander Deck")
        self.assertFalse(data["deck"]["has_ai_analysis"])
        self.assertIn("stats", data["deck"])
        self.assertIn("cmc_curve", data["deck"]["stats"])

    @patch("deck_parser.DeckParser.parse")
    def test_api_deck_bulk_import(self, mock_parse):
        mock_parse.return_value = {
            "deck_name": "Bulk Dragon Deck",
            "commander": ["The Ur-Dragon"],
            "cards": [
                {"name": "The Ur-Dragon", "quantity": 1, "section": "commander"},
                {"name": "Sol Ring", "quantity": 1, "section": "mainboard"}
            ],
            "total_cards": 2,
            "source_type": "manabox_url",
            "raw_text": "ManaBox export",
        }
        self._login()
        payload = {
            "text": "https://manabox.app/decks/deck-1\nhttps://manabox.app/decks/deck-2",
        }
        resp = self.client.post("/api/deck/bulk-import", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["imported_count"], 2)

    @patch("deck_parser.DeckParser.parse")
    def test_api_deck_sync(self, mock_parse):
        mock_parse.return_value = {
            "deck_name": "Synced Dragon Deck",
            "commander": ["The Ur-Dragon"],
            "cards": [
                {"name": "The Ur-Dragon", "quantity": 1, "section": "commander"},
                {"name": "Sol Ring", "quantity": 1, "section": "mainboard"},
                {"name": "Mana Crypt", "quantity": 1, "section": "mainboard"}
            ],
            "total_cards": 3,
            "source_type": "manabox_url",
            "raw_text": "ManaBox export updated",
        }
        self._login()
        with self.app.app_context():
            entry = DeckAnalysis(
                user_id=self.user_id,
                deck_name="Old Name",
                source_url="https://manabox.app/decks/deck-123",
                source_type="manabox_url",
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        resp = self.client.post(f"/api/deck/{entry_id}/sync")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deck"]["deck_name"], "Synced Dragon Deck")

    @patch("gemini_analyzer.GeminiAnalyzer.analyze_deck")
    def test_api_deck_analyze_saved(self, mock_analyze):
        mock_analyze.return_value = {
            "deck_name": "Dragon Storm",
            "commander": ["The Ur-Dragon"],
            "estimated_power_level": 8.5,
            "power_bracket": "Optimized (7-8)",
            "overall_summary": "High powered dragon tribal.",
            "card_ratings": [
                {"card_name": "The Ur-Dragon", "rating": 9.5, "role": "Commander", "purpose": "Cost reducer."}
            ],
            "win_conditions": [],
            "upgrades": [],
            "cut_recommendations": []
        }
        self._login()
        with self.app.app_context():
            entry = DeckAnalysis(
                user_id=self.user_id,
                deck_name="Dragon Storm",
                commander_name="The Ur-Dragon",
                cards_data=json.dumps([{"name": "The Ur-Dragon", "quantity": 1, "section": "commander"}]),
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        resp = self.client.post(f"/api/deck/{entry_id}/analyze", json={"model": "gemini-3.7-flash"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deck"]["power_level"], 8.5)
    @patch("requests.get")
    def test_parse_manabox_astro_island(self, mock_get):
        mock_props = json.dumps({
            "deck": [0, {
                "id": [0, "123"],
                "name": [0, "Cloud Voltron"],
                "imageUrl": [0, "https://cards.scryfall.io/art_crop/front/2/2/cloud.jpg"],
                "cards": [1, [
                    [0, {
                        "name": [0, "Cloud, Ex-SOLDIER"],
                        "quantity": [0, 1],
                        "boardCategory": [0, 0],
                        "setId": [0, "fin"],
                        "collectorNumber": [0, "1"],
                        "pricing": [0, {"tcgplayer": [0, {"value": [0, 25.5]}]}]
                    }],
                    [0, {
                        "name": [0, "Sol Ring"],
                        "quantity": [0, 1],
                        "boardCategory": [0, 3],
                        "setId": [0, "cmm"],
                        "collectorNumber": [0, "383"],
                        "pricing": [0, {"tcgplayer": [0, {"value": [0, 1.5]}]}]
                    }]
                ]]
            }]
        })
        mock_html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Cloud Voltron | ManaBox</title></head>
        <body>
        <astro-island component-url="/_astro/deck.js" props='{mock_props}'></astro-island>
        </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html
        mock_get.return_value = mock_resp

        parsed = DeckParser.parse_manabox_url("https://manabox.app/decks/AZp7RL1ndfG1rsqSUcis2A")
        self.assertEqual(parsed["deck_name"], "Cloud Voltron")
        self.assertEqual(parsed["commander"], ["Cloud, Ex-SOLDIER"])
        self.assertEqual(parsed["commander_art"], "https://cards.scryfall.io/art_crop/front/2/2/cloud.jpg")
        self.assertEqual(len(parsed["cards"]), 2)
        self.assertEqual(parsed["cards"][0]["name"], "Cloud, Ex-SOLDIER")
        self.assertEqual(parsed["cards"][0]["section"], "commander")
        self.assertEqual(parsed["cards"][0]["price_usd"], 25.5)
        self.assertEqual(parsed["cards"][1]["name"], "Sol Ring")
        self.assertEqual(parsed["cards"][1]["section"], "mainboard")

    def test_gemini_models_and_selection(self):
        """Verifies GeminiAnalyzer and endpoints support Gemini 3.7 Flash, 3.6 Flash, 3.5 Flash, and 3.5 Flash-Lite."""
        analyzer_default = GeminiAnalyzer(api_key="test_key")
        self.assertEqual(analyzer_default.model, "gemini-3.7-flash")

        analyzer_36 = GeminiAnalyzer(api_key="test_key", model="gemini-3.6-flash")
        self.assertEqual(analyzer_36.model, "gemini-3.6-flash")

        analyzer_35 = GeminiAnalyzer(api_key="test_key", model="gemini-3.5-flash")
        self.assertEqual(analyzer_35.model, "gemini-3.5-flash")

        analyzer_lite = GeminiAnalyzer(api_key="test_key", model="gemini-3.5-flash-lite")
        self.assertEqual(analyzer_lite.model, "gemini-3.5-flash-lite")

        models = GeminiAnalyzer.get_available_models()
        model_ids = [m["id"] for m in models]
        self.assertIn("gemini-3.7-flash", model_ids)
        self.assertIn("gemini-3.6-flash", model_ids)
        self.assertIn("gemini-3.5-flash", model_ids)
        self.assertIn("gemini-3.5-flash-lite", model_ids)

        self._login()
        resp = self.client.get("/api/deck/gemini-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["default_model"], "gemini-3.7-flash")
        resp_model_ids = [m["id"] for m in data["supported_models"]]
        self.assertIn("gemini-3.7-flash", resp_model_ids)
        self.assertIn("gemini-3.6-flash", resp_model_ids)
        self.assertIn("gemini-3.5-flash", resp_model_ids)
        self.assertIn("gemini-3.5-flash-lite", resp_model_ids)


if __name__ == "__main__":
    unittest.main()
