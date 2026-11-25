"""
Tests for treating Card Kingdom like Mighty Meeple for alerts and filtering:
- WatchlistItem model properties (notify_ck_stock, ck_in_stock, card_kingdom_price, to_dict)
- /api/watchlist/toggle-ck-alert/<id>
- /api/watchlist/update-target/<id> with notify_ck_stock
- /api/settings/cadence with notify_ck_stock_enabled
- DealEngine.poll_card dispatching send_discord_ck_stock_alert on out->in stock transition
- Index/Deals/Admin HTML rendering of ck_in_stock filter, data-ck-stock, and CK Alert toggle
- chimera_mcp.get_watchlist ck_in_stock_only filtering
"""

import json
import unittest
from unittest.mock import patch

from app import create_app
from deal_engine import DealEngine
from models import AllowedEmail, SystemSetting, User, VendorPrice, WatchlistItem, db
import chimera_mcp


class CardKingdomAlertsAndFilteringTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/abc",
            "ADMIN_EMAIL": "ck_test_admin@chimera.local",
        })
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        with self.app.app_context():
            db.session.query(VendorPrice).delete()
            db.session.query(WatchlistItem).delete()
            db.session.query(SystemSetting).delete()
            db.session.query(User).delete()
            db.session.query(AllowedEmail).delete()
            db.session.commit()

    def login_as(self, email="ck_user@chimera.local", is_admin=False):
        with self.app.app_context():
            if not AllowedEmail.get_by_email(email):
                db.session.add(AllowedEmail(email=email, is_admin=is_admin, notes="Test", added_by="TestSuite"))
                db.session.commit()
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email, name="CK Tester", is_admin=is_admin, is_active=True)
                db.session.add(user)
                db.session.commit()
            user_id = user.id

        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
            sess["is_admin"] = is_admin
        return user_id

    def test_model_ck_properties_and_to_dict(self):
        user_id = self.login_as()
        with self.app.app_context():
            item = WatchlistItem(user_id=user_id, name="Sol Ring", finish="nonfoil", target_price=2.0)
            db.session.add(item)
            db.session.flush()

            self.assertTrue(item.notify_ck_stock)
            self.assertFalse(item.ck_in_stock)
            self.assertIsNone(item.card_kingdom_price)

            vp = VendorPrice(
                watchlist_id=item.id,
                vendor_name="Card Kingdom",
                price=1.79,
                condition="NM",
                in_stock=True,
                product_url="https://www.cardkingdom.com/mtg/sol-ring",
            )
            db.session.add(vp)
            db.session.commit()

            fetched = db.session.get(WatchlistItem, item.id)
            self.assertTrue(fetched.ck_in_stock)
            self.assertEqual(fetched.card_kingdom_price, 1.79)
            d = fetched.to_dict()
            self.assertTrue(d["notify_ck_stock"])
            self.assertTrue(d["ck_in_stock"])
            self.assertEqual(d["card_kingdom_price"], 1.79)

    def test_toggle_ck_alert_and_update_target_and_cadence(self):
        user_id = self.login_as()
        with self.app.app_context():
            item = WatchlistItem(user_id=user_id, name="Force of Will", finish="nonfoil", target_price=60.0)
            db.session.add(item)
            db.session.commit()
            item_id = item.id

        # Toggle off
        r1 = self.client.post(f"/api/watchlist/toggle-ck-alert/{item_id}")
        self.assertEqual(r1.status_code, 200)
        d1 = r1.get_json()
        self.assertFalse(d1["notify_ck_stock"])
        self.assertIn("disabled", d1["message"].lower())

        # Toggle on
        r2 = self.client.post(f"/api/watchlist/toggle-ck-alert/{item_id}")
        self.assertEqual(r2.status_code, 200)
        d2 = r2.get_json()
        self.assertTrue(d2["notify_ck_stock"])
        self.assertIn("enabled", d2["message"].lower())

        # Update target with notify_ck_stock=False
        r3 = self.client.post(
            f"/api/watchlist/update-target/{item_id}",
            data=json.dumps({"target_price": 55.0, "notify_ck_stock": False}),
            content_type="application/json",
        )
        self.assertEqual(r3.status_code, 200)
        self.assertFalse(r3.get_json()["card"]["notify_ck_stock"])

        # Cadence settings GET/POST
        c1 = self.client.get("/api/settings/telemetry")
        self.assertEqual(c1.status_code, 200)
        self.assertTrue(c1.get_json()["notify_ck_stock_enabled"])

        c2 = self.client.post(
            "/api/settings/cadence",
            data=json.dumps({"notify_ck_stock_enabled": False}),
            content_type="application/json",
        )
        self.assertEqual(c2.status_code, 200)
        self.assertFalse(c2.get_json()["notify_ck_stock_enabled"])

    def test_deal_engine_ck_restock_alert_transition(self):
        user_id = self.login_as()
        engine = DealEngine(app=self.app)
        with self.app.app_context():
            item = WatchlistItem(
                user_id=user_id,
                name="Rhystic Study",
                finish="nonfoil",
                target_price=20.0,
                notify_mm_stock=False,
                notify_ck_stock=True,
            )
            db.session.add(item)
            db.session.commit()

            ck_in_stock = {
                "vendor_name": "Card Kingdom",
                "price": 39.99,
                "condition": "NM",
                "in_stock": True,
                "product_url": "https://www.cardkingdom.com/mtg/rhystic-study",
            }
            tcg_stub = {
                "vendor_name": "TCGplayer",
                "price": 40.00,
                "condition": "Market NM",
                "in_stock": True,
                "product_url": "https://www.tcgplayer.com/rhystic-study",
            }

            with patch.object(engine.scryfall, "get_cheapest_tcgplayer_price", return_value=tcg_stub), \
                 patch.object(engine.mightymeeple, "search_card", return_value=None), \
                 patch.object(engine.ebay, "search_card", return_value=None), \
                 patch.object(engine.cardkingdom_singles, "search_card", return_value=ck_in_stock), \
                 patch.object(engine, "send_discord_ck_stock_alert", return_value=True) as mock_ck_alert:
                # 1st poll: None/Out-of-stock -> In-stock => alert fires
                res1 = engine.poll_card(item, notify=True, fast_mode=True)
                self.assertTrue(res1["ck_in_stock"])
                mock_ck_alert.assert_called_once()

                # 2nd poll: Already in stock -> In-stock => no duplicate alert
                mock_ck_alert.reset_mock()
                res2 = engine.poll_card(item, notify=True, fast_mode=True)
                self.assertTrue(res2["ck_in_stock"])
                mock_ck_alert.assert_not_called()

    def test_index_rendering_and_mcp_filter(self):
        user_id = self.login_as()
        with self.app.app_context():
            i1 = WatchlistItem(user_id=user_id, name="Lightning Bolt", finish="nonfoil", target_price=1.0)
            i2 = WatchlistItem(user_id=user_id, name="Counterspell", finish="nonfoil", target_price=1.0)
            db.session.add_all([i1, i2])
            db.session.flush()
            db.session.add(VendorPrice(watchlist_id=i1.id, vendor_name="Card Kingdom", price=1.49, in_stock=True))
            db.session.add(VendorPrice(watchlist_id=i2.id, vendor_name="Card Kingdom", price=1.49, in_stock=False))
            db.session.commit()

            resp = self.client.get("/")
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn('<option value="ck_in_stock">CARD KINGDOM IN STOCK</option>', html)
            self.assertIn('data-ck-stock="true"', html)
            self.assertIn('data-ck-stock="false"', html)
            self.assertIn(f'id="ck-alert-btn-{i1.id}"', html)
            self.assertIn('id="cadence-notify-ck"', html)

            user = db.session.get(User, user_id)
            mcp_res = chimera_mcp._execute_get_watchlist(user, {"ck_in_stock_only": True})
            names = [c["name"] for c in mcp_res["watchlist"]]
            self.assertEqual(names, ["Lightning Bolt"])
            self.assertEqual(mcp_res["summary"]["ck_in_stock_count"], 1)


if __name__ == "__main__":
    unittest.main()
