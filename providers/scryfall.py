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

    def _format_card_object(self, card: dict) -> dict:
        """Standardizes Scryfall card JSON into Chimaera card dictionary."""
        image_uri = None
        if "image_uris" in card and card["image_uris"].get("normal"):
            image_uri = card["image_uris"]["normal"]
        elif "card_faces" in card and card["card_faces"]:
            first_face = card["card_faces"][0]
            if "image_uris" in first_face and first_face["image_uris"].get("normal"):
                image_uri = first_face["image_uris"]["normal"]

        return {
            "id": card.get("id"),
            "name": card.get("name"),
            "set_code": card.get("set", "").upper(),
            "collector_number": card.get("collector_number", ""),
            "image_uri": image_uri,
            "prices": card.get("prices", {}),
            "tcgplayer_url": card.get("purchase_uris", {}).get("tcgplayer"),
        }

    def get_card_named(self, card_name: str) -> dict | None:
        """Fetches canonical card object from Scryfall by exact card name."""
        if not card_name:
            return None

        try:
            url = f"{SCRYFALL_BASE_URL}/cards/named"
            response = self.session.get(url, params={"exact": card_name.strip()}, timeout=8)
            if response.status_code == 200:
                return self._format_card_object(response.json())
            logger.warning(f"Scryfall named lookup failed for '{card_name}' (status: {response.status_code})")
        except Exception as e:
            logger.error(f"Error fetching card named '{card_name}': {e}")
        return None

    def get_cards_collection(self, card_names: list[str]) -> tuple[dict[str, dict], list[str]]:
        """
        Batch resolves multiple card names via Scryfall's /cards/collection endpoint.
        Returns a tuple of (found_map, not_found_list).
        found_map keys are lowercase card names.
        """
        if not card_names:
            return {}, []

        found_map: dict[str, dict] = {}
        not_found_list: list[str] = []

        # Scryfall /cards/collection accepts at most 75 identifiers per request
        batch_size = 75
        for i in range(0, len(card_names), batch_size):
            chunk = card_names[i:i + batch_size]
            identifiers = [{"name": name.strip()} for name in chunk if name.strip()]
            if not identifiers:
                continue

            try:
                url = f"{SCRYFALL_BASE_URL}/cards/collection"
                response = self.session.post(
                    url,
                    json={"identifiers": identifiers},
                    headers={"Content-Type": "application/json"},
                    timeout=15,
                )
                if response.status_code == 200:
                    payload = response.json()
                    for card in payload.get("data", []):
                        formatted = self._format_card_object(card)
                        c_name = formatted.get("name", "")
                        found_map[c_name.lower()] = formatted
                        # Also index by base name before // for double-faced cards
                        if " // " in c_name:
                            found_map[c_name.split(" // ")[0].lower()] = formatted

                    for nf in payload.get("not_found", []):
                        nf_name = nf.get("name", "")
                        if nf_name:
                            not_found_list.append(nf_name)
                else:
                    logger.warning(f"Scryfall collection endpoint returned status {response.status_code}")
                    for item in identifiers:
                        not_found_list.append(item["name"])
            except Exception as e:
                logger.error(f"Error during Scryfall collection lookup: {e}")
                for item in identifiers:
                    not_found_list.append(item["name"])

        # Attempt fallback lookup for any unresolved cards using named endpoint
        still_unresolved = []
        for name in not_found_list:
            if name.lower() in found_map:
                continue
            fallback = self.get_card_named(name)
            if fallback:
                found_map[name.lower()] = fallback
                if fallback.get("name"):
                    found_map[fallback["name"].lower()] = fallback
            else:
                still_unresolved.append(name)

        return found_map, still_unresolved

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

    def get_cheapest_tcgplayer_price(self, card_name: str, finish: str = "nonfoil") -> dict | None:
        """
        Finds the lowest TCGplayer market price across all printings/versions of a card.
        """
        if not card_name:
            return None

        prints = self.search_card_prints(card_name)
        finish = (finish or "nonfoil").lower()

        valid_prints = []
        for p in prints:
            prices = p.get("prices", {})
            price_val = None
            if finish == "foil":
                price_val = prices.get("usd_foil") or prices.get("usd_etched") or prices.get("usd")
            elif finish == "etched":
                price_val = prices.get("usd_etched") or prices.get("usd_foil") or prices.get("usd")
            elif finish == "any":
                candidates = [prices.get("usd"), prices.get("usd_foil"), prices.get("usd_etched")]
                cand_nums = []
                for c in candidates:
                    try:
                        if c is not None:
                            cand_nums.append(float(c))
                    except (ValueError, TypeError):
                        pass
                if cand_nums:
                    price_val = min(cand_nums)
            else:  # nonfoil
                price_val = prices.get("usd") or prices.get("usd_foil")

            if price_val is not None:
                try:
                    num_p = float(price_val)
                    if num_p > 0:
                        valid_prints.append({
                            "print": p,
                            "price": num_p,
                        })
                except (ValueError, TypeError):
                    pass

        encoded_name = urllib.parse.quote(card_name)
        search_fallback_url = f"https://www.tcgplayer.com/search/magic/product?q={encoded_name}"

        if valid_prints:
            valid_prints.sort(key=lambda x: x["price"])
            best = valid_prints[0]
            best_print = best["print"]
            purchase_url = best_print.get("tcgplayer_url") or search_fallback_url
            set_tag = best_print.get("set_code", "").upper()
            cond = f"Market NM ({set_tag})" if set_tag else "Market NM"

            return {
                "vendor_name": "TCGplayer",
                "price": round(best["price"], 2),
                "condition": cond,
                "in_stock": True,
                "product_url": purchase_url,
            }

        # Fallback to single card named lookup if prints yielded no valid price
        card_info = self.get_card_named(card_name)
        if card_info and card_info.get("id"):
            return self.get_tcgplayer_price(card_info["id"], finish=finish)

        return {
            "vendor_name": "TCGplayer",
            "price": 0.0,
            "condition": "Market NM",
            "in_stock": False,
            "product_url": search_fallback_url,
        }
