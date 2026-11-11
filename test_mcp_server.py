"""
Automated test suite for the Chimaera Model Context Protocol (MCP) Server
and Google Gemini Mobile App / Cloud Run integration.
"""

import json
import unittest
from unittest.mock import patch

from app import create_app
from models import (
    DeckAnalysis,
    MicrocenterItem,
    User,
    UserInventoryCard,
    VendorPrice,
    WatchlistItem,
    db,
)


class TestChimeraMcpServer(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "CHIMERA_API_KEY": "master-cloud-run-secret-key",
                "ADMIN_EMAIL": "commander@chimera.io",
                "MCP_ENABLED": True,
            }
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            user = User.query.filter(db.func.lower(User.email) == "commander@chimera.io").first()
            if not user:
                user = User(
                    email="commander@chimera.io",
                    name="Tactical Commander",
                    is_admin=True,
                    is_active=True,
                )
                db.session.add(user)
            self.api_token = user.ensure_api_token()
            db.session.commit()
            self.user_id = user.id

            # Seed sample inventory cards
            c1 = UserInventoryCard(
                user_id=user.id,
                name="Rhystic Study",
                set_code="WOT",
                set_name="Wilds of Eldraine: Enchanting Tales",
                quantity=2,
                foil="foil",
                condition="Near Mint",
                binder_name="Staples Binder",
                type_line="Enchantment",
                oracle_text="Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
                color_identity="U",
                price_usd=42.0,
                price_usd_foil=65.0,
            )
            c2 = UserInventoryCard(
                user_id=user.id,
                name="Sol Ring",
                set_code="CMM",
                set_name="Commander Masters",
                quantity=4,
                foil="normal",
                condition="Near Mint",
                binder_name="Commander Core",
                type_line="Artifact",
                oracle_text="{T}: Add {C}{C}.",
                color_identity="",
                price_usd=2.50,
            )
            db.session.add_all([c1, c2])

            # Seed watchlist item with active deal
            w1 = WatchlistItem(
                user_id=user.id,
                name="The One Ring",
                set_code="LTR",
                finish="nonfoil",
                target_price=60.0,
                tag="Priority Staples",
            )
            db.session.add(w1)
            db.session.flush()

            vp1 = VendorPrice(
                watchlist_id=w1.id,
                vendor_name="TCGplayer",
                price=52.50,
                condition="NM",
                in_stock=True,
                product_url="https://www.tcgplayer.com/product/123",
            )
            db.session.add(vp1)

            # Seed MicroCenter & Best Buy deals
            mc = MicrocenterItem(
                sku="MC-999",
                name="MTG Foundations Collector Booster Box",
                current_price=189.99,
                previous_price=229.99,
                original_price=249.99,
                in_stock=True,
                stock_count=5,
                target_price=200.0,
                is_active=True,
            )
            db.session.add(mc)

            # Seed Commander Deck Analysis
            deck = DeckAnalysis(
                user_id=user.id,
                deck_name="The Ur-Dragon Tribal",
                commander_name="The Ur-Dragon",
                archetype="Dragon Tribal Ramp",
                power_level=8.0,
                power_bracket="High Power (Bracket 4)",
                total_cards=100,
                total_value=850.0,
                avg_cmc=3.65,
                color_identity="W,U,B,R,G",
                cards_data=json.dumps([
                    {"name": "The Ur-Dragon", "quantity": 1, "type_line": "Legendary Creature — Dragon Avatar", "cmc": 9},
                    {"name": "Sol Ring", "quantity": 1, "type_line": "Artifact", "cmc": 1},
                ]),
                analysis_json=json.dumps({
                    "strategic_summary": "Explosive 5-color dragon tribal deck.",
                    "win_conditions": ["Combat damage via Miirym and Scourge of Valkas"],
                    "weaknesses": ["Vulnerable to early farewell board wipes"],
                    "card_ratings": [{"name": "Sol Ring", "rating": 10, "reason": "Essential fast mana"}],
                }),
                upgrades_json=json.dumps({
                    "high_priority": [
                        {"add": "Rhystic Study", "cut": "Divination", "reason": "Superior card draw engine"}
                    ]
                }),
            )
            db.session.add(deck)
            db.session.commit()
            self.deck_id = deck.id

    def _login(self):
        with self.app.app_context():
            from models import AllowedEmail
            if not AllowedEmail.get_by_email("commander@chimera.io"):
                db.session.add(AllowedEmail(email="commander@chimera.io", is_admin=True, added_by="Test"))
                db.session.commit()
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["user_email"] = "commander@chimera.io"
            sess["is_admin"] = True

    def test_mcp_auth_methods_and_rejection(self):
        """Verifies unauthorized requests return 401 and valid tokens (query, Bearer, X-API-Key, master key) succeed."""
        # 1. No token -> 401
        resp = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(resp.status_code, 401)

        # 2. Query parameter ?token=
        resp_q = self.client.post(
            f"/mcp?token={self.api_token}",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        self.assertEqual(resp_q.status_code, 200)
        self.assertEqual(resp_q.get_json()["result"], {})

        # 3. Authorization: Bearer header
        resp_b = self.client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {self.api_token}"},
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
        )
        self.assertEqual(resp_b.status_code, 200)

        # 4. X-API-Key header with master CHIMERA_API_KEY
        resp_k = self.client.post(
            "/mcp",
            headers={"X-API-Key": "master-cloud-run-secret-key"},
            json={"jsonrpc": "2.0", "id": 3, "method": "ping"},
        )
        self.assertEqual(resp_k.status_code, 200)

    def test_mcp_initialize_and_tools_list(self):
        """Verifies MCP protocol handshake and tool schema registration."""
        init_resp = self.client.post(
            f"/mcp?token={self.api_token}",
            json={
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "google-gemini-mobile"}},
            },
        )
        self.assertEqual(init_resp.status_code, 200)
        init_data = init_resp.get_json()
        self.assertEqual(init_data["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(init_data["result"]["serverInfo"]["name"], "chimera-mtg-mcp")

        # Notification returns 202 with empty body
        notif_resp = self.client.post(
            f"/mcp?token={self.api_token}",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        self.assertEqual(notif_resp.status_code, 202)

        # List tools
        list_resp = self.client.post(
            f"/mcp?token={self.api_token}",
            json={"jsonrpc": "2.0", "id": "list-1", "method": "tools/list"},
        )
        self.assertEqual(list_resp.status_code, 200)
        tools = list_resp.get_json()["result"]["tools"]
        self.assertGreaterEqual(len(tools), 13)
        tool_names = {t["name"] for t in tools}
        self.assertIn("get_inventory_summary", tool_names)
        self.assertIn("check_inventory_cards", tool_names)
        self.assertIn("search_inventory", tool_names)
        self.assertIn("get_watchlist", tool_names)
        self.assertIn("get_active_deals", tool_names)
        self.assertIn("get_deck_analyses", tool_names)
        self.assertIn("get_deck_cards", tool_names)
        self.assertIn("search_card_prices", tool_names)
        self.assertIn("add_to_watchlist", tool_names)
        self.assertIn("check_card_deck_fit", tool_names)
        self.assertIn("upload_inventory", tool_names)
        self.assertIn("upload_decks", tool_names)
        self.assertIn("reconcile_watchlist", tool_names)

    def test_mcp_sse_transport_and_messages(self):
        """Verifies GET /mcp returns 405 per Streamable HTTP spec and /mcp/sse + /mcp/messages works for legacy SSE."""
        get_mcp_resp = self.client.get(f"/mcp?token={self.api_token}")
        self.assertEqual(get_mcp_resp.status_code, 405)
        self.assertIn("POST", get_mcp_resp.headers.get("Allow", ""))

        sse_resp = self.client.get(f"/mcp/sse?token={self.api_token}&single_event=1")
        self.assertEqual(sse_resp.status_code, 200)
        self.assertIn("text/event-stream", sse_resp.content_type)
        raw_sse = sse_resp.get_data(as_text=True)
        self.assertIn("event: endpoint", raw_sse)
        self.assertIn("/mcp/messages?session_id=", raw_sse)

        # Post a message to /mcp/messages
        msg_resp = self.client.post(
            f"/mcp/messages?token={self.api_token}",
            json={"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
        )
        self.assertEqual(msg_resp.status_code, 200)
        self.assertGreaterEqual(len(msg_resp.get_json()["result"]["tools"]), 13)

    def _call_tool(self, name: str, arguments: dict | None = None, expect_error: bool = False) -> dict:
        resp = self.client.post(
            f"/mcp?token={self.api_token}",
            json={
                "jsonrpc": "2.0",
                "id": "call-1",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.get_json()["result"]
        if not expect_error:
            self.assertFalse(result.get("isError", False), f"Tool {name} returned error: {result}")
        else:
            self.assertTrue(result.get("isError", False), f"Expected error for tool {name}, but isError was not set: {result}")
        return json.loads(result["content"][0]["text"])

    def test_inventory_tools(self):
        """Verifies get_inventory_summary and search_inventory tools."""
        summary = self._call_tool("get_inventory_summary", {"top_n": 5})
        self.assertEqual(summary["total_cards_quantity"], 6)
        self.assertEqual(summary["unique_inventory_entries"], 2)
        # 2 * 65.0 (foil Rhystic Study) + 4 * 2.50 (Sol Ring) = 140.0
        self.assertEqual(summary["total_value_usd"], 140.0)
        self.assertEqual(summary["top_valuable_cards"][0]["name"], "Rhystic Study")

        search_res = self._call_tool("search_inventory", {"query": "Rhystic", "foil": "foil"})
        self.assertEqual(search_res["total_matching_entries"], 1)
        self.assertEqual(search_res["total_matching_quantity"], 2)
        self.assertEqual(search_res["cards"][0]["set_code"], "WOT")

    def test_check_inventory_cards(self):
        """Verifies check_inventory_cards tool with single cards, multi-card lists, decklists, aliases, and filters."""
        # 1. Multi-card check with found and missing cards
        batch_res = self._call_tool(
            "check_inventory_cards",
            {"cards": ["Rhystic Study", "Sol Ring", "Mana Crypt"]},
        )
        self.assertEqual(batch_res["total_queried"], 3)
        self.assertEqual(batch_res["found_count"], 2)
        self.assertEqual(batch_res["missing_count"], 1)
        self.assertFalse(batch_res["all_owned"])
        self.assertEqual(batch_res["total_owned_quantity"], 6)
        self.assertIn("Rhystic Study", batch_res["owned_card_names"])
        self.assertIn("Sol Ring", batch_res["owned_card_names"])
        self.assertEqual(batch_res["missing_card_names"], ["Mana Crypt"])

        # Check detail for found cards
        rhystic_entry = next(c for c in batch_res["found_cards"] if c["matched_name"] == "Rhystic Study")
        self.assertTrue(rhystic_entry["in_inventory"])
        self.assertEqual(rhystic_entry["total_quantity"], 2)
        self.assertTrue(rhystic_entry["has_foil"])
        self.assertFalse(rhystic_entry["has_nonfoil"])
        self.assertEqual(rhystic_entry["binders"], ["Staples Binder"])
        self.assertEqual(len(rhystic_entry["inventory_items"]), 1)
        self.assertEqual(rhystic_entry["inventory_items"][0]["set_code"], "WOT")
        self.assertEqual(rhystic_entry["inventory_items"][0]["effective_price"], 65.0)

        missing_entry = batch_res["missing_cards"][0]
        self.assertEqual(missing_entry["query"], "Mana Crypt")
        self.assertFalse(missing_entry["in_inventory"])
        self.assertEqual(missing_entry["total_quantity"], 0)

        # 2. Single card check via card_name
        single_res = self._call_tool("check_inventory_cards", {"card_name": "Rhystic Study"})
        self.assertEqual(single_res["total_queried"], 1)
        self.assertEqual(single_res["found_count"], 1)
        self.assertEqual(single_res["missing_count"], 0)
        self.assertTrue(single_res["all_owned"])

        # 3. Decklist text with quantities
        decklist_res = self._call_tool(
            "check_inventory_cards",
            {"text": "1x Rhystic Study\n4 Sol Ring\n1 Black Lotus"},
        )
        self.assertEqual(decklist_res["total_queried"], 3)
        self.assertEqual(decklist_res["found_count"], 2)
        self.assertEqual(decklist_res["missing_count"], 1)
        sol_entry = next(c for c in decklist_res["found_cards"] if c["matched_name"] == "Sol Ring")
        self.assertEqual(sol_entry["requested_quantity"], 4)
        self.assertEqual(sol_entry["total_quantity"], 4)
        self.assertTrue(sol_entry["has_sufficient_quantity"])
        self.assertEqual(sol_entry["missing_quantity"], 0)

        # 4. Smart normalization: case-insensitive & accent support
        norm_res = self._call_tool(
            "check_inventory_cards",
            {"cards": ["rhystic study", "SOL RING"]},
        )
        self.assertEqual(norm_res["found_count"], 2)
        self.assertTrue(norm_res["all_owned"])

        # 5. Compact mode (include_details=False)
        compact_res = self._call_tool(
            "check_inventory_cards",
            {"cards": ["Sol Ring"], "include_details": False},
        )
        self.assertNotIn("inventory_items", compact_res["found_cards"][0])

        # 6. Binder filtering
        binder_res = self._call_tool(
            "check_inventory_cards",
            {"cards": ["Rhystic Study", "Sol Ring"], "binder_name": "Staples Binder"},
        )
        self.assertEqual(binder_res["found_count"], 1)
        self.assertEqual(binder_res["missing_count"], 1)
        self.assertEqual(binder_res["owned_card_names"], ["Rhystic Study"])
        self.assertEqual(binder_res["missing_card_names"], ["Sol Ring"])

        # 7. Aliases
        alias_res1 = self._call_tool("check_cards_in_inventory", {"cards": ["Sol Ring"]})
        self.assertEqual(alias_res1["found_count"], 1)

        alias_res2 = self._call_tool("batch_check_inventory", {"cards": ["Sol Ring"]})
        self.assertEqual(alias_res2["found_count"], 1)

        # 8. search_inventory routing when 'cards' is passed
        search_batch_res = self._call_tool("search_inventory", {"cards": ["Rhystic Study", "Mana Crypt"]})
        self.assertEqual(search_batch_res["total_queried"], 2)
        self.assertEqual(search_batch_res["found_count"], 1)
        self.assertEqual(search_batch_res["missing_count"], 1)

        # 9. Empty arguments handling
        empty_res = self._call_tool("check_inventory_cards", {})
        self.assertEqual(empty_res["total_queried"], 0)
        self.assertEqual(empty_res["found_count"], 0)

    def test_watchlist_and_deals_tools(self):
        """Verifies get_watchlist, get_active_deals, and add_to_watchlist tools."""
        wl = self._call_tool("get_watchlist", {"deals_only": True})
        self.assertEqual(wl["deals_count"], 1)
        self.assertEqual(wl["watchlist"][0]["name"], "The One Ring")
        self.assertEqual(wl["watchlist"][0]["lowest_price"], 52.50)
        self.assertEqual(wl["watchlist"][0]["median_price"], 52.50)
        self.assertEqual(wl["watchlist"][0]["vendor_median_price"], 52.50)
        self.assertEqual(wl["watchlist"][0]["tcgplayer_price"], 52.50)
        self.assertIn("summary", wl)
        self.assertEqual(wl["summary"]["deals_count"], 1)
        self.assertEqual(wl["summary"]["total_target_value_usd"], 60.0)
        self.assertEqual(wl["summary"]["total_median_price_usd"], 52.50)

        # Seed additional cards with multiple vendor prices to test statistical median and sorting
        with self.app.app_context():
            user = User.query.get(self.user_id)
            w2 = WatchlistItem(
                user_id=user.id,
                name="Mana Drain",
                set_code="2X2",
                finish="nonfoil",
                type_line="Instant",
                color_identity="U",
                cmc=2.0,
                target_price=35.0,
                tag="Priority Staples",
            )
            w3 = WatchlistItem(
                user_id=user.id,
                name="Demonic Tutor",
                set_code="DMR",
                finish="nonfoil",
                type_line="Sorcery",
                color_identity="B",
                cmc=2.0,
                target_price=25.0,
                tag="Tutors",
            )
            db.session.add_all([w2, w3])
            db.session.flush()

            # Mana Drain: TCG=40, MM=36, eBay=44 -> Statistical Median = 40.0
            vp2_1 = VendorPrice(watchlist_id=w2.id, vendor_name="TCGplayer", price=40.0, in_stock=True)
            vp2_2 = VendorPrice(watchlist_id=w2.id, vendor_name="Mighty Meeple", price=36.0, in_stock=True)
            vp2_3 = VendorPrice(watchlist_id=w2.id, vendor_name="eBay", price=44.0, in_stock=True)

            # Demonic Tutor: TCG=30, MM=28, eBay=32 -> Statistical Median = 30.0
            vp3_1 = VendorPrice(watchlist_id=w3.id, vendor_name="TCGplayer", price=30.0, in_stock=True)
            vp3_2 = VendorPrice(watchlist_id=w3.id, vendor_name="Mighty Meeple", price=28.0, in_stock=True)
            vp3_3 = VendorPrice(watchlist_id=w3.id, vendor_name="eBay", price=32.0, in_stock=True)
            db.session.add_all([vp2_1, vp2_2, vp2_3, vp3_1, vp3_2, vp3_3])
            db.session.commit()

        # Test querying full watchlist with median prices and sort_by
        wl_all = self._call_tool("get_watchlist", {"sort_by": "median_price_desc"})
        self.assertEqual(wl_all["total_matching"], 3)
        # Order should be The One Ring (52.50) -> Mana Drain (40.0) -> Demonic Tutor (30.0)
        self.assertEqual(wl_all["watchlist"][0]["name"], "The One Ring")
        self.assertEqual(wl_all["watchlist"][1]["name"], "Mana Drain")
        self.assertEqual(wl_all["watchlist"][1]["vendor_median_price"], 40.0)
        self.assertEqual(wl_all["watchlist"][1]["tcgplayer_price"], 40.0)
        self.assertEqual(wl_all["watchlist"][1]["mighty_meeple_price"], 36.0)
        self.assertEqual(wl_all["watchlist"][1]["ebay_price"], 44.0)
        self.assertEqual(wl_all["watchlist"][1]["best_vendor"], "Mighty Meeple")
        self.assertEqual(wl_all["watchlist"][2]["name"], "Demonic Tutor")
        self.assertEqual(wl_all["watchlist"][2]["vendor_median_price"], 30.0)

        # Test sort_by median_price_asc
        wl_asc = self._call_tool("get_watchlist", {"sort_by": "median_price_asc"})
        self.assertEqual(wl_asc["watchlist"][0]["name"], "Demonic Tutor")
        self.assertEqual(wl_asc["watchlist"][2]["name"], "The One Ring")

        # Test filtering by color and card_type
        wl_blue = self._call_tool("get_watchlist", {"color": "u"})
        self.assertEqual(wl_blue["total_matching"], 1)
        self.assertEqual(wl_blue["watchlist"][0]["name"], "Mana Drain")

        wl_sorcery = self._call_tool("get_watchlist", {"card_type": "Sorcery"})
        self.assertEqual(wl_sorcery["total_matching"], 1)
        self.assertEqual(wl_sorcery["watchlist"][0]["name"], "Demonic Tutor")

        # Test price bounds filter
        wl_budget = self._call_tool("get_watchlist", {"max_price": 35.0})
        self.assertEqual(wl_budget["total_matching"], 1)
        self.assertEqual(wl_budget["watchlist"][0]["name"], "Demonic Tutor")

        # Test grouping
        wl_grouped = self._call_tool("get_watchlist", {"group_by": "tag"})
        self.assertIn("grouped_watchlist", wl_grouped)
        self.assertIn("Priority Staples", wl_grouped["grouped_watchlist"])
        self.assertIn("Tutors", wl_grouped["grouped_watchlist"])
        self.assertEqual(len(wl_grouped["grouped_watchlist"]["Priority Staples"]), 2)

        # Test tool alias search_watchlist
        wl_alias = self._call_tool("search_watchlist", {"query": "Mana Drain"})
        self.assertEqual(wl_alias["total_matching"], 1)
        self.assertEqual(wl_alias["watchlist"][0]["name"], "Mana Drain")

        deals = self._call_tool("get_active_deals", {"category": "all"})
        self.assertEqual(deals["singles_deals_count"], 1)
        self.assertEqual(len(deals["microcenter_deals"]), 1)

        with patch("providers.scryfall.ScryfallProvider.get_card_named") as mock_sf:
            mock_sf.return_value = {
                "id": "sf-cyc-1",
                "name": "Cyclonic Rift",
                "set_code": "RVR",
                "collector_number": "40",
                "image_uri": "https://example.com/cyc.jpg",
                "prices": {"usd": "28.50"},
                "tcgplayer_url": "https://tcgplayer.com/cyc",
            }
            added = self._call_tool(
                "add_to_watchlist",
                {"card_name": "Cyclonic Rift", "target_price": 25.0, "set_code": "RVR", "tag": "Blue Staples"},
            )
            self.assertEqual(added["status"], "created")
            self.assertEqual(added["item"]["name"], "Cyclonic Rift")
            self.assertEqual(added["item"]["target_price"], 25.0)

        # Multi-card watchlist tests
        with patch("providers.scryfall.ScryfallProvider.get_cards_collection") as mock_coll:
            mock_coll.return_value = (
                {
                    "smothering tithe": {
                        "id": "sf-smoth-1",
                        "name": "Smothering Tithe",
                        "prices": {"usd": "30.00"},
                    },
                    "esper sentinel": {
                        "id": "sf-esper-1",
                        "name": "Esper Sentinel",
                        "prices": {"usd": "35.00"},
                    },
                },
                [],
            )
            multi_added = self._call_tool(
                "add_to_watchlist",
                {
                    "cards": ["Smothering Tithe", "Esper Sentinel"],
                    "target_price": 22.0,
                    "tag": "White Staples",
                },
            )
            self.assertEqual(multi_added["total_requested"], 2)
            self.assertEqual(multi_added["added_count"], 2)
            self.assertEqual(len(multi_added["added_items"]), 2)
            self.assertEqual(multi_added["added_items"][0]["tag"], "White Staples")
            self.assertEqual(multi_added["added_items"][0]["target_price"], 22.0)

        # Multi-card mixed update & create with dict objects
        with patch("providers.scryfall.ScryfallProvider.get_cards_collection") as mock_coll:
            mock_coll.return_value = (
                {
                    "cyclonic rift": {
                        "id": "sf-cyc-1",
                        "name": "Cyclonic Rift",
                        "set_code": "RVR",
                        "collector_number": "40",
                        "prices": {"usd": "28.50"},
                    },
                    "mystic remora": {
                        "id": "sf-remora-1",
                        "name": "Mystic Remora",
                        "prices": {"usd": "10.00", "usd_foil": "15.00"},
                    },
                },
                [],
            )
            mixed_res = self._call_tool(
                "add_to_watchlist",
                {
                    "cards": [
                        {"name": "Cyclonic Rift", "target_price": 20.0, "set_code": "RVR", "tag": "Updated Blue"},
                        {"name": "Mystic Remora", "target_price": 7.5, "finish": "foil", "tag": "Draw Engine"},
                    ]
                },
            )
            self.assertEqual(mixed_res["total_requested"], 2)
            self.assertEqual(mixed_res["updated_count"], 1)
            self.assertEqual(mixed_res["added_count"], 1)
            self.assertEqual(mixed_res["updated_items"][0]["target_price"], 20.0)
            self.assertEqual(mixed_res["updated_items"][0]["tag"], "Updated Blue")
            self.assertEqual(mixed_res["added_items"][0]["finish"], "foil")

        # Multi-card semicolon-separated card_name input
        with patch("providers.scryfall.ScryfallProvider.get_cards_collection") as mock_coll:
            mock_coll.return_value = (
                {
                    "fierce guardianship": {
                        "id": "sf-fierce-1",
                        "name": "Fierce Guardianship",
                        "prices": {"usd": "40.00"},
                    },
                    "deflecting swat": {
                        "id": "sf-swat-1",
                        "name": "Deflecting Swat",
                        "prices": {"usd": "50.00"},
                    },
                },
                [],
            )
            semi_res = self._call_tool(
                "add_to_watchlist",
                {
                    "card_name": "Fierce Guardianship; Deflecting Swat",
                    "target_strategy": "good_deal",
                    "tag": "Free Spells",
                },
            )
            self.assertEqual(semi_res["total_requested"], 2)
            self.assertEqual(semi_res["added_count"], 2)
            # 40 * 0.90 = 36.00, 50 * 0.90 = 45.00
            self.assertEqual(semi_res["added_items"][0]["target_price"], 36.00)
            self.assertEqual(semi_res["added_items"][1]["target_price"], 45.00)

        # Empty card input error handling
        err_res = self._call_tool("add_to_watchlist", {}, expect_error=True)
        self.assertIn("error", err_res)

        with patch("providers.scryfall.ScryfallProvider.get_card_named") as mock_named, patch(
            "providers.scryfall.ScryfallProvider.get_cheapest_tcgplayer_price"
        ) as mock_cheapest:
            mock_named.return_value = {"name": "Rhystic Study", "set_code": "WOT", "prices": {"usd": "42.00"}}
            mock_cheapest.return_value = {"vendor_name": "TCGplayer", "price": 39.99, "in_stock": True}
            price_lookup = self._call_tool("search_card_prices", {"card_name": "Rhystic Study"})
            self.assertEqual(price_lookup["total_owned_quantity"], 2)
            self.assertEqual(price_lookup["lowest_market_printing"]["price"], 39.99)

    def test_deck_analyses_and_card_fit_tools(self):
        """Verifies get_deck_analyses, get_deck_cards, and check_card_deck_fit tools."""
        decks_res = self._call_tool("get_deck_analyses", {"query": "Ur-Dragon"})
        self.assertIn("deck", decks_res)
        self.assertEqual(decks_res["deck"]["commander_name"], "The Ur-Dragon")
        self.assertEqual(decks_res["deck"]["power_level"], 8.0)
        self.assertIn("cards_data", decks_res["deck"])

        # Test get_deck_cards tool
        deck_cards = self._call_tool("get_deck_cards", {"query": "Ur-Dragon", "group_by_type": True})
        self.assertEqual(deck_cards["deck_name"], "The Ur-Dragon Tribal")
        self.assertEqual(deck_cards["commander_name"], "The Ur-Dragon")
        self.assertIn("cards_by_type", deck_cards)
        self.assertIn("text_decklist", deck_cards)
        self.assertIn("1 The Ur-Dragon", deck_cards["text_decklist"])
        self.assertIn("1 Sol Ring", deck_cards["text_decklist"])
        self.assertIn("Commander", deck_cards["cards_by_type"])
        self.assertIn("Artifacts", deck_cards["cards_by_type"])

        # Test get_deck_cards with flat list
        flat_cards = self._call_tool("get_deck_cards", {"query": "Ur-Dragon", "group_by_type": False})
        self.assertIn("cards", flat_cards)
        self.assertEqual(len(flat_cards["cards"]), 2)

        # Check fit for Rhystic Study (owned + in upgrade recommendations)
        fit_res = self._call_tool("check_card_deck_fit", {"card_name": "Rhystic Study"})
        self.assertEqual(fit_res["owned_copies"], 2)
        self.assertEqual(fit_res["decks_evaluated"], 1)
        deck_fit = fit_res["deck_fit_breakdown"][0]
        self.assertFalse(deck_fit["already_in_deck"])
        self.assertIsNotNone(deck_fit["upgrade_recommendation"])

        # Check fit for Sol Ring (already in deck + rated 10)
        sol_fit = self._call_tool("check_card_deck_fit", {"card_name": "Sol Ring"})
        self.assertTrue(sol_fit["deck_fit_breakdown"][0]["already_in_deck"])
        self.assertEqual(sol_fit["deck_fit_breakdown"][0]["ai_rating_in_deck"]["rating"], 10)

        # Bulk check fit
        bulk_fit = self._call_tool("check_card_deck_fit", {"cards": ["Rhystic Study", "Sol Ring"]})
        self.assertEqual(bulk_fit["total_cards_evaluated"], 2)
        self.assertEqual(len(bulk_fit["cards"]), 2)

        # Batch deck cards and analyses
        batch_deck_cards = self._call_tool("get_deck_cards", {"deck_ids": [self.deck_id]})
        self.assertEqual(batch_deck_cards["total_decks"], 1)

        batch_deck_analyses = self._call_tool("get_deck_analyses", {"deck_ids": [self.deck_id]})
        self.assertEqual(batch_deck_analyses["total_decks"], 1)

    @patch("inventory_manager.InventoryManager.start_background_enrichment")
    def test_bulk_upload_inventory(self, mock_enrich):
        """Verifies upload_inventory tool with card objects, decklist text, and CSV content."""
        mock_enrich.return_value = {"is_running": False}
        with patch("providers.scryfall.ScryfallProvider.get_cards_collection") as mock_coll:
            mock_coll.return_value = ({}, [])
            # 1. Upload inventory via card objects in merge mode
            res1 = self._call_tool(
                "upload_inventory",
                {
                    "cards": [
                        {"name": "Mana Drain", "quantity": 2, "foil": "foil", "binder_name": "Blue Counterspells"},
                        {"name": "Sol Ring", "quantity": 1, "foil": "normal", "binder_name": "Commander Core"},
                    ],
                    "mode": "merge",
                },
            )
            self.assertTrue(res1["success"])
            self.assertEqual(res1["mode"], "merge")
            self.assertEqual(res1["cards_parsed_count"], 2)

            # Verify inventory has incremented Sol Ring count to 5 and added Mana Drain
            check_sol = self._call_tool("check_inventory_cards", {"card_name": "Sol Ring"})
            self.assertEqual(check_sol["total_owned_quantity"], 5)

            check_drain = self._call_tool("check_inventory_cards", {"card_name": "Mana Drain"})
            self.assertEqual(check_drain["total_owned_quantity"], 2)
            self.assertTrue(check_drain["found_cards"][0]["has_foil"])

            # 2. Upload inventory via decklist text
            res2 = self._call_tool(
                "upload_inventory",
                {
                    "text": "1x Fierce Guardianship\n2 Deflecting Swat",
                    "binder_name": "Commander Free Spells",
                },
            )
            self.assertTrue(res2["success"])
            self.assertEqual(res2["cards_parsed_count"], 2)

            check_free = self._call_tool("check_inventory_cards", {"cards": ["Fierce Guardianship", "Deflecting Swat"]})
            self.assertEqual(check_free["found_count"], 2)

            # 3. Upload inventory via ManaBox CSV content
            csv_sample = (
                "Binder Name,Name,Set code,Set name,Collector number,Foil,Rarity,Quantity\n"
                "Vintage,Black Lotus,2ED,Unlimited Edition,232,normal,Rare,1\n"
                "Vintage,Mox Sapphire,2ED,Unlimited Edition,264,normal,Rare,1\n"
            )
            res3 = self._call_tool("upload_inventory", {"csv_content": csv_sample, "mode": "merge"})
            self.assertTrue(res3["success"])
            self.assertEqual(res3["cards_parsed_count"], 2)

            check_p9 = self._call_tool("check_inventory_cards", {"cards": ["Black Lotus", "Mox Sapphire"]})
            self.assertEqual(check_p9["found_count"], 2)

    def test_bulk_upload_decks(self):
        """Verifies upload_decks tool with text decklist and deck objects."""
        with patch("providers.scryfall.ScryfallProvider.get_cards_collection") as mock_coll:
            mock_coll.return_value = ({}, [])
            # 1. Upload deck via text
            decklist = (
                "// Commander\n"
                "1 Edgar Markov\n\n"
                "// Mainboard\n"
                "1 Sol Ring\n"
                "1 Blood Artist\n"
                "1 Cordial Vampire\n"
            )
            res = self._call_tool(
                "upload_decks",
                {
                    "text": decklist,
                    "deck_name": "Edgar Markov Vampire Aristocrats",
                    "commander_name": "Edgar Markov",
                },
            )
            self.assertTrue(res["success"])
            self.assertEqual(res["imported_count"], 1)
            self.assertEqual(res["decks"][0]["deck_name"], "Edgar Markov Vampire Aristocrats")

            # 2. Upload multiple deck objects
            res_multi = self._call_tool(
                "upload_decks",
                {
                    "decks": [
                        {
                            "deck_name": "Krenko Goblin Mob",
                            "commander_name": "Krenko, Mob Boss",
                            "text": "1 Krenko, Mob Boss\n1 Goblin Chieftain\n1 Sol Ring",
                        },
                        {
                            "deck_name": "Atraxa Superfriends",
                            "commander_name": "Atraxa, Praetors' Voice",
                            "text": "1 Atraxa, Praetors' Voice\n1 Sol Ring",
                        },
                    ]
                },
            )
            self.assertTrue(res_multi["success"])
            self.assertEqual(res_multi["imported_count"], 2)

            # Query deck analyses via MCP
            q_decks = self._call_tool("get_deck_analyses", {"queries": ["Edgar", "Krenko", "Atraxa"]})
            self.assertEqual(q_decks["total_decks"], 3)

    @patch("inventory_manager.InventoryManager.start_background_enrichment")
    def test_reconcile_watchlist(self, mock_enrich):
        """Verifies reconcile_watchlist tool with card names, TCGplayer purchase manifest, and inventory ingestion."""
        mock_enrich.return_value = {"is_running": False}
        with patch("providers.scryfall.ScryfallProvider.get_cards_collection") as mock_coll:
            mock_coll.return_value = (
                {
                    "force of will": {"id": "sf-fow-1", "name": "Force of Will", "prices": {"usd": "65.00"}},
                    "mana vault": {"id": "sf-mv-1", "name": "Mana Vault", "prices": {"usd": "45.00"}},
                },
                [],
            )
            self._call_tool("add_to_watchlist", {"cards": ["Force of Will", "Mana Vault"], "target_price": 50.0})

        # Verify added to watchlist
        wl_before = self._call_tool("get_watchlist", {"cards": ["Force of Will", "Mana Vault"]})
        self.assertEqual(wl_before["total_matching"], 2)

        # Reconcile Force of Will via card name with add_to_inventory=True
        with patch("providers.scryfall.ScryfallProvider.get_cards_collection") as mock_coll:
            mock_coll.return_value = ({}, [])
            rec_res1 = self._call_tool(
                "reconcile_watchlist",
                {
                    "cards": ["Force of Will"],
                    "add_to_inventory": True,
                    "binder_name": "Acquired Purchases",
                },
            )
            self.assertTrue(rec_res1["success"])
            self.assertEqual(rec_res1["removed_count"], 1)
            self.assertEqual(rec_res1["inventory_added_count"], 1)

        # Verify Force of Will removed from watchlist and present in inventory
        wl_after1 = self._call_tool("get_watchlist", {"cards": ["Force of Will"]})
        self.assertEqual(wl_after1["total_matching"], 0)

        inv_check = self._call_tool("check_inventory_cards", {"card_name": "Force of Will"})
        self.assertTrue(inv_check["all_owned"])
        self.assertEqual(inv_check["found_cards"][0]["binders"], ["Acquired Purchases"])

        # Reconcile Mana Vault via TCGplayer format text
        tcg_text = "1\tMagic - Double Masters - Mana Vault - Near Mint"
        rec_res2 = self._call_tool("reconcile_watchlist", {"text": tcg_text})
        self.assertTrue(rec_res2["success"])
        self.assertEqual(rec_res2["removed_count"], 1)

        wl_after2 = self._call_tool("get_watchlist", {"cards": ["Mana Vault"]})
        self.assertEqual(wl_after2["total_matching"], 0)

    def test_bulk_search_card_prices(self):
        """Verifies search_card_prices tool with single card and bulk list."""
        with patch("providers.scryfall.ScryfallProvider.get_cards_collection") as mock_coll:
            mock_coll.return_value = (
                {
                    "cyclonic rift": {"name": "Cyclonic Rift", "set_code": "RVR", "prices": {"usd": "28.50"}},
                    "rhystic study": {"name": "Rhystic Study", "set_code": "WOT", "prices": {"usd": "42.00"}},
                },
                [],
            )
            bulk_prices = self._call_tool("search_card_prices", {"cards": ["Cyclonic Rift", "Rhystic Study"]})
            self.assertEqual(bulk_prices["total_queried"], 2)
            self.assertEqual(len(bulk_prices["results"]), 2)
            # Sol Ring and Rhystic Study were seeded in setUp; Rhystic Study should be owned
            rhystic = next(c for c in bulk_prices["results"] if c["card_name"] == "Rhystic Study")
            self.assertEqual(rhystic["owned_quantity"], 2)

    def test_gemini_hub_ui_and_cloud_run_https_url(self):
        """Verifies /gemini-hub renders Cloud Run HTTPS MCP URL, token rotation, and tool testing API."""
        self._login()
        self.app.config["APP_BASE_URL"] = "https://chimera-mtg-xyz.a.run.app"
        resp = self.client.get("/gemini-hub")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn(f"https://chimera-mtg-xyz.a.run.app/mcp?token={self.api_token}", html)
        self.assertIn("Chimaera MTG Tactical Advisor", html)

        # Test token rotation endpoint
        regen_resp = self.client.post("/api/user/regenerate-token")
        self.assertEqual(regen_resp.status_code, 200)
        regen_data = regen_resp.get_json()
        self.assertTrue(regen_data["success"])
        self.assertNotEqual(regen_data["api_token"], self.api_token)
        self.assertTrue(regen_data["mcp_url"].startswith("https://chimera-mtg-xyz.a.run.app/mcp?token="))

        # Test in-browser tool tester API
        test_tool_resp = self.client.post(
            "/api/mcp/test-tool",
            json={"tool_name": "get_inventory_summary", "arguments": {"top_n": 3}},
        )
        self.assertEqual(test_tool_resp.status_code, 200)
        self.assertTrue(test_tool_resp.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
