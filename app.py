import logging
import os
import re
import secrets
from functools import wraps
from datetime import datetime, timezone
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    flash,
    session,
    g,
)
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from config import Config
from models import (
    db,
    User,
    AllowedEmail,
    WatchlistItem,
    VendorPrice,
    SystemSetting,
    ActivityLog,
    MicrocenterItem,
    MicrocenterHistory,
    DeckAnalysis,
    UserInventoryCard,
    utc_now,
)
from deal_engine import DealEngine
from providers import ScryfallProvider, MightyMeepleProvider, MicrocenterProvider
from deck_parser import DeckParser, DeckParseError
from card_classifier import MTGCardClassifier
from deck_analyzer import DeckAnalyzer
from deck_comparator import DeckComparator
from inventory_parser import ManaBoxInventoryParser, InventoryParseError
from inventory_manager import InventoryManager
from deck_upgrade_engine import DualTierUpgradeEngine
from gemini_analyzer import (
    GeminiAnalyzer,
    GeminiAnalysisError,
    SUPPORTED_MODELS as GEMINI_SUPPORTED_MODELS,
    DEFAULT_MODEL as GEMINI_DEFAULT_MODEL,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _migrate_db_schema(app):
    """Ensures user, allowed_email, watchlist_item, and system_setting tables are configured across dialects."""
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
                                    user_id INTEGER,
                                    name VARCHAR(255) NOT NULL,
                                    scryfall_id VARCHAR(64),
                                    set_code VARCHAR(10),
                                    collector_number VARCHAR(20),
                                    image_uri TEXT,
                                    finish VARCHAR(20) DEFAULT 'nonfoil',
                                    target_price FLOAT,
                                    notify_mm_stock BOOLEAN DEFAULT 1 NOT NULL,
                                    created_at DATETIME,
                                    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
                                )
                            """))
                            conn.execute(db.text("""
                                INSERT INTO watchlist_item_migration (id, user_id, name, scryfall_id, set_code, collector_number, image_uri, finish, target_price, notify_mm_stock, created_at)
                                SELECT id, NULL, name, scryfall_id, set_code, collector_number, image_uri, finish, target_price, 1, created_at FROM watchlist_item
                            """))
                            conn.execute(db.text("DROP TABLE watchlist_item"))
                            conn.execute(db.text("ALTER TABLE watchlist_item_migration RENAME TO watchlist_item"))
                            conn.execute(db.text("PRAGMA foreign_keys=ON"))
                            conn.commit()
                            logger.info("SQLite database migration completed successfully.")

                    # Ensure user_id column exists in watchlist_item
                    cols = [r[1] for r in conn.execute(db.text("PRAGMA table_info(watchlist_item)")).fetchall()]
                    if "user_id" not in cols:
                        logger.info("Adding user_id column to watchlist_item...")
                        conn.execute(db.text("ALTER TABLE watchlist_item ADD COLUMN user_id INTEGER REFERENCES user(id) ON DELETE CASCADE"))
                        conn.commit()

                    # Ensure notify_mm_stock column exists in watchlist_item
                    if "notify_mm_stock" not in cols:
                        logger.info("Adding notify_mm_stock column to watchlist_item...")
                        conn.execute(db.text("ALTER TABLE watchlist_item ADD COLUMN notify_mm_stock BOOLEAN DEFAULT 1 NOT NULL"))
                        conn.commit()

                    # Ensure tag column exists in watchlist_item
                    if "tag" not in cols:
                        logger.info("Adding tag column to watchlist_item...")
                        conn.execute(db.text("ALTER TABLE watchlist_item ADD COLUMN tag VARCHAR(100)"))
                        conn.commit()

                    # Ensure search_url column exists in vendor_price
                    vp_cols = [r[1] for r in conn.execute(db.text("PRAGMA table_info(vendor_price)")).fetchall()]
                    if "search_url" not in vp_cols:
                        logger.info("Adding search_url column to vendor_price...")
                        conn.execute(db.text("ALTER TABLE vendor_price ADD COLUMN search_url TEXT"))
                        conn.commit()

                    # Ensure discord_webhook_url column exists in user table
                    user_cols = [r[1] for r in conn.execute(db.text("PRAGMA table_info(user)")).fetchall()]
                    if "discord_webhook_url" not in user_cols:
                        logger.info("Adding discord_webhook_url column to user table...")
                        conn.execute(db.text("ALTER TABLE user ADD COLUMN discord_webhook_url VARCHAR(500)"))
                        conn.commit()

                    # Ensure microcenter_item table exists in SQLite
                    conn.execute(db.text("""
                        CREATE TABLE IF NOT EXISTS microcenter_item (
                            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                            sku VARCHAR(50) NOT NULL UNIQUE,
                            product_id VARCHAR(50),
                            name VARCHAR(255) NOT NULL,
                            product_url TEXT,
                            image_url TEXT,
                            current_price FLOAT NOT NULL DEFAULT 0.0,
                            previous_price FLOAT,
                            original_price FLOAT,
                            in_stock BOOLEAN NOT NULL DEFAULT 1,
                            stock_count INTEGER,
                            stock_text VARCHAR(100),
                            store_id VARCHAR(20) NOT NULL DEFAULT '175',
                            store_name VARCHAR(100) NOT NULL DEFAULT 'Charlotte',
                            category VARCHAR(100),
                            target_price FLOAT,
                            notify_on_price_change BOOLEAN NOT NULL DEFAULT 1,
                            notify_on_restock BOOLEAN NOT NULL DEFAULT 1,
                            notify_on_low_stock BOOLEAN NOT NULL DEFAULT 1,
                            first_seen_at DATETIME,
                            last_scanned_at DATETIME,
                            last_price_change_at DATETIME,
                            last_stock_change_at DATETIME,
                            is_active BOOLEAN NOT NULL DEFAULT 1
                        )
                    """))
                    # Add column if table existed previously without it
                    try:
                        conn.execute(db.text("ALTER TABLE microcenter_item ADD COLUMN notify_on_low_stock BOOLEAN DEFAULT 1 NOT NULL"))
                        conn.commit()
                    except Exception:
                        pass
                    conn.execute(db.text("""
                        CREATE TABLE IF NOT EXISTS microcenter_history (
                            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                            item_id INTEGER NOT NULL,
                            price FLOAT NOT NULL,
                            original_price FLOAT,
                            in_stock BOOLEAN NOT NULL DEFAULT 1,
                            stock_count INTEGER,
                            stock_text VARCHAR(100),
                            price_change FLOAT NOT NULL DEFAULT 0.0,
                            stock_change INTEGER NOT NULL DEFAULT 0,
                            recorded_at DATETIME,
                            FOREIGN KEY (item_id) REFERENCES microcenter_item(id) ON DELETE CASCADE
                        )
                    """))
                    # Ensure deck_analysis table and columns exist in SQLite
                    conn.execute(db.text("""
                        CREATE TABLE IF NOT EXISTS deck_analysis (
                            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            deck_name VARCHAR(255) NOT NULL DEFAULT 'Commander Deck',
                            commander_name VARCHAR(255),
                            commander_art TEXT,
                            source_url TEXT,
                            source_type VARCHAR(50) DEFAULT 'text',
                            raw_decklist TEXT,
                            cards_data TEXT,
                            stats_json TEXT,
                            analysis_json TEXT,
                            model_used VARCHAR(100) DEFAULT 'gemini-3.7-flash',
                            power_level FLOAT,
                            power_bracket VARCHAR(50),
                            archetype VARCHAR(100),
                            total_cards INTEGER DEFAULT 100,
                            total_value FLOAT,
                            avg_cmc FLOAT,
                            color_identity VARCHAR(50),
                            created_at DATETIME,
                            updated_at DATETIME,
                            FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
                        )
                    """))
                    da_cols = [r[1] for r in conn.execute(db.text("PRAGMA table_info(deck_analysis)")).fetchall()]
                    if "commander_art" not in da_cols:
                        conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN commander_art TEXT"))
                    if "stats_json" not in da_cols:
                        conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN stats_json TEXT"))
                    if "total_value" not in da_cols:
                        conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN total_value FLOAT"))
                    if "avg_cmc" not in da_cols:
                        conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN avg_cmc FLOAT"))
                    if "color_identity" not in da_cols:
                        conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN color_identity VARCHAR(50)"))
                    conn.execute(db.text("""
                        CREATE TABLE IF NOT EXISTS user_inventory_card (
                            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            name VARCHAR(255) NOT NULL,
                            raw_name VARCHAR(255),
                            set_code VARCHAR(20),
                            set_name VARCHAR(255),
                            collector_number VARCHAR(50),
                            scryfall_id VARCHAR(64),
                            quantity INTEGER NOT NULL DEFAULT 1,
                            foil VARCHAR(30) NOT NULL DEFAULT 'normal',
                            condition VARCHAR(50),
                            language VARCHAR(20) DEFAULT 'en',
                            purchase_price FLOAT,
                            binder_name VARCHAR(255),
                            rarity VARCHAR(50),
                            mana_cost VARCHAR(100),
                            cmc FLOAT,
                            type_line VARCHAR(255),
                            oracle_text TEXT,
                            color_identity VARCHAR(50),
                            image_uri TEXT,
                            price_usd FLOAT,
                            price_usd_foil FLOAT,
                            created_at DATETIME,
                            updated_at DATETIME,
                            FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
                        )
                    """))
                    conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_inventory_user_id ON user_inventory_card (user_id)"))
                    conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_inventory_name ON user_inventory_card (name)"))
                    conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_inventory_scryfall_id ON user_inventory_card (scryfall_id)"))
                    conn.commit()

            elif dialect in ("postgresql", "postgres"):
                with db.engine.connect() as conn:
                    logger.info("Verifying PostgreSQL watchlist_item and user constraints and columns...")
                    conn.execute(db.text("ALTER TABLE watchlist_item ALTER COLUMN scryfall_id DROP NOT NULL"))
                    conn.execute(db.text("ALTER TABLE watchlist_item DROP CONSTRAINT IF EXISTS watchlist_item_scryfall_id_key"))
                    conn.execute(db.text("ALTER TABLE watchlist_item ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES \"user\"(id) ON DELETE CASCADE"))
                    conn.execute(db.text("ALTER TABLE watchlist_item ADD COLUMN IF NOT EXISTS notify_mm_stock BOOLEAN DEFAULT TRUE NOT NULL"))
                    conn.execute(db.text("ALTER TABLE watchlist_item ADD COLUMN IF NOT EXISTS tag VARCHAR(100)"))
                    conn.execute(db.text("ALTER TABLE vendor_price ADD COLUMN IF NOT EXISTS search_url TEXT"))
                    conn.execute(db.text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS discord_webhook_url VARCHAR(500)"))
                    conn.execute(db.text("""
                        CREATE TABLE IF NOT EXISTS microcenter_item (
                            id SERIAL PRIMARY KEY,
                            sku VARCHAR(50) NOT NULL UNIQUE,
                            product_id VARCHAR(50),
                            name VARCHAR(255) NOT NULL,
                            product_url TEXT,
                            image_url TEXT,
                            current_price FLOAT NOT NULL DEFAULT 0.0,
                            previous_price FLOAT,
                            original_price FLOAT,
                            in_stock BOOLEAN NOT NULL DEFAULT TRUE,
                            stock_count INTEGER,
                            stock_text VARCHAR(100),
                            store_id VARCHAR(20) NOT NULL DEFAULT '175',
                            store_name VARCHAR(100) NOT NULL DEFAULT 'Charlotte',
                            category VARCHAR(100),
                            target_price FLOAT,
                            notify_on_price_change BOOLEAN NOT NULL DEFAULT TRUE,
                            notify_on_restock BOOLEAN NOT NULL DEFAULT TRUE,
                            notify_on_low_stock BOOLEAN NOT NULL DEFAULT TRUE,
                            first_seen_at TIMESTAMP,
                            last_scanned_at TIMESTAMP,
                            last_price_change_at TIMESTAMP,
                            last_stock_change_at TIMESTAMP,
                            is_active BOOLEAN NOT NULL DEFAULT TRUE
                        )
                    """))
                    conn.execute(db.text("ALTER TABLE microcenter_item ADD COLUMN IF NOT EXISTS notify_on_low_stock BOOLEAN DEFAULT TRUE NOT NULL"))
                    conn.execute(db.text("""
                        CREATE TABLE IF NOT EXISTS microcenter_history (
                            id SERIAL PRIMARY KEY,
                            item_id INTEGER NOT NULL REFERENCES microcenter_item(id) ON DELETE CASCADE,
                            price FLOAT NOT NULL,
                            original_price FLOAT,
                            in_stock BOOLEAN NOT NULL DEFAULT TRUE,
                            stock_count INTEGER,
                            stock_text VARCHAR(100),
                            price_change FLOAT NOT NULL DEFAULT 0.0,
                            stock_change INTEGER NOT NULL DEFAULT 0,
                            recorded_at TIMESTAMP
                        )
                    """))
                    conn.execute(db.text("""
                        CREATE TABLE IF NOT EXISTS deck_analysis (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER REFERENCES \"user\"(id) ON DELETE CASCADE,
                            deck_name VARCHAR(255) NOT NULL DEFAULT 'Commander Deck',
                            commander_name VARCHAR(255),
                            commander_art TEXT,
                            source_url TEXT,
                            source_type VARCHAR(50) DEFAULT 'text',
                            raw_decklist TEXT,
                            cards_data TEXT,
                            stats_json TEXT,
                            analysis_json TEXT,
                            model_used VARCHAR(100) DEFAULT 'gemini-3.7-flash',
                            power_level FLOAT,
                            power_bracket VARCHAR(50),
                            archetype VARCHAR(100),
                            total_cards INTEGER DEFAULT 100,
                            total_value FLOAT,
                            avg_cmc FLOAT,
                            color_identity VARCHAR(50),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """))
                    conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN IF NOT EXISTS commander_art TEXT"))
                    conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN IF NOT EXISTS stats_json TEXT"))
                    conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN IF NOT EXISTS total_value FLOAT"))
                    conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN IF NOT EXISTS avg_cmc FLOAT"))
                    conn.execute(db.text("ALTER TABLE deck_analysis ADD COLUMN IF NOT EXISTS color_identity VARCHAR(50)"))
                    conn.execute(db.text("""
                        CREATE TABLE IF NOT EXISTS user_inventory_card (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                            name VARCHAR(255) NOT NULL,
                            raw_name VARCHAR(255),
                            set_code VARCHAR(20),
                            set_name VARCHAR(255),
                            collector_number VARCHAR(50),
                            scryfall_id VARCHAR(64),
                            quantity INTEGER NOT NULL DEFAULT 1,
                            foil VARCHAR(30) NOT NULL DEFAULT 'normal',
                            condition VARCHAR(50),
                            language VARCHAR(20) DEFAULT 'en',
                            purchase_price FLOAT,
                            binder_name VARCHAR(255),
                            rarity VARCHAR(50),
                            mana_cost VARCHAR(100),
                            cmc FLOAT,
                            type_line VARCHAR(255),
                            oracle_text TEXT,
                            color_identity VARCHAR(50),
                            image_uri TEXT,
                            price_usd FLOAT,
                            price_usd_foil FLOAT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """))
                    conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_inventory_user_id ON user_inventory_card (user_id)"))
                    conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_inventory_name ON user_inventory_card (name)"))
                    conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_inventory_scryfall_id ON user_inventory_card (scryfall_id)"))
                    conn.commit()
                    logger.info("PostgreSQL migration check completed.")

            # Bootstrap Initial Primary Admin
            admin_email = (app.config.get("ADMIN_EMAIL") or "jpmclaug@gmail.com").strip().lower()
            if admin_email:
                allowed = AllowedEmail.get_by_email(admin_email)
                if not allowed:
                    allowed = AllowedEmail(
                        email=admin_email,
                        notes="Primary System Administrator (Bootstrap)",
                        is_admin=True,
                        added_by="System Bootstrap",
                    )
                    db.session.add(allowed)
                    db.session.commit()
                else:
                    if not allowed.is_admin:
                        allowed.is_admin = True
                        db.session.commit()

                admin_user = User.query.filter(db.func.lower(User.email) == admin_email).first()
                if not admin_user:
                    admin_user = User(
                        email=admin_email,
                        name="Primary Administrator",
                        is_admin=True,
                        is_active=True,
                    )
                    db.session.add(admin_user)
                    db.session.commit()
                else:
                    if not admin_user.is_admin:
                        admin_user.is_admin = True
                        admin_user.is_active = True
                        db.session.commit()

                # Assign any orphan cards to the primary administrator
                if admin_user.id:
                    orphans = WatchlistItem.query.filter(WatchlistItem.user_id.is_(None)).all()
                    if orphans:
                        for card in orphans:
                            card.user_id = admin_user.id
                        db.session.commit()
                        logger.info(f"Assigned {len(orphans)} legacy watchlist items to admin ({admin_email}).")

        except Exception as e:
            logger.warning(f"Database schema migration check skipped or completed with message: {e}")


def parse_bulk_card_names(raw_input: str) -> list[str]:
    """
    Parses a string of card names separated by semicolons (;) or newlines.
    Trims whitespace and quotes, filters empty lines, and deduplicates while preserving order.
    """
    if not raw_input:
        return []
    normalized = raw_input.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    raw_names = []
    for line in lines:
        if not line:
            continue
        parts = line.split(";")
        for part in parts:
            cleaned = part.strip().strip("\"'").strip()
            if cleaned:
                raw_names.append(cleaned)

    seen = set()
    unique_names = []
    for name in raw_names:
        lower = name.lower()
        if lower not in seen:
            seen.add(lower)
            unique_names.append(name)
    return unique_names


def create_app(test_config=None):
    """Application factory for Chimaera MTG Market Tracker."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    # Initialize Database
    db.init_app(app)

    # Initialize Deal Engine and External Store Providers
    deal_engine = DealEngine(app=app)
    scryfall_provider = ScryfallProvider()
    mightymeeple_provider = MightyMeepleProvider()
    microcenter_provider = MicrocenterProvider(
        store_id=Config.MICROCENTER_STORE_ID,
        store_name=Config.MICROCENTER_STORE_NAME,
    )
    inventory_manager = InventoryManager(scryfall_provider=scryfall_provider)
    upgrade_engine = DualTierUpgradeEngine(scryfall_provider=scryfall_provider)

    with app.app_context():
        db.create_all()
        _migrate_db_schema(app)
        logger.info("Database initialized successfully.")

    # ---------------------------------------------------------
    # Authentication & Activity Telemetry Helpers
    # ---------------------------------------------------------
    def get_client_ip(req=None) -> str:
        """Extracts client IP address respecting X-Forwarded-For if behind a proxy."""
        if req is None:
            try:
                req = request
            except Exception:
                return "127.0.0.1"
        if not req:
            return "127.0.0.1"
        forwarded = req.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return req.remote_addr or "127.0.0.1"

    def log_activity(
        action: str,
        details: str | None = None,
        user: User | None = None,
        user_id: int | None = None,
        email: str | None = None,
        ip: str | None = None,
        endpoint: str | None = None,
    ) -> ActivityLog | None:
        """
        Records an activity or security event into the audit log.
        Fails safely without breaking core application flow.
        """
        try:
            if not ip:
                ip = get_client_ip()
            if not endpoint:
                try:
                    endpoint = request.path
                except Exception:
                    endpoint = None
            if not user and not user_id:
                try:
                    user = get_current_user()
                except Exception:
                    user = None

            final_user_id = user.id if user else user_id
            final_email = email or (user.email if user else None)

            entry = ActivityLog(
                user_id=final_user_id,
                user_email=final_email,
                ip_address=ip,
                action=action,
                endpoint=endpoint,
                details=details,
            )
            db.session.add(entry)
            db.session.commit()
            return entry
        except Exception as e:
            logger.warning(f"Activity logging skipped or failed: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass
            return None

    def get_current_user():
        """Resolves the active user from session."""
        user_id = session.get("user_id")
        if not user_id:
            return None
        if not hasattr(g, "current_user") or g.current_user is None or g.current_user.id != user_id:
            g.current_user = db.session.get(User, user_id)
        return g.current_user

    def login_required(f):
        """Ensures user is authenticated and active."""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user or not user.is_active:
                session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required. Session expired or unauthorized."}), 401
                return redirect(url_for("login_page", next=request.url))
            return f(*args, **kwargs)
        return decorated_function

    def admin_required(f):
        """Ensures user has administrative clearance."""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user or not user.is_active:
                session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required."}), 401
                return redirect(url_for("login_page", next=request.url))
            if not user.is_admin:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Administrative clearance required."}), 403
                flash("ACCESS RESTRICTED // Administrative clearance required for this protocol.", "error")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return decorated_function

    @app.context_processor
    def inject_auth_context():
        """Injects current_user into all Jinja templates."""
        return {
            "current_user": get_current_user(),
        }

    @app.template_filter("eastern")
    def eastern_filter(dt_val, fmt="%Y-%m-%d %H:%M:%S EST"):
        """Formats datetime or ISO string in Eastern Standard/Daylight Time (EST/EDT)."""
        if not dt_val:
            return "--"
        try:
            import zoneinfo
            eastern_tz = zoneinfo.ZoneInfo("America/New_York")
        except Exception:
            eastern_tz = timezone(timedelta(hours=-5), name="EST")

        if isinstance(dt_val, str):
            try:
                dt_val = datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
            except Exception:
                return dt_val

        if isinstance(dt_val, datetime):
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=timezone.utc)
            converted = dt_val.astimezone(eastern_tz)
            return converted.strftime(fmt)
        return str(dt_val)

    @app.template_filter("est")
    def est_filter(dt_val, fmt="%Y-%m-%d %H:%M:%S EST"):
        return eastern_filter(dt_val, fmt=fmt)

    # ---------------------------------------------------------
    # In-Process Background Scheduler (Optional / Monolithic Mode)
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
                    if SystemSetting.get_bool("microcenter_poll_enabled", default=True):
                        try:
                            logger.info("Running scheduled MicroCenter Charlotte inventory sync...")
                            deal_engine.sync_microcenter(notify=True)
                        except Exception as e:
                            logger.error(f"Error syncing MicroCenter in scheduled poll: {e}")

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
    # Authentication & OAuth Routes
    # ---------------------------------------------------------
    @app.route("/login")
    def login_page():
        """Renders sign-in portal."""
        user = get_current_user()
        if user and user.is_active:
            return redirect(url_for("index"))
        next_url = request.args.get("next", "/")
        google_configured = bool(app.config.get("GOOGLE_CLIENT_ID"))
        return render_template(
            "login.html",
            next_url=next_url,
            google_configured=google_configured,
            is_testing=bool(app.config.get("TESTING")),
        )

    @app.route("/auth/google")
    def google_auth():
        """Initiates Google OAuth 2.0 Authorization Code flow."""
        client_id = app.config.get("GOOGLE_CLIENT_ID")
        if not client_id:
            flash("Google OAuth client ID not configured on server.", "error")
            return redirect(url_for("login_page"))

        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        session["oauth_next"] = request.args.get("next", "/")

        base_url = app.config.get("APP_BASE_URL")
        if base_url:
            redirect_uri = f"{base_url}/auth/google/callback"
        else:
            is_https = request.headers.get("X-Forwarded-Proto") == "https" or request.is_secure
            scheme = "https" if is_https else "http"
            redirect_uri = url_for("google_auth_callback", _external=True, _scheme=scheme)

        session["oauth_redirect_uri"] = redirect_uri

        google_auth_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        req = requests.Request("GET", google_auth_endpoint, params=params).prepare()
        return redirect(req.url)

    @app.route("/auth/google/callback")
    def google_auth_callback():
        """Handles Google OAuth authorization code exchange and whitelist verification."""
        state_received = request.args.get("state")
        state_expected = session.pop("oauth_state", None)
        next_url = session.pop("oauth_next", "/")
        redirect_uri = session.pop("oauth_redirect_uri", None)

        if not state_received or not state_expected or state_received != state_expected:
            log_activity("LOGIN_FAILED", details="Access Denied: Invalid OAuth state token")
            flash("SECURITY ALERT // Invalid OAuth state token. Please retry.", "error")
            return redirect(url_for("login_page"))

        code = request.args.get("code")
        if not code:
            log_activity("LOGIN_FAILED", details="Access Denied: Google OAuth cancelled or denied")
            flash("Google authorization denied or cancelled.", "error")
            return redirect(url_for("login_page"))

        client_id = app.config.get("GOOGLE_CLIENT_ID")
        client_secret = app.config.get("GOOGLE_CLIENT_SECRET")

        if not redirect_uri:
            base_url = app.config.get("APP_BASE_URL")
            if base_url:
                redirect_uri = f"{base_url}/auth/google/callback"
            else:
                is_https = request.headers.get("X-Forwarded-Proto") == "https" or request.is_secure
                scheme = "https" if is_https else "http"
                redirect_uri = url_for("google_auth_callback", _external=True, _scheme=scheme)

        # Exchange authorization code for tokens
        token_endpoint = "https://oauth2.googleapis.com/token"
        token_data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }

        try:
            token_resp = requests.post(token_endpoint, data=token_data, timeout=10)
            if token_resp.status_code != 200:
                logger.error(f"Google token exchange failed: {token_resp.text}")
                log_activity("LOGIN_FAILED", details="Access Denied: Google token exchange failure")
                flash("Failed to authenticate with Google.", "error")
                return redirect(url_for("login_page"))

            tokens = token_resp.json()
            access_token = tokens.get("access_token")

            # Retrieve Google User Profile
            userinfo_resp = requests.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if userinfo_resp.status_code != 200:
                log_activity("LOGIN_FAILED", details="Access Denied: Google profile retrieval failure")
                flash("Failed to retrieve Google profile telemetry.", "error")
                return redirect(url_for("login_page"))

            profile = userinfo_resp.json()
            email = (profile.get("email") or "").strip().lower()
            if not email:
                log_activity("LOGIN_FAILED", details="Access Denied: No email in Google account profile")
                flash("No email provided in Google account telemetry.", "error")
                return redirect(url_for("login_page"))

            # Whitelist Verification Check
            primary_admin = (app.config.get("ADMIN_EMAIL") or "jpmclaug@gmail.com").strip().lower()
            allowed_entry = AllowedEmail.get_by_email(email)
            is_primary = (email == primary_admin)

            if not allowed_entry and not is_primary:
                logger.warning(f"Unauthorized login attempt by unwhitelisted account: {email}")
                log_activity(
                    "LOGIN_FAILED",
                    details=f"Access Denied: '{email}' is not on authorized whitelist",
                    email=email,
                )
                return redirect(url_for("access_denied", email=email))

            # Auto-seed primary admin into allowed whitelist if not present
            if is_primary and not allowed_entry:
                allowed_entry = AllowedEmail(
                    email=email,
                    notes="Primary System Administrator",
                    is_admin=True,
                    added_by="System Bootstrap",
                )
                db.session.add(allowed_entry)
                db.session.commit()

            # Find or register User record
            user = User.query.filter(db.func.lower(User.email) == email).first()
            if not user:
                user = User(
                    email=email,
                    name=profile.get("name") or email.split("@")[0],
                    picture=profile.get("picture"),
                    is_admin=bool(is_primary or (allowed_entry and allowed_entry.is_admin)),
                    is_active=True,
                    last_login=datetime.now(timezone.utc),
                )
                db.session.add(user)
                db.session.commit()
            else:
                user.name = profile.get("name") or user.name
                user.picture = profile.get("picture") or user.picture
                user.is_admin = bool(is_primary or (allowed_entry and allowed_entry.is_admin))
                user.last_login = datetime.now(timezone.utc)
                db.session.commit()

            if not user.is_active:
                logger.warning(f"Login attempt by suspended user account: {email}")
                log_activity(
                    "LOGIN_FAILED",
                    details="Access Denied: Account is suspended",
                    user=user,
                    user_id=user.id,
                    email=email,
                )
                return redirect(url_for("access_denied", email=email, suspended="1"))

            # Establish Session
            session["user_id"] = user.id
            session["user_email"] = user.email
            session["is_admin"] = user.is_admin

            log_activity("LOGIN_SUCCESS", details=f"Authenticated via Google OAuth ({user.email})", user=user)
            flash(f"OPERATIONAL // Welcome aboard, {user.name}.", "success")
            return redirect(next_url or url_for("index"))

        except Exception as e:
            logger.error(f"OAuth Callback error: {e}")
            log_activity("LOGIN_FAILED", details=f"Access Denied: OAuth error ({e})")
            flash("Communication failure during authentication handshake.", "error")
            return redirect(url_for("login_page"))

    @app.route("/auth/dev-login", methods=["POST"])
    def dev_login():
        """Development & test mode login route."""
        if not app.config.get("TESTING") and app.config.get("GOOGLE_CLIENT_ID"):
            log_activity("LOGIN_FAILED", details="Access Denied: Direct dev login disabled in production")
            return jsonify({"error": "Direct dev login disabled in production."}), 403

        data = request.get_json(silent=True) or request.form or {}
        email = (data.get("email") or "").strip().lower()
        if not email:
            log_activity("LOGIN_FAILED", details="Access Denied: Email required in dev login")
            return jsonify({"error": "Email required."}), 400

        primary_admin = (app.config.get("ADMIN_EMAIL") or "jpmclaug@gmail.com").strip().lower()
        allowed_entry = AllowedEmail.get_by_email(email)
        is_primary = (email == primary_admin)

        if not allowed_entry and not is_primary:
            log_activity(
                "LOGIN_FAILED",
                details=f"Access Denied: '{email}' is not on authorized whitelist",
                email=email,
            )
            return redirect(url_for("access_denied", email=email)) if not request.is_json else (jsonify({"error": "Email not whitelisted."}), 403)

        user = User.query.filter(db.func.lower(User.email) == email).first()
        if not user:
            user = User(
                email=email,
                name=email.split("@")[0].capitalize(),
                is_admin=bool(is_primary or (allowed_entry and allowed_entry.is_admin)),
                is_active=True,
                last_login=datetime.now(timezone.utc),
            )
            db.session.add(user)
            db.session.commit()
        else:
            user.last_login = datetime.now(timezone.utc)
            user.is_admin = bool(is_primary or (allowed_entry and allowed_entry.is_admin))
            db.session.commit()

        if not user.is_active:
            log_activity(
                "LOGIN_FAILED",
                details="Access Denied: Account is suspended",
                user=user,
                user_id=user.id,
                email=email,
            )
            return redirect(url_for("access_denied", email=email, suspended="1")) if not request.is_json else (jsonify({"error": "Account suspended."}), 403)

        session["user_id"] = user.id
        session["user_email"] = user.email
        session["is_admin"] = user.is_admin

        log_activity("LOGIN_SUCCESS", details=f"Authenticated via Dev Login ({user.email})", user=user)

        if request.is_json:
            return jsonify({"message": f"Authenticated as {user.email}", "user": user.to_dict()})
        return redirect(request.args.get("next") or url_for("index"))

    @app.route("/logout")
    def logout():
        """Clears user session and terminates tactical access."""
        current = get_current_user()
        if current:
            log_activity("LOGOUT", details=f"Session terminated for {current.email}", user=current)
        session.clear()
        flash("SYSTEM LOGOUT // Tactical session terminated.", "info")
        return redirect(url_for("login_page"))

    @app.route("/access-denied")
    def access_denied():
        """Renders tactical access restriction notice."""
        attempted_email = request.args.get("email", "")
        suspended = bool(request.args.get("suspended"))
        admin_contact = app.config.get("ADMIN_EMAIL", "jpmclaug@gmail.com")
        return render_template(
            "access_denied.html",
            attempted_email=attempted_email,
            suspended=suspended,
            admin_contact=admin_contact,
        )

    # ---------------------------------------------------------
    # Admin Management Console Routes
    # ---------------------------------------------------------
    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        """Administrative console for authorized user and whitelist management, security telemetry, and card surveillance."""
        current_admin = get_current_user()
        allowed_emails = AllowedEmail.query.order_by(AllowedEmail.created_at.desc()).all()
        users = User.query.order_by(User.created_at.desc()).all()
        all_cards = WatchlistItem.query.order_by(WatchlistItem.created_at.desc()).all()
        failed_logins = (
            ActivityLog.query.filter_by(action="LOGIN_FAILED")
            .order_by(ActivityLog.created_at.desc())
            .limit(100)
            .all()
        )
        recent_activities = (
            ActivityLog.query.order_by(ActivityLog.created_at.desc())
            .limit(50)
            .all()
        )
        log_activity("PAGE_VIEW", details="Accessed Command Clearance Admin Console", user=current_admin)
        return render_template(
            "admin.html",
            allowed_emails=allowed_emails,
            users=users,
            all_cards=all_cards,
            failed_logins=failed_logins,
            recent_activities=recent_activities,
            total_tracked_cards=len(all_cards),
            active_tab="admin",
        )

    @app.route("/api/admin/whitelist/add", methods=["POST"])
    @admin_required
    def admin_add_whitelist():
        """Authorizes a new Gmail address to access Chimaera."""
        current_admin = get_current_user()
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        notes = (data.get("notes") or "").strip()
        is_admin_grant = bool(data.get("is_admin", False))

        if not email or "@" not in email:
            return jsonify({"error": "A valid email address is required."}), 400

        existing = AllowedEmail.get_by_email(email)
        if existing:
            return jsonify({"error": f"'{email}' is already on the authorized access whitelist."}), 409

        new_allowed = AllowedEmail(
            email=email,
            notes=notes,
            is_admin=is_admin_grant,
            added_by=current_admin.email if current_admin else "Admin",
        )
        db.session.add(new_allowed)

        # If user account already exists, synchronize role
        existing_user = User.query.filter(db.func.lower(User.email) == email).first()
        if existing_user:
            existing_user.is_admin = is_admin_grant
            existing_user.is_active = True

        db.session.commit()
        log_activity("ADMIN_ACTION", details=f"Authorized whitelist email: '{email}' (Admin: {is_admin_grant})", user=current_admin)
        return jsonify({
            "message": f"Authorized '{email}' for Chimaera access.",
            "allowed": new_allowed.to_dict(),
        }), 201

    @app.route("/api/admin/whitelist/delete/<int:item_id>", methods=["POST", "DELETE"])
    @admin_required
    def admin_delete_whitelist(item_id):
        """Revokes access authorization for an email."""
        current_admin = get_current_user()
        entry = db.session.get(AllowedEmail, item_id)
        if not entry:
            return jsonify({"error": "Whitelist record not found."}), 404

        if current_admin and entry.email.lower() == current_admin.email.lower():
            return jsonify({"error": "Action rejected: You cannot revoke your own access clearance."}), 400

        primary_admin = (app.config.get("ADMIN_EMAIL") or "jpmclaug@gmail.com").strip().lower()
        if entry.email.lower() == primary_admin:
            return jsonify({"error": "Action rejected: Primary system administrator cannot be revoked."}), 400

        revoked_email = entry.email
        db.session.delete(entry)

        # Also revoke admin status on User record if exists
        u = User.query.filter(db.func.lower(User.email) == revoked_email.lower()).first()
        if u:
            u.is_admin = False

        db.session.commit()
        log_activity("ADMIN_ACTION", details=f"Revoked access clearance for '{revoked_email}'", user=current_admin)
        return jsonify({"message": f"Revoked authorization for '{revoked_email}'."})

    @app.route("/api/admin/whitelist/toggle-admin/<int:item_id>", methods=["POST"])
    @admin_required
    def admin_toggle_whitelist_admin(item_id):
        """Toggles administrator clearance for an authorized email."""
        current_admin = get_current_user()
        entry = db.session.get(AllowedEmail, item_id)
        if not entry:
            return jsonify({"error": "Whitelist record not found."}), 404

        if current_admin and entry.email.lower() == current_admin.email.lower():
            return jsonify({"error": "Action rejected: You cannot demote your own active admin clearance."}), 400

        entry.is_admin = not entry.is_admin

        u = User.query.filter(db.func.lower(User.email) == entry.email.lower()).first()
        if u:
            u.is_admin = entry.is_admin

        db.session.commit()
        status_label = "ADMINISTRATOR" if entry.is_admin else "OPERATOR"
        log_activity("ADMIN_ACTION", details=f"Updated clearance for '{entry.email}' to {status_label}", user=current_admin)
        return jsonify({
            "message": f"Updated clearance for '{entry.email}' to {status_label}.",
            "is_admin": entry.is_admin,
            "allowed": entry.to_dict(),
        })

    @app.route("/api/admin/users/toggle-status/<int:user_id>", methods=["POST"])
    @admin_required
    def admin_toggle_user_status(user_id):
        """Suspends or activates an existing registered user."""
        current_admin = get_current_user()
        target_user = db.session.get(User, user_id)
        if not target_user:
            return jsonify({"error": "User record not found."}), 404

        if current_admin and target_user.id == current_admin.id:
            return jsonify({"error": "Action rejected: You cannot suspend your own account."}), 400

        target_user.is_active = not target_user.is_active
        db.session.commit()

        status_label = "ACTIVE" if target_user.is_active else "SUSPENDED"
        log_activity("ADMIN_ACTION", details=f"Updated user status for '{target_user.email}' to {status_label}", user=current_admin)
        return jsonify({
            "message": f"User '{target_user.email}' is now {status_label}.",
            "is_active": target_user.is_active,
            "user": target_user.to_dict(),
        })

    @app.route("/api/admin/cards", methods=["GET"])
    @admin_required
    def admin_get_all_cards():
        """Returns JSON list of all watchlist targets across all users."""
        user_id_filter = request.args.get("user_id")
        query = WatchlistItem.query.order_by(WatchlistItem.created_at.desc())
        if user_id_filter:
            try:
                query = query.filter_by(user_id=int(user_id_filter))
            except ValueError:
                pass
        items = query.all()
        return jsonify({
            "total": len(items),
            "cards": [
                {
                    **item.to_dict(),
                    "owner_name": item.user.name or item.user.email.split("@")[0] if item.user else "Unassigned",
                    "owner_email": item.user.email if item.user else "unassigned",
                    "owner_picture": item.user.picture if item.user else None,
                }
                for item in items
            ]
        })

    # ---------------------------------------------------------
    # Core User-Scoped View Routes
    # ---------------------------------------------------------
    @app.route("/system-overview")
    @app.route("/field-manual")
    def system_overview():
        """Tactical Field Manual & Comprehensive System Overview view."""
        user = get_current_user()
        if user:
            log_activity("PAGE_VIEW", details="Accessed Tactical Field Manual & System Overview", user=user)
        return render_template(
            "system_overview.html",
            active_tab="system_overview",
        )

    @app.route("/")
    @login_required
    def index():
        """Wishlist Dashboard view scoped to the authenticated user."""
        user = get_current_user()
        items = WatchlistItem.query.filter_by(user_id=user.id).order_by(WatchlistItem.created_at.desc()).all()

        # Calculate summary KPIs
        total_items = len(items)
        deals_count = sum(1 for item in items if item.is_deal)
        total_target_value = sum(item.target_price for item in items if item.target_price)

        # Total lowest market value
        lowest_market_sum = sum(
            item.lowest_in_stock_price for item in items if item.lowest_in_stock_price is not None
        )

        # Unique active tags
        user_tags = sorted(list({item.tag.strip() for item in items if item.tag and item.tag.strip()}))
        log_activity("PAGE_VIEW", details="Accessed Registry Dashboard", user=user)

        return render_template(
            "index.html",
            items=items,
            total_items=total_items,
            deals_count=deals_count,
            total_target_value=total_target_value,
            lowest_market_sum=lowest_market_sum,
            user_tags=user_tags,
            active_tab="wishlist",
        )

    @app.route("/deals")
    @login_required
    def deals():
        """Dedicated Active Deals view scoped to the authenticated user."""
        user = get_current_user()
        all_items = WatchlistItem.query.filter_by(user_id=user.id).order_by(WatchlistItem.created_at.desc()).all()
        deal_items = [item for item in all_items if item.is_deal]

        total_savings = sum(item.savings_amount for item in deal_items)
        user_tags = sorted(list({item.tag.strip() for item in all_items if item.tag and item.tag.strip()}))
        log_activity("PAGE_VIEW", details="Accessed Priority Deals View", user=user)

        return render_template(
            "deals.html",
            deal_items=deal_items,
            deals_count=len(deal_items),
            total_savings=total_savings,
            user_tags=user_tags,
            active_tab="deals",
        )

    @app.route("/buylist")
    @login_required
    def buylist():
        """Mighty Meeple Live Buylist & Trade-in Scanner view."""
        user = get_current_user()
        items = WatchlistItem.query.filter_by(user_id=user.id).all()
        user_tags = sorted(list({item.tag.strip() for item in items if item.tag and item.tag.strip()}))
        supported_games = mightymeeple_provider.get_supported_games()
        log_activity("PAGE_VIEW", details="Accessed Mighty Meeple Buylist Scanner", user=user)

        return render_template(
            "buylist.html",
            user_tags=user_tags,
            supported_games=supported_games,
            active_tab="buylist",
        )

    @app.route("/microcenter")
    @login_required
    def microcenter():
        """MicroCenter Charlotte Store MTG inventory & price tracking view."""
        user = get_current_user()
        store_id = Config.MICROCENTER_STORE_ID
        store_name = Config.MICROCENTER_STORE_NAME

        # Aggregate statistics
        total_items = MicrocenterItem.query.filter_by(store_id=store_id).count()
        in_stock_items = MicrocenterItem.query.filter_by(store_id=store_id, in_stock=True).count()
        all_items = MicrocenterItem.query.filter_by(store_id=store_id).all()
        deals_count = sum(1 for item in all_items if item.is_deal)
        last_scan_time = SystemSetting.get_val("microcenter_last_scan_time")
        last_scan_status = SystemSetting.get_val("microcenter_last_scan_status")

        log_activity("PAGE_VIEW", details=f"Accessed MicroCenter {store_name} Surveillance Dashboard", user=user)

        return render_template(
            "microcenter.html",
            store_id=store_id,
            store_name=store_name,
            total_items=total_items,
            in_stock_items=in_stock_items,
            deals_count=deals_count,
            last_scan_time=last_scan_time,
            last_scan_status=last_scan_status,
            active_tab="microcenter",
        )

    # ---------------------------------------------------------
    # API Routes: MicroCenter Charlotte Endpoints
    # ---------------------------------------------------------
    @app.route("/api/microcenter/items")
    @login_required
    def microcenter_items():
        """Returns list of tracked MicroCenter Charlotte MTG products with filters and sorting."""
        store_id = (request.args.get("store_id") or Config.MICROCENTER_STORE_ID).strip()
        search_query = (request.args.get("q") or request.args.get("search") or "").strip().lower()
        filter_mode = (request.args.get("filter") or "all").strip().lower()
        sort_by = (request.args.get("sort") or "default").strip().lower()

        query = MicrocenterItem.query.filter_by(store_id=store_id)

        if search_query:
            query = query.filter(
                db.or_(
                    db.func.lower(MicrocenterItem.name).like(f"%{search_query}%"),
                    db.func.lower(MicrocenterItem.sku).like(f"%{search_query}%"),
                )
            )

        if filter_mode == "in_stock":
            query = query.filter(MicrocenterItem.in_stock == True)
        elif filter_mode == "low_stock":
            query = query.filter(
                MicrocenterItem.in_stock == True,
                MicrocenterItem.stock_count.isnot(None),
                MicrocenterItem.stock_count <= 5,
                MicrocenterItem.stock_count > 0,
            )
        elif filter_mode == "out_of_stock":
            query = query.filter(MicrocenterItem.in_stock == False)
        elif filter_mode == "deals":
            pass

        items = query.all()

        if filter_mode == "deals":
            items = [item for item in items if item.is_deal]

        # Sorting
        if sort_by == "price_asc":
            items.sort(key=lambda x: x.current_price)
        elif sort_by == "price_desc":
            items.sort(key=lambda x: x.current_price, reverse=True)
        elif sort_by == "stock_desc":
            items.sort(key=lambda x: (x.in_stock, x.stock_count if x.stock_count is not None else -1), reverse=True)
        elif sort_by == "price_drop":
            items.sort(key=lambda x: x.price_change_amount)
        elif sort_by == "name":
            items.sort(key=lambda x: x.name.lower())
        else:
            # Default: Deals and in-stock first, then by name
            items.sort(key=lambda x: (not x.is_deal, not x.in_stock, x.name.lower()))

        total_tracked = MicrocenterItem.query.filter_by(store_id=store_id).count()
        in_stock_tracked = MicrocenterItem.query.filter_by(store_id=store_id, in_stock=True).count()
        deals_tracked = sum(1 for item in MicrocenterItem.query.filter_by(store_id=store_id).all() if item.is_deal)

        return jsonify({
            "items": [item.to_dict(include_history=False) for item in items],
            "count": len(items),
            "total_tracked": total_tracked,
            "in_stock_tracked": in_stock_tracked,
            "deals_tracked": deals_tracked,
            "last_scan_time": SystemSetting.get_val("microcenter_last_scan_time"),
            "last_scan_status": SystemSetting.get_val("microcenter_last_scan_status"),
        })

    @app.route("/api/microcenter/history/<int:item_id>")
    @login_required
    def microcenter_history(item_id: int):
        """Returns full historical price and inventory time series for a specific item."""
        item = MicrocenterItem.query.get_or_404(item_id)
        entries = (
            MicrocenterHistory.query.filter_by(item_id=item.id)
            .order_by(MicrocenterHistory.recorded_at.asc())
            .all()
        )

        return jsonify({
            "item": item.to_dict(include_history=False),
            "history": [h.to_dict() for h in entries],
            "day_over_day": item.get_day_over_day_change(),
        })

    @app.route("/api/microcenter/sync", methods=["POST"])
    @login_required
    def microcenter_sync():
        """Triggers on-demand synchronization of MicroCenter Charlotte store inventory."""
        user = get_current_user()
        try:
            result = deal_engine.sync_microcenter(notify=True)
            log_activity("MICROCENTER_SYNC", details=result.get("message"), user=user)
            return jsonify(result)
        except Exception as e:
            logger.error(f"Manual MicroCenter sync failed: {e}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/microcenter/item/<int:item_id>/update", methods=["POST"])
    @login_required
    def microcenter_item_update(item_id: int):
        """Updates alert configuration and target price for a MicroCenter product."""
        user = get_current_user()
        item = MicrocenterItem.query.get_or_404(item_id)
        data = request.get_json(silent=True) or {}

        if "target_price" in data:
            raw_tp = data.get("target_price")
            if raw_tp is None or str(raw_tp).strip() == "":
                item.target_price = None
            else:
                try:
                    tp = float(raw_tp)
                    item.target_price = max(0.0, tp) if tp > 0 else None
                except (ValueError, TypeError):
                    pass

        if "notify_on_price_change" in data:
            item.notify_on_price_change = bool(data.get("notify_on_price_change"))

        if "notify_on_restock" in data:
            item.notify_on_restock = bool(data.get("notify_on_restock"))

        if "notify_on_low_stock" in data:
            item.notify_on_low_stock = bool(data.get("notify_on_low_stock"))

        db.session.commit()
        log_activity(
            "CARD_UPDATE",
            details=f"Updated alert settings for MicroCenter item: {item.name} (Target: {item.target_price})",
            user=user,
        )
        return jsonify({"success": True, "item": item.to_dict()})

    @app.route("/api/microcenter/changes", methods=["GET"])
    @login_required
    def microcenter_changes():
        """Returns recent day-over-day price and stock change events across all items."""
        store_id = (request.args.get("store_id") or Config.MICROCENTER_STORE_ID).strip()
        limit = min(int(request.args.get("limit", 50)), 100)

        # Query recent history entries with non-zero changes
        history_rows = (
            db.session.query(MicrocenterHistory, MicrocenterItem)
            .join(MicrocenterItem, MicrocenterHistory.item_id == MicrocenterItem.id)
            .filter(MicrocenterItem.store_id == store_id)
            .filter(db.or_(MicrocenterHistory.price_change != 0, MicrocenterHistory.stock_change != 0))
            .order_by(MicrocenterHistory.recorded_at.desc())
            .limit(limit)
            .all()
        )

        events = []
        for hist, item in history_rows:
            events.append({
                "history_id": hist.id,
                "item_id": item.id,
                "sku": item.sku,
                "name": item.name,
                "image_url": item.image_url,
                "product_url": item.product_url,
                "price": hist.price,
                "price_change": hist.price_change,
                "price_change_percent": round((hist.price_change / (hist.price - hist.price_change)) * 100.0, 1) if (hist.price - hist.price_change) > 0 else 0.0,
                "stock_count": hist.stock_count,
                "stock_change": hist.stock_change,
                "stock_text": hist.stock_text,
                "in_stock": hist.in_stock,
                "recorded_at": hist.recorded_at.isoformat() if hist.recorded_at else None,
            })

        return jsonify({"events": events, "count": len(events)})

    # ---------------------------------------------------------
    # API Routes: Mighty Meeple Buylist Endpoints
    # ---------------------------------------------------------
    @app.route("/api/buylist/search")
    @login_required
    def buylist_search():
        """Searches Mighty Meeple buylist catalog for single card names."""
        query = (request.args.get("q") or request.args.get("keyword") or "").strip()
        if not query:
            return jsonify({"items": [], "total": 0})

        set_name = (request.args.get("set_name") or "").strip() or None
        game = (request.args.get("game") or "mtg").strip()
        try:
            limit = int(request.args.get("limit", 20))
        except (ValueError, TypeError):
            limit = 20
        try:
            offset = int(request.args.get("offset", 0))
        except (ValueError, TypeError):
            offset = 0

        res = mightymeeple_provider.search_buylist(
            query=query,
            set_name=set_name,
            game=game,
            limit=limit,
            offset=offset,
        )
        return jsonify(res)

    @app.route("/api/buylist/bulk", methods=["POST"])
    @login_required
    def buylist_bulk():
        """
        Processes a bulk manifest of card names against the Mighty Meeple buylist.
        Defaults to Lightly Played condition and Store Credit payout.
        """
        user = get_current_user()
        data = request.get_json(silent=True) or {}
        raw_input = data.get("cards") or data.get("raw_input") or ""
        card_names = data.get("card_names")

        if not card_names:
            if isinstance(raw_input, list):
                card_names = raw_input
            else:
                card_names = parse_bulk_card_names(str(raw_input))

        if not card_names:
            return jsonify({"error": "No valid card names found in payload."}), 400

        condition = (data.get("condition") or "Lightly Played").strip()
        payout = (data.get("payout") or "credit").strip().lower()
        finish = (data.get("finish") or "nonfoil").strip().lower()
        game = (data.get("game") or "mtg").strip()

        result = mightymeeple_provider.bulk_buylist_lookup(
            card_names=card_names,
            default_condition=condition,
            default_payout=payout,
            finish=finish,
            game=game,
        )

        log_activity(
            "BUYLIST_LOOKUP",
            details=f"Evaluated {len(card_names)} cards on buylist ({condition}, {payout.upper()})",
            user=user,
        )

        return jsonify(result)

    @app.route("/api/buylist/sets")
    @login_required
    def buylist_sets():
        """Returns valid buylist set names for the requested game."""
        game = (request.args.get("game") or "mtg").strip()
        sets = mightymeeple_provider.get_buylist_sets(game=game)
        return jsonify({"sets": sets})

    @app.route("/api/buylist/games")
    @login_required
    def buylist_games():
        """Returns supported TCG ecosystems."""
        games = mightymeeple_provider.get_supported_games()
        return jsonify({"games": games})

    # ---------------------------------------------------------
    # API Routes: Scryfall Lookups
    # ---------------------------------------------------------
    @app.route("/api/scryfall/autocomplete")
    @login_required
    def scryfall_autocomplete():
        """Card name autocomplete."""
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"suggestions": []})
        suggestions = scryfall_provider.autocomplete(query)
        return jsonify({"suggestions": suggestions})

    @app.route("/api/scryfall/prints")
    @login_required
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
    @login_required
    def add_card():
        """Add a card to the authenticated user's watchlist and run initial price check."""
        user = get_current_user()
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        is_any_version = data.get("is_any_version", False)
        set_code = (data.get("set_code") or "").strip().upper() or None
        collector_number = (data.get("collector_number") or "").strip() or None
        scryfall_id = (data.get("scryfall_id") or "").strip() or None
        image_uri = (data.get("image_uri") or "").strip() or None
        finish = (data.get("finish") or "nonfoil").strip().lower()
        target_price_raw = data.get("target_price")
        notify_mm_stock = bool(data.get("notify_mm_stock", True))
        tag = (data.get("tag") or "").strip() or None

        if not name:
            return jsonify({"error": "Card name is required."}), 400

        # If tracking Any Version, clear set_code and collector_number
        if is_any_version or not set_code or set_code == "ANY":
            set_code = None
            collector_number = None

        # Check for duplicates within user's own watchlist
        if set_code is None:
            existing = WatchlistItem.query.filter(
                WatchlistItem.user_id == user.id,
                WatchlistItem.name.ilike(name),
                (WatchlistItem.set_code == None) | (WatchlistItem.set_code == "") | (WatchlistItem.set_code == "ANY"),
                WatchlistItem.finish == finish,
            ).first()
            if existing:
                return jsonify({"error": f"'{name}' (Any Version, {finish.capitalize()}) is already on your watchlist."}), 409
        else:
            existing = WatchlistItem.query.filter(
                WatchlistItem.user_id == user.id,
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
            user_id=user.id,
            name=name,
            scryfall_id=scryfall_id,
            set_code=set_code,
            collector_number=collector_number,
            image_uri=image_uri,
            finish=finish,
            target_price=target_price,
            notify_mm_stock=notify_mm_stock,
            tag=tag,
        )
        db.session.add(new_item)
        db.session.commit()

        # Run initial price poll across all providers
        deal_engine.poll_card(new_item, notify=True)

        version_desc = "Any Version" if set_code is None else f"{set_code} #{collector_number or '?'}"
        log_activity("CARD_ADD", details=f"Registered target '{name}' ({version_desc})", user=user)
        return jsonify({
            "message": f"Successfully added {name} ({version_desc}) to watchlist.",
            "card": new_item.to_dict(),
        }), 201

    @app.route("/api/watchlist/bulk-add", methods=["POST"])
    @login_required
    def bulk_add_cards():
        """
        Bulk adds multiple cards to the authenticated user's watchlist from a semicolon-separated list.
        """
        user = get_current_user()
        data = request.get_json(silent=True) or {}
        raw_input = data.get("cards") or data.get("raw_input") or ""
        card_names = data.get("card_names")
        if not card_names:
            if isinstance(raw_input, list):
                card_names = raw_input
            else:
                card_names = parse_bulk_card_names(str(raw_input))

        if not card_names:
            return jsonify({"error": "No valid card names provided in payload."}), 400

        finish = (data.get("finish") or "nonfoil").strip().lower()
        notify_mm_stock = bool(data.get("notify_mm_stock", True))
        target_strategy = (data.get("target_strategy") or "none").strip().lower()
        custom_target_price = data.get("target_price")
        tag = (data.get("tag") or "").strip() or None

        # Step 1: Batch resolve metadata via Scryfall collection endpoint
        found_map, not_found = scryfall_provider.get_cards_collection(card_names)

        # Fetch user's existing watchlist items for duplicate detection
        existing_items = WatchlistItem.query.filter_by(user_id=user.id).all()
        existing_any_set = {
            item.name.lower()
            for item in existing_items
            if item.is_any_version and (item.finish or "nonfoil").lower() == finish
        }

        added_items = []
        skipped_items = []
        failed_items = []

        for name in card_names:
            card_info = found_map.get(name.lower())
            canonical_name = card_info.get("name") if card_info else name
            c_lower = canonical_name.lower()

            # Check duplicate in user watchlist
            if c_lower in existing_any_set:
                skipped_items.append({
                    "name": canonical_name,
                    "reason": f"'{canonical_name}' ({finish.capitalize()}) is already on your watchlist."
                })
                continue

            if not card_info:
                failed_items.append({
                    "name": name,
                    "reason": "Card not identified in Scryfall database."
                })
                continue

            # Calculate target price based on selected strategy
            target_price = None
            if target_strategy == "custom" and custom_target_price is not None and str(custom_target_price).strip() != "":
                try:
                    target_price = round(float(custom_target_price), 2)
                except (ValueError, TypeError):
                    target_price = None
            elif target_strategy in ("good", "good_deal", "good_deal_10", "great", "great_deal", "great_deal_20"):
                prices = card_info.get("prices", {})
                p_val = None
                if finish == "foil":
                    p_val = prices.get("usd_foil") or prices.get("usd_etched") or prices.get("usd")
                elif finish == "etched":
                    p_val = prices.get("usd_etched") or prices.get("usd_foil") or prices.get("usd")
                else:
                    p_val = prices.get("usd") or prices.get("usd_foil")
                
                if p_val:
                    try:
                        num = float(p_val)
                        if num > 0:
                            discount = 0.80 if "great" in target_strategy else 0.90
                            target_price = round(num * discount, 2)
                    except (ValueError, TypeError):
                        target_price = None

            new_item = WatchlistItem(
                user_id=user.id,
                name=canonical_name,
                scryfall_id=card_info.get("id"),
                set_code=None,  # Any Version by default for bulk import
                collector_number=None,
                image_uri=card_info.get("image_uri"),
                finish=finish,
                target_price=target_price,
                notify_mm_stock=notify_mm_stock,
                tag=tag,
            )
            db.session.add(new_item)
            added_items.append(new_item)
            existing_any_set.add(c_lower)

        if added_items:
            db.session.commit()

            # Run initial price poll across all providers for newly added items
            for item in added_items:
                try:
                    deal_engine.poll_card(item, notify=True)
                except Exception as e:
                    logger.error(f"Error polling new bulk item {item.name}: {e}")

        summary_msg = f"Successfully registered {len(added_items)} targets ({len(skipped_items)} skipped, {len(failed_items)} unresolved)."
        log_activity("BULK_CARD_ADD", details=f"Batch import: {len(added_items)} registered, {len(skipped_items)} skipped, {len(failed_items)} failed", user=user)

        return jsonify({
            "message": summary_msg,
            "total_requested": len(card_names),
            "added_count": len(added_items),
            "skipped_count": len(skipped_items),
            "failed_count": len(failed_items),
            "added": [item.to_dict() for item in added_items],
            "skipped": skipped_items,
            "failed": failed_items,
        }), 201 if added_items else 200

    @app.route("/api/watchlist/toggle-mm-alert/<int:item_id>", methods=["POST"])
    @login_required
    def toggle_mm_alert(item_id):
        """Toggle Mighty Meeple stock alert for a specific card."""
        user = get_current_user()
        item = db.session.get(WatchlistItem, item_id)
        if not item or item.user_id != user.id:
            return jsonify({"error": "Card not found in your target registry."}), 404

        data = request.get_json(silent=True) or {}
        if "notify_mm_stock" in data:
            item.notify_mm_stock = bool(data["notify_mm_stock"])
        else:
            item.notify_mm_stock = not bool(item.notify_mm_stock if item.notify_mm_stock is not None else True)

        db.session.commit()
        status_str = "ENABLED" if item.notify_mm_stock else "DISABLED"
        log_activity("CARD_UPDATE", details=f"Toggled MM alert to {status_str} for '{item.name}'", user=user)
        return jsonify({
            "message": f"Mighty Meeple in-stock alert {status_str} for {item.name}.",
            "notify_mm_stock": item.notify_mm_stock,
            "card": item.to_dict(),
        })

    @app.route("/api/watchlist/update-scope/<int:item_id>", methods=["POST"])
    @login_required
    def update_card_scope(item_id):
        """Update a card's scope between Any Version (general) and a specific print."""
        user = get_current_user()
        item = db.session.get(WatchlistItem, item_id)
        if not item or item.user_id != user.id:
            return jsonify({"error": "Card not found in your target registry."}), 404

        data = request.get_json(silent=True) or {}
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
        log_activity("CARD_UPDATE", details=f"Updated scope for '{item.name}' to {scope_label}", user=user)
        return jsonify({
            "message": f"Updated tracking scope for {item.name} to {scope_label}.",
            "card": item.to_dict(),
        })

    @app.route("/api/watchlist/update-target/<int:item_id>", methods=["POST"])
    @login_required
    def update_target_price(item_id):
        """Update target price for a card."""
        user = get_current_user()
        item = db.session.get(WatchlistItem, item_id)
        if not item or item.user_id != user.id:
            return jsonify({"error": "Card not found in your target registry."}), 404

        data = request.get_json(silent=True) or {}
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

        if "tag" in data:
            raw_tag = (data.get("tag") or "").strip()
            item.tag = raw_tag or None

        db.session.commit()
        log_activity("CARD_UPDATE", details=f"Updated target price to ${item.target_price or 'None'} for '{item.name}'", user=user)
        return jsonify({
            "message": "Target configuration committed.",
            "card": item.to_dict(),
        })

    @app.route("/api/watchlist/update-tag/<int:item_id>", methods=["POST"])
    @login_required
    def update_card_tag(item_id):
        """Update tag/category for a specific card."""
        user = get_current_user()
        item = db.session.get(WatchlistItem, item_id)
        if not item or item.user_id != user.id:
            return jsonify({"error": "Card not found in your target registry."}), 404

        data = request.get_json(silent=True) or {}
        raw_tag = (data.get("tag") or "").strip()
        item.tag = raw_tag or None
        db.session.commit()

        tag_label = f"'{item.tag}'" if item.tag else "removed"
        log_activity("CARD_UPDATE", details=f"Updated tag to {tag_label} for '{item.name}'", user=user)
        return jsonify({
            "message": f"Target tag updated to {tag_label} for {item.name}.",
            "tag": item.tag,
            "card": item.to_dict(),
        })

    @app.route("/api/watchlist/tags", methods=["GET"])
    @login_required
    def get_watchlist_tags():
        """Returns distinct list of tags used across the authenticated user's watchlist."""
        user = get_current_user()
        items = WatchlistItem.query.filter_by(user_id=user.id).all()
        tags = sorted(list({item.tag.strip() for item in items if item.tag and item.tag.strip()}))
        return jsonify({"tags": tags})

    @app.route("/api/watchlist/refresh/<int:item_id>", methods=["POST"])
    @login_required
    def refresh_card_price(item_id):
        """Manually trigger price refresh for a specific card."""
        user = get_current_user()
        item = db.session.get(WatchlistItem, item_id)
        if not item or item.user_id != user.id:
            return jsonify({"error": "Card not found in your target registry."}), 404

        result = deal_engine.poll_card(item, notify=True)
        log_activity("PRICE_REFRESH", details=f"Price refreshed for '{item.name}'", user=user)
        return jsonify({
            "message": f"Refreshed prices for {item.name}.",
            "card": item.to_dict(),
        })

    @app.route("/api/watchlist/refresh-all", methods=["POST"])
    @login_required
    def refresh_all_cards():
        """Manually refresh all cards on the user's watchlist."""
        user = get_current_user()
        items = WatchlistItem.query.filter_by(user_id=user.id).all()
        results = deal_engine.poll_user_cards(items, notify=True)
        log_activity("PRICE_REFRESH", details=f"Refreshed all {len(results)} targets", user=user)
        return jsonify({
            "message": f"Successfully refreshed {len(results)} cards.",
            "count": len(results),
        })

    @app.route("/api/watchlist/delete/<int:item_id>", methods=["DELETE", "POST"])
    @login_required
    def delete_card(item_id):
        """Remove card and its associated vendor prices."""
        user = get_current_user()
        item = db.session.get(WatchlistItem, item_id)
        if not item or item.user_id != user.id:
            return jsonify({"error": "Card not found in your target registry."}), 404

        card_name = item.name
        db.session.delete(item)
        db.session.commit()

        log_activity("CARD_DELETE", details=f"Removed target '{card_name}'", user=user)
        return jsonify({
            "message": f"Removed {card_name} from watchlist.",
            "deleted_id": item_id,
        })

    @app.route("/api/card/price-intel")
    @login_required
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

    @app.route("/api/user/settings", methods=["GET"])
    @login_required
    def get_user_settings():
        """Returns the authenticated user's profile and notification settings."""
        user = get_current_user()
        return jsonify({
            "user": user.to_dict(),
            "discord_webhook_url": user.discord_webhook_url,
            "global_discord_webhook_set": bool(Config.DISCORD_WEBHOOK_URL),
        })

    @app.route("/api/user/settings/webhook", methods=["POST"])
    @login_required
    def update_user_discord_webhook():
        """Updates or clears the authenticated user's private Discord Webhook URL."""
        user = get_current_user()
        data = request.get_json(silent=True) or {}
        raw_url = data.get("discord_webhook_url")

        if raw_url is not None and str(raw_url).strip() != "":
            valid, clean_url_or_err = User.validate_discord_webhook_url(raw_url)
            if not valid:
                return jsonify({"error": clean_url_or_err}), 400
            user.discord_webhook_url = clean_url_or_err
        else:
            user.discord_webhook_url = None

        db.session.commit()
        log_activity("SETTINGS_UPDATE", details="Updated Discord Webhook configuration", user=user)
        return jsonify({
            "message": "Discord webhook configuration saved successfully.",
            "discord_webhook_url": user.discord_webhook_url,
            "user": user.to_dict(),
        }), 200

    @app.route("/api/discord/test", methods=["POST"])
    @login_required
    def test_discord_webhook():
        """Test Discord webhook integration with optional candidate URL."""
        user = get_current_user()
        data = request.get_json(silent=True) or {}
        target_url = data.get("webhook_url") or data.get("discord_webhook_url")

        success, msg = deal_engine.send_test_discord_notification(webhook_url=target_url, user=user)
        if success:
            return jsonify({"message": msg}), 200
        return jsonify({"error": msg}), 400

    @app.route("/api/settings/telemetry")
    @login_required
    def get_telemetry():
        """Returns current surveillance cadence, worker heartbeat, and telemetry status."""
        user = get_current_user()
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
            "user_discord_webhook_url": user.discord_webhook_url if user else None,
            "global_discord_webhook_set": bool(Config.DISCORD_WEBHOOK_URL),
        })

    @app.route("/api/settings/cadence", methods=["POST"])
    @login_required
    def update_cadence():
        """Updates surveillance cadence interval, auto-refresh toggle, and alert preferences."""
        data = request.get_json(silent=True) or {}
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
    @login_required
    def update_ebay_preference():
        """Updates global default eBay link navigation mode."""
        data = request.get_json(silent=True) or {}
        mode = "search" if str(data.get("ebay_link_mode", "")).lower() == "search" else "direct"
        SystemSetting.set_val("ebay_link_mode", mode)
        return jsonify({
            "message": f"eBay default navigation updated to {'Direct Lowest Listing' if mode == 'direct' else 'Search Results Page'}.",
            "ebay_link_mode": mode,
        })

    @app.route("/api/worker/trigger", methods=["POST"])
    @login_required
    def trigger_worker_poll():
        """Signals the standalone worker via database flag and runs manual price poll."""
        SystemSetting.set_val("manual_poll_requested", "true")
        results = deal_engine.poll_all_cards(notify=True)
        return jsonify({
            "message": f"Surveillance scan completed: {len(results)} targets refreshed.",
            "count": len(results),
        })

    # ---------------------------------------------------------
    # Commander Deck Intelligence & Gemini AI Analysis Routes
    # ---------------------------------------------------------
    @app.route("/deck-analyzer")
    @login_required
    def deck_analyzer_page():
        """Tactical Commander Deck Intelligence & Analysis Dashboard."""
        user = get_current_user()
        has_env_key = bool(app.config.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", "").strip())
        db_key = SystemSetting.get_val("gemini_api_key")
        effective_key = app.config.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", "").strip() or (db_key.strip() if db_key else "")
        has_gemini_key = bool(effective_key)

        available_models = GeminiAnalyzer.get_available_models(effective_key) if has_gemini_key else GEMINI_SUPPORTED_MODELS

        recent_decks = []
        if user:
            recent_decks = DeckAnalysis.query.filter_by(user_id=user.id).order_by(DeckAnalysis.created_at.desc()).limit(20).all()

        log_activity("PAGE_VIEW", details="Accessed Commander Deck Analyzer", user=user)

        return render_template(
            "deck_analyzer.html",
            has_gemini_key=has_gemini_key,
            supported_models=available_models,
            default_model=app.config.get("GEMINI_DEFAULT_MODEL", GEMINI_DEFAULT_MODEL),
            recent_decks=[d.to_dict(include_full=False) for d in recent_decks],
            active_tab="deck_analyzer",
        )

    deck_analyzer = DeckAnalyzer()
    deck_comparator = DeckComparator()

    def _enrich_and_compute_deck_metadata(parsed: dict) -> dict:
        """Helper to batch-enrich parsed deck cards with Scryfall metadata and calculate all tactical stats via DeckAnalyzer."""
        card_names = [c["name"] for c in parsed.get("cards", [])]
        scryfall_map, unresolved = scryfall_provider.get_cards_collection(card_names)

        enriched_cards = []
        for c in parsed.get("cards", []):
            raw_name = c["name"]
            name = re.sub(r"<[^>]+>", "", raw_name).strip()
            qty = c.get("quantity", 1)
            meta = scryfall_map.get(name.lower(), {})

            img_uri = meta.get("image_uri") or meta.get("small_image_uri") or c.get("image_uri")
            small_img = meta.get("small_image_uri") or c.get("small_image_uri") or img_uri
            art_crop = meta.get("art_crop_uri") or img_uri
            price_usd = meta.get("prices", {}).get("usd") or c.get("price_usd")
            card_cmc = meta.get("cmc") if meta.get("cmc") is not None else (c.get("cmc") if c.get("cmc") is not None else 0)

            card_obj = {
                "name": name,
                "quantity": qty,
                "section": c.get("section", "mainboard"),
                "set_code": c.get("set_code") or meta.get("set_code", ""),
                "collector_number": c.get("collector_number") or meta.get("collector_number", ""),
                "image_uri": img_uri,
                "small_image_uri": small_img,
                "art_crop_uri": art_crop,
                "mana_cost": meta.get("mana_cost", "") or c.get("mana_cost", ""),
                "cmc": card_cmc,
                "type_line": meta.get("type_line", "Unknown"),
                "oracle_text": meta.get("oracle_text", ""),
                "colors": meta.get("colors", []),
                "color_identity": meta.get("color_identity", []),
                "rarity": meta.get("rarity", ""),
                "price_usd": price_usd,
                "price_usd_foil": meta.get("prices", {}).get("usd_foil"),
                "tcgplayer_url": meta.get("tcgplayer_url"),
                "card_faces": meta.get("card_faces", []) or c.get("card_faces", []),
                "produced_mana": meta.get("produced_mana", []) or c.get("produced_mana", []),
                "keywords": meta.get("keywords", []) or c.get("keywords", []),
            }
            enriched_cards.append(card_obj)

        # Commander artwork
        commander_art = parsed.get("commander_art")
        if not commander_art:
            for cmd_name in parsed.get("commander", []):
                cmd_meta = scryfall_map.get(cmd_name.lower(), {})
                if cmd_meta.get("art_crop_uri"):
                    commander_art = cmd_meta["art_crop_uri"]
                    break
                elif cmd_meta.get("image_uri"):
                    commander_art = cmd_meta["image_uri"]
                    break

        clean_deck_name = re.sub(r"<[^>]+>", "", parsed.get("deck_name", "Commander Deck")).strip()

        deck_payload = {
            "deck_name": clean_deck_name or "Commander Deck",
            "commander": [re.sub(r"<[^>]+>", "", c).strip() for c in parsed.get("commander", [])],
            "commander_art": commander_art,
            "cards": enriched_cards,
            "source_type": parsed.get("source_type", "text"),
            "raw_text": parsed.get("raw_text", ""),
        }

        analyzed = deck_analyzer.analyze(deck_payload)

        return {
            "deck_name": analyzed["deck_name"],
            "commander": analyzed["commander"],
            "commander_art": commander_art,
            "cards": analyzed["cards"],
            "total_cards": analyzed["total_cards"],
            "source_type": parsed.get("source_type", "text"),
            "raw_text": parsed.get("raw_text", ""),
            "unresolved_cards": unresolved,
            "stats": analyzed["stats"],
            "scryfall_map": scryfall_map,
        }

    @app.route("/api/deck/parse", methods=["POST"])
    @login_required
    def api_deck_parse():
        """Parses deck list or ManaBox URL into structured card format with Scryfall metadata."""
        data = request.get_json(silent=True) or {}
        source = data.get("source", "").strip()
        source_type = data.get("source_type", "auto").strip()

        if not source:
            return jsonify({"error": "No deck source or card list provided."}), 400

        try:
            parsed = DeckParser.parse(source, source_type=source_type)
            enriched = _enrich_and_compute_deck_metadata(parsed)
            return jsonify({"success": True, **enriched})
        except DeckParseError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Unexpected error in api_deck_parse: {e}", exc_info=True)
            return jsonify({"error": f"Failed to parse deck: {str(e)}"}), 500

    @app.route("/api/deck/save", methods=["POST"])
    @login_required
    def api_deck_save():
        """Parses, enriches with Scryfall metadata, and saves a deck directly to the user's Vault without running AI."""
        user = get_current_user()
        data = request.get_json(silent=True) or {}
        source = data.get("source", "").strip()
        source_type = data.get("source_type", "auto").strip()
        provided_deck = data.get("deck_data")

        try:
            if provided_deck and "cards" in provided_deck:
                deck_data = provided_deck
            else:
                if not source:
                    return jsonify({"error": "No deck source provided."}), 400
                parsed = DeckParser.parse(source, source_type=source_type)
                deck_data = _enrich_and_compute_deck_metadata(parsed)

            stats = deck_data.get("stats", {})
            cmdr_name = ", ".join(deck_data.get("commander", [])) or "Commander"
            color_id_str = ",".join(stats.get("color_identity", []))

            import json
            deck_entry = DeckAnalysis(
                user_id=user.id if user else None,
                deck_name=deck_data.get("deck_name", "Commander Deck"),
                commander_name=cmdr_name,
                commander_art=deck_data.get("commander_art"),
                source_url=source if source.startswith("http") else None,
                source_type=deck_data.get("source_type", "text"),
                raw_decklist=deck_data.get("raw_text", ""),
                cards_data=json.dumps(deck_data.get("cards", [])),
                stats_json=json.dumps(stats),
                analysis_json=None,
                total_cards=deck_data.get("total_cards", 100),
                total_value=stats.get("total_value"),
                avg_cmc=stats.get("avg_cmc"),
                color_identity=color_id_str,
            )
            db.session.add(deck_entry)
            db.session.commit()

            log_activity("DECK_SAVE", details=f"Saved Commander deck '{deck_entry.deck_name}' to Vault", user=user)

            return jsonify({
                "success": True,
                "analysis_id": deck_entry.id,
                "deck": deck_entry.to_dict(include_full=True),
            })
        except DeckParseError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error in api_deck_save: {e}", exc_info=True)
            return jsonify({"error": f"Failed to save deck: {str(e)}"}), 500

    @app.route("/api/deck/bulk-import", methods=["POST"])
    @login_required
    def api_deck_bulk_import():
        """Batch-imports multiple ManaBox/Moxfield/Archidekt links into the user's Deck Vault."""
        user = get_current_user()
        data = request.get_json(silent=True) or {}
        raw_text = data.get("text", "").strip()
        urls = data.get("urls", [])

        if raw_text:
            # Split by newlines, commas, or semicolons
            extracted_urls = [u.strip() for u in re.split(r"[\r\n,;]+", raw_text) if u.strip().startswith("http")]
            urls.extend(extracted_urls)

        # Deduplicate URLs
        urls = list(dict.fromkeys(urls))
        if not urls:
            return jsonify({"error": "No valid deck URLs provided. Please paste one or more ManaBox or MTG deck links."}), 400

        imported = []
        failed = []

        import json
        for url in urls:
            try:
                parsed = DeckParser.parse(url, source_type="auto")
                enriched = _enrich_and_compute_deck_metadata(parsed)
                stats = enriched.get("stats", {})
                cmdr_name = ", ".join(enriched.get("commander", [])) or "Unknown Commander"
                color_id_str = ",".join(stats.get("color_identity", []))

                entry = DeckAnalysis(
                    user_id=user.id if user else None,
                    deck_name=enriched.get("deck_name", "Commander Deck"),
                    commander_name=cmdr_name,
                    commander_art=enriched.get("commander_art"),
                    source_url=url,
                    source_type=enriched.get("source_type", "manabox_url"),
                    raw_decklist=enriched.get("raw_text", ""),
                    cards_data=json.dumps(enriched.get("cards", [])),
                    stats_json=json.dumps(stats),
                    total_cards=enriched.get("total_cards", 100),
                    total_value=stats.get("total_value"),
                    avg_cmc=stats.get("avg_cmc"),
                    color_identity=color_id_str,
                )
                db.session.add(entry)
                imported.append(entry)
            except Exception as e:
                logger.warning(f"Failed to import deck URL {url}: {e}")
                failed.append({"url": url, "error": str(e)})

        if imported:
            db.session.commit()
            log_activity("DECK_BULK_IMPORT", details=f"Bulk imported {len(imported)} Commander decks into Vault", user=user)

        return jsonify({
            "success": True,
            "imported_count": len(imported),
            "failed_count": len(failed),
            "imported": [d.to_dict(include_full=False) for d in imported],
            "failed": failed,
        })

    @app.route("/api/deck/<int:deck_id>/sync", methods=["POST"])
    @login_required
    def api_deck_sync(deck_id):
        """Re-fetches and updates a saved deck from its source ManaBox / web URL or refreshes Scryfall pricing and stats."""
        user = get_current_user()
        entry = db.session.get(DeckAnalysis, deck_id)
        if not entry:
            return jsonify({"error": "Saved deck not found."}), 404
        if not user.is_admin and entry.user_id and entry.user_id != user.id:
            return jsonify({"error": "Access denied."}), 403

        try:
            import json
            if entry.source_url:
                parsed = DeckParser.parse(entry.source_url, source_type=entry.source_type or "auto")
            elif entry.raw_decklist:
                parsed = DeckParser.parse(entry.raw_decklist, source_type=entry.source_type or "text")
            elif entry.cards_data:
                cards = entry.get_parsed_cards()
                parsed = {
                    "deck_name": entry.deck_name,
                    "commander": [c.strip() for c in entry.commander_name.split(",") if c.strip()] if entry.commander_name else [],
                    "commander_art": entry.commander_art,
                    "cards": cards,
                    "source_type": entry.source_type or "text",
                    "raw_text": entry.raw_decklist or "",
                }
            else:
                return jsonify({"error": "No decklist or source link available to refresh."}), 400

            enriched = _enrich_and_compute_deck_metadata(parsed)
            stats = enriched.get("stats", {})

            entry.deck_name = enriched.get("deck_name") or entry.deck_name
            entry.commander_name = ", ".join(enriched.get("commander", [])) or entry.commander_name
            if enriched.get("commander_art"):
                entry.commander_art = enriched.get("commander_art")
            entry.cards_data = json.dumps(enriched.get("cards", []))
            entry.stats_json = json.dumps(stats)
            entry.total_cards = enriched.get("total_cards", 100)
            entry.total_value = stats.get("total_value")
            entry.avg_cmc = stats.get("avg_cmc")
            entry.color_identity = ",".join(stats.get("color_identity", []))
            entry.updated_at = utc_now()

            db.session.commit()
            source_desc = entry.source_url if entry.source_url else "decklist"
            log_activity("DECK_SYNC", details=f"Synced deck '{entry.deck_name}' from {source_desc}", user=user)

            return jsonify({
                "success": True,
                "message": f"Successfully refreshed '{entry.deck_name}' with the latest card and pricing data.",
                "deck": entry.to_dict(include_full=True),
            })
        except Exception as e:
            logger.error(f"Error syncing deck {deck_id}: {e}", exc_info=True)
            return jsonify({"error": f"Failed to sync deck: {str(e)}"}), 500

    @app.route("/api/deck/<int:deck_id>/analyze", methods=["POST"])
    @login_required
    def api_deck_analyze_saved(deck_id):
        """Runs Gemini AI analysis on an existing saved deck and saves results in place."""
        user = get_current_user()
        entry = db.session.get(DeckAnalysis, deck_id)
        if not entry:
            return jsonify({"error": "Saved deck not found."}), 404
        if not user.is_admin and entry.user_id and entry.user_id != user.id:
            return jsonify({"error": "Access denied."}), 403

        data = request.get_json(silent=True) or {}
        default_model = SystemSetting.get_val("gemini_default_model") or app.config.get("GEMINI_DEFAULT_MODEL", GEMINI_DEFAULT_MODEL)
        model = data.get("model") or default_model
        user_api_key = data.get("api_key", "").strip()
        custom_instructions = data.get("custom_instructions", "").strip()

        effective_key = user_api_key or SystemSetting.get_val("gemini_api_key") or app.config.get("GEMINI_API_KEY")
        if not effective_key:
            return jsonify({
                "error": "Gemini API key is required. Please set GEMINI_API_KEY or configure your key in the settings modal."
            }), 400

        cards = entry.get_parsed_cards()
        if not cards:
            return jsonify({"error": "Saved deck contains no card entries."}), 400

        try:
            import json
            card_names = [c["name"] for c in cards]
            scryfall_map, _ = scryfall_provider.get_cards_collection(card_names)

            deck_payload = {
                "deck_name": entry.deck_name,
                "commander": [c.strip() for c in entry.commander_name.split(",") if c.strip()] if entry.commander_name else [],
                "cards": cards,
                "total_cards": entry.total_cards,
                "raw_text": entry.raw_decklist or "",
            }

            analyzer = GeminiAnalyzer(api_key=effective_key, model=model)
            analysis_result = analyzer.analyze_deck(
                deck_data=deck_payload,
                scryfall_metadata=scryfall_map,
                custom_instructions=custom_instructions,
            )

            # Enrich ratings and upgrades with Scryfall images & prices
            if "card_ratings" in analysis_result and isinstance(analysis_result["card_ratings"], list):
                for item in analysis_result["card_ratings"]:
                    c_name = item.get("card_name", "")
                    meta = scryfall_map.get(c_name.lower(), {})
                    item["image_uri"] = meta.get("image_uri") or meta.get("small_image_uri")
                    item["small_image_uri"] = meta.get("small_image_uri")
                    item["mana_cost"] = meta.get("mana_cost", "")
                    item["type_line"] = meta.get("type_line", "")
                    item["cmc"] = meta.get("cmc", 0)
                    item["price_usd"] = meta.get("prices", {}).get("usd")
                    item["tcgplayer_url"] = meta.get("tcgplayer_url")

            if "upgrades" in analysis_result and isinstance(analysis_result["upgrades"], list):
                upgrade_names = [u.get("card_in", "") for u in analysis_result["upgrades"] if u.get("card_in")]
                upgrade_names += [u.get("card_out", "") for u in analysis_result["upgrades"] if u.get("card_out")]
                extra_meta, _ = scryfall_provider.get_cards_collection(upgrade_names)

                for u in analysis_result["upgrades"]:
                    card_in = u.get("card_in", "")
                    card_out = u.get("card_out", "")
                    in_meta = extra_meta.get(card_in.lower()) or scryfall_map.get(card_in.lower(), {})
                    out_meta = extra_meta.get(card_out.lower()) or scryfall_map.get(card_out.lower(), {})

                    u["card_in_image"] = in_meta.get("image_uri") or in_meta.get("small_image_uri")
                    u["card_in_price"] = in_meta.get("prices", {}).get("usd")
                    u["card_in_mana"] = in_meta.get("mana_cost", "")
                    u["card_in_type"] = in_meta.get("type_line", "")
                    u["card_in_tcg"] = in_meta.get("tcgplayer_url")

                    u["card_out_image"] = out_meta.get("image_uri") or out_meta.get("small_image_uri")
                    u["card_out_price"] = out_meta.get("prices", {}).get("usd")
                    u["card_out_mana"] = out_meta.get("mana_cost", "")

            # Update entry
            power_level = analysis_result.get("estimated_power_level")
            if power_level:
                try:
                    power_level = float(power_level)
                except Exception:
                    power_level = None

            actual_model = analysis_result.get("_model_used") or analyzer.model or model
            entry.analysis_json = json.dumps(analysis_result)
            entry.model_used = actual_model
            entry.power_level = power_level
            entry.power_bracket = analysis_result.get("power_bracket")
            entry.archetype = analysis_result.get("archetype")
            entry.updated_at = utc_now()
            db.session.commit()

            log_activity("DECK_ANALYSIS", details=f"Analyzed saved Commander deck '{entry.deck_name}' via {actual_model}", user=user)

            return jsonify({
                "success": True,
                "deck": entry.to_dict(include_full=True),
                "analysis": analysis_result,
            })
        except GeminiAnalysisError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error in api_deck_analyze_saved: {e}", exc_info=True)
            return jsonify({"error": f"Analysis execution failed: {str(e)}"}), 500

    @app.route("/api/deck/analyze", methods=["POST"])
    @login_required
    def api_deck_analyze():
        """Runs Google Gemini analysis on a Commander deck source."""
        user = get_current_user()
        data = request.get_json(silent=True) or {}

        source = data.get("source", "").strip()
        source_type = data.get("source_type", "auto").strip()
        default_model = SystemSetting.get_val("gemini_default_model") or app.config.get("GEMINI_DEFAULT_MODEL", GEMINI_DEFAULT_MODEL)
        model = data.get("model") or default_model
        user_api_key = data.get("api_key", "").strip()
        custom_instructions = data.get("custom_instructions", "").strip()
        save_result = data.get("save", True)

        provided_deck = data.get("deck_data")

        # Resolve effective API key
        effective_key = user_api_key or SystemSetting.get_val("gemini_api_key") or app.config.get("GEMINI_API_KEY")
        if not effective_key:
            return jsonify({
                "error": "Gemini API key is required. Please set GEMINI_API_KEY in .env or configure your key in the settings modal."
            }), 400

        try:
            if provided_deck and "cards" in provided_deck:
                deck_data = provided_deck
            else:
                if not source:
                    return jsonify({"error": "No deck source provided for analysis."}), 400
                parsed = DeckParser.parse(source, source_type=source_type)
                deck_data = _enrich_and_compute_deck_metadata(parsed)

            card_names = [c["name"] for c in deck_data.get("cards", [])]
            scryfall_map, _ = scryfall_provider.get_cards_collection(card_names)

            analyzer = GeminiAnalyzer(api_key=effective_key, model=model)
            analysis_result = analyzer.analyze_deck(
                deck_data=deck_data,
                scryfall_metadata=scryfall_map,
                custom_instructions=custom_instructions,
            )

            # Enrich analysis card ratings and upgrades with Scryfall images & prices
            if "card_ratings" in analysis_result and isinstance(analysis_result["card_ratings"], list):
                for item in analysis_result["card_ratings"]:
                    c_name = item.get("card_name", "")
                    meta = scryfall_map.get(c_name.lower(), {})
                    item["image_uri"] = meta.get("image_uri") or meta.get("small_image_uri")
                    item["small_image_uri"] = meta.get("small_image_uri")
                    item["mana_cost"] = meta.get("mana_cost", "")
                    item["type_line"] = meta.get("type_line", "")
                    item["cmc"] = meta.get("cmc", 0)
                    item["price_usd"] = meta.get("prices", {}).get("usd")
                    item["tcgplayer_url"] = meta.get("tcgplayer_url")

            # Enrich upgrade recommendations with Scryfall info
            if "upgrades" in analysis_result and isinstance(analysis_result["upgrades"], list):
                upgrade_names = [u.get("card_in", "") for u in analysis_result["upgrades"] if u.get("card_in")]
                upgrade_names += [u.get("card_out", "") for u in analysis_result["upgrades"] if u.get("card_out")]
                extra_meta, _ = scryfall_provider.get_cards_collection(upgrade_names)

                for u in analysis_result["upgrades"]:
                    card_in = u.get("card_in", "")
                    card_out = u.get("card_out", "")
                    in_meta = extra_meta.get(card_in.lower()) or scryfall_map.get(card_in.lower(), {})
                    out_meta = extra_meta.get(card_out.lower()) or scryfall_map.get(card_out.lower(), {})

                    u["card_in_image"] = in_meta.get("image_uri") or in_meta.get("small_image_uri")
                    u["card_in_price"] = in_meta.get("prices", {}).get("usd")
                    u["card_in_mana"] = in_meta.get("mana_cost", "")
                    u["card_in_type"] = in_meta.get("type_line", "")
                    u["card_in_tcg"] = in_meta.get("tcgplayer_url")

                    u["card_out_image"] = out_meta.get("image_uri") or out_meta.get("small_image_uri")
                    u["card_out_price"] = out_meta.get("prices", {}).get("usd")
                    u["card_out_mana"] = out_meta.get("mana_cost", "")

            # Save to database if requested
            saved_id = None
            if save_result:
                import json
                power_level = analysis_result.get("estimated_power_level")
                if power_level:
                    try:
                        power_level = float(power_level)
                    except Exception:
                        power_level = None

                stats = deck_data.get("stats", {})
                cmdr_name = ", ".join(analysis_result.get("commander", [])) or (deck_data.get("commander", [""])[0])
                color_id_str = ",".join(stats.get("color_identity", []))

                actual_model = analysis_result.get("_model_used") or analyzer.model or model
                deck_entry = DeckAnalysis(
                    user_id=user.id if user else None,
                    deck_name=analysis_result.get("deck_name") or deck_data.get("deck_name", "Commander Deck"),
                    commander_name=cmdr_name,
                    commander_art=deck_data.get("commander_art"),
                    source_url=source if source.startswith("http") else None,
                    source_type=deck_data.get("source_type", "text"),
                    raw_decklist=deck_data.get("raw_text", ""),
                    cards_data=json.dumps(deck_data.get("cards", [])),
                    stats_json=json.dumps(stats),
                    analysis_json=json.dumps(analysis_result),
                    model_used=actual_model,
                    power_level=power_level,
                    power_bracket=analysis_result.get("power_bracket"),
                    archetype=analysis_result.get("archetype"),
                    total_cards=deck_data.get("total_cards", 100),
                    total_value=stats.get("total_value"),
                    avg_cmc=stats.get("avg_cmc"),
                    color_identity=color_id_str,
                )
                db.session.add(deck_entry)
                db.session.commit()
                saved_id = deck_entry.id
                log_activity("DECK_ANALYSIS", details=f"Analyzed Commander deck '{deck_entry.deck_name}' via {actual_model}", user=user)

            return jsonify({
                "success": True,
                "analysis_id": saved_id,
                "deck": deck_data,
                "analysis": analysis_result,
            })

        except GeminiAnalysisError as e:
            return jsonify({"error": str(e)}), 400
        except DeckParseError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error in api_deck_analyze: {e}", exc_info=True)
            return jsonify({"error": f"Analysis execution failed: {str(e)}"}), 500

    @app.route("/api/deck/history", methods=["GET"])
    @login_required
    def api_deck_history():
        """Returns all saved decks with telemetry summary for the active user's Deck Vault."""
        user = get_current_user()
        if not user:
            return jsonify([])

        query = DeckAnalysis.query.filter_by(user_id=user.id) if not user.is_admin else DeckAnalysis.query
        entries = query.order_by(DeckAnalysis.created_at.desc()).limit(100).all()
        return jsonify([e.to_dict(include_full=False) for e in entries])

    @app.route("/api/deck/history/<int:analysis_id>", methods=["GET"])
    @login_required
    def api_deck_get_history(analysis_id):
        """Returns full saved deck details, cards, stats, and AI analysis by ID."""
        user = get_current_user()
        entry = db.session.get(DeckAnalysis, analysis_id)
        if not entry:
            return jsonify({"error": "Saved deck not found."}), 404
        if not user.is_admin and entry.user_id and entry.user_id != user.id:
            return jsonify({"error": "Access denied."}), 403

        return jsonify(entry.to_dict(include_full=True))

    @app.route("/api/deck/history/<int:analysis_id>", methods=["DELETE", "POST"])
    @login_required
    def api_deck_delete_history(analysis_id):
        """Deletes a saved deck from the user's Deck Vault."""
        user = get_current_user()
        entry = db.session.get(DeckAnalysis, analysis_id)
        if not entry:
            return jsonify({"error": "Saved deck not found."}), 404
        if not user.is_admin and entry.user_id and entry.user_id != user.id:
            return jsonify({"error": "Access denied."}), 403

        deck_title = entry.deck_name
        db.session.delete(entry)
        db.session.commit()
        log_activity("DECK_DELETE", details=f"Deleted saved deck '{deck_title}' from Vault", user=user)
        return jsonify({"message": f"Saved deck '{deck_title}' deleted successfully."})

    @app.route("/api/deck/gemini-key", methods=["POST"])
    @login_required
    def api_deck_gemini_key():
        """Tests and saves Gemini API Key into system settings."""
        data = request.get_json(silent=True) or {}
        api_key = str(data.get("api_key", "")).strip()
        default_model = SystemSetting.get_val("gemini_default_model") or app.config.get("GEMINI_DEFAULT_MODEL", GEMINI_DEFAULT_MODEL)
        model = str(data.get("model") or default_model).strip()

        if not api_key:
            return jsonify({"error": "API key cannot be blank."}), 400

        is_valid, msg = GeminiAnalyzer.test_api_key(api_key, model=model)
        if not is_valid:
            return jsonify({"error": msg}), 400

        SystemSetting.set_val("gemini_api_key", api_key)
        if model:
            SystemSetting.set_val("gemini_default_model", model)

        return jsonify({
            "success": True,
            "message": "Gemini API key successfully verified and stored in system settings.",
        })

    @app.route("/api/deck/gemini-status", methods=["GET"])
    @login_required
    def api_deck_gemini_status():
        """Returns current Gemini API key configuration status and supported models."""
        has_env = bool(app.config.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", "").strip())
        db_key = SystemSetting.get_val("gemini_api_key")
        has_db = bool(db_key and db_key.strip())

        key_source = "env" if has_env else ("database" if has_db else "none")
        default_model = SystemSetting.get_val("gemini_default_model") or app.config.get("GEMINI_DEFAULT_MODEL", GEMINI_DEFAULT_MODEL)
        effective_key = app.config.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", "").strip() or (db_key.strip() if db_key else "")
        models = GeminiAnalyzer.get_available_models(effective_key) if (has_env or has_db) else GEMINI_SUPPORTED_MODELS

        return jsonify({
            "has_key": has_env or has_db,
            "key_source": key_source,
            "default_model": default_model,
            "supported_models": models,
        })

    @app.route("/deck-overview")
    @login_required
    def deck_overview_page():
        """Commander Deck Fleet Overview & High-Level Comparison Dashboard."""
        user = get_current_user()
        recent_decks = []
        if user:
            query = DeckAnalysis.query.filter_by(user_id=user.id) if not user.is_admin else DeckAnalysis.query
            recent_decks = query.order_by(DeckAnalysis.created_at.desc()).all()

        # Compute high-level fleet aggregate metrics
        fleet_count = len(recent_decks)
        total_portfolio_value = 0.0
        cmc_sum = 0.0
        cmc_count = 0
        power_sum = 0.0
        power_count = 0
        color_freq = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
        power_brackets = {"Casual (1-4)": 0, "Focused (5-6)": 0, "Optimized (7-8)": 0, "High Power / cEDH (9-10)": 0, "Pre-AI Ready": 0}

        deck_dicts = []
        for d in recent_decks:
            d_dict = d.to_dict(include_full=False)
            deck_dicts.append(d_dict)

            # Value
            val = d.total_value if d.total_value is not None else (d.get_stats().get("total_value", 0.0) if hasattr(d, "get_stats") else 0.0)
            if val is not None:
                total_portfolio_value += float(val)

            # CMC
            cmc = d.avg_cmc if d.avg_cmc is not None else (d.get_stats().get("avg_cmc") if hasattr(d, "get_stats") else None)
            if cmc is not None and float(cmc) > 0:
                cmc_sum += float(cmc)
                cmc_count += 1

            # Power level & bracket
            if d.power_level is not None:
                pwr = float(d.power_level)
                power_sum += pwr
                power_count += 1
                if pwr < 5.0:
                    power_brackets["Casual (1-4)"] += 1
                elif pwr < 7.0:
                    power_brackets["Focused (5-6)"] += 1
                elif pwr < 9.0:
                    power_brackets["Optimized (7-8)"] += 1
                else:
                    power_brackets["High Power / cEDH (9-10)"] += 1
            else:
                power_brackets["Pre-AI Ready"] += 1

            # Colors
            cols = d.get_color_identity_list() if hasattr(d, "get_color_identity_list") else []
            if not cols:
                color_freq["C"] += 1
            else:
                for c in cols:
                    if c in color_freq:
                        color_freq[c] += 1

        avg_fleet_cmc = round(cmc_sum / cmc_count, 2) if cmc_count > 0 else 0.0
        avg_fleet_power = round(power_sum / power_count, 1) if power_count > 0 else None
        avg_deck_value = round(total_portfolio_value / fleet_count, 2) if fleet_count > 0 else 0.0

        fleet_stats = {
            "total_decks": fleet_count,
            "total_portfolio_value": round(total_portfolio_value, 2),
            "avg_deck_value": avg_deck_value,
            "avg_fleet_cmc": avg_fleet_cmc,
            "avg_fleet_power": avg_fleet_power,
            "color_frequencies": color_freq,
            "power_brackets": power_brackets,
        }

        log_activity("PAGE_VIEW", details="Accessed Commander Deck Overview & Comparison", user=user)

        return render_template(
            "deck_overview.html",
            decks=deck_dicts,
            fleet_stats=fleet_stats,
            active_tab="deck_overview",
        )

    @app.route("/api/deck/compare", methods=["POST"])
    @login_required
    def api_deck_compare():
        """Computes comprehensive side-by-side comparison data for 2 to 4 selected Commander decks or raw decklists."""
        user = get_current_user()
        data = request.get_json(silent=True) or {}
        deck_ids = data.get("deck_ids")

        # Check if raw decklists or payload objects were supplied
        deck_a_raw = data.get("deck_a") or data.get("deck_a_text")
        deck_b_raw = data.get("deck_b") or data.get("deck_b_text")
        deck_a_id = data.get("deck_a_id")
        deck_b_id = data.get("deck_b_id")

        if not deck_ids and deck_a_id and deck_b_id:
            deck_ids = [deck_a_id, deck_b_id]

        comparison_decks = []
        deck_card_sets = []
        all_unique_cards = {}

        if deck_ids:
            if not isinstance(deck_ids, list) or len(deck_ids) < 2:
                return jsonify({"error": "Please select at least 2 decks to compare."}), 400
            if len(deck_ids) > 4:
                return jsonify({"error": "You can compare up to 4 decks simultaneously."}), 400

            # Fetch decks
            decks = []
            for did in deck_ids:
                try:
                    did_int = int(did)
                except (ValueError, TypeError):
                    continue
                entry = db.session.get(DeckAnalysis, did_int)
                if entry and (user.is_admin or not entry.user_id or entry.user_id == user.id):
                    decks.append(entry)

            if len(decks) < 2:
                return jsonify({"error": "Could not find at least 2 authorized decks to compare."}), 404

            for d in decks:
                cards = d.get_parsed_cards()
                stats = d.get_stats()
                analysis = d.get_analysis()

                card_names_set = set()
                cards_by_name = {}
                for c in cards:
                    cname = (c.get("name") or "").strip()
                    if cname:
                        cname_key = cname.lower()
                        card_names_set.add(cname_key)
                        cards_by_name[cname_key] = c
                        if cname_key not in all_unique_cards:
                            all_unique_cards[cname_key] = c

                deck_card_sets.append({
                    "id": d.id,
                    "name": d.deck_name,
                    "card_set": card_names_set,
                    "cards_by_name": cards_by_name,
                })

                # Top 5 most expensive cards
                priced_cards = []
                for c in cards:
                    p = c.get("price_usd")
                    if p is not None:
                        try:
                            p_val = float(p)
                            priced_cards.append({
                                "name": c.get("name"),
                                "price": p_val,
                                "type_line": c.get("type_line", ""),
                                "cmc": c.get("cmc", 0),
                                "image_uri": c.get("image_uri") or c.get("small_image_uri"),
                                "tcgplayer_url": c.get("tcgplayer_url"),
                            })
                        except (ValueError, TypeError):
                            pass
                priced_cards.sort(key=lambda x: x["price"], reverse=True)
                top_cards = priced_cards[:5]

                total_cards_count = d.total_cards or sum(c.get("quantity", 1) for c in cards) or 100
                type_counts = stats.get("type_counts", {})
                land_count = type_counts.get("Lands", stats.get("land_count", 0))
                nonland_count = total_cards_count - land_count

                # Heuristic archetype
                archetype = stats.get("archetype") or deck_comparator.determine_archetype(stats)

                deck_item = {
                    "id": d.id,
                    "deck_name": d.deck_name,
                    "commander_name": d.commander_name or "Commander",
                    "commander_art": d.commander_art or (cards[0].get("image_uri") if cards else None),
                    "power_level": d.power_level or (analysis.get("estimated_power_level") if analysis else None),
                    "power_bracket": d.power_bracket or (analysis.get("power_bracket") if analysis else "Pre-AI Ready"),
                    "archetype": archetype,
                    "total_cards": total_cards_count,
                    "land_count": land_count,
                    "nonland_count": nonland_count,
                    "total_value": d.total_value if d.total_value is not None else stats.get("total_value", 0.0),
                    "avg_cmc": d.avg_cmc if d.avg_cmc is not None else stats.get("avg_cmc", 0.0),
                    "color_identity": d.get_color_identity_list(),
                    "cmc_curve": stats.get("cmc_curve", {}),
                    "type_counts": type_counts,
                    "stats": stats,
                    "top_cards": top_cards,
                    "cards": cards,
                    "has_ai_analysis": d.has_ai_analysis,
                    "summary": analysis.get("overall_summary") if analysis else None,
                    "win_conditions": analysis.get("win_conditions") if analysis else [],
                }
                comparison_decks.append(deck_item)

        elif deck_a_raw and deck_b_raw:
            # Parse raw decklists
            def parse_raw_or_payload(raw):
                if isinstance(raw, dict) and "cards" in raw:
                    return _enrich_and_compute_deck_metadata(raw)
                parsed = DeckParser.parse(str(raw), source_type="auto")
                return _enrich_and_compute_deck_metadata(parsed)

            deck_a_meta = parse_raw_or_payload(deck_a_raw)
            deck_b_meta = parse_raw_or_payload(deck_b_raw)

            for idx, d_meta in enumerate([deck_a_meta, deck_b_meta], start=1):
                cards = d_meta.get("cards", [])
                stats = d_meta.get("stats", {})
                card_names_set = set()
                cards_by_name = {}
                for c in cards:
                    cname = (c.get("name") or "").strip()
                    if cname:
                        cname_key = cname.lower()
                        card_names_set.add(cname_key)
                        cards_by_name[cname_key] = c
                        if cname_key not in all_unique_cards:
                            all_unique_cards[cname_key] = c

                deck_card_sets.append({
                    "id": idx,
                    "name": d_meta.get("deck_name", f"Deck {idx}"),
                    "card_set": card_names_set,
                    "cards_by_name": cards_by_name,
                })

                type_counts = stats.get("type_counts", {})
                total_cards_count = d_meta.get("total_cards", len(cards))
                land_count = type_counts.get("Lands", stats.get("land_count", 0))
                nonland_count = total_cards_count - land_count
                archetype = stats.get("archetype") or deck_comparator.determine_archetype(stats)

                deck_item = {
                    "id": idx,
                    "deck_name": d_meta.get("deck_name", f"Deck {idx}"),
                    "commander_name": ", ".join(d_meta.get("commander", [])) or "Commander",
                    "commander_art": d_meta.get("commander_art"),
                    "power_level": None,
                    "power_bracket": "Pre-AI Ready",
                    "archetype": archetype,
                    "total_cards": total_cards_count,
                    "land_count": land_count,
                    "nonland_count": nonland_count,
                    "total_value": stats.get("total_value", 0.0),
                    "avg_cmc": stats.get("avg_cmc", 0.0),
                    "color_identity": stats.get("color_identity", []),
                    "cmc_curve": stats.get("cmc_curve", {}),
                    "type_counts": type_counts,
                    "stats": stats,
                    "top_cards": [],
                    "cards": cards,
                    "has_ai_analysis": False,
                    "summary": None,
                    "win_conditions": [],
                }
                comparison_decks.append(deck_item)
        else:
            return jsonify({"error": "Please provide either deck_ids or two decklists to compare."}), 400

        # Compute Shared Cards Analysis
        shared_in_all = set.intersection(*[ds["card_set"] for ds in deck_card_sets]) if deck_card_sets else set()
        all_deck_sets = [ds["card_set"] for ds in deck_card_sets]
        shared_in_multiple = set()
        for i in range(len(all_deck_sets)):
            for j in range(i + 1, len(all_deck_sets)):
                shared_in_multiple.update(all_deck_sets[i].intersection(all_deck_sets[j]))

        def format_card_list(names_set):
            res = []
            for n in sorted(list(names_set)):
                card_obj = all_unique_cards.get(n, {})
                price_val = None
                if card_obj.get("price_usd") is not None:
                    try:
                        price_val = float(card_obj.get("price_usd"))
                    except (ValueError, TypeError):
                        pass
                res.append({
                    "name": card_obj.get("name", n.title()),
                    "type_line": card_obj.get("type_line", ""),
                    "cmc": card_obj.get("cmc", 0),
                    "mana_cost": card_obj.get("mana_cost", ""),
                    "price_usd": price_val,
                    "image_uri": card_obj.get("image_uri") or card_obj.get("small_image_uri"),
                })
            return res

        shared_all_list = format_card_list(shared_in_all)
        shared_multiple_list = format_card_list(shared_in_multiple - shared_in_all)

        unique_per_deck = {}
        for ds in deck_card_sets:
            other_sets = [other["card_set"] for other in deck_card_sets if other["id"] != ds["id"]]
            other_union = set.union(*other_sets) if other_sets else set()
            unique_names = ds["card_set"] - other_union
            unique_per_deck[str(ds["id"])] = {
                "count": len(unique_names),
                "sample": format_card_list(unique_names)[:12],
            }

        resp_payload = {
            "success": True,
            "deck_count": len(comparison_decks),
            "decks": comparison_decks,
            "shared_all": shared_all_list,
            "shared_multiple": shared_multiple_list,
            "unique_per_deck": unique_per_deck,
        }

        # If comparing exactly 2 decks, compute complete comparator delta matrix and profiles
        if len(comparison_decks) == 2:
            comp_diff = deck_comparator.compare(
                {"cards": comparison_decks[0].get("cards", []), "stats": comparison_decks[0].get("stats", {})},
                {"cards": comparison_decks[1].get("cards", []), "stats": comparison_decks[1].get("stats", {})},
            )
            resp_payload["delta_matrix"] = comp_diff.get("delta_matrix")
            resp_payload["interaction_profile"] = comp_diff.get("interaction_profile")
            resp_payload["card_advantage_profile"] = comp_diff.get("card_advantage_profile")
            resp_payload["velocity_profile"] = comp_diff.get("velocity_profile")
            resp_payload["archetype_a"] = comp_diff.get("archetype_a")
            resp_payload["archetype_b"] = comp_diff.get("archetype_b")

        return jsonify(resp_payload)

    # ----------------------------------------------------------------------
    # Collection Inventory & Dual-Tier Upgrade Endpoints
    # ----------------------------------------------------------------------

    @app.route("/inventory")
    @login_required
    def inventory_view():
        """User collection and inventory dashboard overview page."""
        user = get_current_user()
        summary = inventory_manager.get_inventory_summary(user.id)
        log_activity("PAGE_VIEW", details="Accessed Collection Inventory Dashboard", user=user)
        return render_template(
            "inventory.html",
            inventory=summary,
            active_tab="inventory",
        )

    @app.route("/api/inventory", methods=["GET"])
    @login_required
    def api_inventory_get():
        """Returns JSON collection telemetry and cards for current user."""
        user = get_current_user()
        deck_id = request.args.get("deck_id", type=int)
        summary = inventory_manager.get_inventory_summary(user.id, current_deck_id=deck_id)
        return jsonify({"success": True, **summary})

    @app.route("/api/inventory/upload", methods=["POST"])
    @login_required
    def api_inventory_upload():
        """Parses ManaBox CSV and updates user's collection in replace or merge mode."""
        user = get_current_user()
        mode = request.form.get("mode", "replace").strip().lower()
        if mode not in ("replace", "merge"):
            mode = "replace"

        csv_content = ""
        if "file" in request.files:
            file_obj = request.files["file"]
            if file_obj and file_obj.filename:
                try:
                    csv_content = file_obj.read().decode("utf-8-sig", errors="replace")
                except Exception as e:
                    return jsonify({"error": f"Failed to read uploaded file: {str(e)}"}), 400
        elif request.is_json:
            data = request.get_json(silent=True) or {}
            csv_content = data.get("csv_content", "")
            mode = data.get("mode", mode)
        else:
            csv_content = request.form.get("csv_content", "")

        if not csv_content or not csv_content.strip():
            return jsonify({"error": "No CSV content or file provided for upload."}), 400

        try:
            parsed = ManaBoxInventoryParser.parse(csv_content)
        except InventoryParseError as e:
            return jsonify({"error": str(e), "errors": []}), 400
        except Exception as e:
            logger.error(f"Error parsing inventory CSV: {e}", exc_info=True)
            return jsonify({"error": f"CSV parse error: {str(e)}", "errors": []}), 400

        valid_cards = parsed.get("valid_cards", [])
        errors = parsed.get("errors", [])

        if not valid_cards:
            return jsonify({
                "error": "No valid cards could be parsed from the CSV file.",
                "errors": errors,
                "total_rows": parsed.get("total_rows", 0),
            }), 400

        import_result = inventory_manager.import_inventory(
            user_id=user.id,
            parsed_cards=valid_cards,
            mode=mode,
        )

        log_activity(
            "INVENTORY_IMPORT",
            details=f"Imported {import_result['added_count'] + import_result['updated_count']} cards ({mode} mode) with {len(errors)} row errors",
            user=user,
        )

        return jsonify({
            "success": True,
            **import_result,
            "errors": errors,
            "errors_count": len(errors),
            "parsed_rows": parsed.get("total_rows", 0),
        })

    @app.route("/api/inventory/card/<int:card_id>", methods=["DELETE"])
    @login_required
    def api_inventory_delete_card(card_id: int):
        """Deletes a single card from the user's inventory."""
        user = get_current_user()
        card = db.session.get(UserInventoryCard, card_id)
        if not card or card.user_id != user.id:
            return jsonify({"error": "Card not found in your inventory."}), 404

        card_name = card.name
        db.session.delete(card)
        db.session.commit()
        log_activity("INVENTORY_DELETE", details=f"Removed '{card_name}' from collection", user=user)
        return jsonify({"success": True, "message": f"Removed '{card_name}' from your collection."})

    @app.route("/api/inventory/clear", methods=["POST"])
    @login_required
    def api_inventory_clear():
        """Clears all inventory cards for the current user."""
        user = get_current_user()
        count = UserInventoryCard.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        log_activity("INVENTORY_CLEAR", details=f"Cleared {count} items from collection", user=user)
        return jsonify({"success": True, "message": f"Successfully cleared {count} collection entries."})

    @app.route("/api/deck/<int:deck_id>/upgrades", methods=["GET"])
    @login_required
    def api_deck_upgrades(deck_id: int):
        """Generates dual-tier upgrade recommendations: Owned Swaps ('In Your Binder') & Shopping List ('To Buy')."""
        user = get_current_user()
        entry = db.session.get(DeckAnalysis, deck_id)
        if not entry:
            return jsonify({"error": "Deck not found."}), 404

        if not user.is_admin and entry.user_id and entry.user_id != user.id:
            return jsonify({"error": "Unauthorized access to deck."}), 403

        user_cards = UserInventoryCard.query.filter_by(user_id=user.id).all()
        allocations = inventory_manager.get_user_card_allocations(user.id, current_deck_id=deck_id)
        ai_analysis = entry.get_analysis()

        results = upgrade_engine.generate_upgrades(
            deck=entry,
            user_inventory=user_cards,
            allocations=allocations,
            ai_analysis=ai_analysis,
        )

        return jsonify({"success": True, "deck_id": deck_id, "deck_name": entry.deck_name, **results})

    @app.route("/api/deck/<int:deck_id>/apply-swap", methods=["POST"])
    @login_required
    def api_deck_apply_swap(deck_id: int):
        """Applies a proposed card swap: cuts card_out from deck, slots in card_in, and recalculates stats."""
        user = get_current_user()
        entry = db.session.get(DeckAnalysis, deck_id)
        if not entry:
            return jsonify({"error": "Deck not found."}), 404

        if not user.is_admin and entry.user_id and entry.user_id != user.id:
            return jsonify({"error": "Unauthorized to edit this deck."}), 403

        data = request.get_json(silent=True) or {}
        card_out_name = data.get("card_out", "").strip()
        card_in_name = data.get("card_in", "").strip()

        if not card_out_name or not card_in_name:
            return jsonify({"error": "Both 'card_out' and 'card_in' card names are required."}), 400

        current_cards = entry.get_parsed_cards()
        if not current_cards:
            return jsonify({"error": "Deck contains no cards."}), 400

        # Helper for bidirectional DFC and exact name matching
        def _match_name(name_a: str, name_b: str) -> bool:
            a = (name_a or "").strip().lower()
            b = (name_b or "").strip().lower()
            if a == b:
                return True
            a_front = a.split(" // ")[0].strip() if " // " in a else a
            b_front = b.split(" // ")[0].strip() if " // " in b else b
            return a_front == b_front

        # Locate card_out in deck
        found_idx = -1
        for idx, c in enumerate(current_cards):
            if _match_name(c.get("name", ""), card_out_name):
                found_idx = idx
                break

        if found_idx == -1:
            return jsonify({"error": f"Card to cut '{card_out_name}' was not found in the active deck."}), 404

        # Cut card_out (decrement or remove)
        out_card = current_cards[found_idx]
        out_qty = out_card.get("quantity", 1)
        out_section = out_card.get("section", "mainboard")

        if out_qty > 1:
            out_card["quantity"] = out_qty - 1
        else:
            current_cards.pop(found_idx)

        # Add card_in (or increment if already slotted)
        target_in_lower = card_in_name.lower().strip()
        already_in_idx = -1
        for idx, c in enumerate(current_cards):
            if _match_name(c.get("name", ""), card_in_name):
                already_in_idx = idx
                break

        if already_in_idx >= 0:
            current_cards[already_in_idx]["quantity"] = current_cards[already_in_idx].get("quantity", 1) + 1
        else:
            # Fetch metadata for card_in from user collection first or Scryfall
            inv_card = UserInventoryCard.query.filter(
                UserInventoryCard.user_id == user.id,
                db.or_(
                    db.func.lower(UserInventoryCard.name) == target_in_lower,
                    db.func.lower(UserInventoryCard.name).like(f"{target_in_lower.split(' // ')[0].strip()}%")
                )
            ).first()

            meta_map, _ = scryfall_provider.get_cards_collection([card_in_name])
            meta = meta_map.get(target_in_lower, {})
            if not meta and " // " in card_in_name:
                meta = meta_map.get(card_in_name.split(" // ")[0].lower().strip(), {})

            img = (
                (inv_card.image_uri if inv_card else None)
                or meta.get("image_uri")
                or meta.get("small_image_uri")
            )

            new_card_obj = {
                "name": (inv_card.name if inv_card else None) or meta.get("name") or card_in_name,
                "quantity": 1,
                "section": out_section,
                "set_code": (inv_card.set_code if inv_card else None) or meta.get("set_code", ""),
                "collector_number": (inv_card.collector_number if inv_card else None) or meta.get("collector_number", ""),
                "image_uri": img,
                "small_image_uri": meta.get("small_image_uri") or img,
                "art_crop_uri": meta.get("art_crop_uri") or img,
                "mana_cost": (inv_card.mana_cost if inv_card else None) or meta.get("mana_cost", ""),
                "cmc": inv_card.cmc if (inv_card and inv_card.cmc is not None) else meta.get("cmc", 0),
                "type_line": (inv_card.type_line if inv_card else None) or meta.get("type_line", "Unknown"),
                "oracle_text": (inv_card.oracle_text if inv_card else None) or meta.get("oracle_text", ""),
                "colors": meta.get("colors", []),
                "color_identity": inv_card.get_color_identity_list() if inv_card else meta.get("color_identity", []),
                "rarity": (inv_card.rarity if inv_card else None) or meta.get("rarity", ""),
                "price_usd": inv_card.price_usd if (inv_card and inv_card.price_usd is not None) else meta.get("prices", {}).get("usd"),
                "tcgplayer_url": meta.get("tcgplayer_url"),
            }
            current_cards.append(new_card_obj)

        # Recalculate deck telemetry using DeckAnalyzer
        cmdrs = [c.strip() for c in (entry.commander_name or "").split(",") if c.strip()]
        analyzed_deck = deck_analyzer.analyze({
            "deck_name": entry.deck_name,
            "commander": cmdrs,
            "cards": current_cards,
        })

        import json
        entry.cards_data = json.dumps(analyzed_deck.get("cards", current_cards))
        entry.stats_json = json.dumps(analyzed_deck.get("stats", {}))
        entry.total_cards = analyzed_deck.get("total_cards", sum(c.get("quantity", 1) for c in current_cards))
        entry.total_value = analyzed_deck.get("stats", {}).get("total_value", entry.total_value)
        entry.avg_cmc = analyzed_deck.get("stats", {}).get("avg_cmc", entry.avg_cmc)
        entry.updated_at = utc_now()
        db.session.commit()

        log_activity(
            "DECK_SWAP",
            details=f"Swapped '{card_out_name}' for '{card_in_name}' in deck '{entry.deck_name}'",
            user=user,
        )

        return jsonify({
            "success": True,
            "message": f"Successfully replaced {card_out_name} with {card_in_name}!",
            "deck": entry.to_dict(include_full=True),
            "swapped": {
                "card_out": card_out_name,
                "card_in": card_in_name,
            },
        })

    @app.route("/api/deck/<int:deck_id>/wishlist/export", methods=["POST", "GET"])
    @login_required
    def api_deck_wishlist_export(deck_id: int):
        """Generates ManaBox-compatible Wishlist export for external acquisitions."""
        user = get_current_user()
        entry = db.session.get(DeckAnalysis, deck_id)
        if not entry:
            return jsonify({"error": "Deck not found."}), 404

        data = request.get_json(silent=True) or {}
        format_type = (data.get("format") or request.args.get("format") or "csv").lower()

        user_cards = UserInventoryCard.query.filter_by(user_id=user.id).all()
        allocations = inventory_manager.get_user_card_allocations(user.id, current_deck_id=deck_id)
        ai_analysis = entry.get_analysis()

        results = upgrade_engine.generate_upgrades(
            deck=entry,
            user_inventory=user_cards,
            allocations=allocations,
            ai_analysis=ai_analysis,
        )

        acquisitions = results.get("all_shopping_cards", [])
        export_content = DualTierUpgradeEngine.generate_manabox_wishlist_export(acquisitions, format_type=format_type)

        safe_deck_name = re.sub(r"[^a-zA-Z0-9_-]", "_", entry.deck_name)
        filename = f"{safe_deck_name}_ManaBox_Wishlist.{ 'csv' if format_type == 'csv' else 'txt' }"

        from flask import Response
        mimetype = "text/csv" if format_type == "csv" else "text/plain"
        return Response(
            export_content,
            mimetype=mimetype,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"Starting Chimaera on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
