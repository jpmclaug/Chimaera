import json
from html.parser import HTMLParser
from unittest.mock import patch

import pytest

from app import create_app
from models import (
    AllowedEmail,
    DeckAnalysis,
    SavedTcgSeller,
    User,
    UserInventoryCard,
    WatchlistItem,
    db,
)
from providers.tcgplayer_seller_scan import (
    SellerNotFoundError,
    TCGplayerSellerScanner,
    clear_seller_scan_cache,
)


class _DummyWatchlistItem:
    def __init__(
        self,
        item_id,
        name,
        target_price=None,
        market_price=None,
        lowest_in_stock_price=None,
        tag=None,
        tcgplayer_product_id=None,
        is_any_version=True,
    ):
        self.id = item_id
        self.name = name
        self.target_price = target_price
        self.market_price = market_price
        self.lowest_in_stock_price = lowest_in_stock_price
        self.tag = tag
        self.tcgplayer_product_id = tcgplayer_product_id
        self.is_any_version = is_any_version
        self.image_uri = f"https://cards.scryfall.io/small/{item_id}.jpg"
        self.in_binder = False
        self.binder_count = 0

    @property
    def is_sealed(self) -> bool:
        return self.tcgplayer_product_id is not None


class _DivBalanceParser(HTMLParser):
    def __init__(self, target_id: str):
        super().__init__()
        self.target_id = target_id
        self.tracking = False
        self.depth = 0
        self.found = False
        self.closed_cleanly = False

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        attrs_d = dict(attrs)
        if not self.tracking and attrs_d.get("id") == self.target_id:
            self.tracking = True
            self.found = True
            self.depth = 1
        elif self.tracking:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag != "div" or not self.tracking:
            return
        self.depth -= 1
        if self.depth == 0:
            self.tracking = False
            self.closed_cleanly = True


def _make_tcg_product(
    pid: int,
    name: str,
    *,
    market_price: float = 10.0,
    lowest_price: float = 9.0,
    price: float = 8.0,
    shipping: float = 0.0,
    condition: str = "Near Mint",
    printing: str = "Normal",
    foil_only: bool = False,
    seller_key: str = "43d828c6",
    seller_name: str = "Test LGS",
    product_type: str = "Cards",
    sealed: bool = False,
    rarity: str = "Rare",
    set_name: str = "Commander Masters",
    set_code: str = "CMM",
):
    return {
        "productId": pid,
        "productName": name,
        "productTypeName": product_type,
        "sealed": sealed,
        "marketPrice": market_price,
        "lowestPrice": lowest_price,
        "lowestPriceWithShipping": round(lowest_price + shipping, 2),
        "medianPrice": market_price,
        "foilOnly": foil_only,
        "normalOnly": not foil_only,
        "setName": set_name,
        "setCode": set_code,
        "rarityName": rarity,
        "customAttributes": {"description": "• 30 Play Boosters" if sealed else ""},
        "listings": [
            {
                "listingId": pid * 100 + 1,
                "sellerKey": seller_key,
                "sellerName": seller_name,
                "sellerRating": 99.8,
                "sellerSales": "10000+",
                "directSeller": False,
                "goldSeller": True,
                "verifiedSeller": True,
                "sellerPrograms": ["CertifiedHobbyShop"],
                "price": price,
                "shippingPrice": shipping,
                "condition": condition,
                "printing": printing,
                "quantity": 2,
                "language": "English",
                "languageAbbreviation": "EN",
            }
        ],
    }


@pytest.fixture
def scanner():
    clear_seller_scan_cache()
    return TCGplayerSellerScanner()


@pytest.fixture
def app_client():
    clear_seller_scan_cache()
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-scout",
        "ADMIN_EMAIL": "scout@example.com",
        "CHIMERA_API_KEY": "master-scout-key",
        "MCP_ENABLED": True,
    })
    with app.app_context():
        db.create_all()
        if not AllowedEmail.query.filter(db.func.lower(AllowedEmail.email) == "scout@example.com").first():
            db.session.add(AllowedEmail(email="scout@example.com", is_admin=True))
        if not AllowedEmail.query.filter(db.func.lower(AllowedEmail.email) == "other@example.com").first():
            db.session.add(AllowedEmail(email="other@example.com", is_admin=False))
        user = User.query.filter(db.func.lower(User.email) == "scout@example.com").first()
        if not user:
            user = User(email="scout@example.com", name="Scout User", is_admin=True, is_active=True)
            db.session.add(user)
        other = User.query.filter(db.func.lower(User.email) == "other@example.com").first()
        if not other:
            other = User(email="other@example.com", name="Other User", is_admin=False, is_active=True)
            db.session.add(other)
        db.session.flush()
        api_token = user.ensure_api_token()
        db.session.commit()
        uid = user.id
        other_id = other.id

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["user_email"] = "scout@example.com"
            sess["is_admin"] = True
        yield client, app, uid, other_id, api_token


def test_extract_and_resolve_seller_variants(scanner):
    assert scanner.extract_seller_key("https://www.tcgplayer.com/search/magic/product?seller=43D828C6&view=grid") == "43d828c6"
    assert scanner.extract_seller_key("https://shop.tcgplayer.com/sellerfeedback/3725cd29") == "3725cd29"
    assert scanner.extract_seller_key("https://www.tcgplayer.com/product/555111?seller=a1b2c3d4&Language=English") == "a1b2c3d4"
    assert scanner.extract_seller_key("43d828c6") == "43d828c6"
    assert scanner.extract_seller_key("Some Random Shop Name") is None

    def fake_post(url, payload, timeout=12.0):
        term = payload["filters"]["term"]
        lterm = payload["listingSearch"]["filters"]["term"]
        if lterm.get("sellerKey") == ["43d828c6"] and term.get("productTypeName") == ["Cards"]:
            return {
                "results": [{
                    "totalResults": 120,
                    "results": [_make_tcg_product(101, "Sol Ring", seller_key="43d828c6", seller_name="The Arcane Archivist")],
                }]
            }
        if term.get("productName"):
            return {
                "results": [{
                    "totalResults": 1,
                    "results": [_make_tcg_product(102, "Sol Ring", seller_key="43d828c6", seller_name="The Arcane Archivist")],
                }]
            }
        return {"results": [{"totalResults": 0, "results": []}]}

    with patch.object(scanner, "_post", side_effect=fake_post):
        res_url = scanner.resolve_seller("https://www.tcgplayer.com/search/magic/product?seller=43d828c6&view=grid")
        assert res_url["sellerKey"] == "43d828c6"
        assert res_url["sellerName"] == "The Arcane Archivist"
        assert res_url["resolvedBy"] == "url"
        assert res_url["certifiedShop"] is True

        res_key = scanner.resolve_seller("43d828c6")
        assert res_key["resolvedBy"] == "key"

        res_name = scanner.resolve_seller("The Arcane Archivist")
        assert res_name["sellerKey"] == "43d828c6"
        assert res_name["resolvedBy"] == "name_sample"

        with pytest.raises(SellerNotFoundError):
            scanner.resolve_seller("Tiny Unknown Store")


def test_fetch_seller_inventory_pagination_cap_bands_and_timeout(scanner):
    # 1. Price band splitting when totalResults > 9500 & deduplication
    calls = []

    def fake_post_banded(url, payload, timeout=12.0):
        calls.append(payload)
        rng = payload["filters"].get("range", {}).get("marketPrice") or {}
        offset = payload["from"]
        gte = rng.get("gte")
        lte = rng.get("lte")
        if gte == 1.0 and lte is None and offset == 0:
            # Initial probe says > 9500 items in store
            return {
                "results": [{
                    "totalResults": 12000,
                    "results": [_make_tcg_product(1, "Placeholder")],
                }]
            }
        if gte == 100.0 and lte is None:
            return {
                "results": [{
                    "totalResults": 2,
                    "results": [
                        _make_tcg_product(201, "Mana Crypt", market_price=150.0, price=120.0),
                        _make_tcg_product(202, "Force of Will", market_price=110.0, price=90.0),
                    ],
                }]
            }
        if gte == 20.0 and lte == 99.99:
            return {
                "results": [{
                    "totalResults": 2,
                    "results": [
                        # Duplicate productId 202 should be deduplicated
                        _make_tcg_product(202, "Force of Will", market_price=110.0, price=90.0),
                        _make_tcg_product(203, "Rhystic Study", market_price=45.0, price=35.0),
                    ],
                }]
            }
        return {"results": [{"totalResults": 0, "results": []}]}

    with patch.object(scanner, "_post", side_effect=fake_post_banded):
        inv = scanner.fetch_seller_inventory("43d828c6", ["Cards"], min_market=1.0, max_products=10, use_cache=False)
        pids = [r["productId"] for r in inv["rows"]]
        assert pids == [201, 202, 203]
        assert inv["totalInStore"] == 12000
        assert inv["partial"] is False

    # 2. max_products cap triggers partial=True
    def fake_post_many(url, payload, timeout=12.0):
        items = [_make_tcg_product(1000 + i, f"Card {i}", market_price=5.0, price=4.0) for i in range(50)]
        return {"results": [{"totalResults": 150, "results": items}]}

    with patch.object(scanner, "_post", side_effect=fake_post_many):
        inv_capped = scanner.fetch_seller_inventory("43d828c6", ["Cards"], max_products=50, use_cache=False)
        assert inv_capped["productsFetched"] == 50
        assert inv_capped["partial"] is True

    # 3. Time budget exceeded triggers partial=True
    with patch("providers.tcgplayer_seller_scan.time.monotonic", side_effect=[100.0, 200.0]):
        inv_timeout = scanner.fetch_seller_inventory("43d828c6", ["Cards"], time_budget_s=10.0, use_cache=False)
        assert inv_timeout["partial"] is True
        assert inv_timeout["rows"] == []


def test_score_value_listing_thresholds_and_guards(scanner):
    # NM 12% off -> eligible Good Value
    nm_good = scanner.score_value_listing({"marketPrice": 10.0, "price": 8.80, "condition": "Near Mint", "printing": "Normal", "foilOnly": False})
    assert nm_good["eligible"] is True
    assert nm_good["discountPct"] == 12.0
    assert nm_good["valueLabel"] == "Good Value"
    assert nm_good["verifyFlag"] is False

    # LP 12% off -> requires 15% (extra 5% for LP), so ineligible
    lp_fail = scanner.score_value_listing({"marketPrice": 10.0, "price": 8.80, "condition": "Lightly Played", "printing": "Normal", "foilOnly": False})
    assert lp_fail["eligible"] is False

    # LP 16% off -> eligible
    lp_pass = scanner.score_value_listing({"marketPrice": 10.0, "price": 8.40, "condition": "Lightly Played", "printing": "Normal", "foilOnly": False})
    assert lp_pass["eligible"] is True

    # Non-foilOnly Foil listing -> foilUnverified=True & ineligible for pure value
    foil_non_only = scanner.score_value_listing({"marketPrice": 20.0, "price": 12.0, "condition": "Near Mint", "printing": "Foil", "foilOnly": False})
    assert foil_non_only["eligible"] is False
    assert foil_non_only["foilUnverified"] is True

    # foilOnly Foil listing -> eligible!
    foil_only_ok = scanner.score_value_listing({"marketPrice": 20.0, "price": 15.0, "condition": "Near Mint", "printing": "Foil", "foilOnly": True})
    assert foil_only_ok["eligible"] is True
    assert foil_only_ok["foilUnverified"] is False
    assert foil_only_ok["valueLabel"] == "Great Value"

    # < 50% of market -> Steal + verifyFlag
    steal = scanner.score_value_listing({"marketPrice": 20.0, "price": 9.50, "condition": "Near Mint", "printing": "Normal", "foilOnly": False})
    assert steal["eligible"] is True
    assert steal["valueLabel"] == "Steal"
    assert steal["verifyFlag"] is True

    # Non-playable Art Card / Token excluded
    art_card = scanner.score_value_listing({"productName": "Sol Ring Art Card", "marketPrice": 5.0, "price": 1.0, "condition": "Near Mint", "printing": "Normal", "foilOnly": False})
    assert art_card["eligible"] is False


def test_scan_seller_value_buckets_owned_and_variants(scanner):
    watchlist = [
        _DummyWatchlistItem(1, "Rhystic Study", target_price=40.0, market_price=45.0),
        _DummyWatchlistItem(2, "Dockside Box", target_price=110.0, market_price=120.0, tcgplayer_product_id=706138),
    ]
    owned_map = {
        "rhystic study": 1,      # Owned, yet Watchlist keeps it with Own badge!
        "smothering tithe": 2,   # Owned -> excluded from deckUpgrades & valuePicks
    }
    deck_upgrades = {
        "raise the palisade": [{"deckId": 10, "deckName": "Elves Tribal", "synergy": 0.62, "refPrice": 15.0}],
        "smothering tithe": [{"deckId": 10, "deckName": "Elves Tribal", "synergy": 0.70, "refPrice": 25.0}],
    }

    card_products = [
        _make_tcg_product(11, "Rhystic Study", market_price=45.0, price=38.0),
        _make_tcg_product(12, "Raise the Palisade (Extended Art)", market_price=16.0, price=13.0),
        _make_tcg_product(13, "Smothering Tithe", market_price=25.0, price=18.0),
        _make_tcg_product(14, "Cyclonic Rift", market_price=35.0, price=24.50),
        _make_tcg_product(15, "Mystic Remora Art Card", market_price=3.0, price=1.0, rarity="Art Card"),
    ]
    sealed_products = [
        _make_tcg_product(
            706138,
            "Dockside Box",
            market_price=120.0,
            price=105.0,
            shipping=0.0,
            condition="Unopened",
            product_type="Booster Box",
            sealed=True,
        ),
        _make_tcg_product(
            706999,
            "Innistrad Remastered - Play Booster Display",
            market_price=140.0,
            price=125.0,
            shipping=0.0,
            condition="Unopened",
            product_type="Booster Box",
            sealed=True,
        ),
        _make_tcg_product(
            707000,
            "Innistrad Remastered - Collector Booster Display Case",
            market_price=1600.0,
            price=1350.0,
            shipping=0.0,
            condition="Unopened",
            product_type="Magic Booster Box Case",
            sealed=True,
        ),
    ]

    def fake_post(url, payload, timeout=12.0):
        ptypes = payload["filters"]["term"]["productTypeName"]
        if ptypes == ["Cards"]:
            return {"results": [{"totalResults": len(card_products), "results": card_products}]}
        return {"results": [{"totalResults": len(sealed_products), "results": sealed_products}]}

    with patch.object(scanner, "_post", side_effect=fake_post):
        res = scanner.scan_seller_value(
            "https://www.tcgplayer.com/search/magic/product?seller=43d828c6",
            watchlist_items=watchlist,
            owned_map=owned_map,
            deck_upgrades=deck_upgrades,
            options={"use_cache": False},
        )

    assert res["success"] is True
    assert len(res["watchlistHits"]) == 1
    wl_hit = res["watchlistHits"][0]
    assert wl_hit["cleanName"] == "Rhystic Study"
    assert wl_hit["inBinder"] is True
    assert wl_hit["ownedCount"] == 1

    assert len(res["deckUpgrades"]) == 1
    du_hit = res["deckUpgrades"][0]
    assert du_hit["productName"] == "Raise the Palisade (Extended Art)"
    assert du_hit["decks"][0]["deckName"] == "Elves Tribal"

    # Smothering Tithe was owned and Mystic Remora Art Card is non-playable -> only Cyclonic Rift remains
    assert [v["cleanName"] for v in res["valuePicks"]] == ["Cyclonic Rift"]
    assert res["summary"]["ownedExcludedCount"] >= 1

    # Both watched sealed and below-market booster display appear; non-watched Master Case is excluded by default
    assert len(res["sealedPicks"]) == 2
    assert res["sealedPicks"][0]["isWatchedSealed"] is True
    assert all(p["productId"] != 707000 for p in res["sealedPicks"])

    # Mass Entry includes recommended singles (Rhystic Study + Raise the Palisade)
    assert "1 Rhystic Study" in res["cartSummary"]["massEntryText"]
    assert "1 Raise the Palisade" in res["cartSummary"]["massEntryText"]


def test_find_combined_carts_1_plus_1_and_shipping_math(scanner):
    wl_items = [
        _DummyWatchlistItem(1, "Foundations Play Booster Box", target_price=115.0, market_price=125.0, tcgplayer_product_id=8001),
        _DummyWatchlistItem(2, "Sol Ring", target_price=2.0, market_price=2.0),
        _DummyWatchlistItem(3, "Arcane Signet", target_price=1.5, market_price=1.5),
    ]

    # Seller A: 1 sealed + 1 single (qualifies under 1+1 default)
    # Seller B: 1 sealed + 2 singles (qualifies, ranks higher)
    # Seller C: 1 sealed + 0 singles (rejected)
    sealed_listings = [
        {"sellerKey": "seller_a", "sellerName": "Alpha Games", "price": 110.0, "shippingPrice": 0.0, "landedPrice": 110.0, "sellerRating": 99.5, "sellerSales": "5000+"},
        {"sellerKey": "seller_b", "sellerName": "Beta Shop", "price": 108.0, "shippingPrice": 0.0, "landedPrice": 108.0, "sellerRating": 100.0, "sellerSales": "10000+"},
        {"sellerKey": "seller_c", "sellerName": "Gamma Box Only", "price": 109.0, "shippingPrice": 0.0, "landedPrice": 109.0, "sellerRating": 98.0, "sellerSales": "500+"},
    ]

    def fake_post(url, payload, timeout=12.0):
        skey = payload["listingSearch"]["filters"]["term"]["sellerKey"][0]
        return {
            "results": [{
                "totalResults": 1,
                "results": [
                    _make_tcg_product(
                        8001,
                        "Foundations Play Booster Box",
                        market_price=125.0,
                        price=110.0 if skey == "seller_a" else (108.0 if skey == "seller_b" else 109.0),
                        shipping=0.0,
                        condition="Unopened",
                        product_type="Booster Box",
                        sealed=True,
                        seller_key=skey,
                        seller_name=skey,
                    )
                ],
            }]
        }

    def fake_singles_inv(skey, names, english_only=True):
        if skey == "seller_a":
            return [_make_tcg_product(501, "Sol Ring", market_price=2.0, price=1.80, shipping=1.49, seller_key=skey)]
        if skey == "seller_b":
            return [
                _make_tcg_product(501, "Sol Ring", market_price=2.0, price=1.70, shipping=1.49, seller_key=skey),
                _make_tcg_product(502, "Arcane Signet", market_price=1.50, price=1.20, shipping=1.49, seller_key=skey),
            ]
        return []

    with patch.object(scanner.sealed_provider, "get_product_live_listings", return_value=sealed_listings), \
         patch.object(scanner.lot_provider, "_discover_sellers_for_watchlist", return_value=[]), \
         patch.object(scanner.lot_provider, "_query_seller_watchlist_inventory", side_effect=fake_singles_inv), \
         patch.object(scanner, "_post", side_effect=fake_post):
        res = scanner.find_combined_carts(wl_items)

    assert res["success"] is True
    assert res["minSealed"] == 1
    assert res["minSingles"] == 1
    assert res["totalCartsFound"] == 2
    # Beta Shop ranks first (more savings + more items)
    assert res["carts"][0]["sellerKey"] == "seller_b"
    assert res["carts"][0]["singlesCount"] == 2
    assert res["carts"][0]["estShipping"] == 0.0  # Waived under $5 combined singles rule + $0 sealed ship
    assert res["carts"][0]["shippingSaved"] == 2.98  # Saved two $1.49 single shipping charges
    assert res["carts"][1]["sellerKey"] == "seller_a"
    assert res["carts"][1]["singlesCount"] == 1


def test_flask_routes_ssr_saved_sellers_and_mcp_tools(app_client):
    client, app, uid, other_id, api_token = app_client

    # Seed inventory, watchlist, and cached deck upgrade
    with app.app_context():
        db.session.add(UserInventoryCard(user_id=uid, name="Sol Ring", quantity=2))
        db.session.add(WatchlistItem(user_id=uid, name="Rhystic Study", target_price=40.0))
        db.session.add(WatchlistItem(user_id=uid, name="Foundations Play Booster Box", target_price=115.0, tcgplayer_product_id=8001))
        db.session.add(
            DeckAnalysis(
                user_id=uid,
                deck_name="Atraxa Superfriends",
                commander_name="Atraxa, Praetors' Voice",
                raw_decklist="1 Atraxa, Praetors' Voice",
                upgrades_json=json.dumps({
                    "all_shopping_cards": [
                        {"name": "Doubling Season", "price_usd": 42.0, "synergy": 0.65},
                        {"name": "Sol Ring", "price_usd": 2.0, "synergy": 0.80},
                    ]
                }),
            )
        )
        db.session.commit()

    # 1. Unauthenticated access redirected
    anon = app.test_client()
    assert anon.post("/api/tcgplayer/seller/value-scan", json={"seller": "43d828c6"}).status_code in (302, 401)

    # 2. SSR /deals renders Seller Scout tab, balanced container divs, and deferred js/seller_scout.js
    r_deals = client.get("/deals")
    assert r_deals.status_code == 200
    html = r_deals.get_data(as_text=True)
    assert 'id="deals-seller-scout-container"' in html
    assert 'js/seller_scout.js' in html
    parser = _DivBalanceParser("deals-seller-scout-container")
    parser.feed(html)
    assert parser.found and parser.closed_cleanly

    # 3. Saved sellers CRUD + per-user isolation
    mock_products = [
        _make_tcg_product(11, "Rhystic Study", market_price=45.0, price=37.0),
        _make_tcg_product(22, "Doubling Season", market_price=42.0, price=36.0),
        _make_tcg_product(33, "Sol Ring", market_price=2.0, price=1.50),
        _make_tcg_product(44, "Fierce Guardianship", market_price=40.0, price=31.0),
    ]

    def fake_post(url, payload, timeout=12.0):
        ptypes = payload["filters"]["term"]["productTypeName"]
        if ptypes == ["Cards"]:
            return {"results": [{"totalResults": len(mock_products), "results": mock_products}]}
        return {"results": [{"totalResults": 0, "results": []}]}

    scanner_inst = app.tcgplayer_seller_scanner
    with patch.object(scanner_inst, "_post", side_effect=fake_post):
        save_resp = client.post(
            "/api/tcgplayer/saved-sellers",
            json={"seller": "https://www.tcgplayer.com/search/magic/product?seller=43d828c6", "note": "BF Pick"},
        )
        assert save_resp.status_code == 200
        saved_id = save_resp.get_json()["seller"]["id"]

        # Value scan updates last_scanned_at on saved seller
        scan_resp = client.post(
            "/api/tcgplayer/seller/value-scan",
            json={"seller": "43d828c6", "min_discount_pct": 10},
        )
        assert scan_resp.status_code == 200
        scan_data = scan_resp.get_json()
        assert scan_data["summary"]["watchlistCount"] == 1
        assert scan_data["summary"]["deckUpgradesCount"] == 1  # Sol Ring excluded as owned
        assert scan_data["summary"]["valuePicksCount"] == 1    # Fierce Guardianship
        assert scan_data["savedSeller"]["lastScannedAt"] is not None

    # Other user cannot delete first user's saved seller
    with client.session_transaction() as sess:
        sess["user_id"] = other_id
        sess["user_email"] = "other@example.com"
        sess["is_admin"] = False
    assert client.delete(f"/api/tcgplayer/saved-sellers/{saved_id}").status_code == 404

    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["user_email"] = "scout@example.com"
        sess["is_admin"] = True

    del_resp = client.delete(f"/api/tcgplayer/saved-sellers/{saved_id}")
    assert del_resp.status_code == 200
    assert del_resp.get_json()["sellers"] == []

    # 4. MCP tools/list & tools/call verification
    list_resp = client.post(
        f"/mcp?token={api_token}",
        json={"jsonrpc": "2.0", "id": "list-scout", "method": "tools/list"},
    )
    tool_names = {t["name"] for t in list_resp.get_json()["result"]["tools"]}
    assert "scan_tcgplayer_seller" in tool_names
    assert "find_tcgplayer_combined_carts" in tool_names

    with patch.object(scanner_inst, "_post", side_effect=fake_post):
        mcp_scan = client.post(
            f"/mcp?token={api_token}",
            json={
                "jsonrpc": "2.0",
                "id": "call-scout",
                "method": "tools/call",
                "params": {"name": "scan_tcgplayer_seller", "arguments": {"seller": "43d828c6"}},
            },
        )
        assert mcp_scan.status_code == 200
        mcp_res = mcp_scan.get_json()["result"]
        assert not mcp_res.get("isError", False)
        mcp_payload = json.loads(mcp_res["content"][0]["text"])
        assert mcp_payload["summary"]["watchlistCount"] == 1
        assert mcp_payload["summary"]["deckUpgradesCount"] == 1

    # 5. Combined Carts Flask route & MCP tool
    sealed_listings = [
        {"sellerKey": "43d828c6", "sellerName": "Test LGS", "price": 109.0, "shippingPrice": 0.0, "landedPrice": 109.0, "sellerRating": 99.8, "sellerSales": "10000+"},
    ]
    sealed_prod = _make_tcg_product(
        8001,
        "Foundations Play Booster Box",
        market_price=125.0,
        price=109.0,
        shipping=0.0,
        condition="Unopened",
        product_type="Booster Box",
        sealed=True,
    )
    single_prod = _make_tcg_product(11, "Rhystic Study", market_price=45.0, price=37.0)

    with patch.object(scanner_inst.sealed_provider, "get_product_live_listings", return_value=sealed_listings), \
         patch.object(scanner_inst.lot_provider, "_discover_sellers_for_watchlist", return_value=[]), \
         patch.object(scanner_inst.lot_provider, "_query_seller_watchlist_inventory", return_value=[single_prod]), \
         patch.object(scanner_inst, "_post", return_value={"results": [{"totalResults": 1, "results": [sealed_prod]}]}):
        comb_resp = client.post("/api/tcgplayer/combined-carts", json={"min_sealed": 1, "min_singles": 1})
        assert comb_resp.status_code == 200
        comb_data = comb_resp.get_json()
        assert comb_data["totalCartsFound"] == 1
        assert comb_data["carts"][0]["sellerKey"] == "43d828c6"

        mcp_comb = client.post(
            f"/mcp?token={api_token}",
            json={
                "jsonrpc": "2.0",
                "id": "call-comb",
                "method": "tools/call",
                "params": {"name": "find_tcgplayer_combined_carts", "arguments": {"min_sealed": 1, "min_singles": 1}},
            },
        )
        assert mcp_comb.status_code == 200
        mcp_comb_res = mcp_comb.get_json()["result"]
        assert not mcp_comb_res.get("isError", False)
        assert json.loads(mcp_comb_res["content"][0]["text"])["totalCartsFound"] == 1
