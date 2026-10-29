"""
Unit & Integration Test Suite for Watchlist In-Binder Indicator.
Verifies that cards on the user's watchlist display an indicator if owned in their collection binder.
"""

import unittest
from app import create_app
from models import db, User, AllowedEmail, WatchlistItem, UserInventoryCard, VendorPrice


class WatchlistBinderIndicatorTestSuite(unittest.TestCase):
    """Tests for Watchlist in-binder status indicators, badges, and filters."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "DISCORD_WEBHOOK_URL": "",
            "ADMIN_EMAIL": "binder_test_admin@chimera.local",
        })
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        with self.app.app_context():
            db.session.query(VendorPrice).delete()
            db.session.query(WatchlistItem).delete()
            db.session.query(UserInventoryCard).delete()
            db.session.query(User).delete()
            db.session.query(AllowedEmail).delete()
            db.session.commit()

    def login_as(self, email="binder_collector@chimera.local", is_admin=False):
        """Authenticates client as test user."""
        with self.app.app_context():
            allowed = AllowedEmail.get_by_email(email)
            if not allowed:
                allowed = AllowedEmail(email=email, is_admin=is_admin, notes="Test User", added_by="TestSuite")
                db.session.add(allowed)
                db.session.commit()
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email, name="Binder Collector", is_admin=is_admin, is_active=True)
                db.session.add(user)
                db.session.commit()
            user_id = user.id

        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
            sess["is_admin"] = is_admin
        return user_id

    def test_model_in_binder_properties_and_fallback(self):
        """Verifies in_binder properties and on-demand fallback calculation on WatchlistItem."""
        user_id = self.login_as()

        with self.app.app_context():
            # Add card into inventory binder
            inv = UserInventoryCard(
                user_id=user_id,
                name="Rhystic Study",
                quantity=2,
                binder_name="Blue Staples",
            )
            db.session.add(inv)
            db.session.commit()

            # Create watchlist items
            w1 = WatchlistItem(user_id=user_id, name="Rhystic Study", target_price=30.0)
            w2 = WatchlistItem(user_id=user_id, name="Esper Sentinel", target_price=25.0)
            db.session.add_all([w1, w2])
            db.session.commit()

            # Check w1 (in binder)
            self.assertTrue(w1.in_binder)
            self.assertEqual(w1.binder_count, 2)
            self.assertIn("Blue Staples", w1.binder_names)

            # Check w2 (not in binder)
            self.assertFalse(w2.in_binder)
            self.assertEqual(w2.binder_count, 0)
            self.assertEqual(w2.binder_names, [])

            # Check to_dict serialization
            d1 = w1.to_dict()
            self.assertTrue(d1["in_binder"])
            self.assertEqual(d1["binder_count"], 2)
            self.assertEqual(d1["binder_names"], ["Blue Staples"])

            d2 = w2.to_dict()
            self.assertFalse(d2["in_binder"])
            self.assertEqual(d2["binder_count"], 0)
            self.assertEqual(d2["binder_names"], [])

    def test_multi_binder_quantity_aggregation(self):
        """Verifies that copies across multiple binders are summed and binder names listed."""
        user_id = self.login_as()

        with self.app.app_context():
            # User has Sol Ring in 2 different binders
            c1 = UserInventoryCard(user_id=user_id, name="Sol Ring", quantity=1, binder_name="Commander Binder")
            c2 = UserInventoryCard(user_id=user_id, name="Sol Ring", quantity=3, binder_name="Trade Binder")
            db.session.add_all([c1, c2])
            db.session.commit()

            item = WatchlistItem(user_id=user_id, name="Sol Ring", target_price=1.0)
            db.session.add(item)
            db.session.commit()

            self.assertTrue(item.in_binder)
            self.assertEqual(item.binder_count, 4)
            self.assertEqual(item.binder_names, ["Commander Binder", "Trade Binder"])

    def test_user_isolation_for_binder_indicator(self):
        """Verifies that User A's binder inventory does not mark User B's watchlist cards."""
        user_a_id = self.login_as(email="user_a@chimera.local")
        user_b_id = self.login_as(email="user_b@chimera.local")

        with self.app.app_context():
            # User A owns Cyclonic Rift
            inv_a = UserInventoryCard(user_id=user_a_id, name="Cyclonic Rift", quantity=1, binder_name="Vault A")
            db.session.add(inv_a)

            # Both users have Cyclonic Rift on watchlist
            w_a = WatchlistItem(user_id=user_a_id, name="Cyclonic Rift", target_price=30.0)
            w_b = WatchlistItem(user_id=user_b_id, name="Cyclonic Rift", target_price=30.0)
            db.session.add_all([w_a, w_b])
            db.session.commit()

            self.assertTrue(w_a.in_binder)
            self.assertFalse(w_b.in_binder)
            self.assertEqual(w_b.binder_count, 0)

    def test_watchlist_index_dashboard_renders_binder_indicator(self):
        """Verifies the UI rendering of the IN BINDER badge on the main Watchlist page."""
        user_id = self.login_as()

        with self.app.app_context():
            # 1 owned card, 1 unowned card
            inv = UserInventoryCard(user_id=user_id, name="Demonic Tutor", quantity=1, binder_name="Black Staples")
            db.session.add(inv)

            w1 = WatchlistItem(user_id=user_id, name="Demonic Tutor", target_price=35.0, finish="nonfoil")
            w2 = WatchlistItem(user_id=user_id, name="Vampiric Tutor", target_price=30.0, finish="nonfoil")
            db.session.add_all([w1, w2])
            db.session.commit()

        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Verify indicator badge for Demonic Tutor
        self.assertIn("IN BINDER (1)", html)
        self.assertIn("data-in-binder=\"true\"", html)
        self.assertIn("data-in-binder=\"false\"", html)
        self.assertIn("Black Staples", html)
        self.assertIn("/inventory?q=Demonic%20Tutor", html)

        # Verify filter options exist in the dropdown
        self.assertIn("value=\"in_binder\"", html)
        self.assertIn("value=\"not_in_binder\"", html)

    def test_deals_dashboard_renders_binder_indicator(self):
        """Verifies the UI rendering of the IN BINDER badge on Active Deals."""
        user_id = self.login_as()

        with self.app.app_context():
            inv = UserInventoryCard(user_id=user_id, name="Smothering Tithe", quantity=2, binder_name="White Vault")
            db.session.add(inv)

            item = WatchlistItem(user_id=user_id, name="Smothering Tithe", target_price=25.0, finish="nonfoil")
            db.session.add(item)
            db.session.flush()

            # Add deal vendor price
            vp = VendorPrice(
                watchlist_id=item.id,
                vendor_name="Mighty Meeple",
                price=20.0,
                in_stock=True,
                product_url="https://mightymeeple.com/products/smothering-tithe",
            )
            db.session.add(vp)
            db.session.commit()

        resp = self.client.get("/deals")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Verify deal shows binder badge
        self.assertIn("IN BINDER (2)", html)
        self.assertIn("White Vault", html)
        self.assertIn("/inventory?q=Smothering%20Tithe", html)


if __name__ == "__main__":
    unittest.main()
