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
from card_classifier import MTGCardClassifier
from deck_analyzer import DeckAnalyzer
from deck_comparator import DeckComparator


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
        self.assertIn(b"insp-tab-cards", resp.data)
        self.assertIn(b"insp-tab-ai", resp.data)
        self.assertIn(b"insp-tab-stats", resp.data)
        self.assertIn(b"Full Card Registry", resp.data)
        self.assertIn(b"Gemini AI Strategic Intel", resp.data)
        self.assertIn(b"pointer-events-none", resp.data)

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

    def test_api_deck_sync_from_cards_data_without_url(self):
        """Tests that sync successfully re-enriches and computes stats for decks saved without source_url."""
        self._login()
        with self.app.app_context():
            entry = DeckAnalysis(
                user_id=self.user_id,
                deck_name="Custom Deck Without URL",
                commander_name="The Ur-Dragon",
                cards_data=json.dumps([
                    {"name": "The Ur-Dragon", "quantity": 1, "section": "commander", "cmc": 9, "type_line": "Legendary Creature — Dragon Avatar"},
                    {"name": "Sol Ring", "quantity": 1, "section": "mainboard", "cmc": 1, "type_line": "Artifact"},
                ]),
                source_url=None,
                source_type="text",
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        resp = self.client.post(f"/api/deck/{entry_id}/sync")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("fast_ramp_count", data["deck"]["stats"])

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

        models = GeminiAnalyzer.get_available_models("test_key")
        model_ids = [m["id"] for m in models]
        self.assertEqual(len(model_ids), 4)
        self.assertEqual(model_ids, ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"])

        self._login()
        resp = self.client.get("/api/deck/gemini-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["default_model"], "gemini-3.7-flash")
        resp_model_ids = [m["id"] for m in data["supported_models"]]
        self.assertEqual(resp_model_ids, ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"])

    @patch("gemini_analyzer.requests.post")
    def test_gemini_503_fallback_cascade(self, mock_post):
        """Verifies that a 503 error on higher model automatically falls back to lower model."""
        sample_analysis = {
            "deck_name": "Fallback Test Deck",
            "commander": ["Atraxa, Praetors' Voice"],
            "archetype": "+1/+1 Counters",
            "estimated_power_level": 8.0,
            "overall_summary": "Super strong deck.",
            "card_ratings": [],
            "win_conditions": [],
            "upgrades": [],
            "cut_recommendations": []
        }

        mock_503_resp = MagicMock()
        mock_503_resp.status_code = 503
        mock_503_resp.json.return_value = {"error": {"message": "The model is overloaded. Please try again later."}}
        mock_503_resp.text = "The model is overloaded."

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.json.return_value = {
            "candidates": [{
                "content": {"parts": [{"text": json.dumps(sample_analysis)}]}
            }]
        }

        # First call (gemini-3.7-flash) returns 503, second call (gemini-3.6-flash) returns 200
        mock_post.side_effect = [mock_503_resp, mock_200_resp]

        analyzer = GeminiAnalyzer(api_key="test_key", model="gemini-3.7-flash")
        deck_data = {
            "deck_name": "Fallback Test Deck",
            "commander": ["Atraxa, Praetors' Voice"],
            "cards": [{"name": "Sol Ring", "quantity": 1}]
        }
        res = analyzer.analyze_deck(deck_data)
        self.assertEqual(res["_model_used"], "gemini-3.6-flash")
        self.assertEqual(analyzer.model, "gemini-3.6-flash")
        self.assertEqual(mock_post.call_count, 2)

    @patch("gemini_analyzer.requests.post")
    def test_gemini_all_models_503_failure_shows_all_timestamps(self, mock_post):
        """Verifies that when all models fail, error message includes timestamp and status for each attempted model."""
        mock_503_resp = MagicMock()
        mock_503_resp.status_code = 503
        mock_503_resp.json.return_value = {"error": {"message": "The model is overloaded. Please try again later."}}
        mock_503_resp.text = "The model is overloaded."

        # All 4 models fail with 503
        mock_post.return_value = mock_503_resp

        analyzer = GeminiAnalyzer(api_key="test_key", model="gemini-3.7-flash")
        deck_data = {
            "deck_name": "All Fail Deck",
            "commander": ["Atraxa, Praetors' Voice"],
            "cards": [{"name": "Sol Ring", "quantity": 1}]
        }

        with self.assertRaises(GeminiAnalysisError) as ctx:
            analyzer.analyze_deck(deck_data)

        err_msg = str(ctx.exception)
        self.assertIn("gemini-3.7-flash", err_msg)
        self.assertIn("gemini-3.6-flash", err_msg)
        self.assertIn("gemini-3.5-flash", err_msg)
        self.assertIn("gemini-3.5-flash-lite", err_msg)
        self.assertIn("EST", err_msg)
        self.assertIn("HTTP 503", err_msg)
        self.assertEqual(mock_post.call_count, 4)

    def test_deck_overview_page_authenticated(self):
        """Verifies /deck-overview page renders successfully with fleet stats and decks."""
        app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        with app.app_context():
            db.create_all()
            user = User(email="player@example.com", name="Commander Player", is_admin=True)
            db.session.add(user)
            db.session.commit()

            # Add two sample decks
            d1 = DeckAnalysis(
                user_id=user.id,
                deck_name="Dragon Fleet",
                commander_name="The Ur-Dragon",
                total_cards=100,
                total_value=350.0,
                avg_cmc=3.8,
                power_level=8.0,
                color_identity="W,U,B,R,G",
                cards_data=json.dumps([{"name": "The Ur-Dragon", "quantity": 1}, {"name": "Sol Ring", "quantity": 1}]),
                stats_json=json.dumps({"total_value": 350.0, "avg_cmc": 3.8, "type_counts": {"Creatures": 30, "Lands": 36}, "cmc_curve": {"1": 5, "2": 10}})
            )
            d2 = DeckAnalysis(
                user_id=user.id,
                deck_name="Vampire Bloodline",
                commander_name="Edgar Markov",
                total_cards=100,
                total_value=250.0,
                avg_cmc=2.9,
                power_level=7.5,
                color_identity="W,B,R",
                cards_data=json.dumps([{"name": "Edgar Markov", "quantity": 1}, {"name": "Sol Ring", "quantity": 1}]),
                stats_json=json.dumps({"total_value": 250.0, "avg_cmc": 2.9, "type_counts": {"Creatures": 38, "Lands": 35}, "cmc_curve": {"1": 12, "2": 15}})
            )
            db.session.add_all([d1, d2])
            db.session.commit()

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["user_id"] = user.id
                resp = client.get("/deck-overview")
                self.assertEqual(resp.status_code, 200)
                html = resp.data.decode("utf-8")
                self.assertIn("Fleet Overview & Compare", html)
                self.assertIn("Dragon Fleet", html)
                self.assertIn("Vampire Bloodline", html)
                self.assertIn("600.00", html)  # Total portfolio value 350 + 250

    def test_deck_compare_api(self):
        """Verifies /api/deck/compare endpoint correctly computes side-by-side comparison, shared staples, and top cards."""
        app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        with app.app_context():
            db.create_all()
            user = User(email="player@example.com", name="Commander Player", is_admin=True)
            db.session.add(user)
            db.session.commit()

            # Add two decks sharing Sol Ring and Command Tower
            d1 = DeckAnalysis(
                user_id=user.id,
                deck_name="Ur-Dragon Midrange",
                commander_name="The Ur-Dragon",
                total_cards=100,
                total_value=400.0,
                avg_cmc=3.8,
                power_level=8.0,
                color_identity="W,U,B,R,G",
                cards_data=json.dumps([
                    {"name": "The Ur-Dragon", "quantity": 1, "price_usd": "15.00", "type_line": "Legendary Creature — Dragon Avatar", "cmc": 9},
                    {"name": "Sol Ring", "quantity": 1, "price_usd": "2.00", "type_line": "Artifact", "cmc": 1},
                    {"name": "Command Tower", "quantity": 1, "price_usd": "0.50", "type_line": "Land", "cmc": 0},
                    {"name": "Terror of the Peaks", "quantity": 1, "price_usd": "35.00", "type_line": "Creature — Dragon", "cmc": 5}
                ]),
                stats_json=json.dumps({"total_value": 400.0, "avg_cmc": 3.8, "type_counts": {"Creatures": 30, "Lands": 36}, "cmc_curve": {"1": 5, "2": 10, "5": 8, "7+": 6}})
            )
            d2 = DeckAnalysis(
                user_id=user.id,
                deck_name="Edgar Markov Aggro",
                commander_name="Edgar Markov",
                total_cards=100,
                total_value=280.0,
                avg_cmc=2.7,
                power_level=7.5,
                color_identity="W,B,R",
                cards_data=json.dumps([
                    {"name": "Edgar Markov", "quantity": 1, "price_usd": "25.00", "type_line": "Legendary Creature — Vampire Knight", "cmc": 6},
                    {"name": "Sol Ring", "quantity": 1, "price_usd": "2.00", "type_line": "Artifact", "cmc": 1},
                    {"name": "Command Tower", "quantity": 1, "price_usd": "0.50", "type_line": "Land", "cmc": 0},
                    {"name": "Vampiric Tutor", "quantity": 1, "price_usd": "45.00", "type_line": "Instant", "cmc": 1}
                ]),
                stats_json=json.dumps({"total_value": 280.0, "avg_cmc": 2.7, "type_counts": {"Creatures": 40, "Lands": 34}, "cmc_curve": {"1": 15, "2": 18, "6": 2}})
            )
            db.session.add_all([d1, d2])
            db.session.commit()

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["user_id"] = user.id

                # Valid compare 2 decks
                resp = client.post("/api/deck/compare", json={"deck_ids": [d1.id, d2.id]})
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertTrue(data["success"])
                self.assertEqual(data["deck_count"], 2)
                
                # Check decks data
                self.assertEqual(data["decks"][0]["deck_name"], "Ur-Dragon Midrange")
                self.assertEqual(data["decks"][1]["deck_name"], "Edgar Markov Aggro")
                
                # Check shared staples
                shared_names = [c["name"].lower() for c in data["shared_all"]]
                self.assertIn("sol ring", shared_names)
                self.assertIn("command tower", shared_names)
                self.assertNotIn("vampiric tutor", shared_names)
                self.assertNotIn("terror of the peaks", shared_names)

                # Check unique cards
                unique_d1 = data["unique_per_deck"][str(d1.id)]
                unique_d2 = data["unique_per_deck"][str(d2.id)]
                self.assertGreaterEqual(unique_d1["count"], 2)
                self.assertGreaterEqual(unique_d2["count"], 2)

                # Validation error checks: less than 2 decks
                bad_resp1 = client.post("/api/deck/compare", json={"deck_ids": [d1.id]})
                self.assertEqual(bad_resp1.status_code, 400)

                # Validation error checks: more than 4 decks
                bad_resp2 = client.post("/api/deck/compare", json={"deck_ids": [1, 2, 3, 4, 5]})
                self.assertEqual(bad_resp2.status_code, 400)



class TestCardClassifier(unittest.TestCase):
    """Verifies MTGCardClassifier assigns functional roles, tags staples, handles multi-face cards, and ignores land tap abilities."""

    def setUp(self):
        self.classifier = MTGCardClassifier()

    def test_ramp_classification_staples(self):
        # Fast Ramp: CMC <= 2
        sol_ring = self.classifier.classify({
            "name": "Sol Ring", "cmc": 1, "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}."
        })
        self.assertTrue(sol_ring["is_ramp"])
        self.assertEqual(sol_ring["ramp_tier"], "fast")

        llanowar = self.classifier.classify({
            "name": "Llanowar Elves", "cmc": 1, "type_line": "Creature — Elf Druid", "oracle_text": "{T}: Add {G}."
        })
        self.assertTrue(llanowar["is_ramp"])
        self.assertEqual(llanowar["ramp_tier"], "fast")

        signet = self.classifier.classify({
            "name": "Arcane Signet", "cmc": 2, "type_line": "Artifact", "oracle_text": "{T}: Add one mana of any color in your commander's color identity."
        })
        self.assertTrue(signet["is_ramp"])
        self.assertEqual(signet["ramp_tier"], "fast")

        three_visits = self.classifier.classify({
            "name": "Three Visits", "cmc": 2, "type_line": "Sorcery", "oracle_text": "Search your library for a Forest card, put that card onto the battlefield, then shuffle."
        })
        self.assertTrue(three_visits["is_ramp"])
        self.assertEqual(three_visits["ramp_tier"], "fast")

        # Standard Ramp: CMC >= 3
        cultivate = self.classifier.classify({
            "name": "Cultivate", "cmc": 3, "type_line": "Sorcery", "oracle_text": "Search your library for up to two basic land cards, reveal them, put one onto the battlefield tapped and the other into your hand."
        })
        self.assertTrue(cultivate["is_ramp"])
        self.assertEqual(cultivate["ramp_tier"], "standard")

        tithe = self.classifier.classify({
            "name": "Smothering Tithe", "cmc": 4, "type_line": "Enchantment", "oracle_text": "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token."
        })
        self.assertTrue(tithe["is_ramp"])
        self.assertEqual(tithe["ramp_tier"], "standard")

    def test_lands_excluded_from_ramp(self):
        forest = self.classifier.classify({
            "name": "Forest", "cmc": 0, "type_line": "Basic Land — Forest", "oracle_text": "{T}: Add {G}."
        })
        self.assertFalse(forest["is_ramp"])

        tower = self.classifier.classify({
            "name": "Command Tower", "cmc": 0, "type_line": "Land", "oracle_text": "{T}: Add one mana of any color in your commander's color identity."
        })
        self.assertFalse(tower["is_ramp"])

    def test_removal_classification_staples(self):
        # Targeted Removal
        stp = self.classifier.classify({
            "name": "Swords to Plowshares", "cmc": 1, "type_line": "Instant", "oracle_text": "Exile target creature. Its controller gains life equal to its power."
        })
        self.assertTrue(stp["is_targeted_removal"])
        self.assertFalse(stp["is_board_wipe"])

        beast_within = self.classifier.classify({
            "name": "Beast Within", "cmc": 3, "type_line": "Instant", "oracle_text": "Destroy target permanent. Its controller creates a 3/3 green Beast creature token."
        })
        self.assertTrue(beast_within["is_targeted_removal"])

        chaos_warp = self.classifier.classify({
            "name": "Chaos Warp", "cmc": 3, "type_line": "Instant", "oracle_text": "The owner of target permanent shuffles it into their library, then reveals the top card of their library."
        })
        self.assertTrue(chaos_warp["is_targeted_removal"])

        # Board Wipes
        blasphemous = self.classifier.classify({
            "name": "Blasphemous Act", "cmc": 9, "type_line": "Sorcery", "oracle_text": "This spell costs {1} less to cast for each creature on the battlefield. Deals 13 damage to each creature."
        })
        self.assertTrue(blasphemous["is_board_wipe"])
        self.assertFalse(blasphemous["is_targeted_removal"])

        toxic = self.classifier.classify({
            "name": "Toxic Deluge", "cmc": 3, "type_line": "Sorcery", "oracle_text": "As an additional cost to cast this spell, pay X life. All creatures get -X/-X until end of turn."
        })
        self.assertTrue(toxic["is_board_wipe"])

        farewell = self.classifier.classify({
            "name": "Farewell", "cmc": 6, "type_line": "Sorcery", "oracle_text": "Choose one or more — Exile all artifacts; Exile all creatures; Exile all enchantments; Exile all graveyards."
        })
        self.assertTrue(farewell["is_board_wipe"])

        supreme_verdict = self.classifier.classify({
            "name": "Supreme Verdict", "cmc": 4, "type_line": "Sorcery", "oracle_text": "This spell can't be countered. Destroy all creatures."
        })
        self.assertTrue(supreme_verdict["is_board_wipe"])

        # Overload / Modal (Targeted + Wipe)
        cyc_rift = self.classifier.classify({
            "name": "Cyclonic Rift", "cmc": 2, "type_line": "Instant", "oracle_text": "Return target nonland permanent you don't control to its owner's hand. Overload {6}{U} (Return each nonland permanent you don't control to its owner's hand.)"
        })
        self.assertTrue(cyc_rift["is_targeted_removal"])
        self.assertTrue(cyc_rift["is_board_wipe"])

    def test_draw_classification_staples(self):
        # Engines
        rhystic = self.classifier.classify({
            "name": "Rhystic Study", "cmc": 3, "type_line": "Enchantment", "oracle_text": "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}."
        })
        self.assertTrue(rhystic["is_draw"])
        self.assertEqual(rhystic["draw_type"], "engine")

        whisperer = self.classifier.classify({
            "name": "Beast Whisperer", "cmc": 4, "type_line": "Creature — Elf Druid", "oracle_text": "Whenever you cast a creature spell, draw a card."
        })
        self.assertTrue(whisperer["is_draw"])
        self.assertEqual(whisperer["draw_type"], "engine")

        sylvan = self.classifier.classify({
            "name": "Sylvan Library", "cmc": 2, "type_line": "Enchantment", "oracle_text": "At the beginning of your draw step, you may draw two additional cards. If you do, choose two cards in your hand drawn this turn. For each of those cards, pay 4 life or put it back."
        })
        self.assertTrue(sylvan["is_draw"])
        self.assertEqual(sylvan["draw_type"], "engine")

        # Burst Draw
        harmonize = self.classifier.classify({
            "name": "Harmonize", "cmc": 4, "type_line": "Sorcery", "oracle_text": "Draw three cards."
        })
        self.assertTrue(harmonize["is_draw"])
        self.assertEqual(harmonize["draw_type"], "burst")

        nights_whisper = self.classifier.classify({
            "name": "Night's Whisper", "cmc": 2, "type_line": "Sorcery", "oracle_text": "You draw two cards and you lose 2 life."
        })
        self.assertTrue(nights_whisper["is_draw"])
        self.assertEqual(nights_whisper["draw_type"], "burst")

        # Cantrips
        gitaxian = self.classifier.classify({
            "name": "Gitaxian Probe", "cmc": 1, "type_line": "Sorcery", "oracle_text": "Look at target player's hand. Draw a card."
        })
        self.assertTrue(gitaxian["is_draw"])
        self.assertEqual(gitaxian["draw_type"], "cantrip")

        ponder = self.classifier.classify({
            "name": "Ponder", "cmc": 1, "type_line": "Sorcery", "oracle_text": "Look at the top three cards of your library, then put them back in any order. You may shuffle. Draw a card."
        })
        self.assertTrue(ponder["is_draw"])
        self.assertEqual(ponder["draw_type"], "cantrip")

    def test_tutor_classification_staples(self):
        demonic = self.classifier.classify({
            "name": "Demonic Tutor", "cmc": 2, "type_line": "Sorcery", "oracle_text": "Search your library for a card, put that card into your hand, then shuffle."
        })
        self.assertTrue(demonic["is_tutor"])
        self.assertEqual(demonic["tutor_type"], "general")

        vampiric = self.classifier.classify({
            "name": "Vampiric Tutor", "cmc": 1, "type_line": "Instant", "oracle_text": "Search your library for a card, then shuffle and put that card on top. You lose 2 life."
        })
        self.assertTrue(vampiric["is_tutor"])
        self.assertEqual(vampiric["tutor_type"], "general")

        farseek = self.classifier.classify({
            "name": "Farseek", "cmc": 2, "type_line": "Sorcery", "oracle_text": "Search your library for a Plains, Island, Swamp, or Mountain card, put it onto the battlefield tapped, then shuffle."
        })
        self.assertTrue(farseek["is_tutor"])
        self.assertEqual(farseek["tutor_type"], "land")

    def test_tapland_classification(self):
        temple = self.classifier.classify({
            "name": "Temple of Mystery", "cmc": 0, "type_line": "Land", "oracle_text": "Temple of Mystery enters the battlefield tapped. When Temple of Mystery enters the battlefield, scry 1. {T}: Add {G} or {U}."
        })
        self.assertTrue(temple["is_tapland"])

        guildgate = self.classifier.classify({
            "name": "Dimir Guildgate", "cmc": 0, "type_line": "Land — Gate", "oracle_text": "Dimir Guildgate enters the battlefield tapped. {T}: Add {U} or {B}."
        })
        self.assertTrue(guildgate["is_tapland"])

        # Shocklands / Fetchlands / Basics should NOT be unconditional taplands
        overgrown_tomb = self.classifier.classify({
            "name": "Overgrown Tomb", "cmc": 0, "type_line": "Land — Swamp Forest", "oracle_text": "({T}: Add {B} or {G}.) As Overgrown Tomb enters the battlefield, you may pay 2 life. If you don't, it enters the battlefield tapped."
        })
        self.assertFalse(overgrown_tomb["is_tapland"])

        basic_forest = self.classifier.classify({
            "name": "Forest", "cmc": 0, "type_line": "Basic Land — Forest", "oracle_text": "{T}: Add {G}."
        })
        self.assertFalse(basic_forest["is_tapland"])

    def test_multi_face_and_split_cards(self):
        # Split card: Fire // Ice
        fire_ice = self.classifier.classify({
            "name": "Fire // Ice",
            "cmc": 2,
            "type_line": "Instant // Instant",
            "card_faces": [
                {"name": "Fire", "oracle_text": "Fire deals 2 damage divided as you choose among one or two targets."},
                {"name": "Ice", "oracle_text": "Tap target permanent. Draw a card."}
            ]
        })
        self.assertTrue(fire_ice["is_targeted_removal"])
        self.assertTrue(fire_ice["is_draw"])
        self.assertEqual(fire_ice["draw_type"], "cantrip")

        # MDFC: Bala Ged Recovery // Bala Ged Sanctuary
        bala_ged = self.classifier.classify({
            "name": "Bala Ged Recovery // Bala Ged Sanctuary",
            "cmc": 3,
            "type_line": "Sorcery // Land",
            "card_faces": [
                {"name": "Bala Ged Recovery", "oracle_text": "Return target card from your graveyard to your hand."},
                {"name": "Bala Ged Sanctuary", "oracle_text": "Bala Ged Sanctuary enters the battlefield tapped. {T}: Add {G}."}
            ]
        })
        self.assertTrue(bala_ged["is_tapland"])


class TestDeckAnalyzerEngine(unittest.TestCase):
    """Verifies DeckAnalyzer pip-to-source ratios, AMV, instant speed ratios, and metric calculations."""

    def test_parse_mana_pips(self):
        analyzer = DeckAnalyzer()

        # Simple pips
        pips1 = analyzer.parse_mana_pips("{1}{W}{U}{B}{R}{G}")
        self.assertEqual(pips1["W"], 1.0)
        self.assertEqual(pips1["U"], 1.0)
        self.assertEqual(pips1["B"], 1.0)
        self.assertEqual(pips1["R"], 1.0)
        self.assertEqual(pips1["G"], 1.0)
        self.assertEqual(pips1["C"], 0.0)

        # Hybrid & Phyrexian & Colorless pips
        pips2 = analyzer.parse_mana_pips("{2/W}{B/P}{U/R}{C}")
        self.assertEqual(pips2["W"], 0.5)
        self.assertEqual(pips2["B"], 1.0)
        self.assertEqual(pips2["U"], 0.5)
        self.assertEqual(pips2["R"], 0.5)
        self.assertEqual(pips2["C"], 1.0)

    def test_extract_mana_sources(self):
        analyzer = DeckAnalyzer()

        # From produced_mana array
        card1 = {"name": "Hallowed Fountain", "type_line": "Land", "produced_mana": ["W", "U"]}
        sources1 = analyzer.extract_mana_sources(card1)
        self.assertIn("W", sources1)
        self.assertIn("U", sources1)

        # Basic Land type fallback
        card2 = {"name": "Island", "type_line": "Basic Land — Island"}
        sources2 = analyzer.extract_mana_sources(card2)
        self.assertIn("U", sources2)

        # Mana rock with oracle text
        card3 = {"name": "Arcane Signet", "type_line": "Artifact", "oracle_text": "{T}: Add one mana of any color in your commander's color identity."}
        sources3 = analyzer.extract_mana_sources(card3)
        self.assertGreater(len(sources3), 0)

    def test_full_deck_analysis(self):
        analyzer = DeckAnalyzer()
        deck_cards = [
            {"name": "Sol Ring", "cmc": 1, "type_line": "Artifact", "mana_cost": "{1}", "oracle_text": "{T}: Add {C}{C}.", "produced_mana": ["C"], "price_usd": "2.00"},
            {"name": "Arcane Signet", "cmc": 2, "type_line": "Artifact", "mana_cost": "{2}", "oracle_text": "{T}: Add {W}.", "produced_mana": ["W", "U"], "price_usd": "1.00"},
            {"name": "Swords to Plowshares", "cmc": 1, "type_line": "Instant", "mana_cost": "{W}", "oracle_text": "Exile target creature.", "price_usd": "1.50"},
            {"name": "Counterspell", "cmc": 2, "type_line": "Instant", "mana_cost": "{U}{U}", "oracle_text": "Counter target spell.", "price_usd": "1.50"},
            {"name": "Rhystic Study", "cmc": 3, "type_line": "Enchantment", "mana_cost": "{2}{U}", "oracle_text": "Whenever an opponent casts a spell, draw a card.", "price_usd": "35.00"},
            {"name": "Demonic Tutor", "cmc": 2, "type_line": "Sorcery", "mana_cost": "{1}{B}", "oracle_text": "Search your library for a card.", "price_usd": "30.00"},
            {"name": "Supreme Verdict", "cmc": 4, "type_line": "Sorcery", "mana_cost": "{1}{W}{W}{U}", "oracle_text": "Destroy all creatures.", "price_usd": "4.00"},
            {"name": "Temple of Mystery", "cmc": 0, "type_line": "Land", "oracle_text": "Temple of Mystery enters the battlefield tapped. {T}: Add {G} or {U}.", "produced_mana": ["G", "U"], "price_usd": "0.25"},
            {"name": "Command Tower", "cmc": 0, "type_line": "Land", "oracle_text": "{T}: Add one mana of any color.", "produced_mana": ["W", "U", "B", "R", "G"], "price_usd": "0.50"},
            {"name": "Island", "cmc": 0, "type_line": "Basic Land — Island", "produced_mana": ["U"], "price_usd": "0.10"},
        ]

        stats = analyzer.analyze(deck_cards)

        self.assertIn("nonland_amv", stats)
        self.assertGreater(stats["nonland_amv"], 0)
        self.assertEqual(stats["fast_ramp_count"], 2)  # Sol Ring + Arcane Signet
        self.assertGreater(stats["instant_speed_ratio"], 0)
        self.assertEqual(stats["targeted_removal_count"], 2)  # Swords to Plowshares + Counterspell
        self.assertEqual(stats["board_wipe_count"], 1)  # Supreme Verdict
        self.assertEqual(stats["draw_engine_count"], 1)  # Rhystic Study
        self.assertEqual(stats["tutor_general_count"], 1)  # Demonic Tutor
        self.assertEqual(stats["taplands_count"], 1)  # Temple of Mystery
        self.assertIn("pip_breakdown", stats)
        self.assertIn("W", stats["pip_breakdown"])
        self.assertIn("U", stats["pip_breakdown"])
        self.assertIn("archetype", stats)


class TestDeckComparatorEngine(unittest.TestCase):
    """Verifies DeckComparator calculates delta matrices, categorized profiles, and heuristic archetypes."""

    def test_side_by_side_comparison(self):
        deck_a = {
            "id": 1,
            "deck_name": "Aggro Combo",
            "commander_name": "Edgar Markov",
            "cards_data": [
                {"name": "Sol Ring", "cmc": 1, "type_line": "Artifact", "mana_cost": "{1}", "oracle_text": "{T}: Add {C}{C}.", "price_usd": "2.00"},
                {"name": "Vampiric Tutor", "cmc": 1, "type_line": "Instant", "mana_cost": "{B}", "oracle_text": "Search your library for a card.", "price_usd": "40.00"},
                {"name": "Swords to Plowshares", "cmc": 1, "type_line": "Instant", "mana_cost": "{W}", "oracle_text": "Exile target creature.", "price_usd": "2.00"},
                {"name": "Command Tower", "cmc": 0, "type_line": "Land", "oracle_text": "{T}: Add any color.", "price_usd": "0.50"}
            ],
            "stats": {
                "nonland_amv": 2.2,
                "fast_ramp_count": 8,
                "standard_ramp_count": 2,
                "instant_speed_ratio": 35.0,
                "tapland_penalty_index": 5.0,
                "removal_mana_efficiency": 1.5,
                "total_value": 350.0,
                "draw_engine_count": 4,
                "tutor_general_count": 3,
                "archetype": "Aggro / Fast Combo"
            }
        }

        deck_b = {
            "id": 2,
            "deck_name": "Dragon Battlecruiser",
            "commander_name": "The Ur-Dragon",
            "cards_data": [
                {"name": "Sol Ring", "cmc": 1, "type_line": "Artifact", "mana_cost": "{1}", "oracle_text": "{T}: Add {C}{C}.", "price_usd": "2.00"},
                {"name": "Cultivate", "cmc": 3, "type_line": "Sorcery", "mana_cost": "{2}{G}", "oracle_text": "Search for lands.", "price_usd": "0.50"},
                {"name": "Blasphemous Act", "cmc": 9, "type_line": "Sorcery", "mana_cost": "{8}{R}", "oracle_text": "Deals 13 damage to each creature.", "price_usd": "3.00"},
                {"name": "Command Tower", "cmc": 0, "type_line": "Land", "oracle_text": "{T}: Add any color.", "price_usd": "0.50"}
            ],
            "stats": {
                "nonland_amv": 3.8,
                "fast_ramp_count": 3,
                "standard_ramp_count": 9,
                "instant_speed_ratio": 12.0,
                "tapland_penalty_index": 22.0,
                "removal_mana_efficiency": 3.6,
                "total_value": 200.0,
                "draw_engine_count": 2,
                "tutor_general_count": 1,
                "archetype": "Battlecruiser / Big Mana"
            }
        }

        comparator = DeckComparator()
        comp_result = comparator.compare(deck_a, deck_b)

        self.assertIn("delta_matrix", comp_result)
        matrix = comp_result["delta_matrix"]
        self.assertIn("nonland_amv", matrix)
        self.assertAlmostEqual(matrix["nonland_amv"]["delta"], -1.6, places=2)
        self.assertEqual(matrix["nonland_amv"]["advantage"], "deck_a")  # Lower AMV is better

        self.assertIn("fast_ramp", matrix)
        self.assertEqual(matrix["fast_ramp"]["delta"], 5)
        self.assertEqual(matrix["fast_ramp"]["advantage"], "deck_a")

        self.assertIn("velocity_profile", comp_result)
        self.assertEqual(comp_result["velocity_profile"]["velocity_leader"], "deck_a")

        self.assertIn("shared_staples", comp_result)
        shared_names = [c["name"].lower() for c in comp_result["shared_staples"]]
        self.assertIn("sol ring", shared_names)
        self.assertIn("command tower", shared_names)


class TestDeckAnalyzerAdvancedMetrics(unittest.TestCase):
    """
    Tests for the 11-Metric Advanced Commander Telemetry Suite:
    - Turn 1-4 Land Drop Hypergeometric Probabilities
    - Effective Opening Hand Keepability Rate
    - Earliest Commander Cast Turn Simulation
    - Threat Type Coverage Matrix
    - Counterspell vs Protection Density
    - Instant-Speed Mana Holdout
    - Enabler-to-Payoff Ratio
    - Typal / Kindred Density
    - Virtual Card Advantage
    - Win Condition Classification
    - Mana Sink Availability
    """

    def setUp(self):
        self.analyzer = DeckAnalyzer()
        self.classifier = MTGCardClassifier()

    def test_land_drop_probabilities_hypergeometric(self):
        """Test hypergeometric land drop probability computation for turns 1 through 4."""
        probs = self.analyzer.compute_land_drop_probabilities(land_count=36, cheap_cantrip_count=4, total_cards=99)
        
        self.assertIn("turn_1", probs)
        self.assertIn("turn_2", probs)
        self.assertIn("turn_3", probs)
        self.assertIn("turn_4", probs)
        self.assertIn("effective_with_mulligan", probs)

        # Turn 1 >= Turn 2 >= Turn 3 >= Turn 4
        self.assertGreaterEqual(probs["turn_1"], probs["turn_2"])
        self.assertGreaterEqual(probs["turn_2"], probs["turn_3"])
        self.assertGreaterEqual(probs["turn_3"], probs["turn_4"])

        # Bound checks
        self.assertGreater(probs["turn_1"], 90.0)
        self.assertGreater(probs["turn_3"], 70.0)
        self.assertLessEqual(probs["turn_4"], 100.0)

        # Effective probability with 1 free mulligan must be >= raw probability
        eff = probs["effective_with_mulligan"]
        self.assertGreaterEqual(eff["turn_3"], probs["turn_3"])

    def test_opening_hand_keepability_simulation(self):
        """Test 7-card opening hand keepability Monte Carlo simulation."""
        deck_cards = [
            {"name": f"Forest {i}", "type_line": "Basic Land — Forest", "cmc": 0} for i in range(36)
        ] + [
            {"name": f"Elvish Mystic {i}", "type_line": "Creature — Elf Druid", "cmc": 1} for i in range(10)
        ] + [
            {"name": f"Rampant Growth {i}", "type_line": "Sorcery", "cmc": 2} for i in range(10)
        ] + [
            {"name": f"Big Beast {i}", "type_line": "Creature — Beast", "cmc": 6} for i in range(43)
        ]

        result = self.analyzer.simulate_opening_hand_keepability(deck_cards, iterations=500)
        self.assertIn("effective_keep_rate", result)
        self.assertIn("natural_keep_rate", result)
        self.assertIn("breakdown", result)

        # Effective keep rate with 1 free mulligan >= natural keep rate
        self.assertGreaterEqual(result["effective_keep_rate"], result["natural_keep_rate"])
        self.assertGreater(result["effective_keep_rate"], 50.0)

        # Lands in hand average should hover around 2.5 for a 36/99 deck
        self.assertAlmostEqual(result["avg_lands_in_hand"], 2.5, delta=0.5)

    def test_earliest_commander_cast_simulation(self):
        """Test early commander cast turn simulation comparing cheap vs expensive commanders."""
        cheap_cmdr = {
            "name": "Uro, Titan of Nature's Wrath",
            "mana_cost": "{1}{G}{U}",
            "cmc": 3,
            "type_line": "Legendary Creature — Elder Giant",
            "section": "commander"
        }
        expensive_cmdr = {
            "name": "The Ur-Dragon",
            "mana_cost": "{4}{W}{U}{B}{R}{G}",
            "cmc": 9,
            "type_line": "Legendary Creature — Dragon Avatar",
            "section": "commander"
        }

        library = [
            {"name": "Sol Ring", "type_line": "Artifact", "cmc": 1, "oracle_text": "{T}: Add {C}{C}."},
            {"name": "Arcane Signet", "type_line": "Artifact", "cmc": 2, "oracle_text": "{T}: Add any color."},
            {"name": "Command Tower", "type_line": "Land", "cmc": 0, "oracle_text": "{T}: Add one mana of any color in your commander's color identity."},
        ] + [{"name": f"Forest {i}", "type_line": "Basic Land — Forest", "cmc": 0, "oracle_text": "{T}: Add {G}."} for i in range(18)] \
          + [{"name": f"Island {i}", "type_line": "Basic Land — Island", "cmc": 0, "oracle_text": "{T}: Add {U}."} for i in range(18)] \
          + [{"name": f"Spell {i}", "type_line": "Instant", "cmc": 2} for i in range(60)]

        # Cheap commander cast result
        cheap_res = self.analyzer.simulate_earliest_commander_cast(cheap_cmdr, library, iterations=300)
        expensive_res = self.analyzer.simulate_earliest_commander_cast(expensive_cmdr, library, iterations=300)

        self.assertIn("median_cast_turn", cheap_res)
        self.assertIn("earliest_possible_turn", cheap_res)
        self.assertIn("turn_distribution", cheap_res)

        # Cheap commander cast median turn must be significantly earlier than 9 CMC commander
        self.assertLess(cheap_res["median_cast_turn"], expensive_res["median_cast_turn"])
        self.assertLessEqual(cheap_res["median_cast_turn"], 4)

    def test_threat_type_coverage(self):
        """Test interaction breakdown across 7 permanent threat types with instant vs sorcery categorization."""
        cards = [
            {
                "name": "Swords to Plowshares", "type_line": "Instant", "cmc": 1,
                "oracle_text": "Exile target creature. Its controller gains life equal to its power."
            },
            {
                "name": "Nature's Claim", "type_line": "Instant", "cmc": 1,
                "oracle_text": "Destroy target artifact or enchantment. Its controller gains 4 life."
            },
            {
                "name": "Feed the Swarm", "type_line": "Sorcery", "cmc": 2,
                "oracle_text": "Destroy target creature or enchantment an opponent controls."
            },
            {
                "name": "Hero's Downfall", "type_line": "Instant", "cmc": 3,
                "oracle_text": "Destroy target creature or planeswalker."
            },
            {
                "name": "Bojuka Bog", "type_line": "Land", "cmc": 0,
                "oracle_text": "When Bojuka Bog enters the battlefield, exile target player's graveyard."
            },
            {
                "name": "Counterspell", "type_line": "Instant", "cmc": 2,
                "oracle_text": "Counter target spell."
            },
            {
                "name": "Demolition Field", "type_line": "Land", "cmc": 0,
                "oracle_text": "{2}, {T}, Sacrifice Demolition Field: Destroy target nonbasic land."
            }
        ]

        # Enrich with classification
        for c in cards:
            c["classification"] = self.classifier.classify_card(c)

        coverage = self.analyzer.analyze_threat_coverage(cards)
        cats = coverage["categories"]

        self.assertGreaterEqual(cats["creatures"]["total"], 3)
        self.assertGreaterEqual(cats["artifacts"]["total"], 1)
        self.assertGreaterEqual(cats["enchantments"]["total"], 2)
        self.assertGreaterEqual(cats["planeswalkers"]["total"], 1)
        self.assertGreaterEqual(cats["graveyards"]["total"], 1)
        self.assertGreaterEqual(cats["spells"]["total"], 1)
        self.assertGreaterEqual(cats["lands"]["total"], 1)

        # Swords & Nature's Claim should be instant answers
        self.assertGreaterEqual(cats["creatures"]["instant"], 2)
        self.assertGreaterEqual(cats["artifacts"]["instant"], 1)

    def test_counter_vs_protection_density(self):
        """Test counterspell vs defensive protection classification."""
        cards = [
            {"name": "Counterspell", "type_line": "Instant", "cmc": 2, "oracle_text": "Counter target spell."},
            {"name": "Negate", "type_line": "Instant", "cmc": 2, "oracle_text": "Counter target noncreature spell."},
            {"name": "Heroic Intervention", "type_line": "Instant", "cmc": 2, "oracle_text": "Permanents you control gain hexproof and indestructible until end of turn."},
            {"name": "Teferi's Protection", "type_line": "Instant", "cmc": 3, "oracle_text": "Your life total can't change and your permanents phase out."},
            {"name": "Lightning Greaves", "type_line": "Artifact — Equipment", "cmc": 2, "oracle_text": "Equipped creature has haste and shroud."},
        ]

        for c in cards:
            c["classification"] = self.classifier.classify_card(c)

        cp = self.analyzer.analyze_counter_vs_protection(cards)
        self.assertEqual(cp["counterspell_count"], 2)
        self.assertGreaterEqual(cp["protection_count"], 3)

        pb = cp["protection_breakdown"]
        self.assertGreaterEqual(pb["hexproof_shroud"], 2)
        self.assertGreaterEqual(pb["indestructible"], 1)
        self.assertGreaterEqual(pb["phase_out"], 1)
        self.assertIn("stance", cp)

    def test_instant_mana_holdout(self):
        """Test instant-speed mana holdout calculation and CMC histogram."""
        cards = [
            {"name": "Force of Will", "type_line": "Instant", "cmc": 5, "oracle_text": "You may pay 1 life and exile a blue card from your hand rather than pay this spell's mana cost. Counter target spell."},
            {"name": "Swords to Plowshares", "type_line": "Instant", "cmc": 1, "oracle_text": "Exile target creature."},
            {"name": "Counterspell", "type_line": "Instant", "cmc": 2, "oracle_text": "Counter target spell."},
            {"name": "Beast Within", "type_line": "Instant", "cmc": 3, "oracle_text": "Destroy target permanent."},
        ]

        for c in cards:
            c["classification"] = self.classifier.classify_card(c)

        holdout = self.analyzer.compute_instant_mana_holdout(cards)
        self.assertIn("avg_holdout_cmc", holdout)
        self.assertIn("rating", holdout)
        self.assertIn("cmc_breakdown", holdout)

        # Breakdown should have 0, 1, 2, 3
        cb = holdout["cmc_breakdown"]
        self.assertEqual(cb["0"], 1)  # Force of Will free alternative
        self.assertEqual(cb["1"], 1)  # Swords
        self.assertEqual(cb["2"], 1)  # Counterspell
        self.assertEqual(cb["3"], 1)  # Beast Within

        # Effective avg CMC should be (0 + 1 + 2 + 3) / 4 = 1.5
        self.assertAlmostEqual(holdout["avg_holdout_cmc"], 1.5, places=2)

    def test_enabler_to_payoff_ratio(self):
        """Test sacrifice and counters engine detection, enabler-to-payoff ratio and diagnosis."""
        sac_deck = [
            {"name": "Viscera Seer", "type_line": "Creature — Vampire Wizard", "cmc": 1, "oracle_text": "Sacrifice a creature: Scry 1."},
            {"name": "Carrion Feeder", "type_line": "Creature — Zombie", "cmc": 1, "oracle_text": "Sacrifice a creature: Put a +1/+1 counter on Carrion Feeder."},
            {"name": "Ashnod's Altar", "type_line": "Artifact", "cmc": 3, "oracle_text": "Sacrifice a creature: Add {C}{C}."},
            {"name": "Blood Artist", "type_line": "Creature — Vampire", "cmc": 2, "oracle_text": "Whenever Blood Artist or another creature dies, target player loses 1 life and you gain 1 life."},
            {"name": "Zulaport Cutthroat", "type_line": "Creature — Human Rogue", "cmc": 2, "oracle_text": "Whenever Zulaport Cutthroat or another creature you control dies, each opponent loses 1 life and you gain 1 life."},
        ]

        for c in sac_deck:
            c["classification"] = self.classifier.classify_card(c)

        ep = self.analyzer.analyze_enabler_payoff_ratio(sac_deck)
        self.assertIn("Sacrifice", ep["theme"])
        self.assertEqual(ep["enabler_count"], 3)
        self.assertEqual(ep["payoff_count"], 2)
        self.assertAlmostEqual(ep["ratio"], 1.5, places=1)
        self.assertIn("health", ep)

    def test_typal_kindred_density(self):
        """Test creature subtype extraction and typal kindred density scoring."""
        bear_deck = [
            {"name": "Ayula, Queen Among Bears", "type_line": "Legendary Creature — Bear", "cmc": 2, "oracle_text": "Whenever another Bear enters the battlefield under your control, put two +1/+1 counters on target Bear."},
            {"name": "Grizzly Bears", "type_line": "Creature — Bear", "cmc": 2, "oracle_text": ""},
            {"name": "Mother Bear", "type_line": "Creature — Bear", "cmc": 2, "oracle_text": "Exile Mother Bear from your graveyard: Create two 2/2 green Bear creature tokens."},
            {"name": "Ashcoat Bear", "type_line": "Creature — Bear", "cmc": 2, "oracle_text": "Flash"},
            {"name": "Ayula's Influence", "type_line": "Enchantment", "cmc": 3, "oracle_text": "Discard a land card: Create a 2/2 green Bear creature token."},
            {"name": "Metallic Mimic", "type_line": "Artifact Creature — Shapeshifter", "cmc": 2, "oracle_text": "As Metallic Mimic enters, choose a creature type. Each other creature you control of the chosen type enters with an additional +1/+1 counter."},
        ]

        for c in bear_deck:
            c["classification"] = self.classifier.classify_card(c)

        typal = self.analyzer.analyze_typal_density(bear_deck, commander_names=["Ayula, Queen Among Bears"])
        self.assertTrue(typal["is_typal_deck"])
        self.assertEqual(typal["primary_type"], "Bear")
        self.assertEqual(typal["matching_creatures_count"], 4)
        self.assertGreaterEqual(typal["kindred_support_count"], 2)

    def test_virtual_card_advantage(self):
        """Test virtual card advantage aggregating pure draw, impulse draw, and graveyard recursion."""
        cards = [
            {"name": "Rhystic Study", "type_line": "Enchantment", "cmc": 3, "oracle_text": "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}."},
            {"name": "Light Up the Stage", "type_line": "Sorcery", "cmc": 3, "oracle_text": "Exile the top two cards of your library. Until the end of your next turn, you may play those cards."},
            {"name": "Reanimate", "type_line": "Sorcery", "cmc": 1, "oracle_text": "Put target creature card from a graveyard onto the battlefield under your control. You lose life equal to its mana value."},
            {"name": "Eternal Witness", "type_line": "Creature — Human Shaman", "cmc": 3, "oracle_text": "When Eternal Witness enters the battlefield, you may return target card from your graveyard to your hand."},
            {"name": "Demonic Tutor", "type_line": "Sorcery", "cmc": 2, "oracle_text": "Search your library for a card, put that card into your hand, then shuffle."},
        ]

        for c in cards:
            c["classification"] = self.classifier.classify_card(c)

        vca = self.analyzer.compute_virtual_card_advantage(cards, {"pure_draw": 1, "tutors": 1})
        self.assertEqual(vca["pure_draw"], 1)
        self.assertEqual(vca["impulse_draw"], 1)
        self.assertEqual(vca["recursion"], 2)
        self.assertEqual(vca["tutors"], 1)
        self.assertEqual(vca["total_virtual_advantage"], 5)
        self.assertIn("resource_depth_rating", vca)

    def test_win_condition_classification(self):
        """Test primary and secondary win condition identification."""
        overrun_deck = [
            {"name": "Craterhoof Behemoth", "type_line": "Creature — Beast", "cmc": 8, "oracle_text": "When Craterhoof Behemoth enters the battlefield, creatures you control gain trample and get +X/+X until end of turn."},
            {"name": "Triumph of the Hordes", "type_line": "Sorcery", "cmc": 4, "oracle_text": "Until end of turn, creatures you control get +1/+1 and gain trample and infect."},
            {"name": "Beastmaster Ascension", "type_line": "Enchantment", "cmc": 3, "oracle_text": "Creatures you control get +5/+5 as long as Beastmaster Ascension has seven or more quest counters on it."},
        ]

        for c in overrun_deck:
            c["classification"] = self.classifier.classify_card(c)

        wincons = self.analyzer.classify_win_conditions(overrun_deck, commander_names=[], archetype="Midrange")
        primary = wincons["primary_wincon"]
        self.assertIn("Overrun", primary["name"])
        self.assertGreaterEqual(len(primary["key_cards"]), 2)
        self.assertIn("clock_estimate", wincons)

    def test_mana_sinks_availability(self):
        """Test mana sinks identification including X-spells, activated abilities, and utility lands."""
        sink_cards = [
            {"name": "Torment of Hailfire", "type_line": "Sorcery", "cmc": 2, "mana_cost": "{X}{B}{B}", "oracle_text": "Repeat the following process X times."},
            {"name": "Thrasios, Triton Hero", "type_line": "Legendary Creature — Merfolk Wizard", "cmc": 2, "oracle_text": "{4}: Scry 1, then reveal the top card of your library."},
            {"name": "Kessig Wolf Run", "type_line": "Land", "cmc": 0, "oracle_text": "{X}{R}{G}, {T}: Target creature gets +X/+0 and gains trample until end of turn."},
        ]

        for c in sink_cards:
            c["classification"] = self.classifier.classify_card(c)

        sinks = self.analyzer.analyze_mana_sinks(sink_cards)
        self.assertEqual(sinks["total_sinks"], 3)
        self.assertEqual(sinks["type_breakdown"]["x_spell"], 1)
        self.assertEqual(sinks["type_breakdown"]["activated_ability"], 1)
        self.assertEqual(sinks["type_breakdown"]["utility_land"], 1)
        self.assertIn("late_game_resilience", sinks)

    def test_deck_comparator_advanced_metrics(self):
        """Test that DeckComparator generates comparative deltas for the new metrics."""
        deck_a = {
            "id": 1,
            "deck_name": "Fast Deck",
            "stats": {
                "land_drop_turn_3_pct": 86.5,
                "effective_keepability_rate": 91.0,
                "median_commander_cast_turn": 3,
                "avg_instant_holdout": 1.5,
                "total_mana_sinks": 5,
                "total_virtual_advantage": 14,
            }
        }
        deck_b = {
            "id": 2,
            "deck_name": "Slow Deck",
            "stats": {
                "land_drop_turn_3_pct": 72.0,
                "effective_keepability_rate": 78.0,
                "median_commander_cast_turn": 5,
                "avg_instant_holdout": 2.8,
                "total_mana_sinks": 2,
                "total_virtual_advantage": 7,
            }
        }

        comparator = DeckComparator()
        result = comparator.compare(deck_a, deck_b)
        matrix = result["delta_matrix"]

        self.assertIn("turn_3_land_pct", matrix)
        self.assertEqual(matrix["turn_3_land_pct"]["delta"], 14.5)
        self.assertEqual(matrix["turn_3_land_pct"]["advantage"], "deck_a")

        self.assertIn("keepability_rate", matrix)
        self.assertEqual(matrix["keepability_rate"]["delta"], 13.0)
        self.assertEqual(matrix["keepability_rate"]["advantage"], "deck_a")

        self.assertIn("commander_cast_turn", matrix)
        self.assertEqual(matrix["commander_cast_turn"]["delta"], -2)
        self.assertEqual(matrix["commander_cast_turn"]["advantage"], "deck_a")  # Lower turn is better

        self.assertIn("instant_holdout", matrix)
        self.assertAlmostEqual(matrix["instant_holdout"]["delta"], -1.3, places=2)
        self.assertEqual(matrix["instant_holdout"]["advantage"], "deck_a")  # Lower holdout CMC is better

        self.assertIn("mana_sinks", matrix)
        self.assertEqual(matrix["mana_sinks"]["delta"], 3)
        self.assertEqual(matrix["mana_sinks"]["advantage"], "deck_a")

        self.assertIn("virtual_card_advantage", matrix)
        self.assertEqual(matrix["virtual_card_advantage"]["delta"], 7)
        self.assertEqual(matrix["virtual_card_advantage"]["advantage"], "deck_a")


if __name__ == "__main__":
    unittest.main()



