import re
from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utc_now():
    """Returns current UTC datetime."""
    return datetime.now(timezone.utc)


class User(db.Model):
    """Registered user account authenticated via Google OAuth."""

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    name = db.Column(db.String(255), nullable=True)
    picture = db.Column(db.Text, nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    discord_webhook_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    last_login = db.Column(db.DateTime, nullable=True)

    # Relationships
    watchlist_items = db.relationship(
        "WatchlistItem",
        backref=db.backref("user", lazy=True),
        cascade="all, delete-orphan",
        lazy=True,
        passive_deletes=True,
    )

    DISCORD_WEBHOOK_REGEX = re.compile(
        r"^https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+/?$"
    )

    @staticmethod
    def validate_discord_webhook_url(url: str | None) -> tuple[bool, str]:
        """
        Validates whether a URL is a valid, secure Discord webhook endpoint.
        Returns (is_valid, sanitized_url_or_error_message).
        """
        if not url or not str(url).strip():
            return True, ""
        clean = str(url).strip()
        if not User.DISCORD_WEBHOOK_REGEX.match(clean):
            return False, "Invalid Discord webhook URL format. Expected: https://discord.com/api/webhooks/<id>/<token>"
        return True, clean

    @property
    def card_count(self):
        """Returns total count of cards monitored by this user."""
        return len(self.watchlist_items)

    def get_usage_past_week(self, days: int = 7) -> int:
        """Returns count of tool activity events for this user in the past N days."""
        try:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            return ActivityLog.query.filter(
                ActivityLog.user_id == self.id,
                ActivityLog.created_at >= since,
            ).count()
        except Exception:
            return 0

    def get_last_active(self):
        """Returns the most recent tool activity timestamp for this user."""
        try:
            latest = (
                ActivityLog.query.filter_by(user_id=self.id)
                .order_by(ActivityLog.created_at.desc())
                .first()
            )
            if latest and latest.created_at:
                return latest.created_at
        except Exception:
            pass
        return self.last_login

    def to_dict(self):
        """Serializes user record into a dict."""
        last_active = self.get_last_active()
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name or self.email.split("@")[0],
            "picture": self.picture,
            "is_admin": self.is_admin,
            "is_active": self.is_active,
            "discord_webhook_url": self.discord_webhook_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "last_active": last_active.isoformat() if last_active else None,
            "card_count": self.card_count,
            "usage_past_week": self.get_usage_past_week(),
        }


class AllowedEmail(db.Model):
    """Whitelist of authorized Gmail addresses permitted to access Chimaera."""

    __tablename__ = "allowed_email"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    notes = db.Column(db.String(255), nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    added_by = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    @classmethod
    def is_allowed(cls, email: str) -> bool:
        """Returns True if the email is on the authorized whitelist."""
        if not email:
            return False
        clean = email.strip().lower()
        return cls.query.filter(db.func.lower(cls.email) == clean).first() is not None

    @classmethod
    def get_by_email(cls, email: str) -> "AllowedEmail | None":
        """Retrieves AllowedEmail entry by email address."""
        if not email:
            return None
        clean = email.strip().lower()
        return cls.query.filter(db.func.lower(cls.email) == clean).first()

    def to_dict(self):
        """Serializes allowed email record into a dict."""
        return {
            "id": self.id,
            "email": self.email,
            "notes": self.notes or "",
            "is_admin": self.is_admin,
            "added_by": self.added_by or "System",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WatchlistItem(db.Model):
    """Magic: The Gathering card monitored on user's watchlist."""

    __tablename__ = "watchlist_item"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(255), index=True, nullable=False)
    scryfall_id = db.Column(db.String(64), nullable=True)
    set_code = db.Column(db.String(10), nullable=True)
    collector_number = db.Column(db.String(20), nullable=True)
    image_uri = db.Column(db.Text, nullable=True)
    finish = db.Column(db.String(20), default="nonfoil")  # nonfoil, foil, etched
    target_price = db.Column(db.Float, nullable=True)
    notify_mm_stock = db.Column(db.Boolean, default=True, nullable=False)
    tag = db.Column(db.String(100), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    # Relationships
    vendor_prices = db.relationship(
        "VendorPrice",
        backref=db.backref("watchlist_item", lazy=True),
        cascade="all, delete-orphan",
        lazy=True,
        passive_deletes=True,
    )

    @property
    def is_any_version(self):
        """Returns True if this card monitors any version / set."""
        return not self.set_code or self.set_code.strip().upper() in ("ANY", "")

    @is_any_version.setter
    def is_any_version(self, value):
        if value:
            self.set_code = None
            self.collector_number = None

    @property
    def in_stock_prices(self):
        """Returns list of vendor prices that are currently in stock."""
        return [vp for vp in self.vendor_prices if vp.in_stock and vp.price > 0]

    @property
    def lowest_in_stock_price(self):
        """Returns lowest live in-stock price across all vendors, or None."""
        in_stock = self.in_stock_prices
        if not in_stock:
            return None
        return min(vp.price for vp in in_stock)

    @property
    def best_vendor(self):
        """Returns the VendorPrice object with the lowest in-stock price."""
        in_stock = self.in_stock_prices
        if not in_stock:
            return None
        return min(in_stock, key=lambda vp: vp.price)

    @property
    def is_deal(self):
        """Returns True if any in-stock vendor price is <= target_price."""
        if self.target_price is None or self.target_price <= 0:
            return False
        lowest = self.lowest_in_stock_price
        return lowest is not None and lowest <= self.target_price

    @property
    def savings_amount(self):
        """Returns monetary difference below target price."""
        if not self.is_deal:
            return 0.0
        return max(0.0, round(self.target_price - self.lowest_in_stock_price, 2))

    @property
    def savings_percent(self):
        """Returns percentage savings below target price."""
        if not self.is_deal or not self.target_price or self.target_price <= 0:
            return 0.0
        diff = self.target_price - self.lowest_in_stock_price
        return max(0.0, round((diff / self.target_price) * 100, 1))

    @property
    def mm_vendor_price(self):
        """Returns Mighty Meeple VendorPrice record or None."""
        return self.get_vendor_price("Mighty Meeple")

    @property
    def mm_in_stock(self):
        """Returns True if Mighty Meeple currently has this card in stock."""
        mm = self.mm_vendor_price
        return bool(mm and mm.in_stock and mm.price > 0)

    @property
    def market_price(self):
        """Returns TCGplayer market reference baseline price or None."""
        tcg = self.get_vendor_price("TCGplayer")
        if tcg and tcg.price > 0:
            return tcg.price
        return None

    @property
    def suggested_good_price(self):
        """Returns target price representing a solid 10% discount from market."""
        mp = self.market_price
        return round(mp * 0.90, 2) if mp and mp > 0 else None

    @property
    def suggested_great_price(self):
        """Returns target price representing a high-value 20% discount from market."""
        mp = self.market_price
        return round(mp * 0.80, 2) if mp and mp > 0 else None

    @property
    def price_rating(self):
        """Evaluates price rating compared to market baseline: Great Deal, Good Deal, Fair Market, Above Market."""
        lowest = self.lowest_in_stock_price
        market = self.market_price
        if not lowest or not market or market <= 0:
            return "Unknown"
        ratio = lowest / market
        if ratio <= 0.80:
            return "Great Deal"
        elif ratio <= 0.92:
            return "Good Deal"
        elif ratio <= 1.05:
            return "Fair Market"
        else:
            return "Above Market"

    def get_vendor_price(self, vendor_name):
        """Helper to get a specific vendor's latest price record."""
        for vp in self.vendor_prices:
            if vp.vendor_name.lower() == vendor_name.lower():
                return vp
        return None

    def to_dict(self):
        """Serializes watchlist item and its vendor prices into a dict."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "scryfall_id": self.scryfall_id,
            "set_code": self.set_code,
            "collector_number": self.collector_number,
            "image_uri": self.image_uri,
            "finish": self.finish,
            "target_price": self.target_price,
            "notify_mm_stock": bool(self.notify_mm_stock if self.notify_mm_stock is not None else True),
            "tag": self.tag,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_any_version": self.is_any_version,
            "is_deal": self.is_deal,
            "lowest_price": self.lowest_in_stock_price,
            "savings_amount": self.savings_amount,
            "savings_percent": self.savings_percent,
            "market_price": self.market_price,
            "suggested_good_price": self.suggested_good_price,
            "suggested_great_price": self.suggested_great_price,
            "price_rating": self.price_rating,
            "mm_in_stock": self.mm_in_stock,
            "vendor_prices": [vp.to_dict() for vp in self.vendor_prices],
        }


class VendorPrice(db.Model):
    """Price and stock status snapshot for a card at a specific vendor."""

    __tablename__ = "vendor_price"

    id = db.Column(db.Integer, primary_key=True)
    watchlist_id = db.Column(
        db.Integer,
        db.ForeignKey("watchlist_item.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor_name = db.Column(db.String(50), nullable=False)  # 'TCGplayer', 'eBay', 'Mighty Meeple'
    price = db.Column(db.Float, nullable=False)
    condition = db.Column(db.String(20), default="NM")
    in_stock = db.Column(db.Boolean, default=True)
    product_url = db.Column(db.Text, nullable=True)
    search_url = db.Column(db.Text, nullable=True)
    last_checked = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        """Serializes vendor price record into a dict."""
        return {
            "id": self.id,
            "watchlist_id": self.watchlist_id,
            "vendor_name": self.vendor_name,
            "price": self.price,
            "condition": self.condition,
            "in_stock": self.in_stock,
            "product_url": self.product_url,
            "search_url": self.search_url or self.product_url,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }


class SystemSetting(db.Model):
    """Global key-value configuration and telemetry store for Chimaera."""

    __tablename__ = "system_setting"

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    @classmethod
    def get_val(cls, key: str, default: str | None = None) -> str | None:
        """Retrieves a setting value string or default if not found."""
        try:
            row = cls.query.filter_by(key=key).first()
            return row.value if row and row.value is not None else default
        except Exception:
            return default

    @classmethod
    def get_float(cls, key: str, default: float = 6.0) -> float:
        """Retrieves a setting value parsed as a float."""
        val = cls.get_val(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
        return default

    @classmethod
    def get_bool(cls, key: str, default: bool = True) -> bool:
        """Retrieves a setting value parsed as a boolean."""
        val = cls.get_val(key)
        if val is not None:
            return val.strip().lower() in ("true", "1", "yes", "on")
        return default

    @classmethod
    def set_val(cls, key: str, value: any) -> "SystemSetting":
        """Upserts a setting key-value pair and commits to the database."""
        row = cls.query.filter_by(key=key).first()
        str_val = str(value) if value is not None else ""
        if not row:
            row = cls(key=key, value=str_val)
            db.session.add(row)
        else:
            row.value = str_val
            row.updated_at = utc_now()
        db.session.commit()
        return row

    def to_dict(self):
        """Serializes setting record into a dict."""
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ActivityLog(db.Model):
    """Activity tracking, tool usage analytics, and authentication security audit log."""

    __tablename__ = "activity_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_email = db.Column(db.String(255), nullable=True, index=True)
    ip_address = db.Column(db.String(64), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False, index=True)  # LOGIN_SUCCESS, LOGIN_FAILED, CARD_ADD, etc.
    endpoint = db.Column(db.String(255), nullable=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref("activity_logs", lazy=True, cascade="all, delete-orphan", passive_deletes=True),
    )

    def to_dict(self):
        """Serializes activity log record into a dict."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "ip_address": self.ip_address,
            "action": self.action,
            "endpoint": self.endpoint,
            "details": self.details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MicrocenterItem(db.Model):
    """Magic: The Gathering product monitored at MicroCenter (Charlotte Store #175)."""

    __tablename__ = "microcenter_item"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), unique=True, index=True, nullable=False)
    product_id = db.Column(db.String(50), index=True, nullable=True)
    name = db.Column(db.String(255), index=True, nullable=False)
    product_url = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.Text, nullable=True)
    current_price = db.Column(db.Float, default=0.0, nullable=False)
    previous_price = db.Column(db.Float, nullable=True)
    original_price = db.Column(db.Float, nullable=True)  # List / strike price if discounted
    in_stock = db.Column(db.Boolean, default=True, nullable=False)
    stock_count = db.Column(db.Integer, nullable=True)  # e.g. 25, 4, 0
    stock_text = db.Column(db.String(100), nullable=True)  # e.g. "25+ IN STOCK at Charlotte Store"
    store_id = db.Column(db.String(20), default="175", nullable=False)  # 175 = Charlotte
    store_name = db.Column(db.String(100), default="Charlotte", nullable=False)
    category = db.Column(db.String(100), default="Tabletop Games / Trading Card Game", nullable=True)
    target_price = db.Column(db.Float, nullable=True)
    notify_on_price_change = db.Column(db.Boolean, default=True, nullable=False)
    notify_on_restock = db.Column(db.Boolean, default=True, nullable=False)
    first_seen_at = db.Column(db.DateTime, default=utc_now)
    last_scanned_at = db.Column(db.DateTime, default=utc_now)
    last_price_change_at = db.Column(db.DateTime, nullable=True)
    last_stock_change_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Relationships
    history_entries = db.relationship(
        "MicrocenterHistory",
        backref=db.backref("item", lazy=True),
        cascade="all, delete-orphan",
        lazy=True,
        passive_deletes=True,
        order_by="MicrocenterHistory.recorded_at.desc()",
    )

    @property
    def price_change_amount(self) -> float:
        """Returns monetary difference from previous price (negative indicates a price drop)."""
        if self.previous_price is not None and self.previous_price > 0:
            return round(self.current_price - self.previous_price, 2)
        return 0.0

    @property
    def price_change_percent(self) -> float:
        """Returns percentage difference from previous price (negative indicates a price drop)."""
        if self.previous_price is not None and self.previous_price > 0:
            diff = self.current_price - self.previous_price
            return round((diff / self.previous_price) * 100.0, 1)
        return 0.0

    @property
    def has_price_dropped(self) -> bool:
        """Returns True if current price is lower than previous price or original price."""
        if self.previous_price is not None and self.current_price < self.previous_price:
            return True
        if self.original_price is not None and self.current_price < self.original_price:
            return True
        return False

    @property
    def savings_from_original(self) -> float:
        """Returns dollar savings compared to original list price if discounted."""
        if self.original_price is not None and self.original_price > self.current_price:
            return round(self.original_price - self.current_price, 2)
        return 0.0

    @property
    def savings_from_original_percent(self) -> float:
        """Returns percentage savings compared to original list price."""
        if self.original_price is not None and self.original_price > 0 and self.original_price > self.current_price:
            diff = self.original_price - self.current_price
            return round((diff / self.original_price) * 100.0, 1)
        return 0.0

    @property
    def is_deal(self) -> bool:
        """Returns True if item meets user target price or has a recorded price drop."""
        if self.target_price is not None and self.target_price > 0:
            return bool(self.in_stock and self.current_price <= self.target_price)
        return self.has_price_dropped

    def get_day_over_day_change(self) -> dict:
        """
        Calculates day-over-day price and stock level differences
        by looking for historical snapshot entries from ~24 hours ago.
        """
        try:
            now = datetime.now(timezone.utc)
            one_day_ago = now - timedelta(hours=24)
            # Find history entry closest to 24h ago
            past_entry = (
                MicrocenterHistory.query.filter(
                    MicrocenterHistory.item_id == self.id,
                    MicrocenterHistory.recorded_at <= one_day_ago,
                )
                .order_by(MicrocenterHistory.recorded_at.desc())
                .first()
            )
            # If no entry >=24h ago, pick the oldest available entry
            if not past_entry:
                past_entry = (
                    MicrocenterHistory.query.filter(MicrocenterHistory.item_id == self.id)
                    .order_by(MicrocenterHistory.recorded_at.asc())
                    .first()
                )

            if past_entry and past_entry.recorded_at:
                past_rec = past_entry.recorded_at
                if past_rec.tzinfo is None:
                    past_rec = past_rec.replace(tzinfo=timezone.utc)
                if (now - past_rec).total_seconds() > 3600:
                    price_delta = round(self.current_price - past_entry.price, 2)
                    stock_delta = 0
                    if self.stock_count is not None and past_entry.stock_count is not None:
                        stock_delta = self.stock_count - past_entry.stock_count
                    return {
                        "has_baseline": True,
                        "baseline_date": past_entry.recorded_at.isoformat(),
                        "baseline_price": past_entry.price,
                        "price_delta": price_delta,
                        "price_delta_percent": round((price_delta / past_entry.price) * 100.0, 1) if past_entry.price > 0 else 0.0,
                        "baseline_stock": past_entry.stock_count,
                        "stock_delta": stock_delta,
                    }
        except Exception:
            pass

        return {
            "has_baseline": False,
            "baseline_date": None,
            "baseline_price": self.current_price,
            "price_delta": 0.0,
            "price_delta_percent": 0.0,
            "baseline_stock": self.stock_count,
            "stock_delta": 0,
        }

    def to_dict(self, include_history: bool = False, history_limit: int = 30) -> dict:
        """Serializes MicroCenter product item into a dict."""
        dod = self.get_day_over_day_change()
        data = {
            "id": self.id,
            "sku": self.sku,
            "product_id": self.product_id,
            "name": self.name,
            "product_url": self.product_url,
            "image_url": self.image_url,
            "current_price": self.current_price,
            "previous_price": self.previous_price,
            "original_price": self.original_price,
            "in_stock": self.in_stock,
            "stock_count": self.stock_count,
            "stock_text": self.stock_text,
            "store_id": self.store_id,
            "store_name": self.store_name,
            "category": self.category,
            "target_price": self.target_price,
            "notify_on_price_change": self.notify_on_price_change,
            "notify_on_restock": self.notify_on_restock,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_scanned_at": self.last_scanned_at.isoformat() if self.last_scanned_at else None,
            "last_price_change_at": self.last_price_change_at.isoformat() if self.last_price_change_at else None,
            "last_stock_change_at": self.last_stock_change_at.isoformat() if self.last_stock_change_at else None,
            "is_active": self.is_active,
            "price_change_amount": self.price_change_amount,
            "price_change_percent": self.price_change_percent,
            "has_price_dropped": self.has_price_dropped,
            "savings_from_original": self.savings_from_original,
            "savings_from_original_percent": self.savings_from_original_percent,
            "is_deal": self.is_deal,
            "day_over_day": dod,
        }
        if include_history:
            entries = self.history_entries[:history_limit]
            data["history"] = [h.to_dict() for h in entries]
        return data


class MicrocenterHistory(db.Model):
    """Historical timestamped snapshot of price and inventory for a MicroCenter product."""

    __tablename__ = "microcenter_history"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(
        db.Integer,
        db.ForeignKey("microcenter_item.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    price = db.Column(db.Float, nullable=False)
    original_price = db.Column(db.Float, nullable=True)
    in_stock = db.Column(db.Boolean, default=True, nullable=False)
    stock_count = db.Column(db.Integer, nullable=True)
    stock_text = db.Column(db.String(100), nullable=True)
    price_change = db.Column(db.Float, default=0.0, nullable=False)
    stock_change = db.Column(db.Integer, default=0, nullable=False)
    recorded_at = db.Column(db.DateTime, default=utc_now, index=True)

    def to_dict(self):
        """Serializes historical snapshot into a dict."""
        return {
            "id": self.id,
            "item_id": self.item_id,
            "price": self.price,
            "original_price": self.original_price,
            "in_stock": self.in_stock,
            "stock_count": self.stock_count,
            "stock_text": self.stock_text,
            "price_change": self.price_change,
            "stock_change": self.stock_change,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }

