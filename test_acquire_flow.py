import json
import time
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError

from app import create_app
from deal_engine import DealEngine
from models import AllowedEmail, User, VendorPrice, WatchlistItem, db
from providers import EbayProvider, MightyMeepleProvider, ScryfallProvider


class AcquireFlowTestSuite(unittest.TestCase):
    """Deterministic unit tests for Acquire / Watchlist Add reliability, caching, and UI safety."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "DISCORD_WEBHOOK_URL": "",
            "ADMIN_EMAIL": "acquire_tester@chimera.local",
        })
        cls.client = cls.app.test_client()
        with cls.app.app_context():
            db.create_all()

    def login_as(self, email="acquire_tester@chimera.local", is_admin=True):
        with self.app.app_context():
            allowed = AllowedEmail.get_by_email(email)
            if not allowed:
                allowed = AllowedEmail(
                    email=email,
                    is_admin=is_admin,
                    notes="Acquire Test Account",
                    added_by="TestSuite",
                )
                db.session.add(allowed)
                db.session.commit()
            user = User.query.filter(db.func.lower(User.email) == email.lower()).first()
            if not user:
                user = User(
                    email=email,
                    name="Acquire Tester",
                    is_admin=is_admin,
                    is_active=True,
                )
                db.session.add(user)
                db.session.commit()
            user_id = user.id

        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = email
            sess["is_admin"] = is_admin
        return user_id

    def setUp(self):
        ScryfallProvider._prints_cache.clear()
        ScryfallProvider._card_cache.clear()
        MightyMeepleProvider._variant_cache.clear()
        with self.app.app_context():
            db.session.query(VendorPrice).delete()
            db.session.query(WatchlistItem).delete()
            db.session.query(User).delete()
            db.session.query(AllowedEmail).delete()
            db.session.commit()
        self.user_id = self.login_as()

    def test_add_card_succeeds_even_when_poll_fails_and_uses_fast_mode(self):
        """POST /api/watchlist/add returns 201 with success=True even if poll_card raises an exception."""
        mock_meta = {
            "id": "sf-sol-ring-1",
            "name": "Sol Ring",
            "image_uri": "https://cards.scryfall.io/normal/front/1/2/sol.jpg",
            "type_line": "Artifact",
            "colors": [],
            "color_identity": [],
            "mana_cost": "{1}",
            "cmc": 1.0,
        }
        poll_calls = []

        def failing_poll(item, notify=True, fast_mode=False):
            poll_calls.append({"name": item.name, "notify": notify, "fast_mode": fast_mode})
            raise OperationalError("SELECT 1", {}, Exception("SSL SYSCALL error: EOF detected"))

        with patch.object(ScryfallProvider, "get_card_named", return_value=mock_meta), \
             patch.object(DealEngine, "poll_card", side_effect=failing_poll):
            resp = self.client.post(
                "/api/watchlist/add",
                data=json.dumps({
                    "name": "Sol Ring",
                    "is_any_version": True,
                    "finish": "nonfoil",
                    "target_price": 1.50,
                }),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["card"]["name"], "Sol Ring")
        self.assertEqual(len(poll_calls), 1)
        self.assertTrue(poll_calls[0]["fast_mode"])

        with self.app.app_context():
            saved = WatchlistItem.query.filter_by(user_id=self.user_id, name="Sol Ring").first()
            self.assertIsNotNone(saved)
            self.assertEqual(saved.target_price, 1.50)

    def test_add_card_duplicate_returns_409_and_allows_distinct_finish_or_print(self):
        """POST /api/watchlist/add returns 409 on exact duplicate and 201 on distinct finish or specific print."""
        mock_meta = {
            "id": "sf-rhystic-1",
            "name": "Rhystic Study",
            "image_uri": "https://cards.scryfall.io/normal/front/a/b/rhystic.jpg",
            "type_line": "Enchantment",
            "colors": ["U"],
            "color_identity": ["U"],
            "mana_cost": "{2}{U}",
            "cmc": 3.0,
        }
        with patch.object(ScryfallProvider, "get_card_named", return_value=mock_meta), \
             patch.object(DealEngine, "poll_card", return_value={"is_deal": False}):
            base_payload = {
                "name": "Rhystic Study",
                "is_any_version": True,
                "finish": "nonfoil",
                "target_price": 30.0,
            }
            r1 = self.client.post("/api/watchlist/add", data=json.dumps(base_payload), content_type="application/json")
            self.assertEqual(r1.status_code, 201)
            self.assertTrue(r1.get_json()["success"])

            # Genuine duplicate -> 409
            r_dup = self.client.post("/api/watchlist/add", data=json.dumps(base_payload), content_type="application/json")
            self.assertEqual(r_dup.status_code, 409)
            self.assertIn("already on your watchlist", r_dup.get_json()["error"])

            # Distinct finish -> 201
            r_foil = self.client.post(
                "/api/watchlist/add",
                data=json.dumps({**base_payload, "finish": "foil"}),
                content_type="application/json",
            )
            self.assertEqual(r_foil.status_code, 201)

            # Specific printing -> 201
            r_spec = self.client.post(
                "/api/watchlist/add",
                data=json.dumps({
                    "name": "Rhystic Study",
                    "is_any_version": False,
                    "set_code": "WOT",
                    "collector_number": "25",
                    "finish": "nonfoil",
                }),
                content_type="application/json",
            )
            self.assertEqual(r_spec.status_code, 201)

    def test_bulk_add_seeds_tcgplayer_prices_and_survives_poll_errors(self):
        """POST /api/watchlist/bulk-add seeds TCGplayer VendorPrice and succeeds even if poll_card raises."""
        found_map = {
            "arcane signet": {
                "id": "sf-arcane-1",
                "name": "Arcane Signet",
                "set_code": "CMM",
                "image_uri": "https://cards.scryfall.io/normal/front/a/r/arcane.jpg",
                "type_line": "Artifact",
                "colors": [],
                "color_identity": [],
                "mana_cost": "{2}",
                "cmc": 2.0,
                "prices": {"usd": "1.25", "usd_foil": "2.50"},
                "tcgplayer_url": "https://www.tcgplayer.com/product/arcane-signet",
            },
            "command tower": {
                "id": "sf-tower-1",
                "name": "Command Tower",
                "set_code": "CMM",
                "image_uri": "https://cards.scryfall.io/normal/front/c/t/tower.jpg",
                "type_line": "Land",
                "colors": [],
                "color_identity": [],
                "mana_cost": "",
                "cmc": 0.0,
                "prices": {"usd": "0.35", "usd_foil": "1.10"},
                "tcgplayer_url": "https://www.tcgplayer.com/product/command-tower",
            },
        }
        poll_fast_flags = []

        def flaky_poll(item, notify=True, fast_mode=False):
            poll_fast_flags.append(fast_mode)
            if item.name == "Arcane Signet":
                raise RuntimeError("Simulated provider failure")
            return {"is_deal": False}

        with patch.object(ScryfallProvider, "get_cards_collection", return_value=(found_map, [])), \
             patch.object(DealEngine, "poll_card", side_effect=flaky_poll):
            resp = self.client.post(
                "/api/watchlist/bulk-add",
                data=json.dumps({
                    "cards": "Arcane Signet; Command Tower",
                    "finish": "nonfoil",
                    "target_strategy": "none",
                }),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["added_count"], 2)
        self.assertEqual(len(data["added"]), 2)
        self.assertEqual(poll_fast_flags, [True, True])

        with self.app.app_context():
            arcane = WatchlistItem.query.filter_by(user_id=self.user_id, name="Arcane Signet").first()
            tower = WatchlistItem.query.filter_by(user_id=self.user_id, name="Command Tower").first()
            self.assertIsNotNone(arcane)
            self.assertIsNotNone(tower)
            self.assertEqual(arcane.tcgplayer_price, 1.25)
            self.assertEqual(tower.tcgplayer_price, 0.35)

    def test_scryfall_prints_populates_prints_and_card_caches(self):
        """search_card_prints populates _prints_cache and _card_cache so subsequent lookups make 0 HTTP calls."""
        fake_cards = [
            {
                "id": "sf-bolt-lea",
                "name": "Lightning Bolt",
                "set": "lea",
                "set_name": "Limited Edition Alpha",
                "collector_number": "161",
                "rarity": "common",
                "released_at": "1993-08-05",
                "image_uris": {"normal": "https://cards.scryfall.io/normal/front/b/o/bolt_lea.jpg"},
                "finishes": ["nonfoil"],
                "prices": {"usd": "250.00", "usd_foil": None, "usd_etched": None},
                "purchase_uris": {"tcgplayer": "https://www.tcgplayer.com/bolt-lea"},
                "type_line": "Instant",
                "colors": ["R"],
                "color_identity": ["R"],
                "mana_cost": "{R}",
                "cmc": 1.0,
            },
            {
                "id": "sf-bolt-2x2",
                "name": "Lightning Bolt",
                "set": "2x2",
                "set_name": "Double Masters 2022",
                "collector_number": "117",
                "rarity": "uncommon",
                "released_at": "2022-07-08",
                "image_uris": {"normal": "https://cards.scryfall.io/normal/front/b/o/bolt_2x2.jpg"},
                "finishes": ["nonfoil", "foil"],
                "prices": {"usd": "1.20", "usd_foil": "2.40", "usd_etched": None},
                "purchase_uris": {"tcgplayer": "https://www.tcgplayer.com/bolt-2x2"},
                "type_line": "Instant",
                "colors": ["R"],
                "color_identity": ["R"],
                "mana_cost": "{R}",
                "cmc": 1.0,
            },
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": fake_cards}

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        sf = ScryfallProvider(session=mock_session)
        prints1 = sf.search_card_prints("Lightning Bolt")
        self.assertEqual(len(prints1), 2)
        self.assertEqual(mock_session.get.call_count, 1)

        # Subsequent calls hit _prints_cache and _card_cache with 0 HTTP requests
        prints2 = sf.search_card_prints("Lightning Bolt")
        self.assertEqual(len(prints2), 2)
        named = sf.get_card_named("Lightning Bolt")
        self.assertIsNotNone(named)
        self.assertEqual(named["name"], "Lightning Bolt")
        cheapest = sf.get_cheapest_tcgplayer_price("Lightning Bolt", finish="nonfoil")
        self.assertEqual(cheapest["price"], 1.20)
        self.assertEqual(mock_session.get.call_count, 1)

    def test_mightymeeple_fast_mode_aborts_on_cloudflare_and_avoids_duplicate_variant_fetches(self):
        """MightyMeepleProvider fast_mode aborts immediately on CF 403/429 without sleeping, and _filter_products caches per call."""
        mm = MightyMeepleProvider(session=MagicMock())

        # 1. Cloudflare 429 with Retry-After: 60 on variant fetch -> fast_mode must abort after 1 variant call with 0 sleep
        suggest_resp = MagicMock(status_code=200, headers={}, text="{}")
        suggest_resp.json.return_value = {
            "resources": {
                "results": {
                    "products": [
                        {"title": f"Sol Ring [Set {i}]", "handle": f"sol-ring-{i}", "available": True, "url": f"/products/sol-ring-{i}", "price": "2.50"}
                        for i in range(6)
                    ]
                }
            }
        }
        cf_resp = MagicMock(
            status_code=429,
            headers={"Retry-After": "60", "cf-mitigated": "challenge"},
            text="Just a moment...",
        )

        def fake_get(url, timeout=8, **kwargs):
            if "suggest.json" in url:
                return suggest_resp
            return cf_resp

        mm.session.get.side_effect = fake_get

        t0 = time.monotonic()
        with patch("providers.mightymeeple.time.sleep") as mock_sleep:
            res = mm.search_card("Sol Ring", finish="any", fast_mode=True)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 1.0)
        mock_sleep.assert_not_called()
        self.assertEqual(res["vendor_name"], "Mighty Meeple")
        # 2 suggest calls + 1 variant call that hit CF and aborted
        variant_calls = [c for c in mm.session.get.call_args_list if c.args[0].endswith(".js")]
        self.assertEqual(len(variant_calls), 1)

        # 2. _filter_products Step 2 -> Step 2b must not re-fetch handles that returned [] in Step 2
        mm.session.get.reset_mock()
        empty_variant_resp = MagicMock(status_code=200, headers={}, text='{"variants": []}')
        mm.session.get.side_effect = None
        mm.session.get.return_value = empty_variant_resp

        products = [
            {"title": "Elesh Norn [ONE]", "handle": "elesh-norn-1", "available": True},
            {"title": "Elesh Norn [PONE]", "handle": "elesh-norn-2", "available": True},
        ]
        filtered = mm._filter_products(
            products=products,
            card_name="Elesh Norn",
            set_name="Phyrexia",
            set_code="ONE",
            collector_number="10",
            fast_mode=True,
        )
        self.assertEqual(len(filtered), 2)
        # Only 1 fetch per handle across both Step 2 and Step 2b
        self.assertEqual(mm.session.get.call_count, 2)

    def test_deal_engine_fast_mode_runs_vendors_in_parallel_with_budget(self):
        """DealEngine.poll_card(..., fast_mode=True) runs MM & eBay concurrently and returns within fast budget."""
        engine = DealEngine(app=self.app)
        with self.app.app_context():
            item = WatchlistItem(
                user_id=self.user_id,
                name="Counterspell",
                finish="nonfoil",
                target_price=2.00,
            )
            db.session.add(item)
            db.session.commit()

            def slow_mm(*args, **kwargs):
                time.sleep(0.35)
                return {
                    "vendor_name": "Mighty Meeple",
                    "price": 1.50,
                    "condition": "NM",
                    "in_stock": True,
                    "product_url": "https://mightymeeple.com/products/counterspell",
                }

            def slow_ebay(*args, **kwargs):
                time.sleep(0.35)
                return {
                    "vendor_name": "eBay",
                    "price": 1.80,
                    "condition": "Buy It Now",
                    "in_stock": True,
                    "product_url": "https://www.ebay.com/itm/123",
                    "search_url": "https://www.ebay.com/sch/i.html",
                }

            tcg_stub = {
                "vendor_name": "TCGplayer",
                "price": 1.60,
                "condition": "Market NM",
                "in_stock": True,
                "product_url": "https://www.tcgplayer.com/counterspell",
            }

            with patch.object(engine.scryfall, "get_cheapest_tcgplayer_price", return_value=tcg_stub), \
                 patch.object(engine.mightymeeple, "search_card", side_effect=slow_mm) as mm_mock, \
                 patch.object(engine.ebay, "search_card", side_effect=slow_ebay) as ebay_mock:
                t0 = time.monotonic()
                res = engine.poll_card(item, notify=False, fast_mode=True)
                elapsed = time.monotonic() - t0

            # Concurrent 0.35s + 0.35s should finish well under 0.65s (not 0.70s+ serial)
            self.assertLess(elapsed, 0.65)
            self.assertTrue(mm_mock.call_args.kwargs.get("fast_mode"))
            self.assertEqual(ebay_mock.call_args.kwargs.get("timeout"), 4.0)
            self.assertEqual(res["lowest_price"], 1.50)
            self.assertTrue(res["is_deal"])

    def test_rendered_index_toast_zindex_and_safe_acquire_button_quotes(self):
        """Rendered / page puts toast container above modals and safely encodes double quotes/apostrophes on Acquire buttons."""
        with self.app.app_context():
            item = WatchlistItem(
                user_id=self.user_id,
                name='Kongming, "Sleeping Dragon\'s" Aid',
                set_code="ME3",
                collector_number="15",
                finish="nonfoil",
                target_price=5.00,
            )
            db.session.add(item)
            db.session.commit()
            item_id = item.id

        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        self.assertIn('id="toast-container"', html)
        self.assertIn('style="z-index: 9999;"', html)
        self.assertIn("openAcquireCardModalFromBtn(this)", html)
        self.assertIn(f'data-item-id="{item_id}"', html)
        self.assertIn("Kongming, &#34;Sleeping Dragon&#39;s&#34; Aid", html)


if __name__ == "__main__":
    unittest.main()
