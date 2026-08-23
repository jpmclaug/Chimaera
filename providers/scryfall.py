import logging
import urllib.parse
import requests

logger = logging.getLogger(__name__)

SCRYFALL_BASE_URL = "https://api.scryfall.com"
DEFAULT_USER_AGENT = "Chimera-MTGTracker/1.0 (Contact: support@chimera.local)"


class ScryfallProvider:
    """Scryfall API client for MTG card metadata, autocomplete, prints, and TCGplayer pricing."""

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json;q=0.9,*/*;q=0.8",
        })

    def autocomplete(self, query: str) -> list[str]:
        """Returns list of card name suggestions from Scryfall."""
        if not query or len(query.strip()) < 2:
            return []

        try:
            url = f"{SCRYFALL_BASE_URL}/cards/autocomplete"
            response = self.session.get(url, params={"q": query.strip()}, timeout=8)
            if response.status_code == 200:
                data = response.json()
                return data.get("data", [])
            logger.warning(f"Scryfall autocomplete returned status {response.status_code}")
        except Exception as e:
            logger.error(f"Error querying Scryfall autocomplete for '{query}': {e}")
        return []

    def get_card_by_id(self, scryfall_id: str) -> dict | None:
        """Fetches full card object from Scryfall by unique ID."""
        if not scryfall_id:
            return None

        try:
            url = f"{SCRYFALL_BASE_URL}/cards/{scryfall_id}"
            response = self.session.get(url, timeout=8)
            if response.status_code == 200:
                return response.json()
            logger.warning(f"Scryfall card lookup failed for ID {scryfall_id} (status: {response.status_code})")
        except Exception as e:
            logger.error(f"Error fetching card by ID {scryfall_id}: {e}")
        return None

    def search_card_prints(self, card_name: str) -> list[dict]:
        """Fetches all unique prints for a specific card name."""
        if not card_name:
            return []

        try:
            # Exact card search with all prints
            query = f'!"{card_name.strip()}"'
            url = f"{SCRYFALL_BASE_URL}/cards/search"
            response = self.session.get(
                url,
                params={"q": query, "unique": "prints", "order": "released", "dir": "desc"},
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()
                cards = data.get("data", [])
                formatted_prints = []

                for card in cards:
                    # Resolve image
                    image_uri = None
                    if "image_uris" in card and card["image_uris"].get("normal"):
                        image_uri = card["image_uris"]["normal"]
                    elif "card_faces" in card and card["card_faces"]:
                        first_face = card["card_faces"][0]
                        if "image_uris" in first_face and first_face["image_uris"].get("normal"):
                            image_uri = first_face["image_uris"]["normal"]

                    # Determine finishes
                    finishes = card.get("finishes", ["nonfoil"])

                    # Extract price estimates
                    prices = card.get("prices", {})

                    formatted_prints.append({
                        "id": card.get("id"),
                        "name": card.get("name"),
                        "set_code": card.get("set", "").upper(),
                        "set_name": card.get("set_name", ""),
                        "collector_number": card.get("collector_number", ""),
                        "rarity": card.get("rarity", "").capitalize(),
                        "released_at": card.get("released_at", ""),
                        "image_uri": image_uri,
                        "finishes": finishes,
                        "prices": {
                            "usd": prices.get("usd"),
                            "usd_foil": prices.get("usd_foil"),
                            "usd_etched": prices.get("usd_etched"),
                        },
                        "tcgplayer_url": card.get("purchase_uris", {}).get("tcgplayer"),
                    })
                return formatted_prints

            logger.warning(f"Scryfall search prints returned status {response.status_code}")
        except Exception as e:
            logger.error(f"Error searching card prints for '{card_name}': {e}")
        return []

    def get_tcgplayer_price(self, scryfall_id: str, finish: str = "nonfoil") -> dict | None:
        """Resolves TCGplayer market price and purchase link from Scryfall card data."""
        card = self.get_card_by_id(scryfall_id)
        if not card:
            return None

        prices = card.get("prices", {})
        price_val = None

        finish = (finish or "nonfoil").lower()
        if finish == "foil":
            price_val = prices.get("usd_foil") or prices.get("usd_etched") or prices.get("usd")
        elif finish == "etched":
            price_val = prices.get("usd_etched") or prices.get("usd_foil") or prices.get("usd")
        else:
            price_val = prices.get("usd") or prices.get("usd_foil")

        purchase_url = card.get("purchase_uris", {}).get("tcgplayer")
        if not purchase_url:
            encoded_name = urllib.parse.quote(card.get("name", ""))
            purchase_url = f"https://www.tcgplayer.com/search/magic/product?q={encoded_name}"

        if price_val is not None:
            try:
                numeric_price = float(price_val)
                return {
                    "vendor_name": "TCGplayer",
                    "price": round(numeric_price, 2),
                    "condition": "Market NM",
                    "in_stock": True,
                    "product_url": purchase_url,
                }
            except (ValueError, TypeError):
                pass

        return {
            "vendor_name": "TCGplayer",
            "price": 0.0,
            "condition": "Market NM",
            "in_stock": False,
            "product_url": purchase_url,
        }
