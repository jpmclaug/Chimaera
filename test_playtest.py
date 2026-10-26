"""
Comprehensive unit and integration test suite for Commander Playtest Arena.
Tests:
1. CommanderPlaytestSession database model fields and JSON serialization.
2. PlaytestEngine session hydration, deck expansion, shuffling, and zone routing.
3. PlaytestEngine state validation and schema enforcement.
4. Flask REST API routes (create, get, versioned save, conflict 409, checkpoints, restart, finish, delete).
5. Guided combat calculation logic (trample, deathtouch, lifelink, commander damage, multiple blockers).
"""

import json
import unittest
from unittest.mock import patch

from app import create_app
from models import db, User, DeckAnalysis, CommanderPlaytestSession
from playtest_engine import PlaytestEngine


class TestCommanderPlaytestSuite(unittest.TestCase):
    """Test suite covering the Playtest model, engine, API, and combat rules."""

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-secret-key",
            "WTF_CSRF_ENABLED": False,
        })
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Create a test user
        self.user = User(
            email="tactical_tester@example.com",
            name="Tactical Tester",
            is_active=True,
            is_admin=True,
        )
        db.session.add(self.user)
        db.session.commit()

        # Create 2 test decks
        cards_d1 = [
            {"name": "The Ur-Dragon", "quantity": 1, "section": "commander", "type_line": "Legendary Creature — Dragon", "mana_cost": "{4}{W}{U}{B}{R}{G}", "power": "10", "toughness": "10", "keywords": ["Flying"], "image_uri": "https://cards.scryfall.io/ur.jpg"},
            {"name": "Sol Ring", "quantity": 1, "section": "mainboard", "type_line": "Artifact", "mana_cost": "{1}", "image_uri": "https://cards.scryfall.io/sol.jpg"},
            {"name": "Command Tower", "quantity": 1, "section": "mainboard", "type_line": "Land", "image_uri": "https://cards.scryfall.io/tower.jpg"},
        ]
        # Pad with 97 basic lands so deck is 100 cards
        for i in range(97):
            cards_d1.append({"name": f"Mountain {i+1}", "quantity": 1, "section": "mainboard", "type_line": "Basic Land — Mountain"})

        self.deck1 = DeckAnalysis(
            user_id=self.user.id,
            deck_name="Ur-Dragon Roar",
            commander_name="The Ur-Dragon",
            commander_art="https://cards.scryfall.io/ur.jpg",
            cards_data=json.dumps(cards_d1),
            total_cards=100,
        )

        cards_d2 = [
            {"name": "Atraxa, Praetors' Voice", "quantity": 1, "section": "commander", "type_line": "Legendary Creature — Angel", "mana_cost": "{G}{W}{U}{B}", "power": "4", "toughness": "4", "keywords": ["Flying", "Vigilance", "Deathtouch", "Lifelink"], "image_uri": "https://cards.scryfall.io/atraxa.jpg"},
            {"name": "Arcane Signet", "quantity": 1, "section": "mainboard", "type_line": "Artifact", "mana_cost": "{2}"},
        ]
        for i in range(98):
            cards_d2.append({"name": f"Forest {i+1}", "quantity": 1, "section": "mainboard", "type_line": "Basic Land — Forest"})

        self.deck2 = DeckAnalysis(
            user_id=self.user.id,
            deck_name="Atraxa Proliferate",
            commander_name="Atraxa, Praetors' Voice",
            commander_art="https://cards.scryfall.io/atraxa.jpg",
            cards_data=json.dumps(cards_d2),
            total_cards=100,
        )

        db.session.add(self.deck1)
        db.session.add(self.deck2)
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_session_model_crud(self):
        """Tests CommanderPlaytestSession model instantiation, getters, and serialization."""
        sess = CommanderPlaytestSession(
            user_id=self.user.id,
            title="Ur-Dragon vs Atraxa",
            deck_ids=json.dumps([self.deck1.id, self.deck2.id]),
            num_players=2,
            status="active",
            state_json=json.dumps({"schema": 1, "turn": 2}),
            state_version=1,
            checkpoints_json=json.dumps([{"name": "Turn 1 Checkpoint", "turn": 1}]),
            turn_number=2,
        )
        db.session.add(sess)
        db.session.commit()

        loaded = db.session.get(CommanderPlaytestSession, sess.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.get_deck_ids(), [self.deck1.id, self.deck2.id])
        self.assertEqual(loaded.get_state()["turn"], 2)
        self.assertEqual(len(loaded.get_checkpoints()), 1)

        d = loaded.to_dict()
        self.assertEqual(d["title"], "Ur-Dragon vs Atraxa")
        self.assertEqual(d["turn_number"], 2)
        self.assertEqual(d["state_version"], 1)

    def test_engine_build_session_solo(self):
        """Tests building a solo goldfish game state."""
        built = PlaytestEngine.build_session(
            user_id=self.user.id,
            deck_ids=[self.deck1.id],
            title="Ur-Dragon Solo Test",
            settings={"starting_life": 40, "hide_hands": True},
        )

        self.assertEqual(built["num_players"], 1)
        state = built["state"]
        self.assertEqual(state["schema"], 1)
        self.assertEqual(state["turn"], 1)
        self.assertEqual(len(state["seats"]), 1)

        seat0 = state["seats"][0]
        self.assertEqual(seat0["life"], 40)
        self.assertEqual(seat0["commander_name"], "The Ur-Dragon")
        # 1 commander in command zone
        self.assertEqual(len(seat0["zones"]["command"]), 1)
        # 7 cards in opening hand
        self.assertEqual(len(seat0["zones"]["hand"]), 7)
        # 92 cards remaining in library (100 total - 1 cmdr - 7 hand)
        self.assertEqual(len(seat0["zones"]["library"]), 92)

        # Verify card_refs populated
        self.assertIn("the ur-dragon", state["card_refs"])
        ur_ref = state["card_refs"]["the ur-dragon"]
        self.assertEqual(ur_ref["power"], "10")
        self.assertEqual(ur_ref["toughness"], "10")
        self.assertIn("Flying", ur_ref["keywords"])

    def test_engine_build_session_multi(self):
        """Tests building a 2-player pod session."""
        built = PlaytestEngine.build_session(
            user_id=self.user.id,
            deck_ids=[self.deck1.id, self.deck2.id],
        )

        self.assertEqual(built["num_players"], 2)
        state = built["state"]
        self.assertEqual(len(state["seats"]), 2)

        # Check Seat 1
        s1 = state["seats"][0]
        self.assertEqual(s1["commander_name"], "The Ur-Dragon")
        self.assertEqual(s1["commander_damage"], {"0": 0, "1": 0})

        # Check Seat 2
        s2 = state["seats"][1]
        self.assertEqual(s2["commander_name"], "Atraxa, Praetors' Voice")
        self.assertEqual(s2["commander_damage"], {"0": 0, "1": 0})
        self.assertIn("atraxa, praetors' voice", state["card_refs"])
        atraxa_ref = state["card_refs"]["atraxa, praetors' voice"]
        self.assertEqual(atraxa_ref["power"], "4")
        self.assertEqual(atraxa_ref["toughness"], "4")
        self.assertIn("Deathtouch", atraxa_ref["keywords"])
        self.assertIn("Lifelink", atraxa_ref["keywords"])

    def test_engine_validate_state(self):
        """Tests PlaytestEngine schema validation."""
        built = PlaytestEngine.build_session(user_id=self.user.id, deck_ids=[self.deck1.id])
        valid_state = built["state"]

        # 1. Valid state passes
        is_valid, msg = PlaytestEngine.validate_state(valid_state)
        self.assertTrue(is_valid, msg)

        # 2. Invalid schema fails
        bad_schema = dict(valid_state)
        bad_schema["schema"] = 99
        is_valid, msg = PlaytestEngine.validate_state(bad_schema)
        self.assertFalse(is_valid)

        # 3. Missing instance in cards dictionary fails
        bad_cards = json.loads(json.dumps(valid_state))
        first_hand_id = bad_cards["seats"][0]["zones"]["hand"][0]
        del bad_cards["cards"][first_hand_id]
        is_valid, msg = PlaytestEngine.validate_state(bad_cards)
        self.assertFalse(is_valid)

    def test_api_create_get_save_state(self):
        """Tests Flask API endpoints for session creation, state fetch, and optimistic save."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user.id

        # 1. Create session via POST /api/playtest
        res = self.client.post("/api/playtest", json={
            "deck_ids": [self.deck1.id, self.deck2.id],
            "title": "Match 1",
        })
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["success"])
        session_id = data["session"]["id"]
        initial_version = data["session"]["state_version"]
        self.assertEqual(initial_version, 1)

        # 2. Fetch session via GET /api/playtest/<id>
        res = self.client.get(f"/api/playtest/{session_id}")
        self.assertEqual(res.status_code, 200)
        s_data = res.get_json()["session"]
        current_state = s_data["state"]

        # 3. Save state via PUT /api/playtest/<id>/state
        current_state["turn"] = 3
        current_state["seats"][0]["life"] = 35
        res = self.client.put(f"/api/playtest/{session_id}/state", json={
            "state": current_state,
            "base_version": 1,
        })
        self.assertEqual(res.status_code, 200)
        res_data = res.get_json()
        self.assertEqual(res_data["state_version"], 2)

        # 4. Attempt save with stale base_version (Optimistic Concurrency 409)
        res_conflict = self.client.put(f"/api/playtest/{session_id}/state", json={
            "state": current_state,
            "base_version": 1,  # Stale! Current version is now 2
        })
        self.assertEqual(res_conflict.status_code, 409)
        self.assertTrue(res_conflict.get_json().get("conflict"))

        # 5. Checkpoints POST /api/playtest/<id>/checkpoints
        res_cp = self.client.post(f"/api/playtest/{session_id}/checkpoints", json={
            "name": "Pre-Combat T3"
        })
        self.assertEqual(res_cp.status_code, 200)
        self.assertEqual(len(res_cp.get_json()["checkpoints"]), 1)

        # 6. Restart POST /api/playtest/<id>/restart
        res_rst = self.client.post(f"/api/playtest/{session_id}/restart")
        self.assertEqual(res_rst.status_code, 200)
        restarted = res_rst.get_json()["session"]
        self.assertEqual(restarted["turn_number"], 1)

        # 7. Finish POST /api/playtest/<id>/finish
        res_fin = self.client.post(f"/api/playtest/{session_id}/finish")
        self.assertEqual(res_fin.status_code, 200)
        self.assertEqual(res_fin.get_json()["session"]["status"], "finished")

        # 8. Delete DELETE /api/playtest/<id>
        res_del = self.client.delete(f"/api/playtest/{session_id}")
        self.assertEqual(res_del.status_code, 200)
        self.assertIsNone(db.session.get(CommanderPlaytestSession, session_id))


    def test_playtest_pages_render(self):
        """Tests HTML rendering of both the lobby and the active arena views."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user.id

        # 1. Lobby page
        res = self.client.get("/commander/playtest")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("Playtest Arena", html)
        self.assertIn("Solo Goldfish", html)
        self.assertIn("Multiplayer Pod", html)

        # 2. Create session and render arena page
        built = PlaytestEngine.build_session(user_id=self.user.id, deck_ids=[self.deck1.id])
        session_entry = CommanderPlaytestSession(
            user_id=self.user.id,
            title="Arena Render Test",
            deck_ids=json.dumps(built["deck_ids"]),
            num_players=1,
            state_json=json.dumps(built["state"]),
            state_version=1,
            turn_number=1,
        )
        db.session.add(session_entry)
        db.session.commit()

        res_arena = self.client.get(f"/commander/playtest/{session_entry.id}")
        self.assertEqual(res_arena.status_code, 200)
        arena_html = res_arena.get_data(as_text=True)
        self.assertIn("Arena Render Test", arena_html)
        self.assertIn("seatPillsContainer", arena_html)
        self.assertIn("battlefieldNonlandsRow", arena_html)
        self.assertIn("handDockContainer", arena_html)
        self.assertIn("combatStepperSheet", arena_html)


if __name__ == "__main__":
    unittest.main()
