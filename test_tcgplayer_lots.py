import pytest
from unittest.mock import MagicMock, patch
from providers.tcgplayer_lots import TCGplayerLotProvider


class DummyWatchlistItem:
    def __init__(self, id, name, target_price=None, market_price=None, lowest_in_stock_price=None, tag=None, set_code="ANY"):
        self.id = id
        self.name = name
        self.target_price = target_price
        self.market_price = market_price
        self.lowest_in_stock_price = lowest_in_stock_price
        self.tag = tag
        self.set_code = set_code
        self.image_uri = f"https://c1.scryfall.com/{name}.jpg"
        self.in_binder = False
        self.binder_count = 0


@pytest.fixture
def lot_provider():
    return TCGplayerLotProvider()


def test_evaluate_listing_decent_price_decent_10(lot_provider):
    # Case 1: At or below target price qualifies regardless of market
    qualifies, label, delta = lot_provider.evaluate_listing_decent_price(
        listing_price=10.0, market_price=15.0, target_price=12.0, tolerance_mode="decent_10"
    )
    assert qualifies is True
    assert "Target Price" in label

    # Case 2: Within 10% of market when target is None
    qualifies, label, delta = lot_provider.evaluate_listing_decent_price(
        listing_price=10.50, market_price=10.0, target_price=None, tolerance_mode="decent_10"
    )
    assert qualifies is True
    assert delta == 5.0

    # Case 3: More than 10% above market and target is lower
    qualifies, label, delta = lot_provider.evaluate_listing_decent_price(
        listing_price=15.0, market_price=10.0, target_price=8.0, tolerance_mode="decent_10"
    )
    assert qualifies is False
    assert delta == 50.0

    # Case 4: Overpriced placeholder (e.g. $19.99 for a $0.50 card)
    qualifies, label, delta = lot_provider.evaluate_listing_decent_price(
        listing_price=19.99, market_price=0.50, target_price=0.60, tolerance_mode="decent_10"
    )
    assert qualifies is False


def test_evaluate_listing_fancy_treatment_vs_baseline_cheapest_market(lot_provider):
    """
    Verifies that a fancy/special edition (e.g. Masterpiece, Serialized, Showcase)
    listed at $15.00 on a $20.00 edition does NOT qualify as a deal when the
    regular baseline version of the card is $1.50.
    """
    # Case 1: Fancy version listed at $15 (market of that edition is $20), but base card is $1.50
    qualifies, label, delta = lot_provider.evaluate_listing_decent_price(
        listing_price=15.00,
        market_price=20.00,
        target_price=2.00,
        tolerance_mode="decent_10",
        baseline_cheapest_market=1.50,
    )
    assert qualifies is False
    assert delta == 900.0  # +900% compared to baseline market price of $1.50

    # Case 2: Regular version listed at $1.40 on a $1.50 base card
    qualifies, label, delta = lot_provider.evaluate_listing_decent_price(
        listing_price=1.40,
        market_price=1.50,
        target_price=2.00,
        tolerance_mode="decent_10",
        baseline_cheapest_market=1.50,
    )
    assert qualifies is True
    assert delta == -6.7  # -6.7% below baseline market price


def test_evaluate_listing_decent_price_strict_market(lot_provider):
    # Exactly market price
    qualifies, _, _ = lot_provider.evaluate_listing_decent_price(
        listing_price=10.0, market_price=10.0, target_price=None, tolerance_mode="market_0"
    )
    assert qualifies is True

    # 1 cent over market
    qualifies, _, _ = lot_provider.evaluate_listing_decent_price(
        listing_price=10.01, market_price=10.0, target_price=None, tolerance_mode="market_0"
    )
    assert qualifies is False


def test_evaluate_listing_decent_price_great_deal(lot_provider):
    # 15% below market
    qualifies, label, delta = lot_provider.evaluate_listing_decent_price(
        listing_price=8.50, market_price=10.0, target_price=None, tolerance_mode="great_minus_10"
    )
    assert qualifies is True
    assert delta == -15.0

    # 5% below market does not qualify for great deal
    qualifies, _, _ = lot_provider.evaluate_listing_decent_price(
        listing_price=9.50, market_price=10.0, target_price=None, tolerance_mode="great_minus_10"
    )
    assert qualifies is False


def test_evaluate_listing_decent_price_target_only(lot_provider):
    qualifies, _, _ = lot_provider.evaluate_listing_decent_price(
        listing_price=5.00, market_price=10.0, target_price=5.00, tolerance_mode="target_only"
    )
    assert qualifies is True

    qualifies, _, _ = lot_provider.evaluate_listing_decent_price(
        listing_price=5.01, market_price=10.0, target_price=5.00, tolerance_mode="target_only"
    )
    assert qualifies is False


def test_optimize_dollar_bundle(lot_provider):
    cards = [
        {"name": "Card A", "price": 0.50},
        {"name": "Card B", "price": 1.00},
        {"name": "Card C", "price": 2.00},
        {"name": "Card D", "price": 3.00},
    ]

    # Test 1: min_threshold (e.g. $5 free shipping threshold)
    selected, total = lot_provider._optimize_dollar_bundle(cards, target_amount=3.00, bundle_type="min_threshold")
    assert total >= 3.00
    assert len(selected) == 3  # 0.50 + 1.00 + 2.00 = 3.50

    # Test 2: max_budget (e.g. max spend $2.00)
    selected, total = lot_provider._optimize_dollar_bundle(cards, target_amount=2.00, bundle_type="max_budget")
    assert total <= 2.00
    assert len(selected) == 2  # 0.50 + 1.00 = 1.50


def test_find_seller_lots_with_mocked_tcg_api(lot_provider):
    watchlist = [
        DummyWatchlistItem(1, "Sol Ring", target_price=2.00, market_price=1.80),
        DummyWatchlistItem(2, "Arcane Signet", target_price=1.00, market_price=0.80),
        DummyWatchlistItem(3, "Counterspell", target_price=1.50, market_price=1.20),
        DummyWatchlistItem(4, "Swords to Plowshares", target_price=2.00, market_price=1.50),
        DummyWatchlistItem(5, "Cultivate", target_price=0.50, market_price=0.40),
        DummyWatchlistItem(6, "Path to Exile", target_price=2.00, market_price=1.50),
    ]

    mock_search_products = [
        # Fancy Sol Ring Masterpiece ($15) AND Regular Sol Ring ($1.70)
        {
            "productName": "Sol Ring",
            "setName": "Masterpiece Series: Kaladesh Inventions",
            "productId": 999,
            "marketPrice": 650.00,
            "listings": [
                {
                    "sellerKey": "seller_abc",
                    "sellerName": "Great Card Shop",
                    "sellerRating": 99.5,
                    "sellerSales": "10000+",
                    "price": 500.00,
                    "shippingPrice": 1.49,
                    "condition": "Near Mint",
                    "printing": "Foil",
                    "quantity": 1,
                }
            ],
        },
        {
            "productName": "Sol Ring",
            "setName": "Fallout",
            "productId": 101,
            "marketPrice": 1.80,
            "listings": [
                {
                    "sellerKey": "seller_abc",
                    "sellerName": "Great Card Shop",
                    "sellerRating": 99.5,
                    "sellerSales": "10000+",
                    "price": 1.70,
                    "shippingPrice": 1.49,
                    "condition": "Near Mint",
                    "printing": "Normal",
                    "quantity": 4,
                }
            ],
        },
        {
            "productName": "Arcane Signet",
            "setName": "Commander",
            "productId": 102,
            "marketPrice": 0.80,
            "listings": [
                {"sellerKey": "seller_abc", "price": 0.75, "shippingPrice": 1.49, "condition": "Near Mint", "printing": "Normal", "quantity": 2}
            ],
        },
        {
            "productName": "Counterspell",
            "setName": "Dominaria",
            "productId": 103,
            "marketPrice": 1.20,
            "listings": [
                {"sellerKey": "seller_abc", "price": 1.10, "shippingPrice": 1.49, "condition": "Near Mint", "printing": "Normal", "quantity": 1}
            ],
        },
        {
            "productName": "Swords to Plowshares",
            "setName": "Murders",
            "productId": 104,
            "marketPrice": 1.50,
            "listings": [
                {"sellerKey": "seller_abc", "price": 1.40, "shippingPrice": 1.49, "condition": "Near Mint", "printing": "Normal", "quantity": 3}
            ],
        },
        {
            "productName": "Cultivate",
            "setName": "M21",
            "productId": 105,
            "marketPrice": 0.40,
            "listings": [
                {"sellerKey": "seller_abc", "price": 0.35, "shippingPrice": 1.49, "condition": "Near Mint", "printing": "Normal", "quantity": 5}
            ],
        },
    ]

    with patch.object(lot_provider, "_discover_sellers_for_watchlist") as mock_disc, \
         patch.object(lot_provider, "_query_seller_watchlist_inventory") as mock_inv:
        
        mock_disc.return_value = [
            {
                "sellerKey": "seller_abc",
                "sellerName": "Great Card Shop",
                "sellerRating": 99.5,
                "sellerSales": "10000+",
                "directSeller": True,
                "goldSeller": True,
            }
        ]
        mock_inv.return_value = mock_search_products

        res = lot_provider.find_seller_lots(
            watchlist_items=watchlist,
            mode="lots",
            min_cards=5,
            price_tolerance_mode="decent_10",
        )

        assert res["success"] is True
        assert res["total_sellers_found"] == 1
        seller = res["sellers"][0]
        assert seller["sellerName"] == "Great Card Shop"
        assert seller["qualifyingCount"] == 5
        # Sol Ring chosen must be the regular $1.70 version, NOT the $500 Masterpiece!
        assert seller["subtotal"] == round(1.70 + 0.75 + 1.10 + 1.40 + 0.35, 2)
        assert seller["directSeller"] is True
        assert len(seller["cards"]) == 5

        # Verify Store URL and Multi-Card Search URL
        assert seller["storeUrl"] == "https://www.tcgplayer.com/search/magic/product?seller=seller_abc&view=grid"
        assert "seller=seller_abc" in seller["multiCardSearchUrl"]
        assert "productName=Sol+Ring" in seller["multiCardSearchUrl"]
        assert "productName=Arcane+Signet" in seller["multiCardSearchUrl"]

        # Verify card entries have storeSearchUrl
        sol_card = [c for c in seller["cards"] if c["name"] == "Sol Ring"][0]
        assert sol_card["price"] == 1.70
        assert "seller=seller_abc" in sol_card["storeSearchUrl"]
        assert "q=Sol+Ring" in sol_card["storeSearchUrl"]


def test_api_tcgplayer_lots_endpoint(lot_provider):
    from app import app
    from models import db, User

    with app.test_client() as client:
        # Test unauthenticated access redirects or returns 302/401
        res = client.post("/api/deals/tcgplayer-lots", json={})
        assert res.status_code in (302, 401)

        # Authenticate via test session
        with app.app_context():
            user = User.query.first()
            if not user:
                user = User(email="test.lot.user@chimera.local", name="Lot Tester")
                db.session.add(user)
                db.session.commit()

        with client.session_transaction() as sess:
            sess["user_id"] = user.id
            sess["user_email"] = user.email

        with patch("providers.tcgplayer_lots.TCGplayerLotProvider.find_seller_lots") as mock_find:
            mock_find.return_value = {
                "success": True,
                "sellers": [],
                "total_sellers_found": 0,
                "scanned_cards_count": 10,
                "duration_seconds": 0.42,
            }

            resp = client.post(
                "/api/deals/tcgplayer-lots",
                json={
                    "mode": "lots",
                    "min_cards": 5,
                    "price_tolerance_mode": "decent_10",
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert data["total_sellers_found"] == 0
            assert mock_find.called
