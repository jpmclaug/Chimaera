import logging
import os
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
from models import db, User, AllowedEmail, WatchlistItem, VendorPrice, SystemSetting, ActivityLog
from deal_engine import DealEngine
from providers import ScryfallProvider, MightyMeepleProvider

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

    # Initialize Deal Engine, Scryfall provider, and Mighty Meeple provider
    deal_engine = DealEngine(app=app)
    scryfall_provider = ScryfallProvider()
    mightymeeple_provider = MightyMeepleProvider()

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

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"Starting Chimaera on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
