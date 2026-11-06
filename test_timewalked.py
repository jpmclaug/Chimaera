import json
import unittest
from app import create_app
from models import db, User, AllowedEmail, DeckAnalysis


class TestTimewalkedSection(unittest.TestCase):
    """Integration tests for the Timewalked Commander Bracket & Feels-Like Analyzer section."""

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-timewalked-secret",
            "SCHEDULER_AUTO_START": False,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            allowed = AllowedEmail(
                email="pilot@chimaera.mtg",
                is_admin=True,
                notes="Timewalked Test Pilot",
                added_by="TestSuite",
            )
            db.session.add(allowed)
            user = User(
                email="pilot@chimaera.mtg",
                name="Pilot",
                is_admin=True,
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

            # Seed a saved Commander deck in the Vault
            vault_cards = [
                {"name": "Vito, Thorn of the Dusk Rose", "quantity": 1, "section": "commander", "cmc": 3, "type_line": "Legendary Creature — Vampire Cleric"},
                {"name": "Sol Ring", "quantity": 1, "section": "mainboard", "cmc": 1, "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}."},
                {"name": "Demonic Tutor", "quantity": 1, "section": "mainboard", "cmc": 2, "type_line": "Sorcery", "oracle_text": "Search your library for a card."},
                {"name": "Smothering Tithe", "quantity": 1, "section": "mainboard", "cmc": 4, "type_line": "Enchantment", "oracle_text": "Create a Treasure token."},
                {"name": "Exquisite Blood", "quantity": 1, "section": "mainboard", "cmc": 5, "type_line": "Enchantment", "oracle_text": "Whenever an opponent loses life, you gain that much life."},
                {"name": "Sanguine Bond", "quantity": 1, "section": "mainboard", "cmc": 5, "type_line": "Enchantment", "oracle_text": "Whenever you gain life, target opponent loses that much life."},
                {"name": "Swamp", "quantity": 35, "section": "mainboard", "cmc": 0, "type_line": "Basic Land — Swamp"},
            ]
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Vault Vito Lifedrain",
                commander_name="Vito, Thorn of the Dusk Rose",
                cards_data=json.dumps(vault_cards),
                total_cards=41,
                color_identity="B",
            )
            db.session.add(deck)
            db.session.commit()
            self.deck_id = deck.id

        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["user_email"] = "pilot@chimaera.mtg"
            sess["is_admin"] = True

    def test_timewalked_page_renders_and_navigation(self):
        """Verify GET /timewalked renders with navigation links and Vault decks."""
        res = self.client.get("/timewalked")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Timewalked Commander Analyzer", html)
        self.assertIn("ON PAPER BRACKET", html)
        self.assertIn("FEELS LIKE SCORE", html)
        self.assertIn("Vault Vito Lifedrain", html)
        self.assertIn('href="/timewalked"', html)

    def test_analyze_demo_decks_offline(self):
        """Verify demo mode classifies Bracket 2, Bracket 3, and Bracket 5 sample decks."""
        # Demo 0: Omnath (Bracket 2 on paper, plays higher)
        r0 = self.client.post("/api/timewalked/analyze", json={"mode": "demo", "demo_index": 0, "offline": True})
        self.assertEqual(r0.status_code, 200)
        d0 = r0.get_json()
        self.assertTrue(d0["success"])
        self.assertEqual(d0["result"]["on_paper_bracket"], 2)
        self.assertGreaterEqual(d0["result"]["feels_like_score"], 2.5)
        self.assertIn("packages", d0["result"])
        self.assertGreaterEqual(len(d0["result"]["packages"]["ramp"]), 8)

        # Demo 1: Vito (Bracket 3 on paper)
        r1 = self.client.post("/api/timewalked/analyze", json={"mode": "demo", "demo_index": 1, "offline": True})
        self.assertEqual(r1.status_code, 200)
        d1 = r1.get_json()
        self.assertEqual(d1["result"]["on_paper_bracket"], 3)
        self.assertGreaterEqual(len(d1["result"]["game_changers"]), 3)
        self.assertGreaterEqual(len(d1["result"]["combos"]), 1)

        # Demo 2: Urza cEDH (Bracket 5 on paper)
        r2 = self.client.post("/api/timewalked/analyze", json={"mode": "demo", "demo_index": 2, "offline": True})
        self.assertEqual(r2.status_code, 200)
        d2 = r2.get_json()
        self.assertEqual(d2["result"]["on_paper_bracket"], 5)
        self.assertGreaterEqual(d2["result"]["feels_like_score"], 4.4)

    def test_analyze_text_and_what_if_tuner(self):
        """Verify raw decklist classification and What-If card exclusion simulation."""
        raw_list = """
        Commander
        1 Vito, Thorn of the Dusk Rose

        Deck
        1 Sol Ring
        1 Demonic Tutor
        1 Smothering Tithe
        1 Exquisite Blood
        1 Sanguine Bond
        35 Swamp
        """
        # 1. Baseline classification -> Bracket 3 (2 Game Changers + 2-card combo)
        res = self.client.post("/api/timewalked/analyze", json={
            "mode": "text_or_url",
            "source": raw_list,
            "deck_name": "Test Vito",
            "offline": True,
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["result"]["on_paper_bracket"], 3)
        self.assertIsNone(data["baseline"])

        # 2. What-If simulation: exclude Demonic Tutor, Smothering Tithe, and Exquisite Blood -> drops to Bracket 2!
        res_whatif = self.client.post("/api/timewalked/analyze", json={
            "mode": "text_or_url",
            "source": raw_list,
            "deck_name": "Test Vito",
            "offline": True,
            "excluded_cards": ["Demonic Tutor", "Smothering Tithe", "Exquisite Blood"],
        })
        self.assertEqual(res_whatif.status_code, 200)
        w_data = res_whatif.get_json()
        self.assertIsNotNone(w_data["baseline"])
        self.assertEqual(w_data["baseline"]["on_paper_bracket"], 3)
        self.assertEqual(w_data["result"]["on_paper_bracket"], 2)
        self.assertLess(w_data["result"]["feels_like_score"], w_data["baseline"]["feels_like_score"])

    def test_vault_deck_and_fleet_scan(self):
        """Verify vault_deck analysis and /api/timewalked/fleet scan."""
        res = self.client.post("/api/timewalked/analyze", json={
            "mode": "vault_deck",
            "deck_id": self.deck_id,
            "offline": True,
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["result"]["deck_name"], "Vault Vito Lifedrain")
        self.assertEqual(data["result"]["on_paper_bracket"], 3)

        fleet_res = self.client.post("/api/timewalked/fleet", json={"offline": True})
        self.assertEqual(fleet_res.status_code, 200)
        fleet_data = fleet_res.get_json()
        self.assertTrue(fleet_data["success"])
        self.assertEqual(fleet_data["total_scanned"], 1)
        self.assertEqual(fleet_data["fleet"][0]["deck_id"], self.deck_id)
        self.assertEqual(fleet_data["fleet"][0]["on_paper_bracket"], 3)


if __name__ == "__main__":
    unittest.main()
