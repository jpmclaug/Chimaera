import logging
from datetime import datetime, timezone
import requests
from config import Config
from models import db, WatchlistItem, VendorPrice, SystemSetting
from providers import ScryfallProvider, MightyMeepleProvider, EbayProvider

logger = logging.getLogger(__name__)


class DealEngine:
    """Core price aggregation and deal evaluation engine for Chimaera."""

    def __init__(self, app=None):
        self.app = app
        self.scryfall = ScryfallProvider()
        self.mightymeeple = MightyMeepleProvider()
        self.ebay = EbayProvider()

    def poll_card(self, item: WatchlistItem, notify: bool = True) -> dict:
        """
        Polls all 3 providers for a single WatchlistItem, upserts VendorPrice records,
        and dispatches a Discord deal alert if a new deal threshold is met.
        """
        is_any = item.is_any_version
        version_label = "Any Version" if is_any else (item.set_code or "N/A")
        logger.info(f"Polling prices for: {item.name} ({version_label}, {item.finish})")

        # 1. Scryfall / TCGplayer
        if is_any:
            tcg_data = self.scryfall.get_cheapest_tcgplayer_price(item.name, finish=item.finish)
        else:
            tcg_data = self.scryfall.get_tcgplayer_price(item.scryfall_id, finish=item.finish)
        ref_price = tcg_data.get("price") if tcg_data and tcg_data.get("in_stock") else None

        # 2. Mighty Meeple
        mm_data = self.mightymeeple.search_card(
            card_name=item.name,
            set_code=None if is_any else item.set_code,
            finish=item.finish,
        )

        # 3. eBay
        ebay_data = self.ebay.search_card(
            card_name=item.name,
            set_code=None if is_any else item.set_code,
            finish=item.finish,
            reference_price=ref_price,
        )

        now = datetime.now(timezone.utc)
        results = [tcg_data, mm_data, ebay_data]

        for vendor_data in results:
            if not vendor_data:
                continue

            v_name = vendor_data.get("vendor_name")
            existing = (
                VendorPrice.query.filter_by(
                    watchlist_id=item.id,
                    vendor_name=v_name,
                ).first()
            )

            if existing:
                existing.price = float(vendor_data.get("price", 0.0))
                existing.condition = vendor_data.get("condition", "NM")
                existing.in_stock = bool(vendor_data.get("in_stock", True))
                existing.product_url = vendor_data.get("product_url")
                existing.last_checked = now
            else:
                new_vp = VendorPrice(
                    watchlist_id=item.id,
                    vendor_name=v_name,
                    price=float(vendor_data.get("price", 0.0)),
                    condition=vendor_data.get("condition", "NM"),
                    in_stock=bool(vendor_data.get("in_stock", True)),
                    product_url=vendor_data.get("product_url"),
                    last_checked=now,
                )
                db.session.add(new_vp)

        db.session.commit()

        # Check for deal & send notification
        if notify and item.is_deal:
            best = item.best_vendor
            if best:
                self.send_discord_deal_alert(
                    item=item,
                    best_vendor=best,
                    savings_amount=item.savings_amount,
                    savings_percent=item.savings_percent,
                )

        return {
            "card_id": item.id,
            "card_name": item.name,
            "is_deal": item.is_deal,
            "lowest_price": item.lowest_in_stock_price,
            "vendors": [v.to_dict() for v in item.vendor_prices],
        }

    def poll_all_cards(self, notify: bool = True) -> list[dict]:
        """Polls prices for all cards currently in the database."""
        items = WatchlistItem.query.all()
        summary = []
        logger.info(f"Starting scheduled poll for {len(items)} watchlist items...")

        for item in items:
            try:
                res = self.poll_card(item, notify=notify)
                summary.append(res)
            except Exception as e:
                logger.error(f"Error polling card {item.name} (ID: {item.id}): {e}")

        deals_found = sum(1 for s in summary if s.get("is_deal"))
        now = datetime.now(timezone.utc)
        try:
            SystemSetting.set_val("last_poll_time", now.isoformat())
            SystemSetting.set_val("last_poll_count", len(summary))
            SystemSetting.set_val("last_poll_deals", deals_found)
            SystemSetting.set_val(
                "last_poll_status",
                f"Surveillance cycle complete: {len(summary)} targets monitored, {deals_found} active deals triggered."
            )
        except Exception as e:
            logger.debug(f"Could not persist telemetry to SystemSetting: {e}")

        logger.info(f"Completed poll for all watchlist items ({len(summary)} scanned, {deals_found} deals).")
        return summary

    def send_discord_deal_alert(
        self,
        item: WatchlistItem,
        best_vendor: VendorPrice,
        savings_amount: float,
        savings_percent: float,
    ) -> bool:
        """Dispatches a rich Discord Webhook embed for an active deal."""
        webhook_url = Config.DISCORD_WEBHOOK_URL
        if not webhook_url:
            logger.debug("Discord Webhook URL not configured; skipping deal alert.")
            return False

        try:
            # Build multi-vendor price comparison string
            comparison_lines = []
            for vp in item.vendor_prices:
                stock_tag = "✓ In Stock" if vp.in_stock else "✗ Out of Stock"
                price_tag = f"${vp.price:.2f}" if vp.price > 0 else "N/A"
                prefix = "★ **" if vp.id == best_vendor.id else ""
                suffix = "** (Best Deal)" if vp.id == best_vendor.id else ""
                comparison_lines.append(f"{prefix}{vp.vendor_name}: {price_tag} [{stock_tag}] ({vp.condition}){suffix}")

            comparison_text = "\n".join(comparison_lines) if comparison_lines else "No vendor data"

            fields = [
                {
                    "name": "Target Price",
                    "value": f"${item.target_price:.2f}" if item.target_price else "Not set",
                    "inline": True,
                },
                {
                    "name": "Deal Price",
                    "value": f"**${best_vendor.price:.2f}** ({best_vendor.vendor_name})",
                    "inline": True,
                },
                {
                    "name": "Savings",
                    "value": f"**${savings_amount:.2f}** ({savings_percent:.1f}%)",
                    "inline": True,
                },
                {
                    "name": "Live Price Comparison",
                    "value": comparison_text,
                    "inline": False,
                },
            ]

            if best_vendor.product_url:
                fields.append({
                    "name": "Direct Purchase",
                    "value": f"[Click here to buy on {best_vendor.vendor_name}]({best_vendor.product_url})",
                    "inline": False,
                })

            set_label = "Any Version" if item.is_any_version else (item.set_code or "Unknown")
            embed = {
                "title": f"🚨 Priority Deal: {item.name}",
                "description": f"**Set:** {set_label} | **Finish:** {item.finish.capitalize()}",
                "color": 0xDC143C,  # Tactical Crimson
                "fields": fields,
                "footer": {
                    "text": "Chimaera MTG Tactical Intelligence",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if item.image_uri:
                embed["thumbnail"] = {"url": item.image_uri}

            payload = {
                "username": "Chimaera Deal Tracker",
                "embeds": [embed],
            }

            resp = requests.post(webhook_url, json=payload, timeout=8)
            if resp.status_code in (200, 204):
                logger.info(f"Discord deal alert sent successfully for {item.name}")
                return True
            else:
                logger.warning(f"Discord webhook failed with status code {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send Discord webhook deal alert: {e}")

        return False

    def send_test_discord_notification(self) -> tuple[bool, str]:
        """Sends a verification ping to the configured Discord Webhook."""
        webhook_url = Config.DISCORD_WEBHOOK_URL
        if not webhook_url:
            return False, "Discord Webhook URL is not set in configuration."

        try:
            payload = {
                "username": "Chimaera Tactical Intelligence",
                "embeds": [
                    {
                        "title": "🛰️ Chimaera Webhook Telemetry Test",
                        "description": "Imperial tactical market surveillance webhook integration is successfully configured and operational!",
                        "color": 0x008080,  # Sophisticated Teal
                        "fields": [
                            {"name": "Status", "value": "✓ Online & Monitoring", "inline": True},
                            {"name": "Engine", "value": "Chimaera v1.0 // ISD-72", "inline": True},
                        ],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "footer": {"text": "Chimaera MTG Market Surveillance"},
                    }
                ],
            }
            resp = requests.post(webhook_url, json=payload, timeout=8)
            if resp.status_code in (200, 204):
                return True, "Test webhook delivered successfully to Discord!"
            return False, f"Discord returned status {resp.status_code}: {resp.text}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
