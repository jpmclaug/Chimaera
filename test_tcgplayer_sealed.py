import pytest
from unittest.mock import patch

from app import create_app
from config import Config
from models import db, User, AllowedEmail, WatchlistItem, VendorPrice
from providers.tcgplayer_sealed import (
    TCGplayerSealedProvider,
    classify_sealed_product,
    extract_pack_count,
    evaluate_sealed_deal,
)


class SealedTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-secret-sealed"


@pytest.fixture
def sealed_provider():
    TCGplayerSealedProvider._sets_cache = None
    TCGplayerSealedProvider._scryfall_sets_cache = None
    return TCGplayerSealedProvider()


@pytest.fixture
def client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-sealed",
    })
    with app.app_context():
        db.create_all()
        user = User(email="tester@example.com", name="Tester", is_admin=True, is_active=True)
        db.session.add(user)
        db.session.add(AllowedEmail(email="tester@example.com", is_admin=True))
        db.session.commit()
        user_id = user.id

    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["user_email"] = "tester@example.com"
        yield test_client, app


def test_classify_sealed_products_and_pack_counts():
    cat, _, subtype = classify_sealed_product("Duskmourn: House of Horror - Play Booster Display")
    assert cat == "booster_box"
    assert subtype == "Play"
    assert extract_pack_count("Duskmourn: House of Horror - Play Booster Display", "• 36 Magic: The Gathering Play Boosters", cat, subtype) == 36

    cat, _, subtype = classify_sealed_product("Duskmourn: House of Horror - Collector Booster Display")
    assert cat == "booster_box"
    assert subtype == "Collector"
    assert extract_pack_count("Duskmourn: House of Horror - Collector Booster Display", "", cat, subtype) == 12

    cat, _, subtype = classify_sealed_product("Duskmourn: House of Horror - Sleeved Play Booster Pack")
    assert cat == "booster_pack"
    assert subtype == "Play"
    assert extract_pack_count("Duskmourn: House of Horror - Sleeved Play Booster Pack", "", cat, subtype) == 1

    cat, _, subtype = classify_sealed_product("Duskmourn: House of Horror - Nightmare Bundle")
    assert cat == "bundle"

    cat, _, subtype = classify_sealed_product("Khans of Tarkir - Fat Pack")
    assert cat == "bundle"

    cat, _, subtype = classify_sealed_product("Duskmourn: House of Horror - Prerelease Pack")
    assert cat == "prerelease_starter"
    assert extract_pack_count("Duskmourn: House of Horror - Prerelease Pack", "", cat, subtype) == 6

    cat, _, subtype = classify_sealed_product("Foundations - Beginner Box")
    assert cat == "prerelease_starter"

    cat, _, subtype = classify_sealed_product("Duskmourn: House of Horror - Play Booster Display Case")
    assert cat == "case_other"


def test_evaluate_sealed_deal_modes():
    # Great deal: 12% below market
    ev = evaluate_sealed_deal(landed_price=176.00, market_price=200.00, target_price=None, tolerance_mode="great_minus_10")
    assert ev["isDeal"] is True
    assert ev["priceRating"] == "Great Deal"
    assert ev["savingsVsMarket"] == 24.0
    assert ev["savingsVsMarketPct"] == 12.0
    assert ev["deltaVsMarketPct"] == -12.0

    # Good deal: 6% below market fails great_minus_10, passes good_minus_5
    ev_great = evaluate_sealed_deal(landed_price=188.00, market_price=200.00, target_price=None, tolerance_mode="great_minus_10")
    assert ev_great["isDeal"] is False
    ev_good = evaluate_sealed_deal(landed_price=188.00, market_price=200.00, target_price=None, tolerance_mode="good_minus_5")
    assert ev_good["isDeal"] is True
    assert ev_good["priceRating"] == "Good Deal"

    # Target price override
    ev_target = evaluate_sealed_deal(landed_price=205.00, market_price=200.00, target_price=210.00, tolerance_mode="target_only")
    assert ev_target["isDeal"] is True
    assert ev_target["meetsTarget"] is True
    assert ev_target["savingsVsTarget"] == 5.0


def test_scan_sealed_deals_and_multi_seller_overlap(sealed_provider):
    mock_listings_box = [
        {
            "listingId": 1,
            "sellerKey": "shop_alpha",
            "sellerName": "Alpha Games",
            "sellerRating": 100.0,
            "sellerSales": "5000+",
            "directSeller": False,
            "goldSeller": True,
            "verifiedSeller": True,
            "condition": "Unopened",
            "language": "English",
            "languageAbbreviation": "EN",
            "quantity": 4,
            "price": 180.00,
            "shippingPrice": 5.00,
            "landedPrice": 185.00,
            "buyUrl": "https://www.tcgplayer.com/product/557241?seller=shop_alpha&Language=English&page=1",
        },
        {
            "listingId": 2,
            "sellerKey": "shop_beta",
            "sellerName": "Beta Cards",
            "sellerRating": 99.5,
            "sellerSales": "1200",
            "directSeller": True,
            "goldSeller": False,
            "verifiedSeller": False,
            "condition": "Unopened",
            "language": "English",
            "languageAbbreviation": "EN",
            "quantity": 2,
            "price": 195.00,
            "shippingPrice": 0.00,
            "landedPrice": 195.00,
            "buyUrl": "https://www.tcgplayer.com/product/557241?seller=shop_beta&Language=English&page=1",
        },
    ]
    mock_listings_pack = [
        {
            "listingId": 3,
            "sellerKey": "shop_alpha",
            "sellerName": "Alpha Games",
            "sellerRating": 100.0,
            "sellerSales": "5000+",
            "directSeller": False,
            "goldSeller": True,
            "verifiedSeller": True,
            "condition": "Unopened",
            "language": "English",
            "languageAbbreviation": "EN",
            "quantity": 12,
            "price": 5.50,
            "shippingPrice": 1.00,
            "landedPrice": 6.50,
            "buyUrl": "https://www.tcgplayer.com/product/557240?seller=shop_alpha&Language=English&page=1",
        },
    ]

    def fake_live_listings(product_id, english_only=True, size=20):
        return mock_listings_box if int(product_id) == 557241 else mock_listings_pack

    with patch.object(sealed_provider, "get_product_live_listings", side_effect=fake_live_listings):
        res = sealed_provider.scan_sealed_deals(
            selected_products=[
                {
                    "productId": 557241,
                    "productName": "Duskmourn: House of Horror - Play Booster Display",
                    "setName": "Duskmourn: House of Horror",
                    "setCode": "DSK",
                    "category": "booster_box",
                    "categoryLabel": "Booster Box",
                    "boosterSubtype": "Play",
                    "packCount": 36,
                    "marketPrice": 205.00,
                    "targetPrice": 190.00,
                },
                {
                    "productId": 557240,
                    "productName": "Duskmourn: House of Horror - Play Booster Pack",
                    "setName": "Duskmourn: House of Horror",
                    "setCode": "DSK",
                    "category": "booster_pack",
                    "categoryLabel": "Booster Pack",
                    "boosterSubtype": "Play",
                    "packCount": 1,
                    "marketPrice": 7.10,
                },
            ],
            tolerance_mode="market_0",
            english_only=True,
        )

    assert res["success"] is True
    assert res["summary"]["productsScanned"] == 2
    assert res["summary"]["dealsFound"] == 2
    assert res["summary"]["multiSellerCount"] == 1

    multi = res["multiProductSellers"][0]
    assert multi["sellerName"] == "Alpha Games"
    assert multi["productCount"] == 2
    assert multi["itemsSubtotal"] == 185.50
    assert multi["estCombinedShipping"] == 5.00
    assert multi["estCombinedTotal"] == 190.50

    box_res = next(p for p in res["products"] if p["productId"] == 557241)
    assert box_res["bestLandedPrice"] == 185.00
    assert box_res["pricePerPack"] == round(185.00 / 36, 2)
    assert box_res["meetsTarget"] is True


def test_sealed_api_endpoints_and_watchlist_polling(client):
    test_client, app = client

    # 1. Verify /deals renders the Sealed Product Deals tab & container
    resp_page = test_client.get("/deals")
    assert resp_page.status_code == 200
    html = resp_page.get_data(as_text=True)
    assert "deals-sealed-finder-container" in html
    assert "btn-deals-sec-sealed" in html

    # 2. Verify GET /api/sealed/sets
    mock_sets = [
        {"setUrlName": "duskmourn-house-of-horror", "setName": "Duskmourn: House of Horror", "setCode": "DSK", "releasedAt": "2024-09-27", "productCount": 17}
    ]
    with patch.object(app.tcgplayer_sealed_provider, "get_sealed_sets", return_value=mock_sets):
        r_sets = test_client.get("/api/sealed/sets?q=DSK")
        assert r_sets.status_code == 200
        d_sets = r_sets.get_json()
        assert d_sets["success"] is True
        assert d_sets["count"] == 1
        assert d_sets["sets"][0]["setCode"] == "DSK"

    # 3. Verify POST /api/watchlist/add-sealed & DealEngine polling
    r_add = test_client.post(
        "/api/watchlist/add-sealed",
        json={
            "products": [
                {
                    "productId": 557241,
                    "productName": "Duskmourn: House of Horror - Play Booster Display",
                    "setName": "Duskmourn: House of Horror",
                    "setCode": "DSK",
                    "categoryLabel": "Booster Box",
                    "marketPrice": 205.00,
                    "lowestLandedPrice": 188.00,
                    "targetPrice": 195.00,
                    "bestSellerName": "Alpha Games",
                }
            ],
            "tag": "Sealed",
        },
    )
    assert r_add.status_code == 201
    d_add = r_add.get_json()
    assert d_add["success"] is True
    assert d_add["addedCount"] == 1
    card_dict = d_add["added"][0]
    assert card_dict["is_sealed"] is True
    assert card_dict["tcgplayer_product_id"] == 557241
    assert card_dict["market_price"] == 205.00
    assert card_dict["lowest_price"] == 188.00
    assert card_dict["is_deal"] is True

    # 4. Verify GET /api/sealed/products annotates inWatchlist=True
    mock_prods = [
        {
            "productId": 557241,
            "productName": "Duskmourn: House of Horror - Play Booster Display",
            "setName": "Duskmourn: House of Horror",
            "setUrlName": "duskmourn-house-of-horror",
            "setCode": "DSK",
            "category": "booster_box",
            "categoryLabel": "Booster Box",
            "boosterSubtype": "Play",
            "packCount": 36,
            "imageUrl": "https://tcgplayer-cdn.tcgplayer.com/product/557241_in_400x400.jpg",
            "marketPrice": 205.00,
            "lowestLandedPrice": 188.00,
            "inStock": True,
            "totalListings": 40,
            "tcgplayerUrl": "https://www.tcgplayer.com/product/557241?Language=English",
            "deltaVsMarketPct": -8.3,
        }
    ]
    with patch.object(app.tcgplayer_sealed_provider, "get_set_sealed_products", return_value=mock_prods):
        r_prods = test_client.get("/api/sealed/products?set=duskmourn-house-of-horror")
        assert r_prods.status_code == 200
        d_prods = r_prods.get_json()
        assert d_prods["products"][0]["inWatchlist"] is True
        assert d_prods["products"][0]["watchlistTargetPrice"] == 195.00

    # 5. Verify DealEngine.poll_card routes sealed item to poll_sealed_item
    with app.app_context():
        item = WatchlistItem.query.filter_by(tcgplayer_product_id=557241).first()
        assert item is not None
        from deal_engine import DealEngine
        engine = DealEngine(app=app)
        with patch.object(
            engine.tcgplayer_sealed,
            "poll_sealed_product",
            return_value={
                "vendor_name": "TCGplayer",
                "price": 182.50,
                "market_price": 210.00,
                "condition": "Unopened (ShopX)",
                "in_stock": True,
                "product_url": "https://www.tcgplayer.com/product/557241?seller=shopx",
                "search_url": "https://www.tcgplayer.com/product/557241?Language=English",
                "image_url": "https://tcgplayer-cdn.tcgplayer.com/product/557241_in_400x400.jpg",
            },
        ):
            poll_res = engine.poll_card(item, notify=False)
            assert poll_res["is_deal"] is True
            assert poll_res["lowest_price"] == 182.50
            assert item.market_price == 210.00
            assert item.lowest_in_stock_price == 182.50
