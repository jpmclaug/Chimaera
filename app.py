import logging
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from apscheduler.schedulers.background import BackgroundScheduler
from config import Config
from models import db, WatchlistItem, VendorPrice, SystemSetting
from deal_engine import DealEngine
from providers import ScryfallProvider

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _migrate_db_schema(app):
    """Ensures watchlist_item and system_setting tables are configured across dialects."""
    with app.app_context():
        try:
            dialect = db.engine.dialect.name
            if dialect == "sqlite":
                with db.engine.connect() as conn:
                    result = conn.execute(db.text("SELECT sql FROM sqlite_master WHERE type='table' AND name='watchlist_item'"))
                    row = result.fetchone()
                    if row and row[0]:
                        sql = row[0]
                        if "UNIQUE (scryfall_id)" in sql or "UNIQUE(scryfall_id)" in sql or "scryfall_id VARCHAR(64) NOT NULL" in sql:
                            logger.info("Migrating SQLite watchlist_item schema to support Any Version tracking...")
                            conn.execute(db.text("PRAGMA foreign_keys=OFF"))
                            conn.execute(db.text("""
                                CREATE TABLE IF NOT EXISTS watchlist_item_migration (
                                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                                    name VARCHAR(255) NOT NULL,
                                    scryfall_id VARCHAR(64),
                                    set_code VARCHAR(10),
                                    collector_number VARCHAR(20),
                                    image_uri TEXT,
                                    finish VARCHAR(20) DEFAULT 'nonfoil',
                                    target_price FLOAT,
                                    notify_mm_stock BOOLEAN DEFAULT 1 NOT NULL,
                                    created_at DATETIME
                                )
                            """))
                            conn.execute(db.text("""
                                INSERT INTO watchlist_item_migration (id, name, scryfall_id, set_code, collector_number, image_uri, finish, target_price, notify_mm_stock, created_at)
                                SELECT id, name, scryfall_id, set_code, collector_number, image_uri, finish, target_price, 1, created_at FROM watchlist_item
                            """))
                            conn.execute(db.text("DROP TABLE watchlist_item"))
                            conn.execute(db.text("ALTER TABLE watchlist_item_migration RENAME TO watchlist_item"))
                            conn.execute(db.text("PRAGMA foreign_keys=ON"))
                            conn.commit()
                            logger.info("SQLite database migration completed successfully.")

                    # Ensure notify_mm_stock column exists in watchlist_item
                    cols = [r[1] for r in conn.execute(db.text("PRAGMA table_info(watchlist_item)")).fetchall()]
                    if "notify_mm_stock" not in cols:
                        logger.info("Adding notify_mm_stock column to watchlist_item...")
                        conn.execute(db.text("ALTER TABLE watchlist_item ADD COLUMN notify_mm_stock BOOLEAN DEFAULT 1 NOT NULL"))
                        conn.commit()

                    # Ensure search_url column exists in vendor_price
                    vp_cols = [r[1] for r in conn.execute(db.text("PRAGMA table_info(vendor_price)")).fetchall()]
                    if "search_url" not in vp_cols:
                        logger.info("Adding search_url column to vendor_price...")
                        conn.execute(db.text("ALTER TABLE vendor_price ADD COLUMN search_url TEXT"))
                        conn.commit()

            elif dialect in ("postgresql", "postgres"):
                with db.engine.connect() as conn:
                    logger.info("Verifying PostgreSQL watchlist_item constraints and columns...")
                    conn.execute(db.text("ALTER TABLE watchlist_item ALTER COLUMN scryfall_id DROP NOT NULL"))
                    conn.execute(db.text("ALTER TABLE watchlist_item DROP CONSTRAINT IF EXISTS watchlist_item_scryfall_id_key"))
                    conn.execute(db.text("ALTER TABLE watchlist_item ADD COLUMN IF NOT EXISTS notify_mm_stock BOOLEAN DEFAULT TRUE NOT NULL"))
                    conn.execute(db.text("ALTER TABLE vendor_price ADD COLUMN IF NOT EXISTS search_url TEXT"))
                    conn.commit()
                    logger.info("PostgreSQL migration check completed.")
        except Exception as e:
            logger.warning(f"Database schema migration check skipped or completed with message: {e}")


def create_app(test_config=None):
    """Application factory for Chimaera MTG Market Tracker."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    # Initialize Database
    db.init_app(app)

    # Initialize Deal Engine and Scryfall provider
    deal_engine = DealEngine(app=app)
    scryfall_provider = ScryfallProvider()

    with app.app_context():
        db.create_all()
        _migrate_db_schema(app)
        logger.info("Database initialized successfully.")

    # ---------------------------------------------------------
    # In-Process Background Scheduler (Optional / Monolithic Mode)
    # Disabled by default for serverless deployments (handled by standalone worker.py)
    # ---------------------------------------------------------
    enable_inprocess_sched = (
        str(app.config.get("ENABLE_INPROCESS_SCHEDULER", os.getenv("ENABLE_INPROCESS_SCHEDULER", "false"))).lower()
        in ("true", "1", "yes")
    )

    if enable_inprocess_sched and not app.config.get("TESTING"):
        if not os.environ.get("WERKZEUG_RUN_MAIN") or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            def scheduled_price_poll():
                with app.app_context():
                    logger.info("Running scheduled MTG price check via in-process scheduler...")
                    deal_engine.poll_all_cards(notify=True)

            scheduler = BackgroundScheduler(daemon=True)
            with app.app_context():
                poll_hours = SystemSetting.get_float(
                    "poll_interval_hours",
                    default=app.config.get("POLL_INTERVAL_HOURS", 6.0),
                )
            scheduler.add_job(
                func=scheduled_price_poll,
                trigger="interval",
                hours=poll_hours,
                id="chimera_price_poll",
                replace_existing=True,
            )
            try:
                scheduler.start()
                logger.info(f"In-process scheduler started (polling every {poll_hours} hours).")
            except Exception as e:
                logger.warning(f"Could not start in-process scheduler: {e}")

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

    @app.route("/guide")
    def guide():
        """Tactical Operations & How-To Guide view."""
        all_items = WatchlistItem.query.all()
        deals_count = sum(1 for item in all_items if item.is_deal)
        return render_template(
            "guide.html",
            deals_count=deals_count,
            active_tab="guide",
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
        name = (data.get("name") or "").strip()
        is_any_version = data.get("is_any_version", False)
        set_code = (data.get("set_code") or "").strip().upper() or None
        collector_number = (data.get("collector_number") or "").strip() or None
        scryfall_id = (data.get("scryfall_id") or "").strip() or None
        image_uri = (data.get("image_uri") or "").strip() or None
        finish = (data.get("finish") or "nonfoil").strip().lower()
        target_price_raw = data.get("target_price")
        notify_mm_stock = bool(data.get("notify_mm_stock", True))

        if not name:
            return jsonify({"error": "Card name is required."}), 400

        # If tracking Any Version, clear set_code and collector_number
        if is_any_version or not set_code or set_code == "ANY":
            set_code = None
            collector_number = None

        # Check for duplicates
        if set_code is None:
            existing = WatchlistItem.query.filter(
                WatchlistItem.name.ilike(name),
                (WatchlistItem.set_code == None) | (WatchlistItem.set_code == "") | (WatchlistItem.set_code == "ANY"),
                WatchlistItem.finish == finish,
            ).first()
            if existing:
                return jsonify({"error": f"'{name}' (Any Version, {finish.capitalize()}) is already on your watchlist."}), 409
        else:
            existing = WatchlistItem.query.filter(
                WatchlistItem.name.ilike(name),
                WatchlistItem.set_code == set_code,
                WatchlistItem.collector_number == collector_number,
                WatchlistItem.finish == finish,
            ).first()
            if existing:
                return jsonify({"error": f"'{name}' ({set_code} #{collector_number or '?'}, {finish.capitalize()}) is already on your watchlist."}), 409

        # If scryfall_id or image_uri missing, resolve canonical info from Scryfall
        if not scryfall_id or not image_uri:
            card_info = scryfall_provider.get_card_named(name)
            if card_info:
                if not scryfall_id:
                    scryfall_id = card_info.get("id")
                if not image_uri:
                    image_uri = card_info.get("image_uri")

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
            notify_mm_stock=notify_mm_stock,
        )
        db.session.add(new_item)
        db.session.commit()

        # Run initial price poll across all providers
        deal_engine.poll_card(new_item, notify=True)

        version_desc = "Any Version" if set_code is None else f"{set_code} #{collector_number or '?'}"
        return jsonify({
            "message": f"Successfully added {name} ({version_desc}) to watchlist.",
            "card": new_item.to_dict(),
        }), 201

    @app.route("/api/watchlist/toggle-mm-alert/<int:item_id>", methods=["POST"])
    def toggle_mm_alert(item_id):
        """Toggle Mighty Meeple stock alert for a specific card."""
        item = db.session.get(WatchlistItem, item_id)
        if not item:
            return jsonify({"error": "Card not found."}), 404

        data = request.get_json(silent=True) or {}
        if "notify_mm_stock" in data:
            item.notify_mm_stock = bool(data["notify_mm_stock"])
        else:
            item.notify_mm_stock = not bool(item.notify_mm_stock if item.notify_mm_stock is not None else True)

        db.session.commit()
        status_str = "ENABLED" if item.notify_mm_stock else "DISABLED"
        return jsonify({
            "message": f"Mighty Meeple in-stock alert {status_str} for {item.name}.",
            "notify_mm_stock": item.notify_mm_stock,
            "card": item.to_dict(),
        })

    @app.route("/api/watchlist/update-scope/<int:item_id>", methods=["POST"])
    def update_card_scope(item_id):
        """Update a card's scope between Any Version (general) and a specific print."""
        item = db.session.get(WatchlistItem, item_id)
        if not item:
            return jsonify({"error": "Card not found."}), 404

        data = request.get_json() or {}
        is_any = data.get("is_any_version", False)
        set_code = (data.get("set_code") or "").strip().upper() or None
        collector_number = (data.get("collector_number") or "").strip() or None
        scryfall_id = (data.get("scryfall_id") or "").strip() or None
        image_uri = (data.get("image_uri") or "").strip() or None
        finish = (data.get("finish") or item.finish or "nonfoil").strip().lower()

        if is_any or not set_code or set_code == "ANY":
            item.set_code = None
            item.collector_number = None
        else:
            item.set_code = set_code
            item.collector_number = collector_number

        if scryfall_id:
            item.scryfall_id = scryfall_id
        if image_uri:
            item.image_uri = image_uri
        item.finish = finish

        db.session.commit()
        deal_engine.poll_card(item, notify=True)

        scope_label = "Any Version (Card in General)" if item.is_any_version else f"{item.set_code} #{item.collector_number or '?'}"
        return jsonify({
            "message": f"Updated tracking scope for {item.name} to {scope_label}.",
            "card": item.to_dict(),
        })

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

        if "notify_mm_stock" in data:
            item.notify_mm_stock = bool(data["notify_mm_stock"])

        db.session.commit()
        return jsonify({
            "message": "Target price and alert preferences updated.",
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

    @app.route("/api/card/price-intel")
    def card_price_intel():
        """Returns real-time price intelligence and good-price deal targets for a card."""
        name = request.args.get("name", "").strip()
        finish = request.args.get("finish", "nonfoil").strip().lower()
        set_code = request.args.get("set_code", "").strip().upper() or None

        if not name:
            return jsonify({"error": "Card name required."}), 400

        market_price = None
        if set_code and set_code != "ANY":
            prints = scryfall_provider.search_card_prints(name)
            match_print = next((p for p in prints if p.get("set_code", "").upper() == set_code), None)
            if match_print and match_print.get("prices"):
                p_dict = match_print["prices"]
                p_val = p_dict.get("usd_foil") if finish == "foil" else (p_dict.get("usd_etched") if finish == "etched" else p_dict.get("usd"))
                if p_val:
                    try:
                        market_price = float(p_val)
                    except (ValueError, TypeError):
                        pass
        else:
            cheapest = scryfall_provider.get_cheapest_tcgplayer_price(name, finish=finish)
            market_price = cheapest.get("price") if cheapest and cheapest.get("in_stock") else None

        good_target = round(market_price * 0.90, 2) if market_price and market_price > 0 else None
        great_target = round(market_price * 0.80, 2) if market_price and market_price > 0 else None
        fair_target = round(market_price, 2) if market_price and market_price > 0 else None

        return jsonify({
            "name": name,
            "finish": finish,
            "market_price": market_price,
            "targets": {
                "great_deal_20": great_target,
                "good_deal_10": good_target,
                "fair_market": fair_target,
            }
        })

    @app.route("/api/discord/test", methods=["POST"])
    def test_discord_webhook():
        """Test Discord webhook integration."""
        success, msg = deal_engine.send_test_discord_notification()
        if success:
            return jsonify({"message": msg}), 200
        return jsonify({"error": msg}), 400

    @app.route("/api/settings/telemetry")
    def get_telemetry():
        """Returns current surveillance cadence, worker heartbeat, and telemetry status."""
        interval_hours = SystemSetting.get_float("poll_interval_hours", default=Config.POLL_INTERVAL_HOURS)
        auto_enabled = SystemSetting.get_bool("auto_poll_enabled", default=True)
        notify_mm_stock = SystemSetting.get_bool("notify_mm_stock_enabled", default=True)
        ebay_link_mode = SystemSetting.get_val("ebay_link_mode", default="direct")
        last_poll_time = SystemSetting.get_val("last_poll_time")
        last_poll_status = SystemSetting.get_val("last_poll_status", "No surveillance cycles recorded.")
        last_poll_count = SystemSetting.get_val("last_poll_count", "0")
        last_poll_deals = SystemSetting.get_val("last_poll_deals", "0")
        worker_heartbeat = SystemSetting.get_val("worker_heartbeat")
        worker_status = SystemSetting.get_val("worker_status", "standby")

        return jsonify({
            "poll_interval_hours": interval_hours,
            "auto_poll_enabled": auto_enabled,
            "notify_mm_stock_enabled": notify_mm_stock,
            "ebay_link_mode": ebay_link_mode,
            "last_poll_time": last_poll_time,
            "last_poll_status": last_poll_status,
            "last_poll_count": int(last_poll_count) if str(last_poll_count).isdigit() else 0,
            "last_poll_deals": int(last_poll_deals) if str(last_poll_deals).isdigit() else 0,
            "worker_heartbeat": worker_heartbeat,
            "worker_status": worker_status,
        })

    @app.route("/api/settings/cadence", methods=["POST"])
    def update_cadence():
        """Updates surveillance cadence interval, auto-refresh toggle, and alert preferences."""
        data = request.get_json() or {}
        interval_raw = data.get("poll_interval_hours")
        auto_enabled_raw = data.get("auto_poll_enabled")
        notify_mm_raw = data.get("notify_mm_stock_enabled")
        ebay_mode_raw = data.get("ebay_link_mode")

        if interval_raw is not None:
            try:
                interval_float = float(interval_raw)
                if interval_float <= 0:
                    return jsonify({"error": "Interval must be greater than 0 hours."}), 400
                SystemSetting.set_val("poll_interval_hours", round(interval_float, 2))
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid interval value."}), 400

        if auto_enabled_raw is not None:
            auto_val = "true" if bool(auto_enabled_raw) else "false"
            SystemSetting.set_val("auto_poll_enabled", auto_val)

        if notify_mm_raw is not None:
            mm_val = "true" if bool(notify_mm_raw) else "false"
            SystemSetting.set_val("notify_mm_stock_enabled", mm_val)

        if ebay_mode_raw is not None:
            mode_str = "search" if str(ebay_mode_raw).lower() == "search" else "direct"
            SystemSetting.set_val("ebay_link_mode", mode_str)

        current_interval = SystemSetting.get_float("poll_interval_hours", default=Config.POLL_INTERVAL_HOURS)
        current_auto = SystemSetting.get_bool("auto_poll_enabled", default=True)
        current_mm = SystemSetting.get_bool("notify_mm_stock_enabled", default=True)
        current_ebay = SystemSetting.get_val("ebay_link_mode", default="direct")

        return jsonify({
            "message": f"Surveillance configuration committed: every {current_interval}h ({'Active' if current_auto else 'Paused'}).",
            "poll_interval_hours": current_interval,
            "auto_poll_enabled": current_auto,
            "notify_mm_stock_enabled": current_mm,
            "ebay_link_mode": current_ebay,
        })

    @app.route("/api/settings/ebay-preference", methods=["POST"])
    def update_ebay_preference():
        """Updates global default eBay link navigation mode."""
        data = request.get_json() or {}
        mode = "search" if str(data.get("ebay_link_mode", "")).lower() == "search" else "direct"
        SystemSetting.set_val("ebay_link_mode", mode)
        return jsonify({
            "message": f"eBay default navigation updated to {'Direct Lowest Listing' if mode == 'direct' else 'Search Results Page'}.",
            "ebay_link_mode": mode,
        })

    @app.route("/api/worker/trigger", methods=["POST"])
    def trigger_worker_poll():
        """Signals the standalone worker via database flag and runs manual fleet poll."""
        SystemSetting.set_val("manual_poll_requested", "true")
        results = deal_engine.poll_all_cards(notify=True)
        return jsonify({
            "message": f"Surveillance scan completed: {len(results)} targets refreshed.",
            "count": len(results),
        })

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"Starting Chimaera on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
