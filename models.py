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
    inventory_gdrive_url = db.Column(db.Text, nullable=True)
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
    deck_analyses = db.relationship(
        "DeckAnalysis",
        backref=db.backref("user", lazy=True),
        cascade="all, delete-orphan",
        lazy=True,
        passive_deletes=True,
    )
    inventory_cards = db.relationship(
        "UserInventoryCard",
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

    @property
    def total_inventory_cards(self) -> int:
        """Returns total quantity count of cards in user's inventory."""
        try:
            return sum(c.quantity for c in self.inventory_cards)
        except Exception:
            return 0

    @property
    def unique_inventory_cards(self) -> int:
        """Returns count of distinct card items in user's inventory."""
        try:
            return len(self.inventory_cards)
        except Exception:
            return 0

    @property
    def total_inventory_value(self) -> float:
        """Returns sum total market value ($ USD) of user's inventory."""
        try:
            total = 0.0
            for c in self.inventory_cards:
                price = c.price_usd_foil if (c.foil and c.foil.lower() in ("foil", "etched") and c.price_usd_foil is not None) else c.price_usd
                if price:
                    total += price * c.quantity
            return round(total, 2)
        except Exception:
            return 0.0

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
            "inventory_gdrive_url": self.inventory_gdrive_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "last_active": last_active.isoformat() if last_active else None,
            "card_count": self.card_count,
            "total_inventory_cards": self.total_inventory_cards,
            "unique_inventory_cards": self.unique_inventory_cards,
            "total_inventory_value": self.total_inventory_value,
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
    notify_on_low_stock = db.Column(db.Boolean, default=True, nullable=False)
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

    @staticmethod
    def clean_name_text(raw_name: str | None) -> str:
        """Strips redundant company/brand prefixes like 'Wizards of the Coast Magic: The Gathering -'."""
        if not raw_name:
            return ""
        name = str(raw_name).strip()
        # Strip common verbose brand prefixes from MicroCenter titles
        prefixes = [
            r"^Wizards\s+of\s+the\s+Coast\s+Magic:\s+The\s+Gathering\s*[-–—:]\s*",
            r"^Wizards\s+of\s+the\s+Coast\s+Magic\s+The\s+Gathering\s*[-–—:]\s*",
            r"^Wizards\s+of\s+the\s+Coast\s*",
            r"^Magic:\s+The\s+Gathering\s*[-–—:]\s*",
            r"^Magic\s+The\s+Gathering\s*[-–—:]\s*",
            r"^MTG\s*[-–—:]\s*",
        ]
        for pattern in prefixes:
            name = re.sub(pattern, "", name, flags=re.IGNORECASE).strip()
        return name

    @property
    def display_name(self) -> str:
        """Returns clean human-readable product title without verbose brand prefixes."""
        return self.clean_name_text(self.name) or self.name

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
            "display_name": self.display_name,
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
            "notify_on_low_stock": self.notify_on_low_stock,
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


class DeckAnalysis(db.Model):
    """Saved Commander deck analysis generated by Gemini AI with Scryfall metadata and instant stats."""

    __tablename__ = "deck_analysis"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    deck_name = db.Column(db.String(255), nullable=False, default="Commander Deck")
    commander_name = db.Column(db.String(255), nullable=True)
    commander_art = db.Column(db.Text, nullable=True)
    source_url = db.Column(db.Text, nullable=True)
    source_type = db.Column(db.String(50), default="text")
    raw_decklist = db.Column(db.Text, nullable=True)
    cards_data = db.Column(db.Text, nullable=True)  # JSON string of parsed cards + scryfall metadata
    stats_json = db.Column(db.Text, nullable=True)  # JSON string of pre-computed stats (curve, types, value)
    analysis_json = db.Column(db.Text, nullable=True)  # JSON string of Gemini analysis (nullable if pre-AI)
    model_used = db.Column(db.String(100), default="gemini-3.7-flash")
    power_level = db.Column(db.Float, nullable=True)
    power_bracket = db.Column(db.String(50), nullable=True)
    archetype = db.Column(db.String(100), nullable=True)
    total_cards = db.Column(db.Integer, default=100)
    total_value = db.Column(db.Float, nullable=True)
    avg_cmc = db.Column(db.Float, nullable=True)
    color_identity = db.Column(db.String(50), nullable=True)  # Comma-separated e.g. "W,U,B,R,G"
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    @property
    def has_ai_analysis(self) -> bool:
        """Returns True if Gemini analysis has been generated for this deck."""
        return bool(self.analysis_json and self.analysis_json.strip() and self.analysis_json.strip() != "{}")

    @property
    def status(self) -> str:
        """Returns clinical lifecycle status: 'ready', 'stats_only', or 'draft'."""
        if self.has_ai_analysis:
            return "ready"
        stats = self.get_stats()
        if stats and "land_drop_probabilities" in stats:
            return "stats_only"
        return "draft"

    def get_parsed_cards(self) -> list[dict]:
        """Returns deserialized cards_data list."""
        if not self.cards_data:
            return []
        try:
            import json
            return json.loads(self.cards_data)
        except Exception:
            return []

    def get_stats(self) -> dict:
        """Returns deserialized stats dict or calculates it dynamically from cards_data."""
        import json
        if self.stats_json:
            try:
                data = json.loads(self.stats_json)
                if isinstance(data, dict) and "pip_breakdown" in data and "land_drop_probabilities" in data and "mana_sinks" in data:
                    return data
            except Exception:
                pass

        # Calculate stats dynamically using DeckAnalyzer if missing or partial
        cards = self.get_parsed_cards()
        if not cards:
            return {}

        try:
            from deck_analyzer import DeckAnalyzer
            analyzer = DeckAnalyzer()
            cmdrs = [c.strip() for c in (self.commander_name or "").split(",") if c.strip()]
            res = analyzer.analyze({"cards": cards, "deck_name": self.deck_name, "commander": cmdrs})
            computed_stats = res.get("stats", {})
            if computed_stats:
                try:
                    self.stats_json = json.dumps(computed_stats)
                except Exception:
                    pass
            return computed_stats
        except Exception:
            # Fallback basic calculation
            type_counts = {}
            cmc_curve = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
            total_val = 0.0
            total_cmc_val = 0.0
            nonland_count = 0
            colors_set = set()

            for c in cards:
                qty = c.get("quantity", 1)
                t_line = (c.get("type_line") or "").lower()

                primary = "Other"
                if "creature" in t_line:
                    primary = "Creatures"
                elif "instant" in t_line:
                    primary = "Instants"
                elif "sorcery" in t_line:
                    primary = "Sorceries"
                elif "artifact" in t_line:
                    primary = "Artifacts"
                elif "enchantment" in t_line:
                    primary = "Enchantments"
                elif "planeswalker" in t_line:
                    primary = "Planeswalkers"
                elif "land" in t_line:
                    primary = "Lands"
                elif "battle" in t_line:
                    primary = "Battles"
                type_counts[primary] = type_counts.get(primary, 0) + qty

                if "land" not in t_line:
                    cmc = float(c.get("cmc", 0))
                    total_cmc_val += (cmc * qty)
                    nonland_count += qty
                    cmc_key = "7+" if cmc >= 7 else str(int(cmc))
                    cmc_curve[cmc_key] = cmc_curve.get(cmc_key, 0) + qty

                price_usd = c.get("price_usd")
                if price_usd:
                    try:
                        total_val += float(price_usd) * qty
                    except Exception:
                        pass

                for col in c.get("color_identity", []):
                    colors_set.add(col)

            return {
                "total_value": round(total_val, 2),
                "avg_cmc": round(total_cmc_val / nonland_count, 2) if nonland_count > 0 else 0.0,
                "type_counts": type_counts,
                "cmc_curve": cmc_curve,
                "color_identity": sorted(list(colors_set)),
            }

    def get_color_identity_list(self) -> list[str]:
        """Returns list of color identity letters e.g. ['W', 'U', 'B']."""
        if self.color_identity:
            return [c.strip() for c in self.color_identity.split(",") if c.strip()]
        stats = self.get_stats()
        return stats.get("color_identity", [])

    def get_analysis(self) -> dict:
        """Returns deserialized Gemini analysis dict."""
        if not self.analysis_json:
            return {}
        try:
            import json
            return json.loads(self.analysis_json)
        except Exception:
            return {}

    def to_dict(self, include_full: bool = True):
        """Serializes deck record into a dict for JSON API responses."""
        stats = self.get_stats()
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "deck_name": self.deck_name,
            "commander_name": self.commander_name,
            "commander_art": self.commander_art,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "model_used": self.model_used,
            "power_level": self.power_level,
            "power_bracket": self.power_bracket,
            "archetype": self.archetype,
            "total_cards": self.total_cards,
            "total_value": self.total_value if self.total_value is not None else stats.get("total_value", 0.0),
            "avg_cmc": self.avg_cmc if self.avg_cmc is not None else stats.get("avg_cmc", 0.0),
            "color_identity": self.get_color_identity_list(),
            "has_ai_analysis": self.has_ai_analysis,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "stats": stats,
        }
        if include_full:
            data["raw_decklist"] = self.raw_decklist
            data["cards_data"] = self.get_parsed_cards()
            data["analysis"] = self.get_analysis()
        return data


class UserInventoryCard(db.Model):
    """Magic: The Gathering card owned by user in their collection/inventory (e.g. imported from ManaBox)."""

    __tablename__ = "user_inventory_card"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(255), index=True, nullable=False)
    raw_name = db.Column(db.String(255), nullable=True)
    set_code = db.Column(db.String(20), index=True, nullable=True)
    set_name = db.Column(db.String(255), nullable=True)
    collector_number = db.Column(db.String(50), nullable=True)
    scryfall_id = db.Column(db.String(64), index=True, nullable=True)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    foil = db.Column(db.String(30), default="normal", nullable=False)
    condition = db.Column(db.String(50), nullable=True)
    language = db.Column(db.String(20), default="en", nullable=True)
    purchase_price = db.Column(db.Float, nullable=True)
    binder_name = db.Column(db.String(255), nullable=True)
    rarity = db.Column(db.String(50), nullable=True)
    mana_cost = db.Column(db.String(100), nullable=True)
    cmc = db.Column(db.Float, nullable=True)
    type_line = db.Column(db.String(255), nullable=True)
    oracle_text = db.Column(db.Text, nullable=True)
    color_identity = db.Column(db.String(50), nullable=True)
    image_uri = db.Column(db.Text, nullable=True)
    price_usd = db.Column(db.Float, nullable=True)
    price_usd_foil = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    def get_color_identity_list(self) -> list[str]:
        """Returns list of color identity letters e.g. ['W', 'U', 'B']."""
        if self.color_identity:
            return [c.strip() for c in self.color_identity.split(",") if c.strip()]
        return []

    def to_dict(self, include_metadata: bool = True) -> dict:
        """Serializes collection card record into a dict."""
        effective_price = (
            self.price_usd_foil
            if (self.foil and self.foil.lower() in ("foil", "etched") and self.price_usd_foil is not None)
            else self.price_usd
        )
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "raw_name": self.raw_name or self.name,
            "set_code": self.set_code or "",
            "set_name": self.set_name or "",
            "collector_number": self.collector_number or "",
            "scryfall_id": self.scryfall_id,
            "quantity": self.quantity,
            "foil": self.foil or "normal",
            "condition": self.condition or "Near Mint",
            "language": self.language or "en",
            "purchase_price": self.purchase_price,
            "binder_name": self.binder_name or "",
            "rarity": self.rarity or "",
            "mana_cost": self.mana_cost or "",
            "cmc": self.cmc or 0.0,
            "type_line": self.type_line or "",
            "color_identity": self.get_color_identity_list(),
            "image_uri": self.image_uri,
            "price_usd": self.price_usd,
            "price_usd_foil": self.price_usd_foil,
            "effective_price": effective_price,
            "total_value": round((effective_price or 0.0) * self.quantity, 2),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_metadata:
            data["oracle_text"] = self.oracle_text or ""
        return data


class EDHRECCache(db.Model):
    """Local cache for EDHREC JSON API payloads with 24-hour TTL."""

    __tablename__ = "edhrec_cache"

    id = db.Column(db.Integer, primary_key=True)
    cache_key = db.Column(db.String(255), unique=True, index=True, nullable=False)
    data_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    expires_at = db.Column(db.DateTime, index=True, nullable=False)

    @classmethod
    def get_cached(cls, cache_key: str) -> dict | None:
        """Retrieves and deserializes unexpired cached data, or None if expired/missing."""
        import json
        try:
            now = utc_now()
            entry = cls.query.filter(cls.cache_key == cache_key).first()
            if not entry:
                return None
            exp = entry.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < now:
                return None
            return json.loads(entry.data_json)
        except Exception:
            return None

    @classmethod
    def set_cached(cls, cache_key: str, data: dict, ttl_hours: int = 24) -> None:
        """Stores or updates cached data with TTL in hours."""
        import json
        try:
            now = utc_now()
            expires = now + timedelta(hours=ttl_hours)
            entry = cls.query.filter_by(cache_key=cache_key).first()
            if not entry:
                entry = cls(
                    cache_key=cache_key,
                    data_json=json.dumps(data),
                    created_at=now,
                    expires_at=expires,
                )
                db.session.add(entry)
            else:
                entry.data_json = json.dumps(data)
                entry.created_at = now
                entry.expires_at = expires
            db.session.commit()
        except Exception:
            db.session.rollback()

    @classmethod
    def clear_expired(cls) -> int:
        """Deletes expired cache records from the database."""
        try:
            now = utc_now()
            deleted = cls.query.filter(cls.expires_at < now).delete(synchronize_session=False)
            db.session.commit()
            return deleted
        except Exception:
            db.session.rollback()
            return 0
