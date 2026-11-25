"""
Unit & Integration Tests for Star City Games and CoolStuffInc Providers,
DealEngine polling/alerts, Flask toggle/deals routes, and MCP server tools.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from deal_engine import DealEngine
from models import AllowedEmail, SystemSetting, User, VendorPrice, WatchlistItem, db
from providers.coolstuffinc import CoolStuffIncProvider
from providers.starcitygames import StarCityGamesProvider


class ScgProviderUnitTests(unittest.TestCase):
    def setUp(self):
        StarCityGamesProvider.clear_shared_cache()

    def test_scg_singles_matching_finish_condition_and_caching(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Results": [
                {
                    "DocId": "101",
                    "Document": {
                        "card_name": ["Sol Ring"],
                        "item_display_name": ["Sol Ring"],
                        "language": ["English"],
                        "set": ["Commander Masters"],
                        "collector_number": ["410"],
                        "finish": ["Non-foil"],
                        "sku": ["SGL-MTG-CMM-410-ENN"],
                        "url_detail": ["/sol-ring-sgl-mtg-cmm-410-enn/"],
                        "hawk_child_attributes": [
                            {
                                "qty": [0],
                                "calculated_price": ["2.99"],
                                "option_values": ['{"label":"Near Mint"}'],
                            },
                            {
                                "qty": [4],
                                "calculated_price": ["2.49"],
                                "option_values": ['{"label":"Played"}'],
                            },
                        ],
                    },
                },
                {
                    "DocId": "102",
                    "Document": {
                        "card_name": ["Sol Ring"],
                        "item_display_name": ["Sol Ring"],
                        "language": ["English"],
                        "set": ["Commander Masters"],
                        "collector_number": ["410"],
                        "finish": ["Foil"],
                        "sku": ["SGL-MTG-CMM-410-ENF"],
                        "url_detail": ["/sol-ring-sgl-mtg-cmm-410-enf/"],
                        "hawk_child_attributes": [
                            {
                                "qty": [2],
                                "calculated_price": ["5.99"],
                                "option_values": ['{"label":"Near Mint"}'],
                            }
                        ],
                    },
                },
            ]
        }
        mock_session.post.return_value = mock_resp

        provider = StarCityGamesProvider(session=mock_session, cache_ttl_seconds=600, use_shared_cache=False)
        nf = provider.search_card("Sol Ring", set_code="CMM", collector_number="410", finish="nonfoil")
        self.assertIsNotNone(nf)
        self.assertEqual(nf["vendor_name"], "Star City Games")
        self.assertEqual(nf["price"], 2.49)
        self.assertEqual(nf["condition"], "LP")
        self.assertTrue(nf["in_stock"])
        self.assertEqual(nf["product_url"], "https://starcitygames.com/sol-ring-sgl-mtg-cmm-410-enn/")

        # Second call with foil uses cached HawkSearch response (1 HTTP call total)
        foil = provider.search_card("Sol Ring", set_code="CMM", collector_number="410", finish="foil")
        self.assertIsNotNone(foil)
        self.assertEqual(foil["price"], 5.99)
        self.assertEqual(foil["condition"], "NM")
        self.assertEqual(mock_session.post.call_count, 1)

    def test_scg_sealed_find_offer_display_to_box(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Results": [
                {
                    "DocId": "201",
                    "Document": {
                        "item_display_name": ["Wilds of Eldraine Set Booster Box"],
                        "language": ["English"],
                        "set": ["Sealed Products"],
                        "tcg_player_id": ["509999"],
                        "url_detail": ["/wilds-of-eldraine-set-booster-box/"],
                        "hawk_child_attributes": [
                            {"qty": [3], "calculated_price": ["129.99"], "option_values": []}
                        ],
                    },
                }
            ]
        }
        mock_session.post.return_value = mock_resp

        provider = StarCityGamesProvider(session=mock_session, cache_ttl_seconds=600, use_shared_cache=False)
        offer = provider.find_offer("Wilds of Eldraine - Set Booster Display", tcgplayer_product_id=509999)
        self.assertIsNotNone(offer)
        self.assertEqual(offer["vendor_name"], "Star City Games")
        self.assertEqual(offer["price"], 129.99)
        self.assertTrue(offer["in_stock"])
        self.assertEqual(offer["condition"], "Unopened")


class CsiProviderUnitTests(unittest.TestCase):
    SAMPLE_SINGLES_HTML = """
    <html><body>
      <div class="row product-search-row main-container" itemscope itemtype="https://schema.org/Product">
        <a class="productLink" href="/p/Magic%3A+The+Gathering/Sol+Ring">
          <img itemprop="image" src="https://res.cloudinary.com/csicdn/image/upload/v1/Images/Products/mtg%20art/Final%20Fantasy%20Commander/full/FIC0357.jpg"/>
        </a>
        <h3><a class="productLink" href="/p/Magic%3A+The+Gathering/Sol+Ring"><span itemprop="name">Sol Ring</span></a></h3>
        <div class="breadcrumb-trail">Magic: The Gathering » Final Fantasy Commander</div>
        <span class="rating-display" data-pid="415448"></span>
        <div class="product-notes"><strong>Notes:</strong> 0357 - FFVII</div>
        <div class="search-ppq-cell">
          <div class="row" itemprop="offers" itemscope itemtype="https://schema.org/Offer">
            <span class="card-qty">20+</span> Near Mint
            <b itemprop="price" content="2.75">2.75</b>
            <meta itemprop="availability" content="https://schema.org/InStock"/>
          </div>
          <div class="row" itemprop="offers" itemscope itemtype="https://schema.org/Offer">
            <span class="card-qty">3</span> Foil Near Mint
            <b itemprop="price" content="6.50">6.50</b>
            <meta itemprop="availability" content="https://schema.org/InStock"/>
          </div>
        </div>
      </div>
    </body></html>
    """

    SAMPLE_SEALED_HTML = """
    <html><body>
      <div class="row product-search-row main-container" itemscope itemtype="https://schema.org/Product">
        <a class="productLink" href="/p/391000"><img itemprop="image" src="https://example.com/woe.jpg"/></a>
        <h3><a class="productLink" href="/p/391000"><span itemprop="name">MTG - Wilds of Eldraine - Set Booster Box</span></a></h3>
        <div class="breadcrumb-trail">Magic: The Gathering » Wilds of Eldraine</div>
        <span class="rating-display" data-pid="391000"></span>
        <div class="search-ppq-cell">
          <div class="row" itemprop="offers" itemscope itemtype="https://schema.org/Offer">
            <span class="card-qty">2</span> New
            <b itemprop="price" content="124.99">124.99</b>
            <meta itemprop="availability" content="https://schema.org/InStock"/>
          </div>
        </div>
      </div>
    </body></html>
    """

    def setUp(self):
        CoolStuffIncProvider.clear_shared_cache()

    def test_csi_singles_parsing_and_403_recovery(self):
        mock_session = MagicMock()
        r_403 = MagicMock(status_code=403, text="")
        r_ok = MagicMock(status_code=200, text=self.SAMPLE_SINGLES_HTML)
        mock_session.get.side_effect = [r_403, MagicMock(status_code=200, text=""), r_ok]

        provider = CoolStuffIncProvider(session=mock_session, cache_ttl_seconds=600, use_shared_cache=False)
        nf = provider.search_card("Sol Ring", set_code="FIC", collector_number="357", finish="nonfoil")
        self.assertIsNotNone(nf)
        self.assertEqual(nf["vendor_name"], "CoolStuffInc")
        self.assertEqual(nf["price"], 2.75)
        self.assertEqual(nf["qty_retail"], 20)
        self.assertTrue(nf["in_stock"])
        self.assertEqual(nf["product_url"], "https://www.coolstuffinc.com/p/415448")

        foil = provider.search_card("Sol Ring", set_code="FIC", collector_number="357", finish="foil")
        self.assertIsNotNone(foil)
        self.assertEqual(foil["price"], 6.50)
        self.assertEqual(foil["condition"], "NM Foil")

    def test_csi_sealed_find_offer(self):
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(status_code=200, text=self.SAMPLE_SEALED_HTML)
        provider = CoolStuffIncProvider(session=mock_session, cache_ttl_seconds=600, use_shared_cache=False)

        offer = provider.find_offer("Wilds of Eldraine - Set Booster Display")
        self.assertIsNotNone(offer)
        self.assertEqual(offer["vendor_name"], "CoolStuffInc")
        self.assertEqual(offer["price"], 124.99)
        self.assertTrue(offer["in_stock"])
        self.assertEqual(offer["condition"], "Unopened")


class ScgCsiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "DISCORD_WEBHOOK_URL": "",
            "ADMIN_EMAIL": "admin@chimera.local",
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

    def _login(self):
        with self.app.app_context():
            db.session.add(AllowedEmail(email="tester@chimera.local", is_admin=False, added_by="test"))
            u = User(email="tester@chimera.local", name="Tester", is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        with self.client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["user_email"] = "tester@chimera.local"
            sess["is_admin"] = False
        return uid

    def test_poll_card_upserts_scg_csi_and_dispatches_restock_alerts(self):
        uid = self._login()
        with self.app.app_context():
            item = WatchlistItem(
                user_id=uid,
                name="Sol Ring",
                scryfall_id="abc-123",
                finish="nonfoil",
                target_price=1.00,
                notify_mm_stock=False,
                notify_ck_stock=False,
                notify_scg_stock=True,
                notify_csi_stock=True,
            )
            db.session.add(item)
            db.session.commit()
            item_id = item.id

            engine = DealEngine(app=self.app)
            with patch.object(engine.scryfall, "get_tcgplayer_price", return_value={"vendor_name": "TCGplayer", "price": 2.00, "condition": "NM", "in_stock": True, "product_url": "https://tcg.test"}), \
                 patch.object(engine.mightymeeple, "search_card", return_value=None), \
                 patch.object(engine.ebay, "search_card", return_value=None), \
                 patch.object(engine.cardkingdom_singles, "search_card", return_value=None), \
                 patch.object(engine.starcitygames, "search_card", return_value={"vendor_name": "Star City Games", "price": 2.49, "condition": "NM", "in_stock": True, "product_url": "https://starcitygames.com/sol-ring"}), \
                 patch.object(engine.coolstuffinc, "search_card", return_value={"vendor_name": "CoolStuffInc", "price": 2.25, "condition": "NM", "in_stock": True, "product_url": "https://www.coolstuffinc.com/p/1"}), \
                 patch.object(engine, "send_discord_scg_stock_alert") as mock_scg_alert, \
                 patch.object(engine, "send_discord_csi_stock_alert") as mock_csi_alert:
                res = engine.poll_card(item, notify=True, fast_mode=False)

            self.assertTrue(res["scg_in_stock"])
            self.assertTrue(res["csi_in_stock"])
            self.assertEqual(mock_scg_alert.call_count, 1)
            self.assertEqual(mock_csi_alert.call_count, 1)

            refreshed = db.session.get(WatchlistItem, item_id)
            self.assertEqual(refreshed.star_city_games_price, 2.49)
            self.assertEqual(refreshed.coolstuffinc_price, 2.25)

    def test_toggle_routes_cadence_and_deals_sealed_board(self):
        uid = self._login()
        with self.app.app_context():
            card = WatchlistItem(user_id=uid, name="Counterspell", finish="nonfoil", target_price=2.0)
            sealed = WatchlistItem(
                user_id=uid,
                name="Wilds of Eldraine - Set Booster Display",
                finish="sealed",
                tcgplayer_product_id=509999,
                target_price=130.0,
            )
            db.session.add_all([card, sealed])
            db.session.flush()
            db.session.add_all([
                VendorPrice(watchlist_id=sealed.id, vendor_name="Star City Games", price=129.99, condition="Unopened", in_stock=True, product_url="https://starcitygames.com/woe"),
                VendorPrice(watchlist_id=sealed.id, vendor_name="CoolStuffInc", price=124.99, condition="Unopened", in_stock=True, product_url="https://www.coolstuffinc.com/p/391000"),
            ])
            db.session.commit()
            cid = card.id

        r_scg = self.client.post(f"/api/watchlist/toggle-scg-alert/{cid}")
        self.assertEqual(r_scg.status_code, 200)
        self.assertFalse(r_scg.get_json()["notify_scg_stock"])

        r_csi = self.client.post(f"/api/watchlist/toggle-csi-alert/{cid}")
        self.assertEqual(r_csi.status_code, 200)
        self.assertFalse(r_csi.get_json()["notify_csi_stock"])

        r_cad = self.client.post(
            "/api/settings/cadence",
            data=json.dumps({"notify_scg_stock_enabled": False, "notify_csi_stock_enabled": True}),
            content_type="application/json",
        )
        self.assertEqual(r_cad.status_code, 200)
        self.assertFalse(r_cad.get_json()["notify_scg_stock_enabled"])
        self.assertTrue(r_cad.get_json()["notify_csi_stock_enabled"])

        r_deals = self.client.get("/deals")
        self.assertEqual(r_deals.status_code, 200)
        html = r_deals.data.decode("utf-8")
        self.assertIn("Star City Games", html)
        self.assertIn("CoolStuffInc", html)
        self.assertIn("$129.99", html)
        self.assertIn("$124.99", html)

        r_idx = self.client.get("/")
        self.assertEqual(r_idx.status_code, 200)
        idx_html = r_idx.data.decode("utf-8")
        self.assertIn("scg_in_stock", idx_html)
        self.assertIn("csi_in_stock", idx_html)
        self.assertIn(f"scg-alert-btn-{cid}", idx_html)
        self.assertIn(f"csi-alert-btn-{cid}", idx_html)

        # Batched Refresh-All endpoints (Cloud Run friendly)
        r_ids = self.client.get("/api/watchlist/ids")
        self.assertEqual(r_ids.status_code, 200)
        ids_json = r_ids.get_json()
        self.assertEqual(ids_json["total"], 2)

        with patch.object(DealEngine, "poll_user_cards", return_value=[{"is_deal": False}]) as mock_poll:
            r_batch = self.client.post(
                "/api/watchlist/refresh-batch",
                data=json.dumps({"item_ids": [cid], "finalize": True}),
                content_type="application/json",
            )
            self.assertEqual(r_batch.status_code, 200)
            self.assertEqual(r_batch.get_json()["polled"], 1)
            self.assertEqual(mock_poll.call_count, 1)


if __name__ == "__main__":
    unittest.main()
