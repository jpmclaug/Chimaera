"""
Unit & Integration Tests for CardAddEvaluator and Add Analysis Suite in Chimaera MTG.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from card_add_evaluator import CardAddEvaluator
from models import CardAddAnalysis, DeckAnalysis, User, db


class TestCardAddEvaluator(unittest.TestCase):
    """Unit tests for CardAddEvaluator core logic."""

    def setUp(self):
        self.evaluator = CardAddEvaluator()

    def test_parse_card_input_single_and_batch(self):
        """Tests parsing of single card names, multiline quantities, and formats."""
        # 1. Single card
        res1 = self.evaluator.parse_card_input("Smothering Tithe")
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0]["name"], "Smothering Tithe")
        self.assertEqual(res1[0]["quantity"], 1)

        # 2. Multiline MTG quantities and set suffixes
        raw_text = """
        1x Sol Ring
        2 Esper Sentinel
        Cyclonic Rift (RTR) 35
        # Comment line
        1 Demonic Tutor
        1x Sol Ring
        """
        res2 = self.evaluator.parse_card_input(raw_text)
        names = [c["name"] for c in res2]
        self.assertIn("Sol Ring", names)
        self.assertIn("Esper Sentinel", names)
        self.assertIn("Cyclonic Rift", names)
        self.assertIn("Demonic Tutor", names)

        # Verify deduplication accumulated quantities
        sol_ring = next(c for c in res2 if c["name"] == "Sol Ring")
        self.assertEqual(sol_ring["quantity"], 2)

        # 3. Semicolon-separated line
        res3 = self.evaluator.parse_card_input("Swords to Plowshares; Path to Exile; Beast Within")
        self.assertEqual(len(res3), 3)
        self.assertEqual([c["name"] for c in res3], ["Swords to Plowshares", "Path to Exile", "Beast Within"])

    def test_color_identity_normalization(self):
        """Tests color identity extraction and normalization."""
        self.assertEqual(CardAddEvaluator.normalize_color_identity(["W", "U"]), {"W", "U"})
        self.assertEqual(CardAddEvaluator.normalize_color_identity("WUBRG"), {"W", "U", "B", "R", "G"})
        self.assertEqual(CardAddEvaluator.normalize_color_identity("C"), set())
        self.assertEqual(CardAddEvaluator.normalize_color_identity(None), set())

    def test_card_deck_compatibility_color_legality(self):
        """Tests color identity legality enforcement."""
        dimir_deck = {
            "deck_name": "Wilhelt Zombies",
            "color_identity": ["U", "B"],
            "cards": [{"name": "Swamp"}, {"name": "Island"}, {"name": "Zombie Master"}],
        }

        # 1. Blue card is legal in Dimir
        blue_card = {
            "name": "Cyclonic Rift",
            "color_identity": ["U"],
            "type_line": "Instant",
        }
        res_blue = self.evaluator.check_card_deck_compatibility(blue_card, dimir_deck)
        self.assertTrue(res_blue["is_legal"])
        self.assertTrue(res_blue["is_color_legal"])

        # 2. Colorless card is legal in Dimir
        colorless_card = {
            "name": "Roaming Throne",
            "color_identity": [],
            "type_line": "Artifact Creature",
        }
        res_colorless = self.evaluator.check_card_deck_compatibility(colorless_card, dimir_deck)
        self.assertTrue(res_colorless["is_legal"])
        self.assertTrue(res_colorless["is_color_legal"])

        # 3. Red/Green card is illegal in Dimir
        gruul_card = {
            "name": "Manamorphose",
            "color_identity": ["R", "G"],
            "type_line": "Instant",
        }
        res_gruul = self.evaluator.check_card_deck_compatibility(gruul_card, dimir_deck)
        self.assertFalse(res_gruul["is_legal"])
        self.assertFalse(res_gruul["is_color_legal"])
        self.assertIn("Illegal Colors", res_gruul["status_summary"])

    def test_card_deck_compatibility_banlist(self):
        """Tests EDH banlist detection."""
        deck = {
            "deck_name": "Test Deck",
            "color_identity": ["U", "B", "R"],
            "cards": [],
        }

        # Hullbreacher is banned in Commander
        banned_card = {
            "name": "Hullbreacher",
            "color_identity": ["U"],
            "type_line": "Creature — Merfolk Pirate",
        }
        res = self.evaluator.check_card_deck_compatibility(banned_card, deck)
        self.assertFalse(res["is_legal"])
        self.assertTrue(res["is_format_banned"])
        self.assertIn("Banned", res["status_summary"])

    def test_card_deck_compatibility_duplicate(self):
        """Tests detection of cards already in deck."""
        deck = {
            "deck_name": "Voja Elves",
            "color_identity": ["G", "W", "R"],
            "cards": [{"name": "Sol Ring"}, {"name": "Llanowar Elves"}],
        }

        card = {
            "name": "Sol Ring",
            "color_identity": [],
            "type_line": "Artifact",
        }
        res = self.evaluator.check_card_deck_compatibility(card, deck)
        self.assertTrue(res["is_already_in_deck"])

    def test_evaluate_card_algorithmic(self):
        """Tests algorithmic scoring, role classification, and cut candidate matching."""
        deck = {
            "deck_name": "Token Swarm",
            "color_identity": ["W", "G"],
            "cards": [
                {"name": "Plains", "type_line": "Basic Land", "cmc": 0},
                {"name": "Forest", "type_line": "Basic Land", "cmc": 0},
                {"name": "Grizzly Bears", "type_line": "Creature", "cmc": 2},
                {"name": "Ironroot Treefolk", "type_line": "Creature", "cmc": 5},
            ],
            "stats": {"pacing": {"targeted_removal_count": 2}},
        }

        cut_candidates = [
            {"name": "Ironroot Treefolk", "cmc": 5, "type_line": "Creature", "rating": 4.0, "is_basic": False},
            {"name": "Grizzly Bears", "cmc": 2, "type_line": "Creature", "rating": 5.0, "is_basic": False},
        ]

        card_meta = {
            "canonical_name": "Swords to Plowshares",
            "name": "Swords to Plowshares",
            "type_line": "Instant",
            "oracle_text": "Exile target creature. Its controller gains life equal to its power.",
            "cmc": 1,
            "mana_cost": "{W}",
            "color_identity": ["W"],
            "price_usd": 1.50,
        }

        res = self.evaluator.evaluate_card_algorithmic(
            card_meta=card_meta,
            deck=deck,
            cut_candidates=cut_candidates,
            edhrec_synergies={"swords to plowshares": {"synergy": 0.45, "synergy_percent": 45.0, "inclusion_percent": 65.0}},
        )

        self.assertEqual(res["role"], "Spot Removal")
        self.assertGreaterEqual(res["synergy_rating"], 8.5)
        self.assertIn(res["fit_verdict"], ["Essential Upgrade", "High Synergy"])
        self.assertTrue(bool(res["suggested_cut"]))

    def test_evaluate_cards_suite_algorithmic(self):
        """Tests full evaluate_cards_suite execution without Gemini (pure algorithmic)."""
        decks = [
            {
                "id": 101,
                "deck_name": "Mono-White Soldiers",
                "commander_name": "Myrel, Shield of Argive",
                "color_identity": ["W"],
                "cards": [
                    {"name": "Plains", "type_line": "Basic Land"},
                    {"name": "Silvercoat Lion", "type_line": "Creature", "cmc": 2},
                ],
            },
            {
                "id": 102,
                "deck_name": "Mono-Red Goblins",
                "commander_name": "Krenko, Mob Boss",
                "color_identity": ["R"],
                "cards": [
                    {"name": "Mountain", "type_line": "Basic Land"},
                    {"name": "Goblin Raider", "type_line": "Creature", "cmc": 2},
                ],
            },
        ]

        cards = [
            {
                "name": "Esper Sentinel",
                "canonical_name": "Esper Sentinel",
                "mana_cost": "{W}",
                "cmc": 1,
                "type_line": "Artifact Creature — Human Soldier",
                "oracle_text": "Whenever an opponent casts their first noncreature spell each turn, draw a card unless that player pays {X}...",
                "colors": ["W"],
                "color_identity": ["W"],
                "price_usd": 28.50,
            },
            {
                "name": "Roaming Throne",
                "canonical_name": "Roaming Throne",
                "mana_cost": "{4}",
                "cmc": 4,
                "type_line": "Artifact Creature — Golem",
                "oracle_text": "As Roaming Throne enters the battlefield, choose a creature type...",
                "colors": [],
                "color_identity": [],
                "price_usd": 24.00,
            },
        ]

        suite = self.evaluator.evaluate_cards_suite(
            cards=cards,
            decks=decks,
            use_gemini=False,
        )

        self.assertEqual(suite["mode"], "cards")
        self.assertEqual(len(suite["card_matrix"]), 2)

        # Esper Sentinel: legal in Mono-White (deck 101), illegal in Mono-Red (deck 102)
        esper_matrix = next(cm for cm in suite["card_matrix"] if cm["card_name"] == "Esper Sentinel")
        self.assertEqual(len(esper_matrix["deck_recommendations"]), 1)
        self.assertEqual(esper_matrix["deck_recommendations"][0]["deck_id"], 101)
        self.assertEqual(len(esper_matrix["incompatible_decks"]), 1)
        self.assertEqual(esper_matrix["incompatible_decks"][0]["deck_id"], 102)

        # Roaming Throne: colorless, legal in both Mono-White and Mono-Red
        throne_matrix = next(cm for cm in suite["card_matrix"] if cm["card_name"] == "Roaming Throne")
        self.assertEqual(len(throne_matrix["deck_recommendations"]), 2)
        self.assertEqual(len(throne_matrix["incompatible_decks"]), 0)
        self.assertEqual(suite.get("_model_used"), "timewalked-methodology")

    def test_timewalked_ratio_and_bracket_warning(self):
        """Tests Timewalked ratio filling, game changer bracket warnings, and cut pairing."""
        deck = {
            "id": 201,
            "deck_name": "Testing Ratios",
            "commander_name": "Krenko, Mob Boss",
            "color_identity": ["R"],
            "cards": [
                {"name": "Mountain", "type_line": "Basic Land"},
                {"name": "Goblins of the Flarg", "type_line": "Creature", "cmc": 1},
            ],
        }

        # Armageddon is a Game Changer / MLD card (White, but let's test evaluation directly)
        mld_card = {
            "canonical_name": "Armageddon",
            "name": "Armageddon",
            "type_line": "Sorcery",
            "oracle_text": "Destroy all lands.",
            "cmc": 4,
            "mana_cost": "{3}{W}",
            "color_identity": ["W"],
            "price_usd": 12.00,
        }

        cut_candidates = [
            {"name": "Goblins of the Flarg", "cmc": 1, "type_line": "Creature", "inclusion_percent": 15.0},
        ]

        res = self.evaluator.evaluate_card_algorithmic(
            card_meta=mld_card,
            deck=deck,
            cut_candidates=cut_candidates,
            edhrec_synergies={},
        )

        # MLD card should be recognized and trigger Bracket 4 warning
        self.assertTrue(res.get("is_mass_land_denial"))
        self.assertIsNotNone(res.get("bracket_warning"))
        self.assertIn("Bracket 4", res["bracket_warning"])
        # Should have cut reason
        self.assertTrue(bool(res.get("suggested_cut")))
        self.assertTrue(bool(res.get("cut_reason")))

        # Test official Game Changer (Rhystic Study) with deck context near 3 GC cap
        rhystic_card = {
            "canonical_name": "Rhystic Study",
            "name": "Rhystic Study",
            "type_line": "Enchantment",
            "oracle_text": "Whenever an opponent casts a spell...",
            "cmc": 3,
            "mana_cost": "{2}{U}",
            "color_identity": ["U"],
            "price_usd": 35.00,
        }
        tw_context_high_gc = {
            "ratios": {},
            "on_paper_bracket": 3,
            "feels_like": 3,
            "game_changers_count": 3,
            "included_combos": [],
            "almost_included_combos": [],
            "cut_pool": cut_candidates,
        }
        res_gc = self.evaluator.evaluate_card_algorithmic(
            card_meta=rhystic_card,
            deck=deck,
            cut_candidates=cut_candidates,
            edhrec_synergies={},
            tw_context=tw_context_high_gc,
        )
        self.assertTrue(res_gc.get("is_game_changer"))
        self.assertIsNotNone(res_gc.get("bracket_warning"))
        self.assertIn("raises deck to Bracket 4", res_gc["bracket_warning"])

    def test_timewalked_combo_completion_boost(self):
        """Tests that a 1-card-away combo completion boosts synergy and emits combo signal."""
        deck = {
            "id": 202,
            "deck_name": "Heliod Sun-Crowned Ballista",
            "commander_name": "Heliod, Sun-Crowned",
            "color_identity": ["W"],
            "cards": [
                {"name": "Plains", "type_line": "Basic Land"},
                {"name": "Heliod, Sun-Crowned", "type_line": "Legendary Enchantment Creature — God", "cmc": 3},
            ],
        }

        ballista_card = {
            "canonical_name": "Walking Ballista",
            "name": "Walking Ballista",
            "type_line": "Artifact Creature — Construct",
            "oracle_text": "Walking Ballista enters the battlefield with X +1/+1 counters on it...",
            "cmc": 0,
            "mana_cost": "{X}{X}",
            "color_identity": [],
            "price_usd": 15.00,
        }

        # Mock deck context with almost_included_combos completing with Walking Ballista
        tw_context = {
            "ratios": {},
            "bracket": 2,
            "feels_like": 2,
            "game_changer_count": 0,
            "included_combos": [],
            "almost_included_combos": [
                {
                    "cards": ["Heliod, Sun-Crowned", "Walking Ballista"],
                    "missing_cards": ["Walking Ballista"],
                    "present_cards": ["Heliod, Sun-Crowned"],
                    "missing_count": 1,
                    "combo_id": "test-combo-1",
                }
            ],
            "cut_pool": [{"name": "Plains", "type_line": "Basic Land", "cmc": 0}],
        }

        res = self.evaluator.evaluate_card_algorithmic(
            card_meta=ballista_card,
            deck=deck,
            cut_candidates=[],
            edhrec_synergies={},
            timewalked_context=tw_context,
        )

        self.assertIn("combo", res.get("signals", []))
        self.assertGreaterEqual(res["synergy_rating"], 7.5)
        self.assertEqual(res["fit_verdict"], "Essential Upgrade")
        self.assertTrue(any("combo" in r.lower() for r in res.get("reasons", [])))

    def test_evaluate_cards_suite_inventory_badge(self):
        """Tests that cards in user inventory receive in_binder flag."""
        decks = [
            {
                "id": 103,
                "deck_name": "Mono-White",
                "commander_name": "Elesh Norn",
                "color_identity": ["W"],
                "cards": [{"name": "Plains", "type_line": "Basic Land"}],
            }
        ]
        cards = [
            {
                "name": "Sol Ring",
                "canonical_name": "Sol Ring",
                "cmc": 1,
                "type_line": "Artifact",
                "color_identity": [],
            }
        ]
        user_inv = {
            "sol ring": {
                "id": 999,
                "binder_id": 12,
                "binder_name": "Staples Binder",
                "quantity": 1,
                "finish": "foil",
            }
        }

        suite = self.evaluator.evaluate_cards_suite(
            cards=cards,
            decks=decks,
            use_gemini=False,
            user_inventory_index=user_inv,
        )

        card_entry = suite["card_matrix"][0]
        self.assertTrue(card_entry.get("in_binder"))
        self.assertEqual(card_entry.get("binder_name"), "Staples Binder")

    def test_evaluate_secret_lair_suite_timewalked_deterministic(self):
        """Tests deterministic Secret Lair evaluation without Gemini."""
        decks = [
            {
                "id": 104,
                "deck_name": "Mono-White",
                "commander_name": "Heliod",
                "color_identity": ["W"],
                "cards": [{"name": "Plains", "type_line": "Basic Land"}],
            }
        ]
        drop_data = {
            "drop_name": "Secret Lair: Artist Series",
            "cards": [
                {
                    "name": "Swords to Plowshares",
                    "canonical_name": "Swords to Plowshares",
                    "cmc": 1,
                    "type_line": "Instant",
                    "color_identity": ["W"],
                    "price_usd": 5.0,
                }
            ],
            "price_usd": 29.99,
        }

        suite = self.evaluator.evaluate_secret_lair_suite(
            drops=[drop_data],
            decks=decks,
            use_gemini=False,
        )

        self.assertEqual(suite["mode"], "secret_lair")
        self.assertEqual(suite.get("_model_used"), "timewalked-methodology")
        self.assertIn("best_drops_to_buy", suite)
        self.assertIn("bundle_analysis", suite)
        self.assertTrue(len(suite["best_drops_to_buy"]) >= 1)


class TestAddEvaluatorAPI(unittest.TestCase):
    """API integration tests for Add Analysis Suite endpoints."""

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-key",
            "WTF_CSRF_ENABLED": False,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            # Create test user
            self.user = User(
                email="commander@test.com",
                name="Fleet Admiral",
                is_active=True,
                is_admin=True,
            )
            db.session.add(self.user)
            db.session.commit()
            self.user_id = self.user.id

            # Create test deck
            cards_sample = [
                {"name": "Swords to Plowshares", "cmc": 1, "type_line": "Instant"},
                {"name": "Sol Ring", "cmc": 1, "type_line": "Artifact"},
                {"name": "Silvercoat Lion", "cmc": 2, "type_line": "Creature"},
                {"name": "Plains", "cmc": 0, "type_line": "Basic Land"},
            ]
            self.deck = DeckAnalysis(
                user_id=self.user.id,
                deck_name="Selesnya Tokens",
                commander_name="Trostani, Selesnya's Voice",
                color_identity="G,W",
                cards_data=json.dumps(cards_sample),
                stats_json=json.dumps({"total_cards": 100}),
            )
            db.session.add(self.deck)
            db.session.commit()
            self.deck_id = self.deck.id

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id

    def test_add_evaluator_page_get(self):
        """Tests rendering of /add-evaluator page."""
        self._login()
        resp = self.client.get("/add-evaluator")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Tactical Card Add & Secret Lair Suite", resp.data)

    def test_secret_lair_route_renders_suite(self):
        """Tests that /secret-lair renders the unified Add Analysis Suite."""
        self._login()
        resp = self.client.get("/secret-lair")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Tactical Card Add & Secret Lair Suite", resp.data)

    def test_api_add_evaluator_evaluate_cards(self):
        """Tests POST /api/add-evaluator/evaluate with cards input."""
        self._login()
        payload = {
            "mode": "cards",
            "cards": ["Esper Sentinel", "Smothering Tithe"],
            "deck_ids": [self.deck_id],
            "use_gemini": False,  # Algorithmic test
            "save_to_history": True,
        }
        resp = self.client.post("/api/add-evaluator/evaluate", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("mode"), "cards")
        self.assertIn("card_matrix", data)
        self.assertIn("deck_breakdowns", data)
        self.assertIsNotNone(data.get("id"))

        # Verify persisted in database
        with self.app.app_context():
            saved = db.session.get(CardAddAnalysis, data["id"])
            self.assertIsNotNone(saved)
            self.assertIn("Esper Sentinel", saved.title)

    def test_api_add_evaluator_history_get_and_delete(self):
        """Tests history retrieval and deletion."""
        self._login()
        with self.app.app_context():
            rec = CardAddAnalysis(
                user_id=self.user_id,
                title="Test Evaluation",
                source_type="card_list",
                cards_data=json.dumps([{"name": "Sol Ring"}]),
                analysis_json=json.dumps({"card_matrix": []}),
            )
            db.session.add(rec)
            db.session.commit()
            rec_id = rec.id

        # GET history list
        resp = self.client.get("/api/add-evaluator/history")
        self.assertEqual(resp.status_code, 200)
        hist = resp.get_json()
        self.assertIsInstance(hist, list)
        self.assertTrue(any(h["id"] == rec_id for h in hist))

        # GET single record
        resp_item = self.client.get(f"/api/add-evaluator/history/{rec_id}")
        self.assertEqual(resp_item.status_code, 200)
        self.assertEqual(resp_item.get_json()["id"], rec_id)

        # DELETE record
        resp_del = self.client.delete(f"/api/add-evaluator/history/{rec_id}")
        self.assertEqual(resp_del.status_code, 200)
        self.assertTrue(resp_del.get_json().get("success"))

        # Verify deletion
        with self.app.app_context():
            self.assertIsNone(db.session.get(CardAddAnalysis, rec_id))


if __name__ == "__main__":
    unittest.main()
