"""
Unit and Integration Test Suite for EDHREC Metadata and Synergy Engine.
"""

import json
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import requests

from app import create_app
from models import db, User, AllowedEmail, DeckAnalysis, UserInventoryCard, EDHRECCache
from providers.edhrec import EDHRECProvider
from deck_upgrade_engine import DualTierUpgradeEngine


class TestEDHRECSlugNormalization(unittest.TestCase):
    """Tests for EDHREC slug normalization, DFC front face handling, and partner resolution."""

    def test_normalize_slug_standard(self):
        self.assertEqual(EDHRECProvider.normalize_slug("The Ur-Dragon"), "the-ur-dragon")
        self.assertEqual(EDHRECProvider.normalize_slug("Atraxa, Praetors' Voice"), "atraxa-praetors-voice")
        self.assertEqual(EDHRECProvider.normalize_slug("Korvold, Fae-Cursed King"), "korvold-fae-cursed-king")

    def test_normalize_slug_apostrophes_and_punctuation(self):
        # Apostrophes removed without hyphens
        self.assertEqual(EDHRECProvider.normalize_slug("Urza, Lord High Artificer"), "urza-lord-high-artificer")
        self.assertEqual(EDHRECProvider.normalize_slug("Y'shtola, Night's Blessed"), "yshtola-nights-blessed")
        self.assertEqual(EDHRECProvider.normalize_slug("Hazoret the Fervent"), "hazoret-the-fervent")
        self.assertEqual(EDHRECProvider.normalize_slug("Lim-Dûl the Necromancer"), "lim-dul-the-necromancer")

    def test_normalize_slug_dfc_transform_front_face(self):
        # Front face resolution before ' // '
        self.assertEqual(
            EDHRECProvider.normalize_slug("Jace, Vryn's Prodigy // Jace, Telepath Unbound"),
            "jace-vryns-prodigy"
        )
        self.assertEqual(
            EDHRECProvider.normalize_slug("Beryll, Master of the Waves // Ocean King"),
            "beryll-master-of-the-waves"
        )

    def test_normalize_slug_partner_separators(self):
        self.assertEqual(
            EDHRECProvider.normalize_slug("Thrasios, Triton Hero + Tymna the Weaver"),
            "thrasios-triton-hero-tymna-the-weaver"
        )
        self.assertEqual(
            EDHRECProvider.normalize_slug("Tymna the Weaver & Kraum, Ludevic's Opus"),
            "tymna-the-weaver-kraum-ludevics-opus"
        )

    def test_normalize_commander_input_list_partners(self):
        cmdrs = ["Thrasios, Triton Hero", "Tymna the Weaver"]
        joint, fallback = EDHRECProvider.normalize_commander_input(cmdrs)
        self.assertEqual(joint, "thrasios-triton-hero-tymna-the-weaver")
        self.assertEqual(fallback, "thrasios-triton-hero")

    def test_normalize_commander_input_single_list(self):
        joint, fallback = EDHRECProvider.normalize_commander_input(["The Ur-Dragon"])
        self.assertEqual(joint, "the-ur-dragon")
        self.assertIsNone(fallback)

    def test_normalize_commander_input_empty(self):
        self.assertEqual(EDHRECProvider.normalize_commander_input(""), ("", None))
        self.assertEqual(EDHRECProvider.normalize_commander_input([]), ("", None))
        self.assertEqual(EDHRECProvider.normalize_commander_input(None), ("", None))


class TestEDHRECProviderClient(unittest.TestCase):
    """Tests for EDHREC HTTP requests, rate limiting, retry backoff, and JSON parsing."""

    def setUp(self):
        self.provider = EDHRECProvider(cache_ttl_hours=24)

    def test_throttler_enforces_interval(self):
        start = time.time()
        self.provider._last_request_time = start
        # Calling throttle right after setting _last_request_time must sleep ~0.5s
        self.provider._throttle()
        elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 0.45)

    @patch("requests.Session.get")
    def test_backoff_retry_on_429_then_200(self, mock_get):
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_200 = MagicMock()
        resp_200.status_code = 200
        mock_get.side_effect = [resp_429, resp_200]

        with patch("time.sleep") as mock_sleep:
            resp = self.provider._request_with_backoff("https://json.edhrec.com/pages/commanders/test.json")
            self.assertEqual(resp.status_code, 200)
            mock_sleep.assert_called()

    @patch("requests.Session.get")
    def test_backoff_retry_on_503_then_200(self, mock_get):
        resp_503 = MagicMock()
        resp_503.status_code = 503
        resp_200 = MagicMock()
        resp_200.status_code = 200
        mock_get.side_effect = [resp_503, resp_200]

        with patch("time.sleep") as mock_sleep:
            resp = self.provider._request_with_backoff("https://json.edhrec.com/pages/commanders/test.json")
            self.assertEqual(resp.status_code, 200)
            mock_sleep.assert_called()

    @patch("requests.Session.get")
    def test_backoff_max_retries_exhausted(self, mock_get):
        resp_429 = MagicMock()
        resp_429.status_code = 429
        mock_get.return_value = resp_429

        with patch("time.sleep"):
            resp = self.provider._request_with_backoff("https://json.edhrec.com/pages/commanders/test.json", max_retries=2)
            self.assertIsNone(resp)

    @patch("requests.Session.get")
    def test_partner_fallback_query_on_404(self, mock_get):
        resp_404 = MagicMock()
        resp_404.status_code = 404

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {
            "container": {"json_dict": {"card": {"name": "Thrasios, Triton Hero", "rank": 15, "num_decks": 8500, "salt": 1.25}}},
            "panels": {}
        }
        mock_get.side_effect = [resp_404, resp_200]

        with patch.object(self.provider, "_get_from_cache", return_value=None):
            with patch.object(self.provider, "_set_in_cache"):
                result = self.provider.get_commander_data(["Thrasios, Triton Hero", "Tymna the Weaver"])
                self.assertIsNotNone(result)
                self.assertEqual(result["name"], "Thrasios, Triton Hero")
                self.assertEqual(mock_get.call_count, 2)

    def test_parse_commander_payload_metadata_and_synergies(self):
        mock_payload = {
            "container": {
                "json_dict": {
                    "card": {
                        "name": "The Ur-Dragon",
                        "rank": 1,
                        "num_decks": 15420,
                        "salt": 0.85,
                        "color_identity": ["W", "U", "B", "R", "G"]
                    },
                    "cardlists": [
                        {
                            "header": "High Synergy Cards",
                            "cardviews": [
                                {
                                    "name": "Miirym, Sentinel Wyrm",
                                    "synergy": 0.65,
                                    "num_decks": 10023,
                                    "potential_decks": 15420,
                                    "slug": "miirym-sentinel-wyrm"
                                }
                            ]
                        },
                        {
                            "header": "Top Cards",
                            "cardviews": [
                                {
                                    "name": "Dragon Tempest",
                                    "synergy": 0.45,
                                    "num_decks": 12000,
                                    "potential_decks": 15420,
                                    "slug": "dragon-tempest"
                                }
                            ]
                        }
                    ]
                }
            },
            "panels": {
                "articles": [
                    {
                        "value": "Mastering The Ur-Dragon in 2026",
                        "href": "/articles/mastering-the-ur-dragon",
                        "date": "2026-08-15",
                        "author": {"name": "Dragon Expert"},
                        "media": "https://edhrec.com/media/dragons.jpg",
                        "excerpt": "A comprehensive guide to brewing 5C Dragons."
                    }
                ],
                "taglinks": [
                    {"slug": "dragon", "value": "Dragons", "count": 12000},
                    {"slug": "tribal", "value": "Tribal", "count": 8500}
                ],
                "combocounts": [
                    {
                        "value": "Hellkite Charger + Sword of Feast and Famine",
                        "href": "/combos/5c/1234",
                    }
                ]
            }
        }

        parsed = self.provider._parse_commander_payload("the-ur-dragon", mock_payload)
        self.assertEqual(parsed["name"], "The Ur-Dragon")
        self.assertEqual(parsed["rank"], 1)
        self.assertEqual(parsed["num_decks"], 15420)
        self.assertEqual(parsed["salt_score"], 0.85)
        self.assertEqual(parsed["edhrec_url"], "https://edhrec.com/commanders/the-ur-dragon")

        # Articles
        self.assertEqual(len(parsed["articles"]), 1)
        self.assertEqual(parsed["articles"][0]["title"], "Mastering The Ur-Dragon in 2026")
        self.assertEqual(parsed["articles"][0]["author"], "Dragon Expert")
        self.assertEqual(parsed["articles"][0]["url"], "https://edhrec.com/articles/mastering-the-ur-dragon")

        # Themes
        self.assertEqual(len(parsed["themes"]), 2)
        self.assertEqual(parsed["themes"][0]["slug"], "dragon")
        self.assertEqual(parsed["themes"][0]["name"], "Dragons")

        # Combos
        self.assertEqual(len(parsed["combos"]), 1)
        self.assertEqual(parsed["combos"][0]["pieces"], ["Hellkite Charger", "Sword of Feast and Famine"])

        # Card synergies
        self.assertIn("miirym, sentinel wyrm", parsed["card_synergies"])
        miirym = parsed["card_synergies"]["miirym, sentinel wyrm"]
        self.assertEqual(miirym["synergy_percent"], 65.0)
        self.assertEqual(miirym["num_decks"], 10023)

        # High synergy and top cards
        self.assertEqual(len(parsed["high_synergy_cards"]), 1)
        self.assertEqual(len(parsed["top_cards"]), 1)

    def test_parse_top_salt_endpoint(self):
        mock_salt_payload = {
            "container": {
                "json_dict": {
                    "cardlists": [
                        {
                            "cardviews": [
                                {"name": "Armageddon", "salt": 2.85},
                                {"name": "Winter Orb", "salt": 2.74},
                                {"name": "Blood Moon // Blood Moon", "salt": 2.10}
                            ]
                        }
                    ]
                }
            }
        }

        with patch("requests.Session.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_salt_payload
            mock_get.return_value = mock_resp

            with patch.object(self.provider, "_get_from_cache", return_value=None):
                with patch.object(self.provider, "_set_in_cache"):
                    salt_map = self.provider.get_top_salt_cards(force_refresh=True)
                    self.assertEqual(salt_map["armageddon"], 2.85)
                    self.assertEqual(salt_map["winter orb"], 2.74)
                    self.assertEqual(salt_map["blood moon"], 2.10)


class TestEDHRECCache(unittest.TestCase):
    """Tests for SQLite/Postgres EDHRECCache database persistence and TTL."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "DISCORD_WEBHOOK_URL": "",
        })
        with cls.app.app_context():
            db.create_all()

    def test_cache_hit_and_expiration(self):
        with self.app.app_context():
            key = "edhrec:cmdr:test-commander"
            data = {"name": "Test Commander", "rank": 42}

            # Set cache for 1 hour
            EDHRECCache.set_cached(key, data, ttl_hours=1)
            cached = EDHRECCache.get_cached(key)
            self.assertIsNotNone(cached)
            self.assertEqual(cached["name"], "Test Commander")
            self.assertEqual(cached["rank"], 42)

            # Manually expire entry in DB
            entry = EDHRECCache.query.filter_by(cache_key=key).first()
            entry.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            db.session.commit()

            # Now get_cached should return None
            expired = EDHRECCache.get_cached(key)
            self.assertIsNone(expired)

            # Test clear_expired() cleans it up
            deleted_count = EDHRECCache.clear_expired()
            self.assertGreaterEqual(deleted_count, 1)
            self.assertIsNone(EDHRECCache.query.filter_by(cache_key=key).first())


class TestUpgradeEngineEDHRECIntegration(unittest.TestCase):
    """Tests for synergy ranking, anti-salt filtering, and combo detection in DualTierUpgradeEngine."""

    def setUp(self):
        self.engine = DualTierUpgradeEngine()
        self.mock_edhrec_data = {
            "slug": "the-ur-dragon",
            "name": "The Ur-Dragon",
            "rank": 1,
            "num_decks": 15000,
            "salt_score": 0.8,
            "card_synergies": {
                "dragon tempest": {"name": "Dragon Tempest", "synergy": 0.55, "synergy_percent": 55.0, "inclusion_percent": 85.0},
                "scourge of valkas": {"name": "Scourge of Valkas", "synergy": 0.40, "synergy_percent": 40.0, "inclusion_percent": 70.0},
                "crucible of fire": {"name": "Crucible of Fire", "synergy": 0.15, "synergy_percent": 15.0, "inclusion_percent": 35.0},
                "sol ring": {"name": "Sol Ring", "synergy": 0.05, "synergy_percent": 5.0, "inclusion_percent": 99.0},
                "armageddon": {"name": "Armageddon", "synergy": 0.30, "synergy_percent": 30.0, "inclusion_percent": 10.0, "salt": 2.85},
            },
            "high_synergy_cards": [
                {"name": "Dragon Tempest", "synergy": 0.55, "synergy_percent": 55.0, "inclusion_percent": 85.0, "slug": "dragon-tempest"},
                {"name": "Scourge of Valkas", "synergy": 0.40, "synergy_percent": 40.0, "inclusion_percent": 70.0, "slug": "scourge-of-valkas"},
                {"name": "Crucible of Fire", "synergy": 0.15, "synergy_percent": 15.0, "inclusion_percent": 35.0, "slug": "crucible-of-fire"},
                {"name": "Armageddon", "synergy": 0.30, "synergy_percent": 30.0, "inclusion_percent": 10.0, "slug": "armageddon"},
            ],
            "top_cards": [],
            "top_salt_map": {
                "armageddon": 2.85,
            },
            "combos": [
                {
                    "name": "Hellkite Charger + Sword of Feast and Famine",
                    "pieces": ["Hellkite Charger", "Sword of Feast and Famine"],
                    "url": "https://edhrec.com/combos/5c/123"
                },
                {
                    "name": "Niv-Mizzet, Parun + Curiosity",
                    "pieces": ["Niv-Mizzet, Parun", "Curiosity"],
                    "url": "https://edhrec.com/combos/ur/456"
                }
            ]
        }

    def test_owned_swaps_sorted_by_edhrec_synergy(self):
        deck = {
            "commander": "The Ur-Dragon",
            "cards": [
                {"name": "The Ur-Dragon", "section": "commander", "type_line": "Legendary Creature — Dragon Avatar"},
                {"name": "Plains", "section": "mainboard", "type_line": "Basic Land — Plains"},
                {"name": "Forest", "section": "mainboard", "type_line": "Basic Land — Forest"},
                {"name": "Island", "section": "mainboard", "type_line": "Basic Land — Island"},
                {"name": "Mountain", "section": "mainboard", "type_line": "Basic Land — Mountain"},
                {"name": "Swamp", "section": "mainboard", "type_line": "Basic Land — Swamp"},
                {"name": "Grizzly Bears", "section": "mainboard", "type_line": "Creature — Bear", "cmc": 2},
                {"name": "Ironclaw Orcs", "section": "mainboard", "type_line": "Creature — Orc", "cmc": 2},
            ]
        }

        # User has Crucible of Fire (+15% synergy) and Dragon Tempest (+55% synergy) in inventory
        mock_owned_cards = [
            UserInventoryCard(
                user_id=1,
                name="Crucible of Fire",
                collector_number="1",
                set_code="ala",
                quantity=1,
                condition="NM",
                language="en"
            ),
            UserInventoryCard(
                user_id=1,
                name="Dragon Tempest",
                collector_number="2",
                set_code="dtk",
                quantity=1,
                condition="NM",
                language="en"
            )
        ]

        upgrades = self.engine.generate_upgrades(
            deck=deck,
            user_inventory=mock_owned_cards,
            allocations={},
            edhrec_data=self.mock_edhrec_data
        )

        owned_swaps = upgrades.get("owned_swaps", [])
        self.assertGreaterEqual(len(owned_swaps), 2)
        card_names = [s["card_in"] for s in owned_swaps]
        # Dragon Tempest (+55%) should rank above Crucible of Fire (+15%)
        self.assertEqual(card_names[0], "Dragon Tempest")
        self.assertIn("Dragon Tempest", card_names)
        self.assertIn("Crucible of Fire", card_names)

        # Check synergy badge metadata
        top_swap = owned_swaps[0]
        self.assertEqual(top_swap["synergy_percent"], 55.0)

    def test_anti_salt_filter_excludes_salty_cards(self):
        deck = {
            "commander": "The Ur-Dragon",
            "cards": [
                {"name": "The Ur-Dragon", "section": "commander"},
                {"name": "Plains", "section": "mainboard"},
            ]
        }

        # With anti_salt=True and max_salt=1.0, Armageddon (salt=2.85) must not appear in shopping list
        upgrades = self.engine.generate_upgrades(
            deck=deck,
            user_inventory=[],
            allocations={},
            edhrec_data=self.mock_edhrec_data,
            anti_salt=True,
            max_salt=1.0
        )

        all_shopping = upgrades.get("all_shopping_cards", [])
        shopping_names = [item["name"].lower() for item in all_shopping]
        self.assertNotIn("armageddon", shopping_names)

    def test_evaluate_combos_active_and_near(self):
        deck_cards = [
            {"name": "The Ur-Dragon"},
            {"name": "Niv-Mizzet, Parun"},
            {"name": "Curiosity"},
            {"name": "Hellkite Charger"},
        ]
        user_inventory = [
            UserInventoryCard(user_id=1, name="Sword of Feast and Famine", collector_number="1", set_code="mbs", quantity=1)
        ]

        combos_result = self.engine.evaluate_combos(
            combos=self.mock_edhrec_data["combos"],
            deck_cards=deck_cards,
            user_inventory=user_inventory
        )

        # Niv-Mizzet + Curiosity is 100% in deck -> active combo
        active = combos_result.get("active", [])
        self.assertEqual(len(active), 1)
        self.assertIn("Niv-Mizzet, Parun", active[0]["pieces"])
        self.assertIn("Curiosity", active[0]["pieces"])

        # Hellkite Charger is in deck, Sword of Feast and Famine is missing but in binder -> near combo
        near = combos_result.get("near", [])
        self.assertEqual(len(near), 1)
        self.assertEqual(near[0]["missing_count"], 1)
        self.assertEqual(near[0]["missing_pieces"][0]["name"], "Sword of Feast and Famine")
        self.assertTrue(near[0]["missing_pieces"][0]["in_binder"])


class TestEDHRECFlaskEndpoints(unittest.TestCase):
    """Tests for Flask endpoints: /api/deck/<id>/edhrec, /api/deck/<id>/upgrades, /api/edhrec/top-salt."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "DISCORD_WEBHOOK_URL": "",
            "ADMIN_EMAIL": "jpmclaug@gmail.com",
        })
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()

    def login_as(self, email="edhrec_tester@chimera.local"):
        with self.app.app_context():
            allowed = AllowedEmail.get_by_email(email)
            if not allowed:
                allowed = AllowedEmail(email=email, is_admin=False, notes="Test User", added_by="TestSuite")
                db.session.add(allowed)
                db.session.commit()
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email, name="EDHRECTester", is_admin=False, is_active=True)
                db.session.add(user)
                db.session.commit()

            with self.client.session_transaction() as sess:
                sess["user_id"] = user.id
                sess["user_email"] = user.email
                sess["is_admin"] = user.is_admin
            return user

    def setUp(self):
        with self.app.app_context():
            DeckAnalysis.query.delete()
            UserInventoryCard.query.delete()
            EDHRECCache.query.delete()
            db.session.commit()

    def test_top_salt_endpoint(self):
        self.login_as()
        with patch.object(self.app.edhrec_provider, "get_top_salt_cards", return_value={"armageddon": 2.85, "winter orb": 2.74}):
            res = self.client.get("/api/edhrec/top-salt")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("salt_cards", {}).get("armageddon"), 2.85)

    def test_deck_edhrec_endpoint_returns_metadata_and_combos(self):
        user = self.login_as()
        # Create test deck analysis
        with self.app.app_context():
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Dragon Overlords",
                commander_name="The Ur-Dragon",
                cards_data=json.dumps([
                    {"name": "The Ur-Dragon", "section": "commander"},
                    {"name": "Niv-Mizzet, Parun", "section": "mainboard"},
                    {"name": "Curiosity", "section": "mainboard"}
                ])
            )
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.id

        mock_provider_data = {
            "slug": "the-ur-dragon",
            "name": "The Ur-Dragon",
            "rank": 1,
            "num_decks": 15000,
            "salt_score": 0.82,
            "edhrec_url": "https://edhrec.com/commanders/the-ur-dragon",
            "articles": [{"title": "Dragon Guide", "url": "https://edhrec.com/articles/1", "author": "Author"}],
            "themes": [{"slug": "dragon", "name": "Dragons", "count": 12000, "is_active": False}],
            "combos": [
                {"name": "Niv-Mizzet, Parun + Curiosity", "pieces": ["Niv-Mizzet, Parun", "Curiosity"], "url": "https://edhrec.com/combos/1"}
            ],
            "card_synergies": {},
            "high_synergy_cards": [],
            "top_cards": []
        }

        with patch.object(self.app.edhrec_provider, "get_commander_data", return_value=mock_provider_data):
            res = self.client.get(f"/api/deck/{deck_id}/edhrec")
            self.assertEqual(res.status_code, 200)
            payload = res.get_json()
            self.assertTrue(payload.get("success"))
            self.assertTrue(payload.get("found"))
            edhrec = payload.get("edhrec", {})
            self.assertEqual(edhrec.get("rank"), 1)
            self.assertEqual(edhrec.get("num_decks"), 15000)
            self.assertEqual(len(edhrec.get("articles", [])), 1)
            self.assertEqual(len(edhrec.get("themes", [])), 1)

            # Active combo detected
            combos = payload.get("combos_evaluation", {})
            self.assertEqual(len(combos.get("active", [])), 1)

    def test_deck_upgrades_endpoint_with_edhrec_theme_and_anti_salt(self):
        user = self.login_as()
        with self.app.app_context():
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Dragon Flight",
                commander_name="The Ur-Dragon",
                cards_data=json.dumps([
                    {"name": "The Ur-Dragon", "section": "commander"},
                    {"name": "Plains", "section": "mainboard"},
                    {"name": "Mountain", "section": "mainboard"},
                ])
            )
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.id

        mock_provider_data = {
            "slug": "the-ur-dragon",
            "name": "The Ur-Dragon",
            "rank": 1,
            "num_decks": 15000,
            "salt_score": 0.82,
            "active_theme": "dragon",
            "themes": [{"slug": "dragon", "name": "Dragons"}],
            "articles": [],
            "card_synergies": {
                "dragon tempest": {"name": "Dragon Tempest", "synergy": 0.65, "synergy_percent": 65.0, "inclusion_percent": 85.0},
            },
            "high_synergy_cards": [
                {"name": "Dragon Tempest", "synergy": 0.65, "synergy_percent": 65.0, "inclusion_percent": 85.0, "slug": "dragon-tempest"}
            ],
            "combos": []
        }

        with patch.object(self.app.edhrec_provider, "get_commander_data", return_value=mock_provider_data):
            with patch.object(self.app.edhrec_provider, "get_top_salt_cards", return_value={}):
                res = self.client.get(f"/api/deck/{deck_id}/upgrades?theme=dragon&anti_salt=true")
                self.assertEqual(res.status_code, 200)
                payload = res.get_json()
                self.assertTrue(payload.get("success"))
                self.assertIn("edhrec", payload)
                self.assertEqual(payload["edhrec"]["rank"], 1)
                self.assertEqual(payload["edhrec"]["active_theme"], "dragon")


if __name__ == "__main__":
    unittest.main()
