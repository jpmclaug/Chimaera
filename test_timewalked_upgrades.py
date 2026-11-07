import json
import unittest
from unittest.mock import MagicMock

from app import create_app
from models import AllowedEmail, DeckAnalysis, User, UserInventoryCard, db
from timewalked_analyzer.upgrades import (
    DEFAULT_RATIO_TARGETS,
    SuggestedUpgradeEngine,
    classify_timewalked_categories,
    compute_ratio_breakdown,
    ratio_state_for,
)


class TestTimewalkedUpgradesEngine(unittest.TestCase):
    """Unit tests for SuggestedUpgradeEngine, ratio categorization, and ranking logic."""

    def test_ratio_state_thresholds(self):
        # target 10: >=10 met, 8-9 close (80%), <8 short
        self.assertEqual(ratio_state_for(10, 10), "met")
        self.assertEqual(ratio_state_for(12, 10), "met")
        self.assertEqual(ratio_state_for(8, 10), "close")
        self.assertEqual(ratio_state_for(9, 10), "close")
        self.assertEqual(ratio_state_for(7, 10), "short")
        self.assertEqual(ratio_state_for(0, 10), "short")

    def test_classify_timewalked_categories(self):
        ramp_rock = {"name": "Arcane Signet", "type_line": "Artifact", "oracle_text": "{T}: Add one mana of any color in your commander's color identity.", "cmc": 2.0}
        cats = classify_timewalked_categories(ramp_rock)
        self.assertIn("ramp", cats)

        draw_spell = {"name": "Rhystic Study", "type_line": "Enchantment", "oracle_text": "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.", "cmc": 3.0}
        cats2 = classify_timewalked_categories(draw_spell)
        self.assertIn("card_advantage", cats2)

        protection_spell = {"name": "Heroic Intervention", "type_line": "Instant", "oracle_text": "Permanents you control gain hexproof and indestructible until end of turn.", "cmc": 2.0}
        cats3 = classify_timewalked_categories(protection_spell)
        self.assertIn("protection", cats3)

        land = {"name": "Command Tower", "type_line": "Land", "oracle_text": "{T}: Add one mana of any color in your commander's color identity.", "cmc": 0.0}
        cats4 = classify_timewalked_categories(land)
        self.assertIn("lands", cats4)

    def test_suggested_upgrade_ranking_and_binder_match(self):
        # Mock EDHREC
        fake_edhrec = {
            "num_decks": 12000,
            "card_synergies": {
                "blood artist": {"name": "Blood Artist", "inclusion_percent": 75.0},
                "sol ring": {"name": "Sol Ring", "inclusion_percent": 90.0},
                "exquisite blood": {"name": "Exquisite Blood", "inclusion_percent": 60.0},
                "bad bulk": {"name": "Bad Bulk", "inclusion_percent": 4.0},
            },
        }

        # Mock Scryfall
        mock_scryfall = MagicMock()
        mock_scryfall.resolve_cards.side_effect = lambda names: {
            n: {
                "name": n,
                "type_line": "Creature — Vampire" if n in ("Vito, Thorn of the Dusk Rose", "Blood Artist") else ("Enchantment" if n in ("Exquisite Blood", "Sanguine Bond") else "Artifact"),
                "cmc": 2.0 if n == "Blood Artist" else (5.0 if n in ("Exquisite Blood", "Sanguine Bond") else 1.0),
                "oracle_text": "Whenever a creature dies, target player loses 1 life and you gain 1 life." if n == "Blood Artist" else ("Whenever you gain life, target opponent loses that much life." if n == "Sanguine Bond" else "Whenever an opponent loses life, you gain that much life."),
                "price_usd": 3.50 if n == "Blood Artist" else (25.0 if n == "Exquisite Blood" else 1.50),
                "legalities": {"commander": "legal"},
            }
            for n in names
        }

        # Mock Spellbook showing deck has Sanguine Bond and is missing Exquisite Blood
        mock_spellbook = MagicMock()
        mock_spellbook.find_combos.return_value = {
            "source": "mock_spellbook",
            "included": [],
            "almost_included_count": 1,
            "almost_included": [
                {
                    "cards": ["Sanguine Bond", "Exquisite Blood"],
                    "missing": ["Exquisite Blood"],
                    "produces": ["Infinite Drain / Win"],
                    "bracket_tag": "3",
                }
            ],
        }

        engine = SuggestedUpgradeEngine(
            edhrec_fetcher=lambda c, p: fake_edhrec,
            scryfall=mock_scryfall,
            spellbook=mock_spellbook,
        )

        # Deck: Vito commander, Sanguine Bond + some filler
        commanders = ["Vito, Thorn of the Dusk Rose"]
        mainboard = {
            "Sanguine Bond": 1,
            "Bad Filler Card": 1,
            "Swamp": 38,
        }

        # User owns Blood Artist in "Vampire Binder" and has allocated copies in "Other Deck"
        owned_index = {
            "bloodartist": {
                "name": "Blood Artist",
                "qty": 2,
                "binders": {"Vampire Binder"},
                "price_usd": 3.50,
            }
        }
        allocations = {
            "bloodartist": {
                "total_allocated": 1,
                "decks": [{"deck_name": "Edgar Markov Deck", "is_current": False}],
            }
        }

        res = engine.suggest(
            commanders=commanders,
            mainboard=mainboard,
            owned_index=owned_index,
            allocations=allocations,
            on_paper_bracket=2,
            game_changer_count=0,
        )

        self.assertEqual(res["commander"], "Vito, Thorn of the Dusk Rose")
        self.assertGreaterEqual(res["free_count"], 1)
        self.assertGreaterEqual(res["buy_count"], 1)

        # Blood Artist is free because it's in owned_index
        free_names = [f["card_in"] for f in res["free_upgrades"]]
        self.assertIn("Blood Artist", free_names)
        ba_item = next(f for f in res["free_upgrades"] if f["card_in"] == "Blood Artist")
        self.assertTrue(ba_item["is_owned"])
        self.assertIn("Vampire Binder", ba_item["binders"])
        self.assertIn("Edgar Markov Deck", ba_item["in_other_decks"])
        self.assertIsNotNone(ba_item["card_out"])  # Paired cut present

        # Exquisite Blood got combo boost (+25) and is on buy list
        buy_names = [b["card_in"] for b in res["buy_upgrades"]]
        self.assertIn("Exquisite Blood", buy_names)
        eb_item = next(b for f in res["buy_upgrades"] for b in [f] if b["card_in"] == "Exquisite Blood")
        self.assertIn("combo", eb_item["signals"])
        self.assertFalse(eb_item["is_owned"])


class TestTimewalkedUpgradesEndpoint(unittest.TestCase):
    """Integration test for POST /api/timewalked/suggested-upgrades."""

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

            # Add an owned card to collection inventory
            inv_card = UserInventoryCard(
                user_id=user.id,
                name="Sol Ring",
                quantity=1,
                binder_name="Staples Binder",
                price_usd=1.25,
            )
            db.session.add(inv_card)

            # Seed a saved Commander deck
            vault_cards = [
                {"name": "Vito, Thorn of the Dusk Rose", "quantity": 1, "section": "commander", "cmc": 3, "type_line": "Legendary Creature — Vampire Cleric"},
                {"name": "Exquisite Blood", "quantity": 1, "section": "mainboard", "cmc": 5, "type_line": "Enchantment"},
                {"name": "Swamp", "quantity": 35, "section": "mainboard", "cmc": 0, "type_line": "Basic Land — Swamp"},
            ]
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Vault Vito Upgrades Test",
                commander_name="Vito, Thorn of the Dusk Rose",
                cards_data=json.dumps(vault_cards),
                total_cards=37,
                color_identity="B",
            )
            db.session.add(deck)
            db.session.commit()
            self.deck_id = deck.id

        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["user_email"] = "pilot@chimaera.mtg"
            sess["is_admin"] = True

    def test_suggested_upgrades_endpoint_offline(self):
        # Test offline mode with mock EDHREC provider attached
        mock_provider = MagicMock()
        mock_provider.get_commander_data.return_value = {
            "num_decks": 5000,
            "card_synergies": {
                "sol ring": {"name": "Sol Ring", "inclusion_percent": 88.0},
                "sanguine bond": {"name": "Sanguine Bond", "inclusion_percent": 65.0},
                "blood artist": {"name": "Blood Artist", "inclusion_percent": 55.0},
            },
        }
        self.app.edhrec_provider = mock_provider

        res = self.client.post("/api/timewalked/suggested-upgrades", json={
            "mode": "vault_deck",
            "deck_id": self.deck_id,
            "offline": False,
        })
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body["success"])
        data = body["data"]

        self.assertIn("free_upgrades", data)
        self.assertIn("buy_upgrades", data)
        self.assertIn("ratios", data)

        # Sol Ring is owned by the user, so it must appear in free_upgrades
        free_names = [u["card_in"] for u in data["free_upgrades"]]
        self.assertIn("Sol Ring", free_names)
        sol_item = next(u for u in data["free_upgrades"] if u["card_in"] == "Sol Ring")
        self.assertTrue(sol_item["is_owned"])
        self.assertIn("Staples Binder", sol_item["binders"])
        self.assertIsNotNone(sol_item["card_out"])


if __name__ == "__main__":
    unittest.main()
