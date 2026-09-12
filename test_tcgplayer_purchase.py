import unittest
import json
from app import create_app
from models import db, User, AllowedEmail, WatchlistItem, VendorPrice, UserInventoryCard, ActivityLog
from tcgplayer_parser import TCGPlayerPurchaseParser, TCGPlayerCardItem


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


if __name__ == "__main__":
    unittest.main()
