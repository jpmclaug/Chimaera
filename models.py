from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utc_now():
    """Returns current UTC datetime."""
    return datetime.now(timezone.utc)


class WatchlistItem(db.Model):
    """Magic: The Gathering card monitored on user's watchlist."""

    __tablename__ = "watchlist_item"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), index=True, nullable=False)
    scryfall_id = db.Column(db.String(64), unique=True, nullable=False)
    set_code = db.Column(db.String(10), nullable=True)
    collector_number = db.Column(db.String(20), nullable=True)
    image_uri = db.Column(db.Text, nullable=True)
    finish = db.Column(db.String(20), default="nonfoil")  # nonfoil, foil, etched
    target_price = db.Column(db.Float, nullable=True)
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
            "name": self.name,
            "scryfall_id": self.scryfall_id,
            "set_code": self.set_code,
            "collector_number": self.collector_number,
            "image_uri": self.image_uri,
            "finish": self.finish,
            "target_price": self.target_price,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_deal": self.is_deal,
            "lowest_price": self.lowest_in_stock_price,
            "savings_amount": self.savings_amount,
            "savings_percent": self.savings_percent,
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
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }
