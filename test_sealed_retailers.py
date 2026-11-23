"""
Automated unit and integration tests for multi-retailer sealed MTG surveillance:
  - providers.sealed_matcher
  - providers.sealed_trust
  - providers.cardkingdom_sealed
  - providers.shopify_catalog
  - DealEngine.poll_sealed_item (multi-retailer upsert & alert safety)
  - UCP agent profile, trusted-shop API, and MCP scan_sealed_retailers tool
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from chimera_mcp import execute_mcp_tool
from deal_engine import DealEngine
from models import AllowedEmail, SystemSetting, User, VendorPrice, WatchlistItem, db
from providers.cardkingdom_sealed import CardKingdomSealedProvider
from providers.cardkingdom_singles import CardKingdomSinglesProvider
from providers.sealed_matcher import build_search_query, match_sealed
from providers.sealed_trust import evaluate_offers_trust
from providers.shopify_catalog import ShopifyCatalogProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SHOPIFY_CATALOG_RESPONSE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "structuredContent": {
            "ucp": {"version": "2026-08-25"},
            "pagination": {"has_next_page": False, "total_count": 9},
            "products": [
                {
                    "id": "gid://shopify/p/1",
                    "title": "Magic: The Gathering: Wilds of Eldraine Booster Box",
                    "handle": "magic-gathering-wilds-eldraine-set-booster-box",
                    "variants": [
                        {
                            "id": "gid://shopify/ProductVariant/1",
                            "title": "Magic: The Gathering: Wilds of Eldraine Booster Box",
                            "handle": "magic-gathering-wilds-eldraine-set-booster-box",
                            "url": "https://cavegamers.com/products/magic-gathering-wilds-eldraine-set-booster-box",
                            "checkout_url": "https://cavegamers.com/buy/1:1",
                            "price": {"amount": 44999, "currency": "USD"},
                            "availability": {"available": True},
                            "seller": {
                                "id": "gid://shopify/Shop/44722094237",
                                "name": "CaveGamers",
                                "url": "https://cavegamers.com",
                                "domain": "cave-gamers.myshopify.com",
                            },
                        }
                    ],
                },
                {
                    "id": "gid://shopify/p/2",
                    "title": "Magic: The Gathering Wilds of Eldraine Set Booster Box - 30 Packs (360 Magic Cards)",
                    "handle": "magic-the-gathering-wilds-of-eldraine-set-booster-box",
                    "variants": [
                        {
                            "id": "gid://shopify/ProductVariant/2",
                            "title": "Magic: The Gathering Wilds of Eldraine Set Booster Box - 30 Packs (360 Magic Cards)",
                            "url": "https://fairstore.shop/products/wilds-of-eldraine-set-booster-box",
                            "price": {"amount": 4000, "currency": "USD"},
                            "availability": {"available": True},
                            "seller": {
                                "id": "gid://shopify/Shop/999001",
                                "name": "Fairstore",
                                "url": "https://fairstore.shop",
                                "domain": "fairstore.shop",
                            },
                        }
                    ],
                },
                {
                    "id": "gid://shopify/p/3",
                    "title": "Hot Sale The Gathering - Wilds of Eldraine - Set Booster Display",
                    "handle": "wilds-of-eldraine-set-booster-display",
                    "variants": [
                        {
                            "id": "gid://shopify/ProductVariant/3",
                            "title": "Hot Sale The Gathering - Wilds of Eldraine - Set Booster Display",
                            "url": "https://diamond-vault.shop/products/wilds-of-eldraine-set-booster-display",
                            "price": {"amount": 7499, "currency": "USD"},
                            "availability": {"available": True},
                            "seller": {
                                "id": "gid://shopify/Shop/999002",
                                "name": "Diamondvault",
                                "url": "https://diamond-vault.shop",
                                "domain": "diamond-vault.shop",
                            },
                        }
                    ],
                },
                *[
                    {
                        "id": f"gid://shopify/p/clone-{idx}",
                        "title": "Wilds of Eldraine Set Booster Box | MAGIC: THE GATHERING | English",
                        "handle": "wilds-of-eldraine-set-booster-box",
                        "variants": [
                            {
                                "id": f"gid://shopify/ProductVariant/clone-{idx}",
                                "title": "Wilds of Eldraine Set Booster Box | MAGIC: THE GATHERING | English",
                                "url": f"https://pullstore-{idx}.com/products/woe-set-box",
                                "price": {"amount": 23795, "currency": "USD"},
                                "availability": {"available": True},
                                "seller": {
                                    "id": f"gid://shopify/Shop/88800{idx}",
                                    "name": label,
                                    "url": f"https://pullstore-{idx}.com",
                                    "domain": f"pullstore-{idx}.com",
                                },
                            }
                        ],
                    }
                    for idx, label in enumerate(
                        ["PULL STORE I", "PULL STORE II", "PULL STORE III", "PULL STORE |||", "PULL STORE 2"],
                        start=1,
                    )
                ],
                {
                    "id": "gid://shopify/p/trusted",
                    "title": "Magic: The Gathering - Wilds of Eldraine Set Booster Box",
                    "handle": "wilds-of-eldraine-set-booster-box",
                    "variants": [
                        {
                            "id": "gid://shopify/ProductVariant/777",
                            "title": "Magic: The Gathering - Wilds of Eldraine Set Booster Box",
                            "url": "https://mightymeeple.com/products/wilds-of-eldraine-set-booster-box",
                            "checkout_url": "https://mightymeeple.com/buy/777:1",
                            "price": {"amount": 28999, "currency": "USD"},
                            "availability": {"available": True},
                            "seller": {
                                "id": "gid://shopify/Shop/123456",
                                "name": "Mighty Meeple",
                                "url": "https://mightymeeple.com",
                                "domain": "mightymeeple.com",
                            },
                        }
                    ],
                },
            ],
        }
    },
}


SAMPLE_CK_PRICELIST = {
    "meta": {"created_at": "2026-11-22 19:11:00", "base_url": "https://www.cardkingdom.com/"},
    "data": [
        {
            "id": 1001,
            "url": "mtg-sealed/wilds-of-eldraine-set-booster-box",
            "name": "Wilds of Eldraine Set Booster Box",
            "edition": "Wilds of Eldraine",
            "price_retail": "329.99",
            "qty_retail": 4,
        },
        {
            "id": 1002,
            "url": "mtg-sealed/wilds-of-eldraine-collector-booster-box",
            "name": "Wilds of Eldraine Collector Booster Box",
            "edition": "Wilds of Eldraine",
            "price_retail": "499.99",
            "qty_retail": 2,
        },
        {
            "id": 1003,
            "url": "mtg-sealed/march-of-the-machine-bundle",
            "name": "March of the Machine Bundle",
            "edition": "March of the Machine",
            "price_retail": "79.99",
            "qty_retail": 0,
        },
        {
            "id": 1004,
            "url": "mtg-sealed/modern-horizons-3-play-booster-box",
            "name": "Modern Horizons 3 Play Booster Box",
            "edition": "Modern Horizons 3",
            "price_retail": "229.99",
            "qty_retail": 10,
        },
    ],
}


@pytest.fixture
def test_client_and_app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-sealed-retailers",
    })
    with app.app_context():
        db.create_all()
        user = User(email="sealed@example.com", name="Sealed Tester", is_admin=True, is_active=True)
        db.session.add(user)
        db.session.add(AllowedEmail(email="sealed@example.com", is_admin=True))
        db.session.commit()
        user_id = user.id

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = "sealed@example.com"
        yield client, app, user_id


# ---------------------------------------------------------------------------
# 1. sealed_matcher tests
# ---------------------------------------------------------------------------

def test_matcher_display_box_equivalence_and_handle_fallback():
    res = match_sealed(
        "Magic: The Gathering - Wilds of Eldraine Set Booster Box",
        "Wilds of Eldraine - Set Booster Display",
    )
    assert res.matched is True
    assert res.score >= 0.8

    # Title omits "Set", but handle disambiguates it
    res_handle = match_sealed(
        "Magic: The Gathering: Wilds of Eldraine Booster Box",
        "Wilds of Eldraine - Set Booster Display",
        listing_handle="magic-gathering-wilds-eldraine-set-booster-box",
    )
    assert res_handle.matched is True


def test_matcher_rejects_subtype_and_category_mismatches():
    assert not match_sealed(
        "Wilds of Eldraine Collector Booster Box",
        "Wilds of Eldraine - Set Booster Display",
    ).matched
    assert not match_sealed(
        "Wilds of Eldraine Set Booster Pack",
        "Wilds of Eldraine - Set Booster Display",
    ).matched
    assert not match_sealed(
        "Wilds of Eldraine Gift Bundle",
        "Wilds of Eldraine - Bundle",
    ).matched


def test_matcher_sleeved_and_omega_pack_specificity():
    assert match_sealed(
        "Magic: The Gathering Foundations Sleeved Play Booster Pack",
        "Magic: The Gathering Foundations - Sleeved Play Booster Pack",
    ).matched
    assert not match_sealed(
        "Magic: The Gathering Foundations Play Booster Pack",
        "Magic: The Gathering Foundations - Sleeved Play Booster Pack",
    ).matched
    assert not match_sealed(
        "Magic: The Gathering Foundations Sleeved Play Booster Pack",
        "Magic: The Gathering Foundations - Play Booster Pack",
    ).matched

    assert match_sealed(
        "Dominaria United Collector Booster Omega Pack",
        "Dominaria United - Collector Booster Omega Pack",
    ).matched
    assert not match_sealed(
        "Dominaria United Collector Booster Pack",
        "Dominaria United - Collector Booster Omega Pack",
    ).matched
    assert not match_sealed(
        "Dominaria United Collector Booster Omega Pack",
        "Dominaria United - Collector Booster Pack",
    ).matched


def test_matcher_rejects_cases_lots_accessories_and_wrong_sets():
    item = "March of the Machine - Set Booster Display"
    for bad in [
        "March of the Machine Set Booster Box Case (6 Boxes)",
        "March of the Machine Set Booster Box - Lot of 2",
        "March of the Machine Set Booster Box 2x",
        "March of the Machine Set Booster Box (Japanese)",
        "March of the Machine Set Booster Box EMPTY",
        "March of the Machine: The Aftermath Set Booster Box",
        "March of the Machine Set Booster Card Sleeves",
    ]:
        assert not match_sealed(bad, item).matched, f"Should have rejected: {bad}"

    # Modern Horizons 2 must not match Modern Horizons 3
    assert not match_sealed(
        "Modern Horizons 2 Play Booster Box",
        "Modern Horizons 3 - Play Booster Display",
    ).matched


def test_build_search_query_prefixes_mtg():
    assert build_search_query("Wilds of Eldraine - Set Booster Display") == "MTG Wilds of Eldraine Set Booster Display"
    assert build_search_query("Magic: The Gathering Foundations - Play Booster Pack") == (
        "Magic: The Gathering Foundations Play Booster Pack"
    )


# ---------------------------------------------------------------------------
# 2. sealed_trust tests (using recorded Shopify catalog shape)
# ---------------------------------------------------------------------------

def test_trust_filter_catches_scams_clones_and_allows_trusted():
    offers = ShopifyCatalogProvider.flatten_catalog_response(
        SAMPLE_SHOPIFY_CATALOG_RESPONSE["result"]["structuredContent"]
    )
    decisions = evaluate_offers_trust(
        offers,
        market_price=363.04,
        min_market_ratio=0.55,
        trusted_shops=[{"name": "Mighty Meeple", "domain": "mightymeeple.com", "shop_gid": ""}],
    )
    by_name = {offer["shop_name"]: decision for offer, decision in decisions}

    # $40 box -> suspicious (11% of market)
    assert by_name["Fairstore"].status == "suspicious"
    # $74.99 Hot Sale display -> suspicious (21% of market)
    assert by_name["Diamondvault"].status == "suspicious"
    # 5x PULL STORE clones at $237.95 -> all suspicious (clone farm)
    for clone_name in ["PULL STORE I", "PULL STORE II", "PULL STORE III", "PULL STORE |||", "PULL STORE 2"]:
        assert by_name[clone_name].status == "suspicious", f"{clone_name} should be suspicious"
    # CaveGamers at $449.99 -> unverified (legit price, not on allowlist)
    assert by_name["CaveGamers"].status == "unverified"
    # Mighty Meeple -> trusted (allowlisted)
    assert by_name["Mighty Meeple"].status == "trusted"


# ---------------------------------------------------------------------------
# 3. CardKingdomSealedProvider tests
# ---------------------------------------------------------------------------

def test_cardkingdom_parsing_stock_and_single_http_call_caching():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"ETag": '"ck-v1"'}
    mock_resp.json.return_value = SAMPLE_CK_PRICELIST
    mock_session.get.return_value = mock_resp

    provider = CardKingdomSealedProvider(session=mock_session, cache_ttl_seconds=1800)

    # In-stock item
    woe = provider.find_offer("Wilds of Eldraine - Set Booster Display")
    assert woe is not None
    assert woe["vendor_name"] == "Card Kingdom"
    assert woe["price"] == 329.99
    assert woe["in_stock"] is True
    assert woe["product_url"] == "https://www.cardkingdom.com/mtg-sealed/wilds-of-eldraine-set-booster-box"

    # Out-of-stock item (qty_retail == 0)
    mom = provider.find_offer("March of the Machine - Bundle")
    assert mom is not None
    assert mom["price"] == 79.99
    assert mom["in_stock"] is False

    # 30 consecutive lookups must still only hit HTTP once
    for _ in range(30):
        provider.find_offer("Modern Horizons 3 - Play Booster Display")
    assert mock_session.get.call_count == 1


# ---------------------------------------------------------------------------
# 4. ShopifyCatalogProvider end-to-end mocked search
# ---------------------------------------------------------------------------

def test_shopify_catalog_find_offers_filters_and_ranks():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = SAMPLE_SHOPIFY_CATALOG_RESPONSE
    mock_session.post.return_value = mock_resp

    provider = ShopifyCatalogProvider(
        session=mock_session,
        min_request_interval=0.0,
        min_market_ratio=0.55,
    )
    res = provider.find_offers(
        "Wilds of Eldraine - Set Booster Display",
        market_price=363.04,
        trusted_shops=[{"name": "Mighty Meeple", "domain": "mightymeeple.com", "shop_gid": ""}],
        limit_pages=1,
    )

    assert res["best_trusted"] is not None
    assert res["best_trusted"]["vendor_name"] == "Shop · Mighty Meeple"
    assert res["best_trusted"]["price"] == 289.99
    assert res["best_trusted"]["in_stock"] is True

    assert res["best_unverified"] is not None
    assert res["best_unverified"]["vendor_name"] == "Shop (unverified) · CaveGamers"
    assert res["best_unverified"]["price"] == 449.99
    assert res["best_unverified"]["in_stock"] is False

    # 7 suspicious offers: Fairstore, Diamondvault, and 5 PULL STORE clones
    assert len(res["suspicious_offers"]) == 7


# ---------------------------------------------------------------------------
# 5. DealEngine.poll_sealed_item integration (alert safety + trusted deals)
# ---------------------------------------------------------------------------

def test_deal_engine_unverified_shop_never_triggers_deal_or_alert(test_client_and_app):
    _, app, user_id = test_client_and_app
    with app.app_context():
        item = WatchlistItem(
            user_id=user_id,
            name="Wilds of Eldraine - Set Booster Display",
            finish="sealed",
            type_line="Sealed Booster Box",
            tcgplayer_product_id=496038,
            target_price=300.0,
            tcgplayer_market_price=363.04,
        )
        db.session.add(item)
        db.session.commit()

        engine = DealEngine(app=app)
        with (
            patch.object(
                engine.tcgplayer_sealed,
                "poll_sealed_product",
                return_value={
                    "vendor_name": "TCGplayer",
                    "price": 364.88,
                    "market_price": 363.04,
                    "condition": "Unopened",
                    "in_stock": True,
                    "product_url": "https://www.tcgplayer.com/product/496038",
                    "search_url": "https://www.tcgplayer.com/product/496038",
                },
            ),
            patch.object(engine.cardkingdom_sealed, "find_offer", return_value=None),
            patch.object(
                engine.shopify_catalog,
                "find_offers",
                return_value={
                    "best_trusted": None,
                    "best_unverified": {
                        "vendor_name": "Shop (unverified) · RandomShop",
                        "price": 250.00,  # Below $300 target, but UNVERIFIED!
                        "condition": "Unverified",
                        "in_stock": False,
                        "product_url": "https://randomshop.com/products/woe-box",
                        "search_url": "https://randomshop.com",
                    },
                    "trusted_offers": [],
                    "unverified_offers": [],
                    "suspicious_offers": [],
                },
            ),
            patch.object(engine, "send_discord_deal_alert") as mock_alert,
        ):
            result = engine.poll_sealed_item(item, notify=True, fast_mode=True)

        assert result["is_deal"] is False
        assert result["lowest_price"] == 364.88
        mock_alert.assert_not_called()

        # Verify unverified row is saved in DB for UI display with in_stock=False
        unv = VendorPrice.query.filter_by(
            watchlist_id=item.id,
            vendor_name="Shop (unverified) · RandomShop",
        ).first()
        assert unv is not None
        assert unv.price == 250.00
        assert unv.in_stock is False


def test_deal_engine_trusted_shop_below_target_triggers_deal_and_alert(test_client_and_app):
    _, app, user_id = test_client_and_app
    with app.app_context():
        item = WatchlistItem(
            user_id=user_id,
            name="Wilds of Eldraine - Set Booster Display",
            finish="sealed",
            type_line="Sealed Booster Box",
            tcgplayer_product_id=496038,
            target_price=300.0,
            tcgplayer_market_price=363.04,
        )
        db.session.add(item)
        db.session.commit()

        engine = DealEngine(app=app)
        with (
            patch.object(
                engine.tcgplayer_sealed,
                "poll_sealed_product",
                return_value={
                    "vendor_name": "TCGplayer",
                    "price": 364.88,
                    "market_price": 363.04,
                    "condition": "Unopened",
                    "in_stock": True,
                    "product_url": "https://www.tcgplayer.com/product/496038",
                    "search_url": "https://www.tcgplayer.com/product/496038",
                },
            ),
            patch.object(
                engine.cardkingdom_sealed,
                "find_offer",
                return_value={
                    "vendor_name": "Card Kingdom",
                    "price": 329.99,
                    "condition": "Unopened",
                    "in_stock": True,
                    "product_url": "https://www.cardkingdom.com/mtg-sealed/woe-box",
                    "search_url": "https://www.cardkingdom.com/mtg-sealed/woe-box",
                },
            ),
            patch.object(
                engine.shopify_catalog,
                "find_offers",
                return_value={
                    "best_trusted": {
                        "vendor_name": "Shop · Mighty Meeple",
                        "price": 289.99,
                        "condition": "Unopened",
                        "in_stock": True,
                        "product_url": "https://mightymeeple.com/products/woe-box",
                        "search_url": "https://mightymeeple.com",
                    },
                    "best_unverified": None,
                    "trusted_offers": [],
                    "unverified_offers": [],
                    "suspicious_offers": [],
                },
            ),
            patch.object(engine, "send_discord_deal_alert") as mock_alert,
        ):
            result = engine.poll_sealed_item(item, notify=True, fast_mode=True)

        assert result["is_deal"] is True
        assert result["lowest_price"] == 289.99
        mock_alert.assert_called_once()
        assert mock_alert.call_args.kwargs["best_vendor"].vendor_name == "Shop · Mighty Meeple"


# ---------------------------------------------------------------------------
# 6. Flask endpoints & MCP tool tests
# ---------------------------------------------------------------------------

def test_ucp_profile_and_trusted_shops_api_and_mcp(test_client_and_app):
    client, app, user_id = test_client_and_app

    # 1. Public UCP profile endpoint
    resp = client.get("/.well-known/ucp/agent-profile.json")
    assert resp.status_code == 200
    profile = resp.get_json()
    assert profile["ucp"]["version"] == "2026-08-25"
    assert any(c["name"] == "dev.ucp.shopping.catalog" for c in profile["capabilities"])

    # 2. Seed a sealed item with an unverified Shop VendorPrice and promote it via API
    with app.app_context():
        item = WatchlistItem(
            user_id=user_id,
            name="Modern Horizons 3 - Play Booster Display",
            finish="sealed",
            tcgplayer_product_id=541164,
            target_price=240.0,
            tcgplayer_market_price=301.42,
        )
        db.session.add(item)
        db.session.flush()
        vp = VendorPrice(
            watchlist_id=item.id,
            vendor_name="Shop (unverified) · CaveGamers",
            price=229.99,
            condition="Unverified",
            in_stock=False,
            product_url="https://cavegamers.com/products/mh3-box",
        )
        db.session.add(vp)
        db.session.commit()
        vp_id = vp.id

    add_resp = client.post(
        "/api/sealed/trusted-shops/add",
        json={"name": "CaveGamers", "domain": "cavegamers.com", "vendor_price_id": vp_id},
    )
    assert add_resp.status_code == 200
    add_data = add_resp.get_json()
    assert add_data["success"] is True
    assert add_data["promoted"] is True

    # Verify DB row promoted to trusted & in_stock=True
    with app.app_context():
        promoted_vp = db.session.get(VendorPrice, vp_id)
        assert promoted_vp.vendor_name == "Shop · CaveGamers"
        assert promoted_vp.in_stock is True

        # 3. MCP tool scan_sealed_retailers (cached snapshot mode)
        user = db.session.get(User, user_id)
        mcp_res = execute_mcp_tool(
            user,
            "scan_sealed_retailers",
            {"query": "Modern Horizons 3", "include_unverified": True, "live": False},
        )
        payload = json.loads(mcp_res["content"][0]["text"])
        assert payload["success"] is True
        assert payload["count"] == 1
        assert payload["deals_found"] == 1
        assert payload["items"][0]["best_vendor"] == "Shop · CaveGamers"
        assert payload["items"][0]["lowest_trusted_price"] == 229.99


# ---------------------------------------------------------------------------
# 7. Seller Blacklist & Whitelist Manager tests
# ---------------------------------------------------------------------------

def test_blacklist_precedence_and_purge_api_and_deals_page(test_client_and_app):
    client, app, user_id = test_client_and_app

    # Blacklisted seller must be dropped even when price is normal and it appears on allowlist
    offers = [
        {
            "title": "Wilds of Eldraine Set Booster Box",
            "price": 320.00,
            "shop_name": "SketchyStore",
            "shop_domain": "sketchystore.com",
            "shop_url": "https://sketchystore.com",
            "shop_gid": "gid://shopify/Shop/555",
        }
    ]
    decisions = evaluate_offers_trust(
        offers,
        market_price=360.0,
        trusted_shops=[{"name": "SketchyStore", "domain": "sketchystore.com", "shop_gid": ""}],
        blocked_shops=[{"name": "SketchyStore", "domain": "sketchystore.com", "shop_gid": ""}],
    )
    assert decisions[0][1].status == "suspicious"
    assert decisions[0][1].reason == "blacklisted"

    # Seed a sealed watchlist item with a trusted Shop row, then blacklist via API
    with app.app_context():
        item = WatchlistItem(
            user_id=user_id,
            name="Wilds of Eldraine - Set Booster Display",
            finish="sealed",
            tcgplayer_product_id=496038,
            target_price=300.0,
            tcgplayer_market_price=363.04,
        )
        db.session.add(item)
        db.session.flush()
        vp = VendorPrice(
            watchlist_id=item.id,
            vendor_name="Shop (unverified) · SketchyStore",
            price=250.00,
            condition="Unverified",
            in_stock=False,
            product_url="https://sketchystore.com/products/woe-box",
        )
        db.session.add(vp)
        db.session.commit()
        vp_id = vp.id

    # Verify /deals renders the Retail & Sellers tab with the unverified seller
    deals_resp = client.get("/deals")
    assert deals_resp.status_code == 200
    html = deals_resp.get_data(as_text=True)
    assert "deals-retailers-container" in html
    assert "SketchyStore" in html

    # Blacklist the seller -> purges VendorPrice row immediately
    block_resp = client.post(
        "/api/sealed/blocked-shops/add",
        json={"name": "SketchyStore", "domain": "sketchystore.com", "vendor_price_id": vp_id},
    )
    assert block_resp.status_code == 200
    block_data = block_resp.get_json()
    assert block_data["success"] is True
    assert block_data["removed_rows"] >= 1
    assert any(s["name"] == "SketchyStore" for s in block_data["blocked_shops"])

    with app.app_context():
        assert db.session.get(VendorPrice, vp_id) is None

    # Unblock via API
    unblock_resp = client.post(
        "/api/sealed/blocked-shops/remove",
        json={"name": "SketchyStore", "domain": "sketchystore.com"},
    )
    assert unblock_resp.status_code == 200
    assert not any(s["name"] == "SketchyStore" for s in unblock_resp.get_json()["blocked_shops"])


# ---------------------------------------------------------------------------
# 8. Card Kingdom Singles Provider & DealEngine.poll_card integration tests
# ---------------------------------------------------------------------------

SAMPLE_CK_SINGLES_PRICELIST = {
    "meta": {"created_at": "2026-11-23 05:06:32", "base_url": "https://www.cardkingdom.com/"},
    "data": [
        {
            "id": 20001,
            "scryfall_id": "sf-sol-ring-cmm",
            "url": "mtg/commander-masters/sol-ring",
            "name": "Sol Ring",
            "edition": "Commander Masters",
            "is_foil": "false",
            "price_retail": "1.99",
            "qty_retail": 15,
            "condition_values": {"nm_price": "1.99", "nm_qty": 10, "ex_price": "1.59", "ex_qty": 5},
        },
        {
            "id": 20002,
            "scryfall_id": "sf-sol-ring-cmm",
            "url": "mtg/commander-masters/sol-ring-foil",
            "name": "Sol Ring",
            "edition": "Commander Masters",
            "is_foil": "true",
            "price_retail": "4.99",
            "qty_retail": 3,
            "condition_values": {"nm_price": "4.99", "nm_qty": 3},
        },
        {
            "id": 20003,
            "scryfall_id": "sf-sol-ring-2xm",
            "url": "mtg/double-masters/sol-ring",
            "name": "Sol Ring",
            "edition": "Double Masters",
            "is_foil": "false",
            "price_retail": "1.29",
            "qty_retail": 8,
            "condition_values": {"nm_price": "1.29", "nm_qty": 8},
        },
        {
            "id": 20004,
            "scryfall_id": "sf-bala-ged",
            "url": "mtg/zendikar-rising/bala-ged-recovery",
            "name": "Bala Ged Recovery // Bala Ged Sanctuary",
            "edition": "Zendikar Rising",
            "is_foil": "false",
            "price_retail": "5.49",
            "qty_retail": 0,
            "condition_values": {"nm_price": "5.49", "nm_qty": 0},
        },
    ],
}


def test_cardkingdom_singles_lookup_and_caching():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"ETag": '"ck-singles-v1"'}
    mock_resp.json.return_value = SAMPLE_CK_SINGLES_PRICELIST
    mock_session.get.return_value = mock_resp

    provider = CardKingdomSinglesProvider(session=mock_session, cache_ttl_seconds=3600, use_shared_cache=False)

    # Exact printing by scryfall_id (nonfoil vs foil)
    exact_nf = provider.search_card("Sol Ring", scryfall_id="sf-sol-ring-cmm", finish="nonfoil", is_any_version=False)
    assert exact_nf is not None
    assert exact_nf["vendor_name"] == "Card Kingdom"
    assert exact_nf["price"] == 1.99
    assert exact_nf["in_stock"] is True
    assert exact_nf["condition"] == "NM"

    exact_foil = provider.search_card("Sol Ring", scryfall_id="sf-sol-ring-cmm", finish="foil", is_any_version=False)
    assert exact_foil is not None
    assert exact_foil["price"] == 4.99

    # Any version picks cheapest in-stock printing ($1.29 Double Masters)
    any_ver = provider.search_card("Sol Ring", finish="nonfoil", is_any_version=True)
    assert any_ver is not None
    assert any_ver["price"] == 1.29

    # DFC front-face lookup & out-of-stock status
    dfc = provider.search_card("Bala Ged Recovery", finish="nonfoil", is_any_version=True)
    assert dfc is not None
    assert dfc["price"] == 5.49
    assert dfc["in_stock"] is False

    # All lookups hit HTTP only once
    assert mock_session.get.call_count == 1


def test_deal_engine_poll_card_includes_cardkingdom_for_singles(test_client_and_app):
    _, app, user_id = test_client_and_app
    with app.app_context():
        card = WatchlistItem(
            user_id=user_id,
            name="Sol Ring",
            scryfall_id="sf-sol-ring-cmm",
            set_code=None,  # Any Version
            finish="nonfoil",
            target_price=1.50,
        )
        db.session.add(card)
        db.session.commit()

        engine = DealEngine(app=app)
        with (
            patch.object(
                engine.scryfall,
                "get_cheapest_tcgplayer_price",
                return_value={
                    "vendor_name": "TCGplayer",
                    "price": 2.10,
                    "condition": "NM",
                    "in_stock": True,
                    "product_url": "https://www.tcgplayer.com/search/magic/product?q=Sol+Ring",
                    "search_url": "https://www.tcgplayer.com/search/magic/product?q=Sol+Ring",
                },
            ),
            patch.object(engine.mightymeeple, "search_card", return_value=None),
            patch.object(engine.ebay, "search_card", return_value=None),
            patch.object(
                engine.cardkingdom_singles,
                "search_card",
                return_value={
                    "vendor_name": "Card Kingdom",
                    "price": 1.29,
                    "condition": "NM",
                    "in_stock": True,
                    "product_url": "https://www.cardkingdom.com/mtg/double-masters/sol-ring",
                    "search_url": "https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=Sol+Ring",
                },
            ),
            patch.object(engine, "send_discord_deal_alert") as mock_alert,
        ):
            result = engine.poll_card(card, notify=True, fast_mode=True)

        assert result["is_deal"] is True
        assert result["lowest_price"] == 1.29
        mock_alert.assert_called_once()
        assert mock_alert.call_args.kwargs["best_vendor"].vendor_name == "Card Kingdom"

