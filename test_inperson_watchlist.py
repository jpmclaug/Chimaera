"""
Unit & Integration Test Suite for In-Person Watchlist View.
Tests metadata properties (color_bucket, primary_card_type, short_type_line),
schema migration, Scryfall metadata enrichment, the in-person acquire endpoint,
and UI template rendering for the In-Person view (Art Grid & Checklist).
"""

import json
import unittest
from unittest.mock import patch

from app import create_app
from models import (
    db,
    User,
    AllowedEmail,
    WatchlistItem,
    UserInventoryCard,
    VendorPrice,
    ActivityLog,
)


class InPersonWatchlistTestSuite(unittest.TestCase):
    """Comprehensive tests for In-Person Watchlist features."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "DISCORD_WEBHOOK_URL": "",
            "ADMIN_EMAIL": "inperson_admin@chimera.local",
        })
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        with self.app.app_context():
            db.session.query(VendorPrice).delete()
            db.session.query(WatchlistItem).delete()
            db.session.query(UserInventoryCard).delete()
            db.session.query(ActivityLog).delete()
            db.session.query(User).delete()
            db.session.query(AllowedEmail).delete()
            db.session.commit()

    def login_as(self, email="inperson_shopper@chimera.local", is_admin=False):
        """Authenticates client as test user."""
        with self.app.app_context():
            allowed = AllowedEmail.get_by_email(email)
            if not allowed:
                allowed = AllowedEmail(email=email, is_admin=is_admin, notes="Shopper User", added_by="TestSuite")
                db.session.add(allowed)
                db.session.commit()
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email, name="In-Person Shopper", is_admin=is_admin, is_active=True)
                db.session.add(user)
                db.session.commit()
            user_id = user.id

        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
            sess["is_admin"] = is_admin
        return user_id

    def test_model_metadata_and_computed_properties(self):
        """Verifies color_bucket, primary_card_type, and short_type_line properties on WatchlistItem."""
        user_id = self.login_as()

        with self.app.app_context():
            cards_data = [
                # White creature
                {
                    "name": "Esper Sentinel",
                    "type_line": "Artifact Creature — Human Soldier",
                    "colors": "W",
                    "color_identity": "W",
                    "mana_cost": "{W}",
                    "cmc": 1.0,
                    "expected_type": "Creature",
                    "expected_bucket": "W",
                    "expected_short_type": "Artifact Creature",
                },
                # Blue Instant
                {
                    "name": "Counterspell",
                    "type_line": "Instant",
                    "colors": "U",
                    "color_identity": "U",
                    "mana_cost": "{U}{U}",
                    "cmc": 2.0,
                    "expected_type": "Instant",
                    "expected_bucket": "U",
                    "expected_short_type": "Instant",
                },
                # Black Sorcery
                {
                    "name": "Demonic Tutor",
                    "type_line": "Sorcery",
                    "colors": "B",
                    "color_identity": "B",
                    "mana_cost": "{1}{B}",
                    "cmc": 2.0,
                    "expected_type": "Sorcery",
                    "expected_bucket": "B",
                    "expected_short_type": "Sorcery",
                },
                # Red Planeswalker
                {
                    "name": "Chandra, Torch of Defiance",
                    "type_line": "Legendary Planeswalker — Chandra",
                    "colors": "R",
                    "color_identity": "R",
                    "mana_cost": "{2}{R}{R}",
                    "cmc": 4.0,
                    "expected_type": "Planeswalker",
                    "expected_bucket": "R",
                    "expected_short_type": "Legendary Planeswalker",
                },
                # Green Enchantment
                {
                    "name": "Sylvan Library",
                    "type_line": "Enchantment",
                    "colors": "G",
                    "color_identity": "G",
                    "mana_cost": "{1}{G}",
                    "cmc": 2.0,
                    "expected_type": "Enchantment",
                    "expected_bucket": "G",
                    "expected_short_type": "Enchantment",
                },
                # Multicolor Creature
                {
                    "name": "Atraxa, Praetors' Voice",
                    "type_line": "Legendary Creature — Phyrexian Angel Horror",
                    "colors": "G,U,W,B",
                    "color_identity": "W,U,B,G",
                    "mana_cost": "{G}{W}{U}{B}",
                    "cmc": 4.0,
                    "expected_type": "Creature",
                    "expected_bucket": "Multicolor",
                    "expected_short_type": "Legendary Creature",
                },
                # Colorless Artifact
                {
                    "name": "Sol Ring",
                    "type_line": "Artifact",
                    "colors": "",
                    "color_identity": "",
                    "mana_cost": "{1}",
                    "cmc": 1.0,
                    "expected_type": "Artifact",
                    "expected_bucket": "Colorless",
                    "expected_short_type": "Artifact",
                },
                # Land
                {
                    "name": "Command Tower",
                    "type_line": "Land",
                    "colors": "",
                    "color_identity": "",
                    "mana_cost": "",
                    "cmc": 0.0,
                    "expected_type": "Land",
                    "expected_bucket": "Land",
                    "expected_short_type": "Land",
                },
            ]

            for d in cards_data:
                item = WatchlistItem(
                    user_id=user_id,
                    name=d["name"],
                    target_price=10.0,
                    type_line=d["type_line"],
                    colors=d["colors"],
                    color_identity=d["color_identity"],
                    mana_cost=d["mana_cost"],
                    cmc=d["cmc"],
                )
                db.session.add(item)
                db.session.flush()

                self.assertEqual(item.primary_card_type, d["expected_type"])
                self.assertEqual(item.color_bucket, d["expected_bucket"])
                self.assertEqual(item.short_type_line, d["expected_short_type"])

                # Check to_dict serialization
                serialized = item.to_dict()
                self.assertEqual(serialized["primary_card_type"], d["expected_type"])
                self.assertEqual(serialized["color_bucket"], d["expected_bucket"])
                self.assertEqual(serialized["short_type_line"], d["expected_short_type"])
                self.assertEqual(serialized["mana_cost"], d["mana_cost"])
                self.assertEqual(serialized["cmc"], d["cmc"])

            db.session.rollback()

    def test_acquire_card_endpoint_with_inventory_ingestion(self):
        """Verifies POST /api/watchlist/acquire/<id> removes card and ingests it into collection binder."""
        user_id = self.login_as()

        with self.app.app_context():
            card = WatchlistItem(
                user_id=user_id,
                name="Mox Diamond",
                set_code="STH",
                collector_number="138",
                finish="nonfoil",
                target_price=500.0,
                image_uri="https://cards.scryfall.io/large/front/4/f/4fb20a91-4475-4089-a212-0453d7170889.jpg",
            )
            db.session.add(card)
            db.session.commit()
            card_id = card.id

        # Call acquire endpoint with add_to_inventory = True
        payload = {
            "add_to_inventory": True,
            "binder_name": "Showcase Trades",
            "purchase_price": 485.50,
            "condition": "Near Mint",
        }
        resp = self.client.post(
            f"/api/watchlist/acquire/{card_id}",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["card_name"], "Mox Diamond")
        self.assertIsNotNone(data["inventory_item_id"])

        with self.app.app_context():
            # Watchlist item should be deleted
            item = WatchlistItem.query.get(card_id)
            self.assertIsNone(item)

            # Inventory card should be created
            inv = UserInventoryCard.query.filter_by(user_id=user_id, name="Mox Diamond").first()
            self.assertIsNotNone(inv)
            self.assertEqual(inv.binder_name, "Showcase Trades")
            self.assertEqual(inv.set_code, "STH")
            self.assertEqual(inv.collector_number, "138")
            self.assertEqual(inv.foil, "nonfoil")
            self.assertEqual(inv.condition, "Near Mint")
            self.assertEqual(inv.purchase_price, 485.50)
            self.assertEqual(inv.quantity, 1)

            # Activity log should be recorded
            log = ActivityLog.query.filter_by(action="CARD_ACQUIRED_IN_PERSON", user_id=user_id).first()
            self.assertIsNotNone(log)
            self.assertIn("Mox Diamond", log.details)
            self.assertIn("Showcase Trades", log.details)

    def test_acquire_card_endpoint_without_inventory_ingestion(self):
        """Verifies POST /api/watchlist/acquire/<id> removes card without creating inventory entry."""
        user_id = self.login_as()

        with self.app.app_context():
            card = WatchlistItem(
                user_id=user_id,
                name="Lightning Bolt",
                set_code="2ED",
                collector_number="162",
                finish="nonfoil",
                target_price=20.0,
            )
            db.session.add(card)
            db.session.commit()
            card_id = card.id

        payload = {
            "add_to_inventory": False,
        }
        resp = self.client.post(
            f"/api/watchlist/acquire/{card_id}",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIsNone(data["inventory_item_id"])

        with self.app.app_context():
            self.assertIsNone(WatchlistItem.query.get(card_id))
            inv = UserInventoryCard.query.filter_by(user_id=user_id, name="Lightning Bolt").first()
            self.assertIsNone(inv)

    def test_acquire_card_endpoint_unauthorized_and_missing(self):
        """Verifies security controls and error responses on the acquire endpoint."""
        user_a = self.login_as(email="usera@chimera.local")
        user_b = self.login_as(email="userb@chimera.local")

        with self.app.app_context():
            card = WatchlistItem(
                user_id=user_a,
                name="Force of Will",
                target_price=60.0,
            )
            db.session.add(card)
            db.session.commit()
            card_id = card.id

        # User B attempts to acquire User A's card -> 404 / Unauthorized
        resp = self.client.post(
            f"/api/watchlist/acquire/{card_id}",
            data=json.dumps({"add_to_inventory": False}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

        # Nonexistent card ID
        resp = self.client.post(
            "/api/watchlist/acquire/999999",
            data=json.dumps({"add_to_inventory": False}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_index_renders_inperson_view_and_controls(self):
        """Verifies that the Watchlist index page renders the In-Person button, controls bar, art grid, checklist, and acquire modal."""
        user_id = self.login_as()

        with self.app.app_context():
            card = WatchlistItem(
                user_id=user_id,
                name="Rhystic Study",
                set_code="WOT",
                collector_number="25",
                finish="foil",
                target_price=35.0,
                type_line="Enchantment",
                colors="U",
                color_identity="U",
                mana_cost="{2}{U}",
                cmc=3.0,
                tag="Commander Staples",
            )
            db.session.add(card)
            db.session.commit()

        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # View switcher button
        self.assertIn("btn-view-inperson", html)
        self.assertIn("In-Person", html)

        # In-Person Controls sub-toolbar
        self.assertIn("inperson-controls-bar", html)
        self.assertIn("btn-inperson-focus", html)
        self.assertIn("btn-inperson-sub-art", html)
        self.assertIn("btn-inperson-sub-checklist", html)
        self.assertIn("inperson-group-select", html)

        # Color filter pills
        self.assertIn("inperson-color-pill", html)
        self.assertIn("White (W)", html)
        self.assertIn("Blue (U)", html)

        # Type filter pills
        self.assertIn("inperson-type-pill", html)
        self.assertIn("Creature", html)
        self.assertIn("Instant", html)

        # Container & Sub-Views
        self.assertIn("watchlist-inperson", html)
        self.assertIn("inperson-art-grid", html)
        self.assertIn("inperson-checklist", html)

        # Data attributes on cards
        self.assertIn("data-color-bucket=\"u\"", html)
        self.assertIn("data-primary-type=\"enchantment\"", html)
        self.assertIn("data-mana-cost=\"{2}{U}\"", html)
        self.assertIn("data-cmc=\"3.0\"", html)

        # Acquire Modal
        self.assertIn("modal-inperson-acquire", html)
        self.assertIn("acquire-add-inventory-check", html)
        self.assertIn("acquire-binder-name-input", html)
        self.assertIn("btn-submit-acquire", html)

    def test_metadata_backfill_on_index_load(self):
        """Verifies that visiting the index route automatically backfills missing metadata fields on existing cards."""
        user_id = self.login_as()

        with self.app.app_context():
            card = WatchlistItem(
                user_id=user_id,
                name="Tivit, Seller of Secrets",
                type_line=None,
                colors=None,
                color_identity=None,
                mana_cost=None,
                cmc=None,
            )
            db.session.add(card)
            db.session.commit()
            card_id = card.id

        mock_scryfall_data = {
            "tivit, seller of secrets": {
                "type_line": "Legendary Creature — Sphinx Rogue",
                "colors": ["W", "U", "B"],
                "color_identity": ["W", "U", "B"],
                "mana_cost": "{3}{W}{U}{B}",
                "cmc": 6.0,
            }
        }

        with patch("providers.ScryfallProvider.get_cards_collection", return_value=(mock_scryfall_data, [])):
            resp = self.client.get("/")
            self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            updated_card = db.session.get(WatchlistItem, card_id)
            self.assertIsNotNone(updated_card)
            self.assertEqual(updated_card.type_line, "Legendary Creature — Sphinx Rogue")
            self.assertEqual(updated_card.primary_card_type, "Creature")
            self.assertEqual(updated_card.color_bucket, "Multicolor")
            self.assertEqual(updated_card.colors, "W,U,B")
            self.assertEqual(updated_card.mana_cost, "{3}{W}{U}{B}")
            self.assertEqual(updated_card.cmc, 6.0)

    def test_schema_migration_idempotent(self):
        """Verifies that _migrate_db_schema runs idempotently without crashing or dropping columns."""
        from app import _migrate_db_schema

        with self.app.app_context():
            # Run migration on already existing schema
            _migrate_db_schema(self.app)

            # Columns should exist and be queryable
            item = WatchlistItem(
                user_id=1,
                name="Schema Test Card",
                type_line="Instant",
                colors="U",
                color_identity="U",
                mana_cost="{U}",
                cmc=1.0,
            )
            db.session.add(item)
            db.session.commit()

            fetched = db.session.get(WatchlistItem, item.id)
            self.assertEqual(fetched.type_line, "Instant")
            self.assertEqual(fetched.color_bucket, "U")


if __name__ == "__main__":
    unittest.main()
