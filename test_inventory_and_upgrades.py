"""
Comprehensive Test Suite for ManaBox Inventory CSV Ingestion & Dual-Tier Deck Upgrade Engine.
"""

import json
import unittest
from unittest.mock import MagicMock, patch
from app import create_app
from models import db, User, AllowedEmail, DeckAnalysis, UserInventoryCard
from inventory_parser import ManaBoxInventoryParser, InventoryParseError
from inventory_manager import InventoryManager
from deck_upgrade_engine import DualTierUpgradeEngine, COMMANDER_BANNED_CARDS, CURATED_UPGRADES
from providers.scryfall import ScryfallProvider


class InventoryAndUpgradeTestSuite(unittest.TestCase):
    """Tests for ManaBox parser, collection manager, allocation tracking, and dual-tier upgrade engine."""

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

    def login_as(self, email="vault_tester@chimera.local", is_admin=False):
        """Authenticates client as test user."""
        with self.app.app_context():
            allowed = AllowedEmail.get_by_email(email)
            if not allowed:
                allowed = AllowedEmail(email=email, is_admin=is_admin, notes="Test User", added_by="TestSuite")
                db.session.add(allowed)
                db.session.commit()
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email, name="VaultTester", is_admin=is_admin, is_active=True)
                db.session.add(user)
                db.session.commit()

            with self.client.session_transaction() as sess:
                sess["user_id"] = user.id
                sess["user_email"] = user.email
                sess["is_admin"] = user.is_admin
            return user

    def setUp(self):
        with self.app.app_context():
            UserInventoryCard.query.delete()
            DeckAnalysis.query.delete()
            db.session.commit()

    # ----------------------------------------------------------------------
    # 1. ManaBox Parser & Normalization Tests
    # ----------------------------------------------------------------------

    def test_parser_standard_manabox_csv(self):
        """Tests parsing clean ManaBox CSV export with all standard headers."""
        csv_text = (
            "Binder Name,Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,Scryfall ID,Purchase price,Condition,Language\n"
            "Commander Binder,Sol Ring,C21,Commander 2021,263,normal,uncommon,2,test-id-1,1.50,Near Mint,en\n"
            "Commander Binder,Arcane Signet,ELD,Throne of Eldraine,331,foil,common,1,test-id-2,2.00,Near Mint,en\n"
            "Traders,Swords to Plowshares,EMA,Eternal Masters,32,normal,rare,3,test-id-3,2.50,Lightly Played,en\n"
        )
        res = ManaBoxInventoryParser.parse(csv_text)
        self.assertEqual(len(res["valid_cards"]), 3)
        self.assertEqual(len(res["errors"]), 0)
        self.assertEqual(res["total_quantity"], 6)
        self.assertEqual(res["unique_names"], 3)

        first = res["valid_cards"][0]
        self.assertEqual(first["name"], "Sol Ring")
        self.assertEqual(first["set_code"], "C21")
        self.assertEqual(first["collector_number"], "263")
        self.assertEqual(first["quantity"], 2)
        self.assertEqual(first["foil"], "normal")
        self.assertEqual(first["purchase_price"], 1.50)

        second = res["valid_cards"][1]
        self.assertEqual(second["name"], "Arcane Signet")
        self.assertEqual(second["foil"], "foil")

    def test_parser_dfc_name_normalization(self):
        """Tests double-faced cards (DFCs) with various slash conventions normalize to ' // '."""
        csv_text = (
            "Name,Quantity,Set code\n"
            "Delver of Secrets/Insectile Aberration,1,ISD\n"
            "Bala Ged Recovery // Bala Ged Sanctuary,2,ZNR\n"
            "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki *F*,1,NEO\n"
        )
        res = ManaBoxInventoryParser.parse(csv_text)
        self.assertEqual(len(res["valid_cards"]), 3)
        self.assertEqual(res["valid_cards"][0]["name"], "Delver of Secrets // Insectile Aberration")
        self.assertEqual(res["valid_cards"][1]["name"], "Bala Ged Recovery // Bala Ged Sanctuary")
        self.assertEqual(res["valid_cards"][2]["name"], "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki")

    def test_parser_malformed_rows_error_reporting(self):
        """Tests that malformed rows generate actionable error details with 1-indexed row numbers."""
        csv_text = (
            "Name,Quantity,Set code\n"
            "Sol Ring,1,C21\n"
            ",2,EMA\n"  # Row 3: Missing card name
            "Demonic Tutor,-1,UMA\n"  # Row 4: Negative quantity
            "Counterspell,abc,MH2\n"  # Row 5: Non-numeric quantity
            "Cyclonic Rift,1,RTR\n"
        )
        res = ManaBoxInventoryParser.parse(csv_text)
        self.assertEqual(len(res["valid_cards"]), 2)  # Sol Ring and Cyclonic Rift
        self.assertEqual(len(res["errors"]), 3)

        err_rows = [e["row"] for e in res["errors"]]
        self.assertEqual(err_rows, [3, 4, 5])
        self.assertIn("Card name is empty", res["errors"][0]["error"])
        self.assertIn("Quantity must be greater than zero", res["errors"][1]["error"])
        self.assertIn("Non-numeric quantity", res["errors"][2]["error"])

    def test_parser_missing_required_header(self):
        """Tests that missing 'Name' header raises InventoryParseError with actionable feedback."""
        csv_text = "Edition,Collector Number,Quantity\nMH2,123,1\n"
        with self.assertRaises(InventoryParseError) as ctx:
            ManaBoxInventoryParser.parse(csv_text)
        self.assertIn("Missing required 'Name' column", str(ctx.exception))

    # ----------------------------------------------------------------------
    # 2. Inventory Manager & Allocation Telemetry Tests
    # ----------------------------------------------------------------------

    def test_inventory_manager_replace_and_merge_modes(self):
        """Tests importing cards in 'replace' vs 'merge' mode."""
        user = self.login_as()
        mock_scryfall = MagicMock(spec=ScryfallProvider)
        mock_scryfall.get_cards_collection.return_value = ({
            "sol ring": {
                "id": "mock-sol-id", "name": "Sol Ring", "mana_cost": "{1}", "cmc": 1.0,
                "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.", "colors": [],
                "color_identity": [], "prices": {"usd": "1.75", "usd_foil": "4.50"},
                "image_uri": "https://example.com/sol_ring.jpg"
            },
            "arcane signet": {
                "id": "mock-arcane-id", "name": "Arcane Signet", "mana_cost": "{2}", "cmc": 2.0,
                "type_line": "Artifact", "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
                "colors": [], "color_identity": [], "prices": {"usd": "1.00"},
                "image_uri": "https://example.com/arcane_signet.jpg"
            }
        }, [])

        manager = InventoryManager(scryfall_provider=mock_scryfall)

        # Batch 1 in Replace Mode
        batch1 = [
            {"name": "Sol Ring", "set_code": "C21", "collector_number": "263", "foil": "normal", "quantity": 1},
            {"name": "Arcane Signet", "set_code": "ELD", "collector_number": "331", "foil": "normal", "quantity": 2},
        ]
        with self.app.app_context():
            res1 = manager.import_inventory(user.id, batch1, mode="replace")
            self.assertEqual(res1["total_cards"], 3)
            self.assertEqual(res1["unique_cards"], 2)

            # Check Scryfall enrichment
            sol_db = UserInventoryCard.query.filter_by(user_id=user.id, name="Sol Ring").first()
            self.assertIsNotNone(sol_db)
            self.assertEqual(sol_db.scryfall_id, "mock-sol-id")
            self.assertEqual(sol_db.price_usd, 1.75)

            # Batch 2 in Merge Mode: Add another Sol Ring (same printing) + 1 new card
            batch2 = [
                {"name": "Sol Ring", "set_code": "C21", "collector_number": "263", "foil": "normal", "quantity": 2},
            ]
            res2 = manager.import_inventory(user.id, batch2, mode="merge")
            self.assertEqual(res2["total_cards"], 5)  # 1 Sol Ring + 2 Arcane Signet + 2 Sol Ring = 5

            sol_updated = UserInventoryCard.query.filter_by(user_id=user.id, name="Sol Ring").first()
            self.assertEqual(sol_updated.quantity, 3)

    def test_cross_deck_allocation_tracking(self):
        """Tests that cards allocated across saved decks are correctly tracked with available copies."""
        user = self.login_as()
        manager = InventoryManager()

        with self.app.app_context():
            # Add 2 physical copies of Cyclonic Rift to inventory
            c1 = UserInventoryCard(
                user_id=user.id, name="Cyclonic Rift", set_code="RTR", quantity=2,
                foil="normal", color_identity="U", price_usd=35.0
            )
            # Add 1 physical copy of Demonic Tutor
            c2 = UserInventoryCard(
                user_id=user.id, name="Demonic Tutor", set_code="UMA", quantity=1,
                foil="normal", color_identity="B", price_usd=40.0
            )
            db.session.add_all([c1, c2])

            # Create Deck A containing 1 Cyclonic Rift and 1 Demonic Tutor
            deck_a = DeckAnalysis(
                user_id=user.id,
                deck_name="Dimir Control",
                commander_name="Talion, the Kindly Lord",
                cards_data=json.dumps([
                    {"name": "Cyclonic Rift", "quantity": 1},
                    {"name": "Demonic Tutor", "quantity": 1},
                ]),
                total_cards=2
            )
            db.session.add(deck_a)
            db.session.commit()

            # Inspect allocation for a new deck (current_deck_id=None)
            allocations = manager.get_user_card_allocations(user.id)
            self.assertEqual(allocations["cyclonic rift"]["total_allocated"], 1)
            self.assertEqual(allocations["demonic tutor"]["total_allocated"], 1)

            summary = manager.get_inventory_summary(user.id)
            cards_map = {c["name"]: c for c in summary["cards"]}

            # Cyclonic Rift: owned 2, allocated 1 -> available 1 (NOT already allocated elsewhere)
            self.assertEqual(cards_map["Cyclonic Rift"]["available_copies"], 1)
            self.assertFalse(cards_map["Cyclonic Rift"]["already_allocated_elsewhere"])

            # Demonic Tutor: owned 1, allocated 1 -> available 0 (already allocated elsewhere)
            self.assertEqual(cards_map["Demonic Tutor"]["available_copies"], 0)
            self.assertTrue(cards_map["Demonic Tutor"]["already_allocated_elsewhere"])

    # ----------------------------------------------------------------------
    # 3. Dual-Tier Upgrade Engine Tests
    # ----------------------------------------------------------------------

    def test_upgrade_engine_isolates_owned_swaps_and_respects_color_identity(self):
        """Tests that owned cards generate zero-cost swaps and never violate color identity or legality."""
        user = self.login_as()
        mock_scryfall = MagicMock(spec=ScryfallProvider)
        mock_scryfall.get_cards_collection.return_value = ({}, [])
        engine = DualTierUpgradeEngine(scryfall_provider=mock_scryfall)

        with self.app.app_context():
            # Inventory contains:
            # - Swords to Plowshares (White - NOT legal in a Simic deck)
            # - Cyclonic Rift (Blue - Legal in Simic)
            # - Arcane Signet (Colorless - Legal in Simic)
            # - Black Lotus (BANNED in Commander)
            inv_cards = [
                UserInventoryCard(user_id=user.id, name="Swords to Plowshares", color_identity="W", quantity=1),
                UserInventoryCard(user_id=user.id, name="Cyclonic Rift", color_identity="U", quantity=1),
                UserInventoryCard(user_id=user.id, name="Arcane Signet", color_identity="", quantity=1),
                UserInventoryCard(user_id=user.id, name="Black Lotus", color_identity="", quantity=1),
            ]
            db.session.add_all(inv_cards)
            db.session.commit()

            # Simic Commander Deck (Colors: G, U)
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Simic Ramp",
                commander_name="Arixmethes, Slumbering Isle",
                color_identity="G,U",
                cards_data=json.dumps([
                    {"name": "Cancel", "quantity": 1, "cmc": 3, "type_line": "Instant"},
                    {"name": "Manalith", "quantity": 1, "cmc": 3, "type_line": "Artifact"},
                ]),
                total_cards=2
            )
            db.session.add(deck)
            db.session.commit()

            allocations = {}
            results = engine.generate_upgrades(deck, inv_cards, allocations)

            owned_names = [u["card_in"] for u in results["owned_swaps"]]

            # 1. Cyclonic Rift and Arcane Signet should be suggested
            self.assertIn("Cyclonic Rift", owned_names)
            self.assertIn("Arcane Signet", owned_names)

            # 2. Swords to Plowshares (White) must NOT be suggested in a Green/Blue deck
            self.assertNotIn("Swords to Plowshares", owned_names)

            # 3. Black Lotus is banned and must NEVER be suggested
            self.assertNotIn("Black Lotus", owned_names)
            for item in results["all_shopping_cards"]:
                self.assertNotIn(item["name"].lower(), COMMANDER_BANNED_CARDS)

    def test_upgrade_engine_budget_brackets_and_wishlist_export(self):
        """Tests that shopping list organizes cards into < $3, $3-$15, and > $15 brackets and exports."""
        user = self.login_as()
        mock_scryfall = MagicMock(spec=ScryfallProvider)
        mock_scryfall.get_cards_collection.return_value = ({
            "counterspell": {"prices": {"usd": "1.50"}},
            "beast within": {"prices": {"usd": "2.00"}},
            "heroic intervention": {"prices": {"usd": "8.50"}},
            "rhystic study": {"prices": {"usd": "38.00"}},
        }, [])

        engine = DualTierUpgradeEngine(scryfall_provider=mock_scryfall)

        deck = DeckAnalysis(
            deck_name="Simic Test",
            commander_name="Arixmethes, Slumbering Isle",
            color_identity="G,U",
            cards_data=json.dumps([{"name": "Forest", "quantity": 1}]),
            total_cards=1
        )

        results = engine.generate_upgrades(deck, user_inventory=[], allocations={})
        brackets = results["shopping_list"]

        # Verify budget brackets exist
        self.assertIn("budget", brackets)
        self.assertIn("moderate", brackets)
        self.assertIn("high_impact", brackets)

        # Test ManaBox CSV Wishlist Export
        sample_acquisitions = [
            {"name": "Rhystic Study"},
            {"name": "Heroic Intervention"},
        ]
        csv_export = DualTierUpgradeEngine.generate_manabox_wishlist_export(sample_acquisitions, format_type="csv")
        self.assertIn("Name,Quantity,Foil,Condition,Language,Binder Name", csv_export)
        self.assertIn("Rhystic Study,1,normal,Near Mint,en,Wishlist", csv_export)

        text_export = DualTierUpgradeEngine.generate_manabox_wishlist_export(sample_acquisitions, format_type="text")
        self.assertIn("1 Rhystic Study", text_export)
        self.assertIn("1 Heroic Intervention", text_export)

    # ----------------------------------------------------------------------
    # 4. API Endpoints & Apply-Swap Tests
    # ----------------------------------------------------------------------

    def test_api_inventory_upload_and_error_handling(self):
        """Tests /api/inventory/upload with valid and malformed rows."""
        self.login_as()

        valid_csv = (
            "Name,Quantity,Set code,Foil\n"
            "Sol Ring,1,C21,normal\n"
            "Arcane Signet,2,ELD,foil\n"
        )
        resp = self.client.post("/api/inventory/upload", data={"csv_content": valid_csv, "mode": "replace"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["added_count"], 2)
        self.assertEqual(data["errors_count"], 0)

        # Upload malformed CSV
        bad_csv = (
            "Name,Quantity,Set code\n"
            ",1,MH2\n"  # missing name
            "Demonic Tutor,not_a_number,UMA\n"  # invalid quantity
        )
        resp_bad = self.client.post("/api/inventory/upload", data={"csv_content": bad_csv, "mode": "replace"})
        self.assertEqual(resp_bad.status_code, 400)
        err_data = resp_bad.get_json()
        self.assertIn("errors", err_data)
        self.assertEqual(len(err_data["errors"]), 2)

    def test_api_deck_apply_swap(self):
        """Tests that /api/deck/<id>/apply-swap slots in owned card, removes cut card, and recalculates stats."""
        user = self.login_as()

        with self.app.app_context():
            # Add Arcane Signet to user's binder
            card = UserInventoryCard(
                user_id=user.id,
                name="Arcane Signet",
                set_code="ELD",
                collector_number="331",
                quantity=1,
                cmc=2.0,
                mana_cost="{2}",
                type_line="Artifact",
                price_usd=1.50
            )
            db.session.add(card)

            # Saved deck containing Commander's Sphere (to be cut)
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Artifact Deck",
                commander_name="Urza, Lord High Artificer",
                cards_data=json.dumps([
                    {"name": "Commander's Sphere", "quantity": 1, "cmc": 3.0, "type_line": "Artifact"},
                    {"name": "Island", "quantity": 99, "cmc": 0.0, "type_line": "Basic Land — Island"},
                ]),
                total_cards=100
            )
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.id

        # Execute Apply Swap
        resp = self.client.post(
            f"/api/deck/{deck_id}/apply-swap",
            json={"card_out": "Commander's Sphere", "card_in": "Arcane Signet"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])

        # Verify database state
        with self.app.app_context():
            updated_deck = db.session.get(DeckAnalysis, deck_id)
            updated_card_names = [c["name"] for c in updated_deck.get_parsed_cards()]
            self.assertIn("Arcane Signet", updated_card_names)
            self.assertNotIn("Commander's Sphere", updated_card_names)
            self.assertEqual(updated_deck.total_cards, 100)

    def test_dfc_bidirectional_swap_and_upgrade_matching(self):
        """Tests that double-faced cards (DFCs) match bidirectionally in upgrades and apply-swap."""
        user = self.login_as()
        mock_scryfall = MagicMock(spec=ScryfallProvider)
        mock_scryfall.get_cards_collection.return_value = ({}, [])
        engine = DualTierUpgradeEngine(scryfall_provider=mock_scryfall)

        with self.app.app_context():
            # Inventory has full DFC name: Bala Ged Recovery // Bala Ged Sanctuary
            inv_card = UserInventoryCard(
                user_id=user.id,
                name="Bala Ged Recovery // Bala Ged Sanctuary",
                color_identity="G",
                quantity=1,
                cmc=3.0,
                type_line="Sorcery // Land"
            )
            db.session.add(inv_card)

            # Deck has front face: "Bala Ged Recovery"
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Mono Green",
                commander_name="Titania, Protector of Argoth",
                color_identity="G",
                cards_data=json.dumps([
                    {"name": "Bala Ged Recovery", "quantity": 1, "cmc": 3.0, "type_line": "Sorcery"},
                    {"name": "Forest", "quantity": 99, "cmc": 0.0, "type_line": "Basic Land"}
                ]),
                total_cards=100
            )
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.id

            # Engine should recognize Bala Ged Recovery is already in deck, not suggest it as unowned or duplicate
            res = engine.generate_upgrades(deck, [inv_card], allocations={})
            owned_ins = [u["card_in"] for u in res["owned_swaps"]]
            self.assertNotIn("Bala Ged Recovery // Bala Ged Sanctuary", owned_ins)

        # Now test Apply Swap cutting front face "Bala Ged Recovery" and slotting "Sol Ring"
        with self.app.app_context():
            sol = UserInventoryCard(user_id=user.id, name="Sol Ring", quantity=1, cmc=1.0, type_line="Artifact")
            db.session.add(sol)
            db.session.commit()

        resp = self.client.post(
            f"/api/deck/{deck_id}/apply-swap",
            json={"card_out": "Bala Ged Recovery // Bala Ged Sanctuary", "card_in": "Sol Ring"}
        )
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            updated = db.session.get(DeckAnalysis, deck_id)
            names = [c["name"] for c in updated.get_parsed_cards()]
            self.assertIn("Sol Ring", names)
            self.assertNotIn("Bala Ged Recovery", names)

    def test_colorless_commander_color_identity_enforcement(self):
        """Tests that a colorless commander never receives colored upgrades in binder or shopping list."""
        user = self.login_as()
        mock_scryfall = MagicMock(spec=ScryfallProvider)
        mock_scryfall.get_cards_collection.return_value = ({}, [])
        engine = DualTierUpgradeEngine(scryfall_provider=mock_scryfall)

        with self.app.app_context():
            inv_cards = [
                UserInventoryCard(user_id=user.id, name="Counterspell", color_identity="U", quantity=1),
                UserInventoryCard(user_id=user.id, name="Sol Ring", color_identity="", quantity=1),
            ]
            db.session.add_all(inv_cards)

            # Colorless deck (Kozilek, Butcher of Truth)
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Eldrazi Titans",
                commander_name="Kozilek, Butcher of Truth",
                color_identity="",
                cards_data=json.dumps([
                    {"name": "Wastes", "quantity": 100, "cmc": 0.0, "type_line": "Basic Land — Wastes"}
                ]),
                total_cards=100
            )
            db.session.add(deck)
            db.session.commit()

            res = engine.generate_upgrades(deck, inv_cards, allocations={})
            owned_ins = [u["card_in"] for u in res["owned_swaps"]]
            self.assertIn("Sol Ring", owned_ins)
            self.assertNotIn("Counterspell", owned_ins)

            # Shopping list should also be strictly colorless
            for shop_item in res["all_shopping_cards"]:
                for staple in CURATED_UPGRADES:
                    if staple["name"].lower() == shop_item["name"].lower():
                        self.assertEqual(len(staple.get("colors", [])), 0, f"{staple['name']} has colors in a colorless deck!")

    def test_partial_success_upload_with_skipped_rows(self):
        """Tests uploading CSV with valid cards and skipped malformed rows returns 200 with diagnostics."""
        self.login_as()
        mixed_csv = (
            "Name,Quantity,Set code\n"
            "Sol Ring,1,C21\n"
            ",2,EMA\n"  # row 3: missing name
            "Arcane Signet,1,ELD\n"
        )
        resp = self.client.post("/api/inventory/upload", data={"csv_content": mixed_csv, "mode": "replace"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["added_count"], 2)
        self.assertEqual(data["errors_count"], 1)
        self.assertEqual(data["errors"][0]["row"], 3)
        self.assertIn("Card name is empty", data["errors"][0]["error"])

    def test_gdrive_file_id_extraction(self):
        """Tests Google Drive file ID extraction across all standard link formats."""
        expected_id = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"

        test_urls = [
            f"https://drive.google.com/file/d/{expected_id}/view?usp=sharing",
            f"https://drive.google.com/open?id={expected_id}",
            f"https://docs.google.com/spreadsheets/d/{expected_id}/edit#gid=0",
            f"https://drive.google.com/uc?id={expected_id}&export=download",
            expected_id,  # Raw ID
        ]

        for url in test_urls:
            extracted = ManaBoxInventoryParser.extract_gdrive_file_id(url)
            self.assertEqual(extracted, expected_id, f"Failed extracting ID from {url}")

        self.assertIsNone(ManaBoxInventoryParser.extract_gdrive_file_id(""))
        self.assertIsNone(ManaBoxInventoryParser.extract_gdrive_file_id("invalid-short-id"))

    @patch("requests.Session")
    def test_download_gdrive_csv(self, mock_session_cls):
        """Tests download_gdrive_csv with mock HTTP responses."""
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        sample_csv = "Name,Quantity,Set code\nSol Ring,1,C21\n"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_csv
        mock_resp.content = sample_csv.encode("utf-8")
        mock_resp.cookies = {}
        mock_session.get.return_value = mock_resp

        gdrive_url = "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/view"
        content = ManaBoxInventoryParser.download_gdrive_csv(gdrive_url)
        self.assertIn("Sol Ring", content)

        # Test error handling when Google returns an HTML access denied / login page
        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.text = "<!DOCTYPE html><html><title>Google Drive - Access Denied</title></html>"
        html_resp.content = html_resp.text.encode("utf-8")
        html_resp.cookies = {}
        mock_session.get.return_value = html_resp

        with self.assertRaises(InventoryParseError) as ctx:
            ManaBoxInventoryParser.download_gdrive_csv(gdrive_url)
        self.assertIn("Anyone with the link can view", str(ctx.exception))

    @patch("inventory_parser.ManaBoxInventoryParser.download_gdrive_csv")
    def test_api_inventory_gdrive_upload_and_persistence(self, mock_download):
        """Tests uploading via Google Drive link saves gdrive_url to user profile and imports cards."""
        user = self.login_as()
        mock_download.return_value = "Name,Quantity,Set code\nCyclonic Rift,1,RTR\nRhystic Study,1,PR"

        gdrive_url = "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/view?usp=sharing"
        resp = self.client.post(
            "/api/inventory/upload",
            json={"gdrive_url": gdrive_url, "mode": "replace"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["added_count"], 2)

        # Verify Google Drive URL is persisted to the user record
        with self.app.app_context():
            db_user = db.session.get(User, user.id)
            self.assertEqual(db_user.inventory_gdrive_url, gdrive_url)

    @patch("inventory_parser.ManaBoxInventoryParser.download_gdrive_csv")
    def test_api_inventory_sync_gdrive_endpoint(self, mock_download):
        """Tests /api/inventory/sync-gdrive uses saved Google Drive link to re-sync."""
        user = self.login_as()
        gdrive_url = "https://drive.google.com/file/d/saved-id-12345678901234567890/view"

        with self.app.app_context():
            db_user = db.session.get(User, user.id)
            db_user.inventory_gdrive_url = gdrive_url
            db.session.commit()

        mock_download.return_value = "Name,Quantity,Set code\nSmothering Tithe,1,RNA\n"

        resp = self.client.post("/api/inventory/sync-gdrive", json={"mode": "replace"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["added_count"], 1)

        with self.app.app_context():
            cards = UserInventoryCard.query.filter_by(user_id=user.id).all()
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].name, "Smothering Tithe")

    def test_inventory_view_renders_upload_modal(self):
        """Tests that /inventory renders with valid uploadModal and Google Drive controls."""
        self.login_as()
        resp = self.client.get("/inventory")
        self.assertEqual(resp.status_code, 200)
        html_text = resp.get_data(as_text=True)

        # Verify uploadModal is rendered properly as a real tag, not commented out
        self.assertIn('<div id="uploadModal"', html_text)
        self.assertIn('id="gdriveUrlInput"', html_text)
        self.assertIn('id="btnStartUpload"', html_text)
        self.assertIn('id="dropZone"', html_text)
        self.assertNotIn('<!-- ====================================================================<div id="uploadModal"', html_text)

    def test_api_deck_add_card(self):
        """Tests tactical direct card addition to a deck via POST /api/deck/<id>/add-card."""
        user = self.login_as()
        with self.app.app_context():
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Test Deck",
                commander_name="Urza, Lord High Artificer",
                cards_data=json.dumps([
                    {"name": "Island", "quantity": 99, "cmc": 0.0, "type_line": "Basic Land"}
                ]),
                total_cards=99
            )
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.id

        resp = self.client.post(
            f"/api/deck/{deck_id}/add-card",
            json={"card_name": "Sol Ring"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deck"]["total_cards"], 100)

        with self.app.app_context():
            saved = db.session.get(DeckAnalysis, deck_id)
            card_names = [c["name"] for c in saved.get_parsed_cards()]
            self.assertIn("Sol Ring", card_names)

    def test_api_deck_apply_swap_resilient_missing_cut(self):
        """Tests that apply-swap succeeds gracefully even if proposed card_out was already cut."""
        user = self.login_as()
        with self.app.app_context():
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="Full Commander Deck",
                commander_name="Urza, Lord High Artificer",
                cards_data=json.dumps([
                    {"name": "Island", "quantity": 99, "cmc": 0.0, "type_line": "Basic Land"},
                    {"name": "Weak Artifact", "quantity": 1, "cmc": 5.0, "type_line": "Artifact"}
                ]),
                total_cards=100
            )
            db.session.add(deck)
            db.session.commit()
            deck_id = deck.id

        # Propose cutting "Nonexistent Card" which was already removed
        resp = self.client.post(
            f"/api/deck/{deck_id}/apply-swap",
            json={"card_out": "Nonexistent Card", "card_in": "Arcane Signet"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])

        with self.app.app_context():
            saved = db.session.get(DeckAnalysis, deck_id)
            card_names = [c["name"] for c in saved.get_parsed_cards()]
            self.assertIn("Arcane Signet", card_names)
            self.assertEqual(saved.total_cards, 100)


if __name__ == "__main__":
    unittest.main()

