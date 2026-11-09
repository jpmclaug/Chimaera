import unittest
import json
from app import create_app
from models import (
    db,
    User,
    AllowedEmail,
    WatchlistItem,
    VendorPrice,
    UserInventoryCard,
    ActivityLog,
    DeckAnalysis,
    TCGPlayerOrder,
)
from tcgplayer_parser import TCGPlayerPurchaseParser, TCGPlayerCardItem, TCGPlayerEmailFetcher
from card_add_evaluator import CardAddEvaluator
from inventory_manager import InventoryManager


SAMPLE_TCG_TEXT = """Qty\tDescription
1\tMagic - Aether Revolt - Metallic Rebuke - Near Mint
1\tMagic - Commander Legends: Battle for Baldur's Gate - Abdel Adrian, Gorion's Ward (Showcase) - Near Mint
1\tMagic - Commander Masters - Palace Jailer - Near Mint
1\tMagic - Dominaria - Artificer's Assistant - Lightly Played
1\tMagic - Duskmourn: House of Horror - Clockwork Percussionist (0130) - Near Mint
1\tMagic - Ice Age - Snow-Covered Mountain - Moderately Played
1\tMagic - Ikoria: Lair of Behemoths - Of One Mind - Lightly Played
1\tMagic - Kamigawa: Neon Dynasty - Twinshot Sniper - Near Mint
1\tMagic - Mirrodin - Bonesplitter - Lightly Played
2\tMagic - Mirrodin - Frogmite - Moderately Played
1\tMagic - Mirrodin - Silver Myr - Lightly Played
1\tMagic - Mirrodin Besieged - Steel Sabotage - Lightly Played
1\tMagic - Modern Horizons 2 - Sojourner's Companion - Lightly Played
1\tMagic - Modern Horizons 3 - Evolution Witness - Near Mint
1\tMagic - Modern Horizons 3 - Molten Gatekeeper - Lightly Played
1\tMagic - Phyrexia: All Will Be One - Norn's Wellspring - Lightly Played
1\tMagic - Shards of Alara - Dragon Fodder - Near Mint
1\tMagic - Streets of New Capenna - Sticky Fingers - Near Mint
1\tMagic - The List Reprints - Hymn of the Wilds - Near Mint
1\tMagic - The List Reprints - Tarfire - Lightly Played
1\tMagic - Theros - Fleetfeather Sandals - Near Mint
1\tMagic - Universes Beyond: The Lord of the Rings: Tales of Middle-earth - Birthday Escape - Near Mint
1\tMagic - Universes Beyond: The Lord of the Rings: Tales of Middle-earth - Mauhur, Uruk-hai Captain - Near Mint
1\tMagic - Universes Beyond: The Lord of the Rings: Tales of Middle-earth - Mordor Muster - Near Mint
1\tMagic - Universes Beyond: The Lord of the Rings: Tales of Middle-earth - Orcish Medicine - Lightly Played
1\tMagic - Universes Beyond: The Lord of the Rings: Tales of Middle-earth - Swarming of Moria - Near Mint
1\tMagic - Urza's Legacy - Weatherseed Elf - Lightly Played
1\tMagic - Urza's Saga - Falter - Lightly Played
1\tMagic - Wilds of Eldraine - Collector's Vault - Lightly Played
1\tMagic - Wilds of Eldraine - Gingerbrute - Near Mint
1\tMagic - Zendikar Rising - Swamp (274) - Full Art - Moderately Played"""


class TestTCGPlayerPurchaseReconciliation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "DISCORD_WEBHOOK_URL": "",
            "ADMIN_EMAIL": "test@chimera.local",
        })
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        with self.app.app_context():
            db.session.query(VendorPrice).delete()
            db.session.query(WatchlistItem).delete()
            db.session.query(UserInventoryCard).delete()
            db.session.query(TCGPlayerOrder).delete()
            db.session.query(DeckAnalysis).delete()
            db.session.query(ActivityLog).delete()
            db.session.query(User).delete()
            db.session.query(AllowedEmail).delete()
            db.session.commit()

    def login_as(self, email="test@chimera.local"):
        with self.app.app_context():
            allowed = AllowedEmail.get_by_email(email)
            if not allowed:
                allowed = AllowedEmail(email=email, is_admin=True, notes="Test Account", added_by="Test")
                db.session.add(allowed)
                db.session.commit()
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email, name="Tester", is_admin=True, is_active=True)
                db.session.add(user)
                db.session.commit()

            with self.client.session_transaction() as sess:
                sess["user_id"] = user.id
                sess["user_email"] = user.email
                sess["is_admin"] = user.is_admin
            return user.id

    # ----------------------------------------------------------------------
    # Parser Unit Tests
    # ----------------------------------------------------------------------

    def test_parse_sample_purchase_manifest(self):
        items = TCGPlayerPurchaseParser.parse(SAMPLE_TCG_TEXT)
        self.assertEqual(len(items), 31)

        # First item: Metallic Rebuke
        rebuke = items[0]
        self.assertEqual(rebuke.quantity, 1)
        self.assertEqual(rebuke.card_name, "Metallic Rebuke")
        self.assertEqual(rebuke.set_name, "Aether Revolt")
        self.assertEqual(rebuke.condition, "Near Mint")
        self.assertEqual(rebuke.finish, "nonfoil")

        # Showcase card
        abdel = items[1]
        self.assertEqual(abdel.card_name, "Abdel Adrian, Gorion's Ward")
        self.assertEqual(abdel.treatment, "Showcase")
        self.assertEqual(abdel.condition, "Near Mint")

        # Collector number in parentheses
        clockwork = items[4]
        self.assertEqual(clockwork.card_name, "Clockwork Percussionist")
        self.assertEqual(clockwork.collector_number, "0130")

        # Quantity 2
        frogmite = [i for i in items if i.card_name == "Frogmite"][0]
        self.assertEqual(frogmite.quantity, 2)
        self.assertEqual(frogmite.condition, "Moderately Played")

        # Full Art land with collector number
        swamp = items[-1]
        self.assertEqual(swamp.card_name, "Swamp")
        self.assertEqual(swamp.collector_number, "274")
        self.assertEqual(swamp.condition, "Moderately Played")
        self.assertIn("Full Art", swamp.treatment)

    def test_parse_edge_case_formats(self):
        # Space separated
        line_space = "1   Magic - Dominaria - Artificer's Assistant - Lightly Played"
        p_space = TCGPlayerPurchaseParser.parse_line(line_space)
        self.assertIsNotNone(p_space)
        self.assertEqual(p_space.card_name, "Artificer's Assistant")
        self.assertEqual(p_space.condition, "Lightly Played")

        # Without Magic prefix
        line_no_prefix = "1 - Modern Horizons 3 - Evolution Witness - Near Mint"
        p_no_prefix = TCGPlayerPurchaseParser.parse_line(line_no_prefix)
        self.assertIsNotNone(p_no_prefix)
        self.assertEqual(p_no_prefix.card_name, "Evolution Witness")

        # Foil suffix
        line_foil = "1 Magic - Neon Dynasty - Boseiju, Who Endures - Foil - Near Mint"
        p_foil = TCGPlayerPurchaseParser.parse_line(line_foil)
        self.assertIsNotNone(p_foil)
        self.assertEqual(p_foil.card_name, "Boseiju, Who Endures")
        self.assertEqual(p_foil.finish, "foil")
        self.assertEqual(p_foil.condition, "Near Mint")

        # Empty / Header
        self.assertIsNone(TCGPlayerPurchaseParser.parse_line(""))
        self.assertIsNone(TCGPlayerPurchaseParser.parse_line("Qty\tDescription"))
        self.assertIsNone(TCGPlayerPurchaseParser.parse_line("Quantity   Product Name"))

    # ----------------------------------------------------------------------
    # API Preview Route Tests
    # ----------------------------------------------------------------------

    def test_reconcile_preview_endpoint(self):
        user_id = self.login_as()

        # Seed targets on user watchlist
        with self.app.app_context():
            t1 = WatchlistItem(user_id=user_id, name="Metallic Rebuke", target_price=0.50, tag="Atraxa")
            t2 = WatchlistItem(user_id=user_id, name="Abdel Adrian, Gorion's Ward", target_price=0.99, tag="Blink")
            t3 = WatchlistItem(user_id=user_id, name="Frogmite", target_price=1.25, tag="Affinity")
            t4 = WatchlistItem(user_id=user_id, name="Rhystic Study", target_price=35.00, tag="Staples")
            db.session.add_all([t1, t2, t3, t4])
            db.session.commit()

        # Send sample text
        res = self.client.post(
            "/api/watchlist/reconcile-purchase/preview",
            data=json.dumps({"raw_text": SAMPLE_TCG_TEXT}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertEqual(data["total_purchased_lines"], 31)
        self.assertEqual(data["matched_targets_count"], 3)  # Metallic Rebuke, Abdel Adrian, Frogmite

        matched_names = {m["name"] for m in data["matched_targets"]}
        self.assertEqual(matched_names, {"Metallic Rebuke", "Abdel Adrian, Gorion's Ward", "Frogmite"})

        # Rhystic Study was NOT in the purchase manifest
        self.assertNotIn("Rhystic Study", matched_names)

        # Unmatched purchases count should be 31 - 3 = 28
        self.assertEqual(data["unmatched_purchases_count"], 28)

    def test_reconcile_preview_empty_payload(self):
        self.login_as()
        res = self.client.post(
            "/api/watchlist/reconcile-purchase/preview",
            data=json.dumps({"raw_text": ""}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    # ----------------------------------------------------------------------
    # API Execute Route Tests
    # ----------------------------------------------------------------------

    def test_reconcile_execute_endpoint(self):
        user_id = self.login_as()

        with self.app.app_context():
            t1 = WatchlistItem(user_id=user_id, name="Metallic Rebuke", target_price=0.50)
            t2 = WatchlistItem(user_id=user_id, name="Frogmite", target_price=1.25)
            t3 = WatchlistItem(user_id=user_id, name="Rhystic Study", target_price=35.00)
            db.session.add_all([t1, t2, t3])
            db.session.commit()
            t1_id, t2_id, t3_id = t1.id, t2.id, t3.id

            # Add vendor price for t1
            vp = VendorPrice(watchlist_id=t1_id, vendor_name="TCGplayer", price=0.45, in_stock=True)
            db.session.add(vp)
            db.session.commit()

        # Execute de-registration for t1 and t2
        res = self.client.post(
            "/api/watchlist/reconcile-purchase/execute",
            data=json.dumps({
                "target_ids": [t1_id, t2_id],
                "add_to_inventory": True,
                "purchased_items": [
                    {"card_name": "Metallic Rebuke", "quantity": 1, "set_name": "Aether Revolt", "finish": "nonfoil", "condition": "Near Mint"},
                    {"card_name": "Frogmite", "quantity": 2, "set_name": "Mirrodin", "finish": "nonfoil", "condition": "Moderately Played"},
                ]
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["deleted_count"], 2)
        self.assertEqual(data["inventory_added_count"], 2)

        # Verify DB state
        with self.app.app_context():
            remaining_targets = WatchlistItem.query.filter_by(user_id=user_id).all()
            self.assertEqual(len(remaining_targets), 1)
            self.assertEqual(remaining_targets[0].id, t3_id)
            self.assertEqual(remaining_targets[0].name, "Rhystic Study")

            # Vendor price deleted
            vps = VendorPrice.query.filter_by(watchlist_id=t1_id).all()
            self.assertEqual(len(vps), 0)

            # Inventory cards added
            inv = UserInventoryCard.query.filter_by(user_id=user_id).all()
            self.assertEqual(len(inv), 2)
            frogmite_inv = next(i for i in inv if i.name == "Frogmite")
            self.assertEqual(frogmite_inv.quantity, 2)
            self.assertEqual(frogmite_inv.condition, "Moderately Played")

            # Activity log recorded
            logs = ActivityLog.query.filter_by(action="PURCHASE_RECONCILIATION").all()
            self.assertEqual(len(logs), 1)
            self.assertIn("Metallic Rebuke", logs[0].details)

    # ----------------------------------------------------------------------
    # Email HTML Parsing, Trailing Prices & CardAddEvaluator Integration
    # ----------------------------------------------------------------------

    def test_parse_trailing_price_and_email_html(self):
        # Line with trailing price
        line_price = "1\tMagic - Aether Revolt - Metallic Rebuke - Near Mint\t$0.49"
        p_price = TCGPlayerPurchaseParser.parse_line(line_price)
        self.assertIsNotNone(p_price)
        self.assertEqual(p_price.card_name, "Metallic Rebuke")
        self.assertEqual(p_price.condition, "Near Mint")
        self.assertAlmostEqual(p_price.price, 0.49)

        # Boilerplate ignored
        self.assertIsNone(TCGPlayerPurchaseParser.parse_line("Shipping & Handling\t$1.99"))
        self.assertIsNone(TCGPlayerPurchaseParser.parse_line("Order Total: $14.50"))

        # HTML Email body parsing
        sample_html = """
        <html>
          <body>
            <h2>Thank you for your TCGplayer order!</h2>
            <p>Order Number: 123456-ABCDEF</p>
            <p>Package from Seller: CardKingdomDirect</p>
            <table>
              <tr><th>Quantity</th><th>Description</th><th>Price</th></tr>
              <tr>
                <td>1</td>
                <td>Magic - Modern Horizons 3 - Evolution Witness - Near Mint</td>
                <td>$0.35</td>
              </tr>
              <tr>
                <td>2</td>
                <td>Magic - Mirrodin - Frogmite - Moderately Played</td>
                <td>$0.50</td>
              </tr>
            </table>
          </body>
        </html>
        """
        items, order_numbers, sellers = TCGPlayerPurchaseParser.parse_email_html(sample_html)
        self.assertEqual(len(items), 2)
        self.assertIn("123456-ABCDEF", order_numbers)
        self.assertIn("CardKingdomDirect", sellers)
        self.assertEqual(items[0].card_name, "Evolution Witness")
        self.assertAlmostEqual(items[0].price, 0.35)
        self.assertEqual(items[1].card_name, "Frogmite")
        self.assertEqual(items[1].quantity, 2)

        # CardAddEvaluator().parse_card_input should seamlessly parse TCGplayer manifest text
        parsed_eval = CardAddEvaluator().parse_card_input(SAMPLE_TCG_TEXT)
        self.assertEqual(len(parsed_eval), 31)
        self.assertEqual(parsed_eval[0]["name"], "Metallic Rebuke")
        self.assertEqual(parsed_eval[0]["quantity"], 1)

    def test_mock_imap_email_fetcher(self):
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = "Your TCGplayer.com Order Confirmation (#998877-XYZ)"
        msg["From"] = "sales@tcgplayer.com"
        msg["To"] = "test@chimera.local"
        msg["Date"] = "Fri, 08 Nov 2026 15:30:00 -0500"
        msg["Message-ID"] = "<order-998877@tcgplayer.com>"
        msg.set_content(
            "Order #998877-XYZ\n"
            "Qty\tDescription\n"
            "1\tMagic - Aether Revolt - Metallic Rebuke - Near Mint\t$0.49\n"
            "1\tMagic - Mirrodin - Silver Myr - Lightly Played\t$0.25\n"
        )
        raw_bytes = msg.as_bytes()

        class FakeIMAP:
            def select(self, folder, readonly=True):
                return ("OK", [b"1"])

            def search(self, charset, *criteria):
                return ("OK", [b"101"])

            def fetch(self, msg_id, parts):
                return ("OK", [(b"101 (RFC822)", raw_bytes)])

            def logout(self):
                return ("BYE", [b""])

        fetcher = TCGPlayerEmailFetcher(
            email_address="test@chimera.local",
            app_password="abcd efgh ijkl mnop",
            imap_conn=FakeIMAP(),
        )
        res = fetcher.fetch_recent_orders(days_back=30, max_emails=5)
        self.assertEqual(res["orders_found"], 1)
        self.assertEqual(res["total_cards_quantity"], 2)
        self.assertIn("Metallic Rebuke", res["manifest_text"])
        self.assertEqual(res["orders"][0]["order_number"], "998877-XYZ")

    # ----------------------------------------------------------------------
    # Commander Deck Fit & Ordered -> Complete Lifecycle Tests
    # ----------------------------------------------------------------------

    def test_preview_deck_fit_and_ordered_to_complete_lifecycle(self):
        user_id = self.login_as()

        # Seed a saved Commander deck (Urza / Artifact theme) and a Watchlist item
        with self.app.app_context():
            deck_cards = [
                {
                    "name": "Urza, Lord High Artificer",
                    "quantity": 1,
                    "is_commander": True,
                    "type_line": "Legendary Creature — Human Artificer",
                    "oracle_text": "Tap an untapped artifact you control: Add {U}.",
                    "cmc": 4,
                    "color_identity": ["U"],
                    "mana_cost": "{2}{U}{U}",
                },
                {
                    "name": "Cancel",
                    "quantity": 1,
                    "is_commander": False,
                    "type_line": "Instant",
                    "oracle_text": "Counter target spell.",
                    "cmc": 3,
                    "color_identity": ["U"],
                    "mana_cost": "{1}{U}{U}",
                },
                {
                    "name": "Sky Diamond",
                    "quantity": 1,
                    "is_commander": False,
                    "type_line": "Artifact",
                    "oracle_text": "Sky Diamond enters the battlefield tapped. {T}: Add {U}.",
                    "cmc": 2,
                    "color_identity": ["U"],
                    "mana_cost": "{2}",
                },
                {
                    "name": "Ornithopter",
                    "quantity": 1,
                    "is_commander": False,
                    "type_line": "Artifact Creature — Thopter",
                    "oracle_text": "Flying",
                    "cmc": 0,
                    "color_identity": [],
                    "mana_cost": "{0}",
                },
            ]
            deck = DeckAnalysis(
                user_id=user_id,
                deck_name="Urza Artifacts",
                commander_name="Urza, Lord High Artificer",
                color_identity="U",
                power_bracket="Bracket 3: Upgraded",
                archetype="Artifacts",
                total_cards=4,
                cards_data=json.dumps(deck_cards),
            )
            db.session.add(deck)
            w_item = WatchlistItem(
                user_id=user_id,
                name="Metallic Rebuke",
                target_price=0.50,
                tag="Urza Artifacts",
            )
            db.session.add(w_item)
            db.session.commit()
            w_item_id = w_item.id

        manifest_subset = (
            "Qty\tDescription\n"
            "1\tMagic - Aether Revolt - Metallic Rebuke - Near Mint\n"
            "2\tMagic - Mirrodin - Frogmite - Moderately Played\n"
            "1\tMagic - Mirrodin - Silver Myr - Lightly Played\n"
        )

        # 1. Preview with Deck Fit
        prev_res = self.client.post(
            "/api/watchlist/reconcile-purchase/preview",
            data=json.dumps({"raw_text": manifest_subset}),
            content_type="application/json",
        )
        self.assertEqual(prev_res.status_code, 200)
        prev_data = prev_res.get_json()
        self.assertEqual(prev_data["evaluated_decks_count"], 1)
        self.assertTrue(len(prev_data["deck_sorting_groups"]) >= 1)

        # Metallic Rebuke should have deck_fit attached pointing to Urza Artifacts
        matched = prev_data["matched_targets"][0]
        self.assertEqual(matched["name"], "Metallic Rebuke")
        self.assertIsNotNone(matched.get("deck_fit"))
        self.assertEqual(matched["deck_fit"]["best_deck_name"], "Urza Artifacts")
        self.assertTrue(matched["deck_fit"]["watchlist_tag_matched_deck"])

        # 2. Execute reconciliation with inventory_mode="ordered"
        exec_res = self.client.post(
            "/api/watchlist/reconcile-purchase/execute",
            data=json.dumps({
                "target_ids": [w_item_id],
                "add_to_inventory": True,
                "track_order": True,
                "inventory_mode": "ordered",
                "target_binder_name": "Acquired",
                "order_metadata": {
                    "order_number": "ORD-2026-001",
                    "order_date_str": "2026-11-08",
                    "seller_summary": "TCG Direct",
                    "source": "manual",
                },
                "purchased_items": prev_data["parsed_items"],
            }),
            content_type="application/json",
        )
        self.assertEqual(exec_res.status_code, 200)
        exec_data = exec_res.get_json()
        self.assertEqual(exec_data["binder_name"], "Ordered")
        self.assertEqual(exec_data["order_status"], "ordered")
        self.assertEqual(len(exec_data["orders"]), 1)
        order_id = exec_data["orders"][0]["id"]
        self.assertEqual(exec_data["orders"][0]["status"], "ordered")

        # 3. Verify cards are in UserInventoryCard with binder_name="Ordered"
        with self.app.app_context():
            ordered_cards = UserInventoryCard.query.filter_by(user_id=user_id, binder_name="Ordered").all()
            self.assertEqual(len(ordered_cards), 3)

            # Verify ManaBox CSV 'replace' import preserves 'Ordered' cards
            from unittest.mock import MagicMock
            mock_sf = MagicMock()
            mock_sf.get_cards_collection.return_value = ({}, [])
            InventoryManager(scryfall_provider=mock_sf).import_inventory(
                user_id=user_id,
                parsed_cards=[{
                    "name": "Sol Ring",
                    "set_code": "CMM",
                    "collector_number": "400",
                    "foil": "normal",
                    "quantity": 1,
                    "condition": "Near Mint",
                    "binder_name": "Main Binder",
                }],
                mode="replace",
            )

            still_ordered = UserInventoryCard.query.filter_by(user_id=user_id, binder_name="Ordered").all()
            self.assertEqual(len(still_ordered), 3)
            main_binder = UserInventoryCard.query.filter_by(user_id=user_id, binder_name="Main Binder").all()
            self.assertEqual(len(main_binder), 1)

        # 4. Verify /api/inventory/cards?allocation=ordered returns only the 3 ordered cards
        inv_res = self.client.get("/api/inventory/cards?allocation=ordered")
        self.assertEqual(inv_res.status_code, 200)
        inv_data = inv_res.get_json()
        self.assertEqual(inv_data["total_count"], 3)
        for c in inv_data["cards"]:
            self.assertTrue(c["is_ordered"])
            self.assertEqual(c["binder_name"], "Ordered")

        # 5. Verify GET /api/orders/tcgplayer returns the active order
        orders_res = self.client.get("/api/orders/tcgplayer")
        self.assertEqual(orders_res.status_code, 200)
        orders_data = orders_res.get_json()
        self.assertEqual(orders_data["active_orders_count"], 1)
        self.assertEqual(orders_data["in_transit_cards_count"], 4)  # 1 Rebuke + 2 Frogmite + 1 Silver Myr

        # 6. Mark the order complete via POST /api/orders/tcgplayer/<order_id>/complete
        comp_res = self.client.post(
            f"/api/orders/tcgplayer/{order_id}/complete",
            data=json.dumps({"binder_name": "Acquired"}),
            content_type="application/json",
        )
        self.assertEqual(comp_res.status_code, 200)
        comp_data = comp_res.get_json()
        self.assertEqual(comp_data["order"]["status"], "completed")
        self.assertEqual(comp_data["order"]["target_binder_name"], "Acquired")

        # 7. Verify "Ordered" binder is now empty and cards are in "Acquired"
        with self.app.app_context():
            ordered_remaining = UserInventoryCard.query.filter_by(user_id=user_id, binder_name="Ordered").all()
            self.assertEqual(len(ordered_remaining), 0)
            acquired_cards = UserInventoryCard.query.filter_by(user_id=user_id, binder_name="Acquired").all()
            self.assertEqual(len(acquired_cards), 3)
            frog = next(c for c in acquired_cards if c.name == "Frogmite")
            self.assertEqual(frog.quantity, 2)

    def test_multi_order_and_seller_splitting_and_deduplication(self):
        user_id = self.login_as()

        # 1. Test HTML email with 2 seller breakdown tables + 1 redundant summary table
        multi_table_html = """
        <html>
          <body>
            <h2>Your TCGplayer Order #261108-A1B2C3-99887</h2>
            <div>
              <p>Package from Seller: CardKingdomDirect</p>
              <table>
                <tr><th>Qty</th><th>Description</th><th>Price</th></tr>
                <tr><td>1</td><td>Magic - Aether Revolt - Metallic Rebuke - Near Mint</td><td>$0.49</td></tr>
                <tr><td>2</td><td>Magic - Mirrodin - Frogmite - Moderately Played</td><td>$0.50</td></tr>
                <tr><td>1</td><td>Magic - Mirrodin - Silver Myr - Lightly Played</td><td>$0.25</td></tr>
              </table>
            </div>
            <div>
              <p>Seller: StarCityVault</p>
              <table>
                <tr><th>Qty</th><th>Description</th><th>Price</th></tr>
                <tr><td>1</td><td>Magic - Dominaria - Artificer's Assistant - Lightly Played</td><td>$0.30</td></tr>
                <tr><td>1</td><td>Magic - Modern Horizons 3 - Evolution Witness - Near Mint</td><td>$0.35</td></tr>
                <tr><td>1</td><td>Magic - Theros - Fleetfeather Sandals - Near Mint</td><td>$0.20</td></tr>
              </table>
            </div>
            <div>
              <p>Order Summary (All Items)</p>
              <table>
                <tr><th>Qty</th><th>Description</th><th>Price</th></tr>
                <tr><td>1</td><td>Magic - Aether Revolt - Metallic Rebuke - Near Mint</td><td>$0.49</td></tr>
                <tr><td>1</td><td>Magic - Dominaria - Artificer's Assistant - Lightly Played</td><td>$0.30</td></tr>
                <tr><td>2</td><td>Magic - Mirrodin - Frogmite - Moderately Played</td><td>$0.50</td></tr>
                <tr><td>1</td><td>Magic - Mirrodin - Silver Myr - Lightly Played</td><td>$0.25</td></tr>
                <tr><td>1</td><td>Magic - Modern Horizons 3 - Evolution Witness - Near Mint</td><td>$0.35</td></tr>
                <tr><td>1</td><td>Magic - Theros - Fleetfeather Sandals - Near Mint</td><td>$0.20</td></tr>
              </table>
            </div>
          </body>
        </html>
        """
        items, ord_nums, sellers = TCGPlayerPurchaseParser.parse_email_html(multi_table_html)
        # Summary table (6 items) is 100% accounted for by CardKingdomDirect (3 items) + StarCityVault (3 items)
        self.assertEqual(len(items), 6)
        self.assertEqual(sum(i.quantity for i in items), 7)
        ck_items = [i for i in items if i.seller == "CardKingdomDirect"]
        sc_items = [i for i in items if i.seller == "StarCityVault"]
        self.assertEqual(len(ck_items), 3)
        self.assertEqual(len(sc_items), 3)

        # 2. Test preview + execute splitting into separate TCGPlayerOrder rows per (Order #, Seller)
        multi_order_manifest = (
            "Qty\tDescription\n"
            "=== Order #261108-A1B2C3-11111 | Seller: CardKingdomDirect | Date: Nov 08, 2026 ===\n"
            "1\tMagic - Aether Revolt - Metallic Rebuke - Near Mint\n"
            "2\tMagic - Mirrodin - Frogmite - Moderately Played\n"
            "=== Order #261108-A1B2C3-11111 | Seller: StarCityVault | Date: Nov 08, 2026 ===\n"
            "1\tMagic - Dominaria - Artificer's Assistant - Lightly Played\n"
            "=== Order #261109-D4E5F6-22222 | Seller: TCGplayer Direct | Date: Nov 09, 2026 ===\n"
            "1\tMagic - Modern Horizons 3 - Evolution Witness - Near Mint\n"
        )

        prev_res = self.client.post(
            "/api/watchlist/reconcile-purchase/preview",
            data=json.dumps({"raw_text": multi_order_manifest}),
            content_type="application/json",
        )
        self.assertEqual(prev_res.status_code, 200)
        prev_data = prev_res.get_json()
        self.assertEqual(prev_data["packages_count"], 3)
        self.assertEqual(len(prev_data["order_groups"]), 3)
        self.assertEqual(prev_data["order_groups"][0]["seller"], "CardKingdomDirect")
        self.assertEqual(prev_data["order_groups"][1]["seller"], "StarCityVault")
        self.assertEqual(prev_data["order_groups"][2]["order_number"], "261109-D4E5F6-22222")
        self.assertEqual(prev_data["order_groups"][2]["seller"], "TCGplayer Direct")

        exec_res = self.client.post(
            "/api/watchlist/reconcile-purchase/execute",
            data=json.dumps({
                "target_ids": [],
                "add_to_inventory": True,
                "track_order": True,
                "inventory_mode": "ordered",
                "target_binder_name": "Acquired",
                "purchased_items": prev_data["parsed_items"],
            }),
            content_type="application/json",
        )
        self.assertEqual(exec_res.status_code, 200)
        exec_data = exec_res.get_json()
        self.assertEqual(len(exec_data["orders"]), 3)
        sellers_created = [o["seller_summary"] for o in exec_data["orders"]]
        self.assertEqual(sellers_created, ["CardKingdomDirect", "StarCityVault", "TCGplayer Direct"])


if __name__ == "__main__":
    unittest.main()
