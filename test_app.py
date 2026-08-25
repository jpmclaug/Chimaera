import unittest
import json
from app import create_app, parse_bulk_card_names
from models import db, User, AllowedEmail, WatchlistItem, VendorPrice, SystemSetting
from providers import ScryfallProvider, MightyMeepleProvider, EbayProvider
from deal_engine import DealEngine
from worker import run_worker_cycle


class ChimeraTestSuite(unittest.TestCase):
    """Test suite for Chimera MTG Market Tracker."""

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

    def login_as(self, email="jpmclaug@gmail.com", is_admin=True):
        """Helper to authenticate test client as a given user."""
        with self.app.app_context():
            allowed = AllowedEmail.get_by_email(email)
            if not allowed:
                allowed = AllowedEmail(
                    email=email,
                    is_admin=is_admin,
                    notes="Test Account",
                    added_by="TestSuite",
                )
                db.session.add(allowed)
                db.session.commit()
            user = User.query.filter(db.func.lower(User.email) == email.lower()).first()
            if not user:
                user = User(
                    email=email,
                    name=email.split("@")[0].capitalize(),
                    is_admin=is_admin,
                    is_active=True,
                )
                db.session.add(user)
                db.session.commit()

            with self.client.session_transaction() as sess:
                sess["user_id"] = user.id
                sess["user_email"] = user.email
                sess["is_admin"] = user.is_admin
            return user

    def setUp(self):
        with self.app.app_context():
            db.session.query(VendorPrice).delete()
            db.session.query(WatchlistItem).delete()
            db.session.query(User).delete()
            db.session.query(AllowedEmail).delete()
            db.session.commit()
        self.admin_user = self.login_as("jpmclaug@gmail.com", is_admin=True)

    def test_01_models_and_deal_logic(self):
        """Tests WatchlistItem, VendorPrice, and calculated properties."""
        with self.app.app_context():
            item = WatchlistItem(
                name="Counterspell",
                scryfall_id="test-scryfall-id-001",
                set_code="EMA",
                collector_number="43",
                finish="nonfoil",
                target_price=2.00,
            )
            db.session.add(item)
            db.session.commit()

            # Add vendor prices
            vp1 = VendorPrice(
                watchlist_id=item.id,
                vendor_name="TCGplayer",
                price=2.50,
                condition="NM",
                in_stock=True,
            )
            vp2 = VendorPrice(
                watchlist_id=item.id,
                vendor_name="Mighty Meeple",
                price=1.75,
                condition="LP",
                in_stock=True,
            )
            vp3 = VendorPrice(
                watchlist_id=item.id,
                vendor_name="eBay",
                price=1.50,
                condition="NM",
                in_stock=False,  # Out of stock
            )
            db.session.add_all([vp1, vp2, vp3])
            db.session.commit()

            # Re-fetch item
            fetched = db.session.get(WatchlistItem, item.id)
            self.assertEqual(fetched.name, "Counterspell")
            self.assertEqual(len(fetched.vendor_prices), 3)

            # Lowest in stock should be Mighty Meeple at 1.75 (since eBay at 1.50 is out of stock)
            self.assertEqual(fetched.lowest_in_stock_price, 1.75)
            self.assertTrue(fetched.is_deal)
            self.assertEqual(fetched.savings_amount, 0.25)
            self.assertEqual(fetched.savings_percent, 12.5)
            self.assertEqual(fetched.best_vendor.vendor_name, "Mighty Meeple")

    def test_02_scryfall_provider(self):
        """Tests live Scryfall autocomplete and prints resolution."""
        scryfall = ScryfallProvider()
        suggestions = scryfall.autocomplete("Sol Ring")
        self.assertIsInstance(suggestions, list)
        self.assertIn("Sol Ring", suggestions)

        prints = scryfall.search_card_prints("Sol Ring")
        self.assertGreater(len(prints), 0)
        first_print = prints[0]
        self.assertIn("id", first_print)
        self.assertIn("set_code", first_print)
        self.assertIn("finishes", first_print)

    def test_03_mightymeeple_provider(self):
        """Tests live Mighty Meeple scanner."""
        mm = MightyMeepleProvider()
        res = mm.search_card("Lightning Bolt")
        self.assertEqual(res["vendor_name"], "Mighty Meeple")
        self.assertIn("price", res)
        self.assertIn("in_stock", res)
        self.assertIn("product_url", res)

    def test_04_ebay_provider_url_builder(self):
        """Tests eBay URL construction, proxy filtering, shipping sort parameters, and title validation."""
        ebay = EbayProvider()

        # 1. Standard search URL with proxy exclusions and shipping sort
        url = ebay.build_search_url("Mox Diamond", set_code="STH", finish="foil")
        self.assertIn("Mox+Diamond", url)
        self.assertIn("STH", url)
        self.assertIn("LH_BIN=1", url)
        self.assertIn("_sop=15", url)
        self.assertIn("LH_PrefLoc=1", url)
        self.assertIn("proxy", url)
        self.assertIn("custom", url)

        # 2. Dynamic keyword preservation: 'Pack Leader' should NOT exclude 'pack'
        query_pack = ebay.build_search_query("Pack Leader")
        self.assertIn('"Pack Leader"', query_pack)
        self.assertIn("-(", query_pack)
        # Verify 'pack' is not in the negative exclusion group
        negative_group = query_pack[query_pack.index("-("):]
        self.assertNotIn("pack,", negative_group)
        self.assertNotIn(",pack", negative_group)
        self.assertIn("proxy", negative_group)

        # 3. Double-faced / Split cards use primary face
        query_dfc = ebay.build_search_query("Fable of the Mirror-Breaker // Reflections of Kiki-Jiki", set_code="NEO")
        self.assertIn('"Fable of the Mirror-Breaker"', query_dfc)
        self.assertNotIn("//", query_dfc)

        # 4. Proxy and Junk Title Detector
        self.assertTrue(ebay._is_proxy_or_junk("Ragavan, Nimble Pilferer MTG Proxy Custom Card Foil", "Ragavan, Nimble Pilferer"))
        self.assertTrue(ebay._is_proxy_or_junk("Sol Ring Art Card Modern Horizons", "Sol Ring"))
        self.assertTrue(ebay._is_proxy_or_junk("Black Lotus Playtest Reproduction Fake", "Black Lotus"))
        self.assertTrue(ebay._is_proxy_or_junk("100 MTG Card Lot with chance of Mox Diamond", "Mox Diamond"))
        self.assertTrue(ebay._is_proxy_or_junk("Force of Will MTG Playmat", "Force of Will"))
        self.assertFalse(ebay._is_proxy_or_junk("Ragavan, Nimble Pilferer - Modern Horizons 2 - NM", "Ragavan, Nimble Pilferer"))
        self.assertFalse(ebay._is_proxy_or_junk("Pack Leader - M21 - Mint", "Pack Leader"))

        # 5. Fallback search_card
        res = ebay.search_card("Mox Diamond", reference_price=500.0)
        self.assertEqual(res["vendor_name"], "eBay")
        self.assertGreater(res["price"], 0.0)
        self.assertIn("_sop=15", res["product_url"])
        self.assertIn("LH_PrefLoc=1", res["product_url"])

    def test_05_api_endpoints_and_workflow(self):
        """Tests end-to-end API routes: add card, edit target, refresh, delete."""
        with self.app.app_context():
            # 1. Autocomplete API
            resp = self.client.get("/api/scryfall/autocomplete?q=Brainstorm")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("Brainstorm", data["suggestions"])

            # 2. Add Card to Watchlist
            add_payload = {
                "scryfall_id": "test-brainstorm-scryfall-id-99",
                "name": "Brainstorm",
                "set_code": "EMA",
                "collector_number": "40",
                "image_uri": "https://cards.scryfall.io/normal/front/1/2/123.jpg",
                "finish": "nonfoil",
                "target_price": 5.00,
            }
            resp = self.client.post(
                "/api/watchlist/add",
                data=json.dumps(add_payload),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 201)
            card_data = resp.get_json()["card"]
            card_id = card_data["id"]
            self.assertEqual(card_data["name"], "Brainstorm")

            # 3. View routes
            resp_index = self.client.get("/")
            self.assertEqual(resp_index.status_code, 200)
            self.assertIn(b"Brainstorm", resp_index.data)

            resp_deals = self.client.get("/deals")
            self.assertEqual(resp_deals.status_code, 200)

            resp_guide = self.client.get("/guide")
            self.assertEqual(resp_guide.status_code, 200)
            self.assertIn(b"Tactical Field Manual", resp_guide.data)

            # 4. Update Target Price
            resp_edit = self.client.post(
                f"/api/watchlist/update-target/{card_id}",
                data=json.dumps({"target_price": 0.50}),
                content_type="application/json",
            )
            self.assertEqual(resp_edit.status_code, 200)
            self.assertEqual(resp_edit.get_json()["card"]["target_price"], 0.50)

            # 5. Delete Card
            resp_del = self.client.delete(f"/api/watchlist/delete/{card_id}")
            self.assertEqual(resp_del.status_code, 200)
            self.assertIsNone(db.session.get(WatchlistItem, card_id))

    def test_06_any_version_card_flow(self):
        """Tests adding a card with 'Any Version' default tracking and duplicate handling."""
        with self.app.app_context():
            # 1. Add Sol Ring with Any Version (no set_code)
            add_payload = {
                "name": "Sol Ring",
                "is_any_version": True,
                "finish": "nonfoil",
                "target_price": 2.00,
            }
            resp = self.client.post(
                "/api/watchlist/add",
                data=json.dumps(add_payload),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 201)
            data = resp.get_json()["card"]
            self.assertEqual(data["name"], "Sol Ring")
            self.assertTrue(data["is_any_version"])
            self.assertIsNone(data["set_code"])
            item_id = data["id"]

            # 2. Verify duplicate prevention for Any Version
            resp_dup = self.client.post(
                "/api/watchlist/add",
                data=json.dumps(add_payload),
                content_type="application/json",
            )
            self.assertEqual(resp_dup.status_code, 409)

            # 3. Add a specific print of Sol Ring - should succeed alongside Any Version
            specific_payload = {
                "name": "Sol Ring",
                "is_any_version": False,
                "scryfall_id": "test-sol-ring-cc2",
                "set_code": "CC2",
                "collector_number": "1",
                "finish": "nonfoil",
                "target_price": 10.00,
            }
            resp_spec = self.client.post(
                "/api/watchlist/add",
                data=json.dumps(specific_payload),
                content_type="application/json",
            )
            self.assertEqual(resp_spec.status_code, 201)
            spec_data = resp_spec.get_json()["card"]
            self.assertFalse(spec_data["is_any_version"])
            self.assertEqual(spec_data["set_code"], "CC2")

            # 4. View dashboard rendering
            resp_index = self.client.get("/")
            self.assertEqual(resp_index.status_code, 200)
            self.assertIn(b"ANY VERSION", resp_index.data)
            self.assertIn(b"CC2", resp_index.data)

    def test_07_scryfall_cheapest_and_named(self):
        """Tests ScryfallProvider get_card_named and get_cheapest_tcgplayer_price."""
        scryfall = ScryfallProvider()
        named = scryfall.get_card_named("Lightning Bolt")
        self.assertIsNotNone(named)
        self.assertEqual(named["name"], "Lightning Bolt")
        self.assertIn("image_uri", named)

        cheapest = scryfall.get_cheapest_tcgplayer_price("Lightning Bolt", finish="nonfoil")
        self.assertIsNotNone(cheapest)
        self.assertEqual(cheapest["vendor_name"], "TCGplayer")
        self.assertGreater(cheapest["price"], 0.0)

    def test_08_system_settings_and_cadence_api(self):
        """Tests SystemSetting CRUD and /api/settings/cadence endpoints."""
        with self.app.app_context():
            # Test model helpers
            SystemSetting.set_val("test_key", "123.45")
            self.assertEqual(SystemSetting.get_val("test_key"), "123.45")
            self.assertEqual(SystemSetting.get_float("test_key"), 123.45)
            self.assertTrue(SystemSetting.get_bool("non_existent", default=True))

            # Test Telemetry API
            resp_tel = self.client.get("/api/settings/telemetry")
            self.assertEqual(resp_tel.status_code, 200)
            data_tel = resp_tel.get_json()
            self.assertIn("poll_interval_hours", data_tel)
            self.assertIn("auto_poll_enabled", data_tel)

            # Test Cadence Update API
            resp_cad = self.client.post(
                "/api/settings/cadence",
                data=json.dumps({"poll_interval_hours": 2.5, "auto_poll_enabled": True}),
                content_type="application/json",
            )
            self.assertEqual(resp_cad.status_code, 200)
            data_cad = resp_cad.get_json()
            self.assertEqual(data_cad["poll_interval_hours"], 2.5)
            self.assertTrue(data_cad["auto_poll_enabled"])
            self.assertEqual(SystemSetting.get_float("poll_interval_hours"), 2.5)

    def test_09_worker_cycle_execution(self):
        """Tests standalone worker cycle execution."""
        with self.app.app_context():
            deal_engine = DealEngine(app=self.app)
            res = run_worker_cycle(deal_engine, notify=False)
            self.assertTrue(res["success"])
            self.assertIn("count", res)
            self.assertEqual(SystemSetting.get_val("worker_status"), "idle")

    def test_10_mightymeeple_restock_alert_and_toggle(self):
        """Tests Mighty Meeple alert toggle API and restock notification flow."""
        with self.app.app_context():
            item = WatchlistItem(
                user_id=self.admin_user.id,
                name="Force of Will",
                finish="nonfoil",
                target_price=60.00,
                notify_mm_stock=True,
            )
            db.session.add(item)
            db.session.commit()
            item_id = item.id

            # 1. Default should be True
            self.assertTrue(item.notify_mm_stock)

            # 2. Toggle via API -> False
            resp = self.client.post(f"/api/watchlist/toggle-mm-alert/{item_id}")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertFalse(data["notify_mm_stock"])
            self.assertIn("disabled", data["message"].lower())

            # 3. Toggle back -> True
            resp2 = self.client.post(f"/api/watchlist/toggle-mm-alert/{item_id}")
            self.assertEqual(resp2.status_code, 200)
            data2 = resp2.get_json()
            self.assertTrue(data2["notify_mm_stock"])
            self.assertIn("enabled", data2["message"].lower())

    def test_11_card_scope_update_api(self):
        """Tests updating tracking scope between Any Version (General) and Specific Printings."""
        with self.app.app_context():
            item = WatchlistItem(
                user_id=self.admin_user.id,
                name="Demonic Tutor",
                is_any_version=True,
                finish="nonfoil",
                target_price=35.00,
            )
            db.session.add(item)
            db.session.commit()
            item_id = item.id

            # Update from Any Version to Specific Set (UMA #93)
            scope_payload = {
                "is_any_version": False,
                "scryfall_id": "test-dt-uma-93",
                "set_code": "UMA",
                "collector_number": "93",
                "finish": "foil",
            }
            resp = self.client.post(
                f"/api/watchlist/update-scope/{item_id}",
                data=json.dumps(scope_payload),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            updated = db.session.get(WatchlistItem, item_id)
            self.assertFalse(updated.is_any_version)
            self.assertEqual(updated.set_code, "UMA")
            self.assertEqual(updated.collector_number, "93")
            self.assertEqual(updated.finish, "foil")

            # Switch back to Any Version
            back_payload = {
                "is_any_version": True,
                "finish": "nonfoil",
            }
            resp_back = self.client.post(
                f"/api/watchlist/update-scope/{item_id}",
                data=json.dumps(back_payload),
                content_type="application/json",
            )
            self.assertEqual(resp_back.status_code, 200)
            updated_back = db.session.get(WatchlistItem, item_id)
            self.assertTrue(updated_back.is_any_version)
            self.assertIsNone(updated_back.set_code)

    def test_12_price_intel_and_benchmarks(self):
        """Tests market price intelligence, suggested deals, and price rating calculation."""
        with self.app.app_context():
            item = WatchlistItem(
                user_id=self.admin_user.id,
                name="Mana Vault",
                finish="nonfoil",
                target_price=50.00,
            )
            db.session.add(item)
            db.session.commit()

            # Add TCGplayer market reference
            vp_tcg = VendorPrice(
                watchlist_id=item.id,
                vendor_name="TCGplayer",
                price=100.00,
                in_stock=True,
            )
            # Add live deal from Mighty Meeple at $75 (25% off)
            vp_mm = VendorPrice(
                watchlist_id=item.id,
                vendor_name="Mighty Meeple",
                price=75.00,
                in_stock=True,
            )
            db.session.add_all([vp_tcg, vp_mm])
            db.session.commit()

            fetched = db.session.get(WatchlistItem, item.id)
            self.assertEqual(fetched.market_price, 100.00)
            self.assertEqual(fetched.suggested_good_price, 90.00)
            self.assertEqual(fetched.suggested_great_price, 80.00)
            # Price ratio = 75 / 100 = 0.75 <= 0.80 -> Great Deal
            self.assertEqual(fetched.price_rating, "Great Deal")
            self.assertTrue(fetched.mm_in_stock)

            # Test API endpoint
            resp = self.client.get("/api/card/price-intel?name=Mana%20Vault")
            self.assertEqual(resp.status_code, 200)
            intel = resp.get_json()
            self.assertEqual(intel["name"], "Mana Vault")
            self.assertIn("market_price", intel)
            self.assertIn("targets", intel)
            self.assertIn("good_deal_10", intel["targets"])
            self.assertIn("great_deal_20", intel["targets"])

    def test_13_ebay_link_modes_and_preferences(self):
        """Tests eBay product_url vs search_url persistence and preference configuration."""
        with self.app.app_context():
            item = WatchlistItem(
                user_id=self.admin_user.id,
                name="Sylvan Library",
                finish="nonfoil",
            )
            db.session.add(item)
            db.session.commit()

            vp_ebay = VendorPrice(
                watchlist_id=item.id,
                vendor_name="eBay",
                price=42.00,
                product_url="https://www.ebay.com/itm/123456789",
                search_url="https://www.ebay.com/sch/i.html?_nkw=Sylvan+Library",
                in_stock=True,
            )
            db.session.add(vp_ebay)
            db.session.commit()

            # Verify VendorPrice serialization contains search_url
            d = vp_ebay.to_dict()
            self.assertEqual(d["product_url"], "https://www.ebay.com/itm/123456789")
            self.assertEqual(d["search_url"], "https://www.ebay.com/sch/i.html?_nkw=Sylvan+Library")

            # Test settings endpoint for eBay link preference
            resp = self.client.post(
                "/api/settings/ebay-preference",
                data=json.dumps({"ebay_link_mode": "search"}),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()["ebay_link_mode"], "search")
            self.assertEqual(SystemSetting.get_val("ebay_link_mode"), "search")

    def test_14_user_and_allowed_email_models(self):
        """Tests User and AllowedEmail model methods, properties, and serializations."""
        with self.app.app_context():
            allowed = AllowedEmail(
                email="scout@gmail.com",
                notes="Field Scout Recon",
                is_admin=False,
                added_by="Admin",
            )
            db.session.add(allowed)
            db.session.commit()

            self.assertTrue(AllowedEmail.is_allowed("scout@gmail.com"))
            self.assertTrue(AllowedEmail.is_allowed("SCOUT@GMAIL.COM"))  # Case insensitive
            self.assertFalse(AllowedEmail.is_allowed("intruder@gmail.com"))

            entry = AllowedEmail.get_by_email("SCOUT@gmail.com")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.notes, "Field Scout Recon")
            self.assertFalse(entry.is_admin)

            d = entry.to_dict()
            self.assertEqual(d["email"], "scout@gmail.com")
            self.assertEqual(d["notes"], "Field Scout Recon")
            self.assertFalse(d["is_admin"])

            user = User(
                email="scout@gmail.com",
                name="Field Scout",
                is_admin=False,
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()

            u_dict = user.to_dict()
            self.assertEqual(u_dict["email"], "scout@gmail.com")
            self.assertEqual(u_dict["name"], "Field Scout")
            self.assertFalse(u_dict["is_admin"])
            self.assertTrue(u_dict["is_active"])
            self.assertEqual(u_dict["card_count"], 0)

    def test_15_whitelist_access_control_and_auth(self):
        """Tests invite-only whitelist rejection, dev login, and suspension."""
        # Unauthenticated request to / should redirect to /login
        with self.client.session_transaction() as sess:
            sess.clear()

        resp = self.client.get("/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

        # Attempt dev login with unwhitelisted email -> access denied
        resp = self.client.post(
            "/auth/dev-login",
            data=json.dumps({"email": "unauthorized_user@gmail.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

        # Authorize the email in AllowedEmail
        with self.app.app_context():
            allowed = AllowedEmail(email="authorized_cadet@gmail.com", is_admin=False, added_by="Test")
            db.session.add(allowed)
            db.session.commit()

        # Dev login now succeeds
        resp = self.client.post(
            "/auth/dev-login",
            data=json.dumps({"email": "authorized_cadet@gmail.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("user", resp.get_json())

        # Suspend user and verify access is rejected
        with self.app.app_context():
            u = User.query.filter_by(email="authorized_cadet@gmail.com").first()
            u.is_active = False
            db.session.commit()

        resp = self.client.post(
            "/auth/dev-login",
            data=json.dumps({"email": "authorized_cadet@gmail.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("suspended", resp.get_json()["error"].lower())

    def test_16_admin_management_endpoints(self):
        """Tests admin dashboard authorization, adding whitelist, toggling roles, and revoking clearance."""
        # Authenticate as regular non-admin operator
        operator = self.login_as("operator@gmail.com", is_admin=False)

        # Operator attempting to access /admin should be blocked (redirected to / with flash)
        resp = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/")

        # Operator attempting to call admin API should get 403
        resp = self.client.post(
            "/api/admin/whitelist/add",
            data=json.dumps({"email": "newbie@gmail.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

        # Authenticate as Administrator
        admin = self.login_as("jpmclaug@gmail.com", is_admin=True)

        # Admin accessing /admin succeeds
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)

        # Admin adding new whitelist email
        resp = self.client.post(
            "/api/admin/whitelist/add",
            data=json.dumps({
                "email": "lead_analyst@gmail.com",
                "notes": "Lead Analyst",
                "is_admin": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        res_data = resp.get_json()
        self.assertIn("allowed", res_data)
        new_id = res_data["allowed"]["id"]

        # Admin toggles admin role on the new entry
        resp = self.client.post(f"/api/admin/whitelist/toggle-admin/{new_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["is_admin"])

        # Admin revokes clearance
        resp = self.client.post(f"/api/admin/whitelist/delete/{new_id}")
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            self.assertFalse(AllowedEmail.is_allowed("lead_analyst@gmail.com"))

        # Admin cannot revoke their own clearance
        with self.app.app_context():
            my_allowed = AllowedEmail.get_by_email("jpmclaug@gmail.com")
            my_id = my_allowed.id
        resp = self.client.post(f"/api/admin/whitelist/delete/{my_id}")
        self.assertEqual(resp.status_code, 400)

    def test_17_multi_user_watchlist_isolation(self):
        """Tests per-user data tenancy: watchlist items and deals are isolated across users."""
        # 1. User Alpha logs in and creates a card
        user_alpha = self.login_as("alpha@gmail.com", is_admin=False)
        with self.app.app_context():
            card_alpha = WatchlistItem(
                user_id=user_alpha.id,
                name="Underground Sea",
                finish="nonfoil",
                target_price=500.0,
            )
            db.session.add(card_alpha)
            db.session.commit()
            card_alpha_id = card_alpha.id

        # User Alpha sees their card
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Underground Sea", resp.data)

        # 2. User Beta logs in
        user_beta = self.login_as("beta@gmail.com", is_admin=False)

        # User Beta's dashboard should NOT have Underground Sea
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Underground Sea", resp.data)

        # User Beta attempts to edit or delete User Alpha's card -> 404
        resp = self.client.post(f"/api/watchlist/update-target/{card_alpha_id}", data=json.dumps({"target_price": 400.0}), content_type="application/json")
        self.assertEqual(resp.status_code, 404)

        resp = self.client.delete(f"/api/watchlist/delete/{card_alpha_id}")
        self.assertEqual(resp.status_code, 404)

        # User Beta adds their own card
        with self.app.app_context():
            card_beta = WatchlistItem(
                user_id=user_beta.id,
                name="Mox Pearl",
                finish="nonfoil",
                target_price=2000.0,
            )
            db.session.add(card_beta)
            db.session.commit()

        # User Beta sees Mox Pearl, not Underground Sea
        resp = self.client.get("/")
        self.assertIn(b"Mox Pearl", resp.data)
        self.assertNotIn(b"Underground Sea", resp.data)

    def test_18_bulk_card_name_parser(self):
        """Tests parsing of semicolon-separated and multi-line card names."""
        # 1. Exact prompt query (semicolon separated)
        input_str = "Simic Growth Chamber; Tangled Islet; Rimewood Falls; Evolving Wilds; Lush Oasis; Thornwood Falls; Lonely Sandbar; Tranquil Thicket; Bant Panorama"
        parsed = parse_bulk_card_names(input_str)
        self.assertEqual(len(parsed), 9)
        self.assertEqual(parsed[0], "Simic Growth Chamber")
        self.assertEqual(parsed[8], "Bant Panorama")

        # 2. Preserves commas inside card names
        comma_input = "Omnath, Locus of Creation; Ragavan, Nimble Pilferer; Atraxa, Praetors' Voice"
        parsed_comma = parse_bulk_card_names(comma_input)
        self.assertEqual(len(parsed_comma), 3)
        self.assertEqual(parsed_comma[0], "Omnath, Locus of Creation")
        self.assertEqual(parsed_comma[1], "Ragavan, Nimble Pilferer")
        self.assertEqual(parsed_comma[2], "Atraxa, Praetors' Voice")

        # 3. Handles mixed newlines, trailing semicolons, quotes, and deduplication
        messy_input = """
        "Lightning Bolt"; Counterspell;
        Lightning Bolt; 
        "Force of Will"
        
        Sol Ring;
        """
        parsed_messy = parse_bulk_card_names(messy_input)
        self.assertEqual(parsed_messy, ["Lightning Bolt", "Counterspell", "Force of Will", "Sol Ring"])

        # 4. Empty input
        self.assertEqual(parse_bulk_card_names(""), [])
        self.assertEqual(parse_bulk_card_names("   ;  ;; \n\n  "), [])

    def test_19_scryfall_batch_collection(self):
        """Tests live Scryfall /cards/collection batch endpoint."""
        scryfall = ScryfallProvider()
        test_cards = ["Simic Growth Chamber", "Tangled Islet", "Rimewood Falls"]
        found, not_found = scryfall.get_cards_collection(test_cards)
        self.assertEqual(len(found), 3)
        self.assertEqual(len(not_found), 0)
        self.assertIn("simic growth chamber", found)
        self.assertTrue(found["simic growth chamber"]["image_uri"].startswith("http"))
        self.assertIsNotNone(found["simic growth chamber"]["id"])

    def test_20_bulk_add_api_endpoint(self):
        """Tests /api/watchlist/bulk-add with semicolon separated card manifest."""
        with self.app.app_context():
            payload = {
                "cards": "Simic Growth Chamber; Tangled Islet; Rimewood Falls; Evolving Wilds; Lush Oasis; Thornwood Falls; Lonely Sandbar; Tranquil Thicket; Bant Panorama",
                "finish": "nonfoil",
                "target_strategy": "none",
                "notify_mm_stock": True,
            }
            resp = self.client.post("/api/watchlist/bulk-add", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(resp.status_code, 201)
            data = resp.get_json()
            self.assertEqual(data["added_count"], 9)
            self.assertEqual(data["skipped_count"], 0)
            self.assertEqual(data["failed_count"], 0)
            self.assertEqual(len(data["added"]), 9)

            # Verify in DB
            db_cards = WatchlistItem.query.filter_by(user_id=self.admin_user.id).all()
            self.assertEqual(len(db_cards), 9)
            for card in db_cards:
                self.assertTrue(card.is_any_version)
                self.assertEqual(card.finish, "nonfoil")
                self.assertTrue(card.notify_mm_stock)
                self.assertIsNotNone(card.image_uri)

    def test_21_bulk_add_duplicates_and_presets(self):
        """Tests duplicate skipping, target pricing presets, and validation in bulk add."""
        with self.app.app_context():
            # 1. Bulk add first time
            p1 = {
                "cards": "Sol Ring; Mana Vault",
                "finish": "nonfoil",
                "target_strategy": "good_deal_10",
                "notify_mm_stock": True,
            }
            resp1 = self.client.post("/api/watchlist/bulk-add", data=json.dumps(p1), content_type="application/json")
            self.assertEqual(resp1.status_code, 201)
            d1 = resp1.get_json()
            self.assertEqual(d1["added_count"], 2)

            # Target prices should be computed from Scryfall market price (-10%)
            sol_ring = WatchlistItem.query.filter_by(user_id=self.admin_user.id, name="Sol Ring").first()
            self.assertIsNotNone(sol_ring)

            # 2. Re-adding the same cards should skip them
            resp2 = self.client.post("/api/watchlist/bulk-add", data=json.dumps(p1), content_type="application/json")
            self.assertEqual(resp2.status_code, 200)
            d2 = resp2.get_json()
            self.assertEqual(d2["added_count"], 0)
            self.assertEqual(d2["skipped_count"], 2)

            # 3. Empty input should return 400
            resp3 = self.client.post("/api/watchlist/bulk-add", data=json.dumps({"cards": ""}), content_type="application/json")
            self.assertEqual(resp3.status_code, 400)

    def test_22_card_tagging_single_and_bulk(self):
        """Tests card tagging in WatchlistItem model, single add, and bulk add."""
        with self.app.app_context():
            # 1. Single card acquisition with tag
            payload_single = {
                "name": "Doubling Season",
                "is_any_version": True,
                "finish": "nonfoil",
                "target_price": 40.00,
                "tag": "Atraxa Commander",
            }
            resp = self.client.post("/api/watchlist/add", data=json.dumps(payload_single), content_type="application/json")
            self.assertEqual(resp.status_code, 201)
            data = resp.get_json()
            self.assertEqual(data["card"]["tag"], "Atraxa Commander")

            # Check DB item
            card = WatchlistItem.query.filter_by(user_id=self.admin_user.id, name="Doubling Season").first()
            self.assertIsNotNone(card)
            self.assertEqual(card.tag, "Atraxa Commander")
            self.assertEqual(card.to_dict()["tag"], "Atraxa Commander")

            # 2. Bulk acquisition with tag
            payload_bulk = {
                "cards": "Breeding Pool; Watery Grave; Overgrown Tomb",
                "finish": "nonfoil",
                "target_strategy": "none",
                "tag": "Shocklands",
            }
            resp_bulk = self.client.post("/api/watchlist/bulk-add", data=json.dumps(payload_bulk), content_type="application/json")
            self.assertEqual(resp_bulk.status_code, 201)
            b_data = resp_bulk.get_json()
            self.assertEqual(b_data["added_count"], 3)
            for item in b_data["added"]:
                self.assertEqual(item["tag"], "Shocklands")

            # Query all shocklands from DB
            shocklands = WatchlistItem.query.filter_by(user_id=self.admin_user.id, tag="Shocklands").all()
            self.assertEqual(len(shocklands), 3)

    def test_23_tag_update_api_and_filtering(self):
        """Tests tag update endpoints, distinct tags API, and view template tags context."""
        with self.app.app_context():
            # Create a card for testing
            card = WatchlistItem(
                user_id=self.admin_user.id,
                name="Sylvan Library",
                finish="nonfoil",
                target_price=25.00,
                tag="Cube Target",
            )
            db.session.add(card)
            db.session.commit()
            card_id = card.id

            # 1. Update tag via /api/watchlist/update-target/<id>
            resp1 = self.client.post(
                f"/api/watchlist/update-target/{card_id}",
                data=json.dumps({"target_price": 24.00, "tag": "Modern Green"}),
                content_type="application/json",
            )
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(resp1.get_json()["card"]["tag"], "Modern Green")

            # 2. Quick update tag via /api/watchlist/update-tag/<id>
            resp2 = self.client.post(
                f"/api/watchlist/update-tag/{card_id}",
                data=json.dumps({"tag": "High Priority"}),
                content_type="application/json",
            )
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(resp2.get_json()["tag"], "High Priority")

            # 3. Clear tag via /api/watchlist/update-tag/<id>
            resp3 = self.client.post(
                f"/api/watchlist/update-tag/{card_id}",
                data=json.dumps({"tag": ""}),
                content_type="application/json",
            )
            self.assertEqual(resp3.status_code, 200)
            self.assertIsNone(resp3.get_json()["tag"])

            # Set tag back to test tag listing API
            card.tag = "Vintage Cube"
            db.session.commit()

            # 4. Fetch distinct tags list
            resp_tags = self.client.get("/api/watchlist/tags")
            self.assertEqual(resp_tags.status_code, 200)
            tags_list = resp_tags.get_json()["tags"]
            self.assertIn("Vintage Cube", tags_list)

            # 5. Verify index view renders tag filter and tag badge
            resp_index = self.client.get("/")
            self.assertEqual(resp_index.status_code, 200)
            self.assertIn(b"Vintage Cube", resp_index.data)
            self.assertIn(b"watchlist-filter-tag", resp_index.data)

    def test_24_mightymeeple_exact_matching_and_accents(self):
        """Tests MightyMeepleProvider title normalization, Art Card exclusion, and exact matching."""
        mm = MightyMeepleProvider()

        # 1. Matching logic unit tests
        self.assertTrue(mm._is_card_name_match("The One Ring [The Lord of the Rings: Tales of Middle-Earth]", "The One Ring"))
        self.assertTrue(mm._is_card_name_match("The One Ring (Borderless) [The Hobbit: Eternal-Legal]", "The One Ring"))
        self.assertFalse(mm._is_card_name_match("One Ring to Rule Them All [The Lord of the Rings: Tales of Middle-Earth]", "The One Ring"))
        self.assertFalse(mm._is_card_name_match("Oath of Eorl [The Lord of the Rings: Tales of Middle-Earth Commander]", "The One Ring"))
        self.assertFalse(mm._is_card_name_match("Sol Ring [Commander Collection: Black]", "The One Ring"))

        # Accented characters (Dáin -> Dain)
        self.assertTrue(mm._is_card_name_match("Dain of the Ancient Halls (Extended Art) [The Hobbit: Eternal-Legal]", "Dáin of the Ancient Halls"))
        self.assertFalse(mm._is_card_name_match("Wall of Spears [Antiquities]", "Dáin of the Ancient Halls"))
        self.assertFalse(mm._is_card_name_match("Haunting Wind [Antiquities]", "Dáin of the Ancient Halls"))

        # Exclude Art Cards and tokens
        self.assertFalse(mm._is_card_name_match("Dain of the Ancient Halls Art Card [The Hobbit Art Series]", "Dáin of the Ancient Halls"))
        self.assertFalse(mm._is_card_name_match("The One Ring Art Card [The Lord of the Rings Art Series]", "The One Ring"))
        self.assertFalse(mm._is_card_name_match("Elf Token [Lorwyn]", "Timberwatch Elf"))

        # Filter products should return empty if no matches (not products[:3])
        unrelated_products = [
            {"title": "Oath of Eorl [The Lord of the Rings]", "handle": "oath-of-eorl"},
            {"title": "Wall of Spears [Antiquities]", "handle": "wall-of-spears"},
        ]
        filtered = mm._filter_products(unrelated_products, "The One Ring", None, None)
        self.assertEqual(len(filtered), 0)

        # 2. Live searches for The One Ring and Dáin of the Ancient Halls
        res_ring = mm.search_card("The One Ring")
        self.assertEqual(res_ring["vendor_name"], "Mighty Meeple")
        # Ensure it didn't match Oath of Eorl or Sol Ring
        self.assertNotIn("oath-of-eorl", res_ring["product_url"])
        self.assertNotIn("sol-ring", res_ring["product_url"])

        res_dain = mm.search_card("Dáin of the Ancient Halls")
        self.assertEqual(res_dain["vendor_name"], "Mighty Meeple")
        # Ensure it didn't match Wall of Spears or Art Card
        self.assertNotIn("wall-of-spears", res_dain["product_url"])
        self.assertNotIn("art-card", res_dain["product_url"])


if __name__ == "__main__":
    unittest.main()

