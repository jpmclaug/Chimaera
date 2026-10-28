"""
Automated test suite for the Chimaera Model Context Protocol (MCP) Server
and Google Gemini Mobile App / Cloud Run integration.
"""

import json
import unittest
from unittest.mock import patch

from app import create_app
from models import (
    BestBuyItem,
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
            bb = BestBuyItem(
                sku="BB-888",
                name="MTG Modern Horizons 3 Bundle",
                current_price=54.99,
                regular_price=69.99,
                in_stock=True,
                target_price=60.0,
                is_active=True,
            )
            db.session.add_all([mc, bb])

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
        self.assertEqual(len(tools), 8)
        tool_names = {t["name"] for t in tools}
        self.assertIn("get_inventory_summary", tool_names)
        self.assertIn("search_inventory", tool_names)
        self.assertIn("get_watchlist", tool_names)
        self.assertIn("get_active_deals", tool_names)
        self.assertIn("get_deck_analyses", tool_names)
        self.assertIn("search_card_prices", tool_names)
        self.assertIn("add_to_watchlist", tool_names)
        self.assertIn("check_card_deck_fit", tool_names)

    def test_mcp_sse_transport_and_messages(self):
        """Verifies SSE stream endpoint discovery and /mcp/messages execution."""
        sse_resp = self.client.get(f"/mcp?token={self.api_token}&single_event=1")
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
        self.assertEqual(len(msg_resp.get_json()["result"]["tools"]), 8)

    def _call_tool(self, name: str, arguments: dict | None = None) -> dict:
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
        self.assertFalse(result.get("isError", False), f"Tool {name} returned error: {result}")
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

    def test_watchlist_and_deals_tools(self):
        """Verifies get_watchlist, get_active_deals, and add_to_watchlist tools."""
        wl = self._call_tool("get_watchlist", {"deals_only": True})
        self.assertEqual(wl["deals_count"], 1)
        self.assertEqual(wl["watchlist"][0]["name"], "The One Ring")
        self.assertEqual(wl["watchlist"][0]["lowest_price"], 52.50)

        deals = self._call_tool("get_active_deals", {"category": "all"})
        self.assertEqual(deals["singles_deals_count"], 1)
        self.assertEqual(len(deals["microcenter_deals"]), 1)
        self.assertEqual(len(deals["bestbuy_deals"]), 1)

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

        with patch("providers.scryfall.ScryfallProvider.get_card_named") as mock_named, patch(
            "providers.scryfall.ScryfallProvider.get_cheapest_tcgplayer_price"
        ) as mock_cheapest:
            mock_named.return_value = {"name": "Rhystic Study", "set_code": "WOT", "prices": {"usd": "42.00"}}
            mock_cheapest.return_value = {"vendor_name": "TCGplayer", "price": 39.99, "in_stock": True}
            price_lookup = self._call_tool("search_card_prices", {"card_name": "Rhystic Study"})
            self.assertEqual(price_lookup["total_owned_quantity"], 2)
            self.assertEqual(price_lookup["lowest_market_printing"]["price"], 39.99)

    def test_deck_analyses_and_card_fit_tools(self):
        """Verifies get_deck_analyses and check_card_deck_fit tools."""
        decks_res = self._call_tool("get_deck_analyses", {"query": "Ur-Dragon"})
        self.assertIn("deck", decks_res)
        self.assertEqual(decks_res["deck"]["commander_name"], "The Ur-Dragon")
        self.assertEqual(decks_res["deck"]["power_level"], 8.0)

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
