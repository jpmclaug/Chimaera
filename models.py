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

