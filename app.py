import logging
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from apscheduler.schedulers.background import BackgroundScheduler
from config import Config
from models import db, WatchlistItem, VendorPrice
from deal_engine import DealEngine
from providers import ScryfallProvider

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app():
    """Application factory for Chimaera MTG Market Tracker."""
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize Database
    db.init_app(app)

    # Initialize Deal Engine and Scryfall provider
    deal_engine = DealEngine(app=app)
    scryfall_provider = ScryfallProvider()

    with app.app_context():
        db.create_all()
        logger.info("Database initialized successfully.")

    # ---------------------------------------------------------
    # Background Scheduler Setup
    # ---------------------------------------------------------
    def scheduled_price_poll():
        with app.app_context():
            logger.info("Running scheduled MTG price check...")
            deal_engine.poll_all_cards(notify=True)

    scheduler = BackgroundScheduler(daemon=True)
    poll_hours = app.config.get("POLL_INTERVAL_HOURS", 6)
    scheduler.add_job(
        func=scheduled_price_poll,
        trigger="interval",
        hours=poll_hours,
        id="chimera_price_poll",
        replace_existing=True,
    )

    # Start scheduler only if not in werkzeug reloader secondary thread
    if not os.environ.get("WERKZEUG_RUN_MAIN") or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        try:
            scheduler.start()
            logger.info(f"Background scheduler started (polling every {poll_hours} hours).")
        except Exception as e:
            logger.warning(f"Could not start scheduler: {e}")

    # ---------------------------------------------------------
    # View Routes
    # ---------------------------------------------------------
    @app.route("/")
    def index():
        """Wishlist Dashboard view."""
        items = WatchlistItem.query.order_by(WatchlistItem.created_at.desc()).all()
        
        # Calculate summary KPIs
        total_items = len(items)
        deals_count = sum(1 for item in items if item.is_deal)
        total_target_value = sum(item.target_price for item in items if item.target_price)
        
        # Total lowest market value
        lowest_market_sum = sum(
            item.lowest_in_stock_price for item in items if item.lowest_in_stock_price is not None
        )

        return render_template(
            "index.html",
            items=items,
            total_items=total_items,
            deals_count=deals_count,
            total_target_value=total_target_value,
            lowest_market_sum=lowest_market_sum,
            active_tab="wishlist",
        )

    @app.route("/deals")
    def deals():
        """Dedicated Active Deals view."""
        all_items = WatchlistItem.query.order_by(WatchlistItem.created_at.desc()).all()
        deal_items = [item for item in all_items if item.is_deal]
        
        total_savings = sum(item.savings_amount for item in deal_items)

        return render_template(
            "deals.html",
            deal_items=deal_items,
            deals_count=len(deal_items),
            total_savings=total_savings,
            active_tab="deals",
        )

    # ---------------------------------------------------------
    # API Routes: Scryfall Lookups
    # ---------------------------------------------------------
    @app.route("/api/scryfall/autocomplete")
    def scryfall_autocomplete():
        """Card name autocomplete."""
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"suggestions": []})
        suggestions = scryfall_provider.autocomplete(query)
        return jsonify({"suggestions": suggestions})

    @app.route("/api/scryfall/prints")
    def scryfall_prints():
        """Fetch all printings for a specific card name."""
        card_name = request.args.get("name", "").strip()
        if not card_name:
            return jsonify({"prints": []})
        prints = scryfall_provider.search_card_prints(card_name)
        return jsonify({"prints": prints})

    # ---------------------------------------------------------
    # API Routes: Watchlist CRUD & Price Refresh
    # ---------------------------------------------------------
    @app.route("/api/watchlist/add", methods=["POST"])
    def add_card():
        """Add a card to watchlist and run initial price check."""
        data = request.get_json() or {}
        scryfall_id = data.get("scryfall_id", "").strip()
        name = data.get("name", "").strip()
        set_code = data.get("set_code", "").strip().upper()
        collector_number = data.get("collector_number", "").strip()
        image_uri = data.get("image_uri", "").strip()
        finish = data.get("finish", "nonfoil").strip().lower()
        target_price_raw = data.get("target_price")

        if not scryfall_id or not name:
            return jsonify({"error": "Card name and Scryfall ID are required."}), 400

        # Check for duplicates
        existing = WatchlistItem.query.filter_by(scryfall_id=scryfall_id).first()
        if existing:
            return jsonify({"error": "This specific card print is already on your watchlist."}), 409

        target_price = None
        if target_price_raw is not None and str(target_price_raw).strip() != "":
            try:
                target_price = round(float(target_price_raw), 2)
            except ValueError:
                return jsonify({"error": "Invalid target price value."}), 400

        new_item = WatchlistItem(
            name=name,
            scryfall_id=scryfall_id,
            set_code=set_code,
            collector_number=collector_number,
            image_uri=image_uri,
            finish=finish,
            target_price=target_price,
        )
        db.session.add(new_item)
        db.session.commit()

        # Run initial price poll across all providers
        deal_engine.poll_card(new_item, notify=True)

        return jsonify({
            "message": f"Successfully added {name} to watchlist.",
            "card": new_item.to_dict(),
        }), 201

    @app.route("/api/watchlist/update-target/<int:item_id>", methods=["POST"])
    def update_target_price(item_id):
        """Update target price for a card."""
        item = db.session.get(WatchlistItem, item_id)
        if not item:
            return jsonify({"error": "Card not found."}), 404

        data = request.get_json() or {}
        target_price_raw = data.get("target_price")

        if target_price_raw is None or str(target_price_raw).strip() == "":
            item.target_price = None
        else:
            try:
                item.target_price = round(float(target_price_raw), 2)
            except ValueError:
                return jsonify({"error": "Invalid target price number."}), 400

        db.session.commit()
        return jsonify({
            "message": "Target price updated.",
            "card": item.to_dict(),
        })

    @app.route("/api/watchlist/refresh/<int:item_id>", methods=["POST"])
    def refresh_card_price(item_id):
        """Manually trigger price refresh for a specific card."""
        item = db.session.get(WatchlistItem, item_id)
        if not item:
            return jsonify({"error": "Card not found."}), 404

        result = deal_engine.poll_card(item, notify=True)
        return jsonify({
            "message": f"Refreshed prices for {item.name}.",
            "card": item.to_dict(),
        })

    @app.route("/api/watchlist/refresh-all", methods=["POST"])
    def refresh_all_cards():
        """Manually refresh all cards on the watchlist."""
        results = deal_engine.poll_all_cards(notify=True)
        return jsonify({
            "message": f"Successfully refreshed {len(results)} cards.",
            "count": len(results),
        })

    @app.route("/api/watchlist/delete/<int:item_id>", methods=["DELETE", "POST"])
    def delete_card(item_id):
        """Remove card and its associated vendor prices."""
        item = db.session.get(WatchlistItem, item_id)
        if not item:
            return jsonify({"error": "Card not found."}), 404

        card_name = item.name
        db.session.delete(item)
        db.session.commit()

        return jsonify({
            "message": f"Removed {card_name} from watchlist.",
            "deleted_id": item_id,
        })

    @app.route("/api/discord/test", methods=["POST"])
    def test_discord_webhook():
        """Test Discord webhook integration."""
        success, msg = deal_engine.send_test_discord_notification()
        if success:
            return jsonify({"message": msg}), 200
        return jsonify({"error": msg}), 400

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"Starting Chimaera on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
