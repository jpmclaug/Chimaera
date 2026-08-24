import unittest
import json
from app import create_app
from models import db, WatchlistItem, VendorPrice, SystemSetting
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
        })
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        with self.app.app_context():
            db.session.query(VendorPrice).delete()
            db.session.query(WatchlistItem).delete()
            db.session.commit()

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
        """Tests eBay URL construction and fallback."""
        ebay = EbayProvider()
        url = ebay.build_search_url("Mox Diamond", set_code="STH", finish="foil")
        self.assertIn("Mox+Diamond", url)
        self.assertIn("STH", url)
        self.assertIn("LH_BIN=1", url)

        res = ebay.search_card("Mox Diamond", reference_price=500.0)
        self.assertEqual(res["vendor_name"], "eBay")
        self.assertGreater(res["price"], 0.0)

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


if __name__ == "__main__":
    unittest.main()
