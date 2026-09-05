"""
Deck parser module for Chimaera MTG.
Supports ManaBox web links & exports, Moxfield, Archidekt, Scryfall, MTGGoldfish, CSV, and MTG plain-text formats.
"""

import csv
import html
import io
import json
import logging
import re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from card_utils import fix_mojibake, normalize_card_name, strip_accents

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
}


class DeckParseError(Exception):
    """Raised when a deck list or URL cannot be parsed."""
    pass


class DeckParser:
    """Parses MTG Commander decks from various URL sources and text/file formats."""

    @staticmethod
    def parse(source: str, source_type: str = "auto") -> dict:
        """
        Parses a deck from a URL, pasted text, or CSV content.
        
        Returns a dict:
        {
            "deck_name": str,
            "commander": list[str],
            "cards": list[dict], # [{"name": str, "quantity": int, "section": str, "set_code": str, "collector_number": str}]
            "total_cards": int,
            "source_type": str,
            "raw_text": str
        }
        """
        source = (source or "").strip()
        if not source:
            raise DeckParseError("No deck content or URL provided.")

        detected_type = DeckParser._detect_type(source) if source_type == "auto" else source_type

        if detected_type == "manabox_url":
            return DeckParser.parse_manabox_url(source)
        elif detected_type == "moxfield_url":
            return DeckParser.parse_moxfield_url(source)
        elif detected_type == "archidekt_url":
            return DeckParser.parse_archidekt_url(source)
        elif detected_type == "scryfall_url":
            return DeckParser.parse_scryfall_url(source)
        elif detected_type == "mtggoldfish_url":
            return DeckParser.parse_mtggoldfish_url(source)
        elif detected_type == "csv":
            return DeckParser.parse_csv(source)
        else:
            return DeckParser.parse_text(source)

    @staticmethod
    def _detect_type(content: str) -> str:
        """Detects whether content is a URL, CSV, or plain text decklist."""
        content_lower = content.lower().strip()
        if content_lower.startswith("http://") or content_lower.startswith("https://"):
            if "manabox.app" in content_lower:
                return "manabox_url"
            elif "moxfield.com" in content_lower:
                return "moxfield_url"
            elif "archidekt.com" in content_lower:
                return "archidekt_url"
            elif "scryfall.com" in content_lower:
                return "scryfall_url"
            elif "mtggoldfish.com" in content_lower:
                return "mtggoldfish_url"
            return "generic_url"

        # Check for CSV format
        first_line = content.splitlines()[0].lower() if content.splitlines() else ""
        if "," in first_line and ("name" in first_line or "quantity" in first_line or "binder" in first_line):
            return "csv"

        return "text"

    # ----------------------------------------------------------------------
    # URL Parsers
    # ----------------------------------------------------------------------

    @staticmethod
    def _unwrap_astro_devalue(val: any) -> any:
        """Recursively unwraps Astro devalue structures like [0, obj], [1, [items]]"""
        if isinstance(val, list):
            if len(val) == 2 and isinstance(val[0], int):
                return DeckParser._unwrap_astro_devalue(val[1])
            return [DeckParser._unwrap_astro_devalue(x) for x in val]
        elif isinstance(val, dict):
            return {k: DeckParser._unwrap_astro_devalue(v) for k, v in val.items()}
        return val

    @staticmethod
    def parse_manabox_url(url: str) -> dict:
        """
        Fetches and parses a ManaBox public share link.
        Supports https://manabox.app/decks/<deck_id> and https://manabox.app/d/<deck_id>.
        """
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=12)
            if resp.status_code != 200:
                raise DeckParseError(f"ManaBox returned HTTP {resp.status_code}. Please verify the deck link is public.")

            resp.encoding = "utf-8"
            html_text = resp.text
            soup = BeautifulSoup(html_text, "html.parser")
            deck_name = "ManaBox Commander Deck"
            commander_list = []
            commander_art = None
            cards = []

            # 1. Try to extract deck name from <title> or <h1>
            if soup.title and soup.title.string:
                raw_title = soup.title.string.split("|")[0].split("-")[0].strip()
                if raw_title and "ManaBox" not in raw_title:
                    deck_name = raw_title

            h1 = soup.find("h1")
            if h1:
                clean_h1 = h1.get_text(strip=True)
                if clean_h1:
                    deck_name = clean_h1

            # 2. Extract from Astro Island components with props
            for isl in soup.find_all("astro-island"):
                props_str = isl.get("props")
                if not props_str or len(props_str) < 50:
                    continue
                try:
                    raw_props = json.loads(props_str)
                    unwrapped = DeckParser._unwrap_astro_devalue(raw_props)
                    deck_obj = unwrapped.get("deck")
                    if deck_obj and isinstance(deck_obj, dict) and "cards" in deck_obj:
                        if deck_obj.get("name"):
                            deck_name = deck_obj["name"]
                        if deck_obj.get("imageUrl"):
                            commander_art = deck_obj["imageUrl"]

                        for c in deck_obj.get("cards", []):
                            if not isinstance(c, dict):
                                continue
                            raw_name = c.get("name") or ""
                            clean_name = DeckParser._clean_card_name(raw_name)
                            if not clean_name or len(clean_name) < 2:
                                continue

                            qty = int(c.get("quantity") or 1)
                            board_cat = c.get("boardCategory")
                            # boardCategory: 0 = commander, 3 = mainboard, 1 = sideboard, 2 = maybeboard
                            section = "commander" if board_cat == 0 else ("sideboard" if board_cat == 1 else "mainboard")
                            if section == "commander":
                                commander_list.append(clean_name)

                            set_code = str(c.get("setId") or c.get("set") or "").upper().strip()
                            col_num = str(c.get("collectorNumber") or "").strip()

                            pricing = c.get("pricing", {})
                            tcg_price = None
                            if isinstance(pricing, dict) and "tcgplayer" in pricing:
                                try:
                                    tcg_price = float(pricing["tcgplayer"].get("value"))
                                except (ValueError, TypeError):
                                    pass

                            images = c.get("images", [])
                            img_uri = None
                            small_img = None
                            if isinstance(images, list) and len(images) > 0 and isinstance(images[0], dict):
                                img_uri = images[0].get("imageUrlNormal")
                                small_img = images[0].get("imageUrlSmall")

                            cards.append({
                                "name": clean_name,
                                "quantity": qty,
                                "section": section,
                                "set_code": set_code,
                                "collector_number": col_num,
                                "price_usd": tcg_price,
                                "image_uri": img_uri,
                                "small_image_uri": small_img,
                                "cmc": c.get("manaValue"),
                            })

                        if cards:
                            break
                except Exception as e:
                    logger.debug(f"Astro island parse skip: {e}")
                    continue

            # 3. Fallback: Search for embedded JSON scripts
            if not cards:
                json_scripts = soup.find_all("script", type="application/json")
                for script in json_scripts:
                    if not script.string:
                        continue
                    try:
                        data = json.loads(script.string)
                        unwrapped = DeckParser._unwrap_astro_devalue(data)
                        extracted = DeckParser._extract_cards_from_json_dict(unwrapped)
                        if extracted and len(extracted.get("cards", [])) > 0:
                            cards = extracted["cards"]
                            commander_list = extracted.get("commander", [])
                            if extracted.get("deck_name"):
                                deck_name = extracted["deck_name"]
                            break
                    except Exception:
                        continue

            if not cards:
                raise DeckParseError(
                    "Could not extract card list from ManaBox link. "
                    "If the link is private, please open your deck in ManaBox, tap 'Share' -> 'Text', and paste the text directly into the text tab."
                )

            # Deduce commander if empty
            if not commander_list and cards:
                commander_candidates = [c["name"] for c in cards if c.get("section") == "commander"]
                if commander_candidates:
                    commander_list = commander_candidates
                else:
                    commander_list = [cards[0]["name"]]

            total_cards = sum(c["quantity"] for c in cards)

            return {
                "deck_name": deck_name,
                "commander": commander_list,
                "commander_art": commander_art,
                "cards": cards,
                "total_cards": total_cards,
                "source_type": "manabox_url",
                "raw_text": f"ManaBox Deck: {deck_name}\nURL: {url}\nCards: {len(cards)}",
            }
        except DeckParseError:
            raise
        except Exception as e:
            logger.error(f"Error parsing ManaBox URL {url}: {e}", exc_info=True)
            raise DeckParseError(f"Failed to fetch ManaBox deck: {str(e)}")

    @staticmethod
    def _extract_cards_from_json_dict(data: any) -> dict | None:
        """Recursively inspects a JSON dict/list for deck list card objects."""
        if not data:
            return None

        cards = []
        commanders = []
        deck_name = None

        if isinstance(data, dict):
            if "name" in data and isinstance(data["name"], str):
                deck_name = data["name"]

            # Common keys for cards
            for key in ["cards", "deckList", "mainboard", "entries", "items"]:
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        c = DeckParser._parse_single_card_dict(item)
                        if c:
                            cards.append(c)

            # Check commander keys
            for key in ["commander", "commanders", "command_zone", "leaders"]:
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        for item in val:
                            c = DeckParser._parse_single_card_dict(item, default_section="commander")
                            if c:
                                commanders.append(c["name"])
                                # If already in cards list, update section to commander
                                existing = next((x for x in cards if x["name"].lower() == c["name"].lower()), None)
                                if existing:
                                    existing["section"] = "commander"
                                else:
                                    cards.append(c)
                    elif isinstance(val, dict):
                        c = DeckParser._parse_single_card_dict(val, default_section="commander")
                        if c:
                            commanders.append(c["name"])
                            existing = next((x for x in cards if x["name"].lower() == c["name"].lower()), None)
                            if existing:
                                existing["section"] = "commander"
                            else:
                                cards.append(c)

            # Recurse if not found
            if not cards:
                for k, v in data.items():
                    if isinstance(v, (dict, list)):
                        sub = DeckParser._extract_cards_from_json_dict(v)
                        if sub and len(sub.get("cards", [])) > 0:
                            return sub

        elif isinstance(data, list):
            for item in data:
                c = DeckParser._parse_single_card_dict(item)
                if c:
                    cards.append(c)

        if cards:
            return {
                "deck_name": deck_name,
                "commander": commanders,
                "cards": cards,
            }
        return None

    @staticmethod
    def _parse_single_card_dict(item: any, default_section: str = "mainboard") -> dict | None:
        """Parses a card dict from JSON payloads."""
        if not isinstance(item, dict):
            return None

        name = item.get("name") or item.get("card_name") or item.get("cardName") or item.get("title")
        if isinstance(name, dict):
            name = name.get("name") or name.get("title")

        if not name or not isinstance(name, str) or len(name.strip()) < 2:
            return None

        clean_name = DeckParser._clean_card_name(name)
        qty = item.get("quantity") or item.get("qty") or item.get("count") or 1
        try:
            qty = int(qty)
        except (ValueError, TypeError):
            qty = 1

        section = default_section
        if item.get("is_commander") or item.get("isCommander") or item.get("category") == "Commander":
            section = "commander"

        set_code = item.get("set") or item.get("set_code") or item.get("setCode") or ""
        col_num = item.get("collector_number") or item.get("collectorNumber") or item.get("number") or ""

        return {
            "name": clean_name,
            "quantity": qty,
            "section": section,
            "set_code": str(set_code).upper().strip(),
            "collector_number": str(col_num).strip(),
        }

    @staticmethod
    def _extract_cards_from_html_dom(html: str) -> tuple[list[dict], list[str]]:
        """Extracts cards from HTML tags, data attributes, and table rows."""
        cards = []
        commanders = []
        seen_names = {}

        card_name_matches = re.findall(r'data-(?:card-)?name=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if not card_name_matches:
            card_name_matches = re.findall(r'<a[^>]*class=["\'][^"\']*card[^"\']*["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE)

        for name in card_name_matches:
            clean = DeckParser._clean_card_name(name)
            if clean and len(clean) > 2 and clean not in seen_names:
                seen_names[clean] = True
                cards.append({
                    "name": clean,
                    "quantity": 1,
                    "section": "mainboard",
                    "set_code": "",
                    "collector_number": "",
                })

        return cards, commanders

    @staticmethod
    def parse_moxfield_url(url: str) -> dict:
        """Fetches and parses a Moxfield public deck using Moxfield's v2 API."""
        try:
            match = re.search(r"/decks/(?:public/)?([A-Za-z0-9_-]+)", url)
            if not match:
                raise DeckParseError("Invalid Moxfield URL format. Expected: https://www.moxfield.com/decks/<id>")

            deck_id = match.group(1)
            api_url = f"https://api.moxfield.com/v2/decks/all/{deck_id}"
            resp = requests.get(api_url, headers=DEFAULT_HEADERS, timeout=12)
            if resp.status_code != 200:
                raise DeckParseError(f"Moxfield API returned HTTP {resp.status_code}. Ensure the deck is set to public.")

            data = resp.json()
            deck_name = data.get("name", "Moxfield Commander Deck")
            commanders = []
            cards = []

            # Commanders
            if "commanders" in data and isinstance(data["commanders"], dict):
                for card_name, card_data in data["commanders"].items():
                    qty = card_data.get("quantity", 1)
                    clean = DeckParser._clean_card_name(card_name)
                    commanders.append(clean)
                    cards.append({
                        "name": clean,
                        "quantity": qty,
                        "section": "commander",
                        "set_code": (card_data.get("card", {}).get("set") or "").upper(),
                        "collector_number": str(card_data.get("card", {}).get("cn") or ""),
                    })

            # Mainboard
            if "mainboard" in data and isinstance(data["mainboard"], dict):
                for card_name, card_data in data["mainboard"].items():
                    qty = card_data.get("quantity", 1)
                    clean = DeckParser._clean_card_name(card_name)
                    cards.append({
                        "name": clean,
                        "quantity": qty,
                        "section": "mainboard",
                        "set_code": (card_data.get("card", {}).get("set") or "").upper(),
                        "collector_number": str(card_data.get("card", {}).get("cn") or ""),
                    })

            total_cards = sum(c["quantity"] for c in cards)
            return {
                "deck_name": deck_name,
                "commander": commanders or ([cards[0]["name"]] if cards else []),
                "cards": cards,
                "total_cards": total_cards,
                "source_type": "moxfield_url",
                "raw_text": f"Moxfield Deck: {deck_name}\nURL: {url}\nCards: {len(cards)}",
            }
        except DeckParseError:
            raise
        except Exception as e:
            logger.error(f"Error fetching Moxfield deck {url}: {e}", exc_info=True)
            raise DeckParseError(f"Failed to parse Moxfield deck: {str(e)}")

    @staticmethod
    def parse_archidekt_url(url: str) -> dict:
        """Fetches and parses an Archidekt deck using Archidekt's API."""
        try:
            match = re.search(r"/decks/(\d+)", url)
            if not match:
                raise DeckParseError("Invalid Archidekt URL format. Expected: https://archidekt.com/decks/<id>")

            deck_id = match.group(1)
            api_url = f"https://archidekt.com/api/decks/{deck_id}/"
            resp = requests.get(api_url, headers=DEFAULT_HEADERS, timeout=12)
            if resp.status_code != 200:
                raise DeckParseError(f"Archidekt API returned HTTP {resp.status_code}. Ensure the deck is set to public.")

            data = resp.json()
            deck_name = data.get("name", "Archidekt Commander Deck")
            commanders = []
            cards = []

            for entry in data.get("cards", []):
                card_obj = entry.get("card", {})
                raw_name = card_obj.get("oracleCard", {}).get("name") or card_obj.get("name", "")
                if not raw_name:
                    continue
                clean = DeckParser._clean_card_name(raw_name)
                qty = entry.get("quantity", 1)
                categories = entry.get("categories", [])
                section = "commander" if "Commander" in categories else "mainboard"
                if section == "commander":
                    commanders.append(clean)

                set_code = (card_obj.get("edition", {}).get("editioncode") or "").upper()
                col_num = str(card_obj.get("collectorNumber") or "")

                cards.append({
                    "name": clean,
                    "quantity": qty,
                    "section": section,
                    "set_code": set_code,
                    "collector_number": col_num,
                })

            total_cards = sum(c["quantity"] for c in cards)
            return {
                "deck_name": deck_name,
                "commander": commanders or ([cards[0]["name"]] if cards else []),
                "cards": cards,
                "total_cards": total_cards,
                "source_type": "archidekt_url",
                "raw_text": f"Archidekt Deck: {deck_name}\nURL: {url}\nCards: {len(cards)}",
            }
        except DeckParseError:
            raise
        except Exception as e:
            logger.error(f"Error fetching Archidekt deck {url}: {e}", exc_info=True)
            raise DeckParseError(f"Failed to parse Archidekt deck: {str(e)}")

    @staticmethod
    def parse_scryfall_url(url: str) -> dict:
        """Fetches and parses a Scryfall public deck link."""
        try:
            match = re.search(r"/decks/([A-Za-z0-9_-]+)", url)
            if not match:
                raise DeckParseError("Invalid Scryfall deck URL format. Expected: https://scryfall.com/@user/decks/<id>")

            deck_id = match.group(1)
            export_url = f"https://api.scryfall.com/decks/{deck_id}/export/text"
            resp = requests.get(export_url, headers=DEFAULT_HEADERS, timeout=12)
            if resp.status_code != 200:
                raise DeckParseError(f"Scryfall returned HTTP {resp.status_code}.")

            resp.encoding = "utf-8"
            result = DeckParser.parse_text(resp.text)
            result["source_type"] = "scryfall_url"
            return result
        except DeckParseError:
            raise
        except Exception as e:
            logger.error(f"Error fetching Scryfall deck {url}: {e}", exc_info=True)
            raise DeckParseError(f"Failed to parse Scryfall deck: {str(e)}")

    @staticmethod
    def parse_mtggoldfish_url(url: str) -> dict:
        """Fetches and parses an MTGGoldfish deck link."""
        try:
            match = re.search(r"/deck/(\d+)", url)
            if not match:
                raise DeckParseError("Invalid MTGGoldfish URL format. Expected: https://www.mtggoldfish.com/deck/<id>")

            deck_id = match.group(1)
            download_url = f"https://www.mtggoldfish.com/deck/download/{deck_id}"
            resp = requests.get(download_url, headers=DEFAULT_HEADERS, timeout=12)
            if resp.status_code != 200:
                raise DeckParseError(f"MTGGoldfish returned HTTP {resp.status_code}.")

            resp.encoding = "utf-8"
            result = DeckParser.parse_text(resp.text)
            result["source_type"] = "mtggoldfish_url"
            return result
        except DeckParseError:
            raise
        except Exception as e:
            logger.error(f"Error fetching MTGGoldfish deck {url}: {e}", exc_info=True)
            raise DeckParseError(f"Failed to parse MTGGoldfish deck: {str(e)}")

    # ----------------------------------------------------------------------
    # Text & CSV Parsers
    # ----------------------------------------------------------------------

    @staticmethod
    def parse_csv(csv_content: str) -> dict:
        """
        Parses ManaBox or standard MTG CSV export.
        Standard ManaBox CSV columns: Name, Quantity, Binder Name, Set code, Set name, Card number, Condition, Foil, Rarity
        """
        try:
            f = io.StringIO(csv_content.strip())
            reader = csv.DictReader(f)
            cards = []
            commanders = []
            deck_name = "Imported CSV Deck"

            for row in reader:
                norm_row = {str(k).strip().lower(): str(v).strip() for k, v in row.items() if k is not None}
                name = norm_row.get("name") or norm_row.get("card name") or norm_row.get("card")
                if not name:
                    continue

                clean = DeckParser._clean_card_name(name)
                qty_str = norm_row.get("quantity") or norm_row.get("qty") or norm_row.get("count") or "1"
                try:
                    qty = int(qty_str)
                except (ValueError, TypeError):
                    qty = 1

                binder = norm_row.get("binder name") or norm_row.get("section") or norm_row.get("category") or ""
                section = "mainboard"
                if "commander" in binder.lower():
                    section = "commander"
                    commanders.append(clean)
                elif binder and deck_name == "Imported CSV Deck":
                    deck_name = binder

                set_code = norm_row.get("set code") or norm_row.get("set") or norm_row.get("edition") or ""
                col_num = norm_row.get("card number") or norm_row.get("collector number") or norm_row.get("number") or ""

                cards.append({
                    "name": clean,
                    "quantity": qty,
                    "section": section,
                    "set_code": set_code.upper(),
                    "collector_number": col_num,
                })

            if not cards:
                raise DeckParseError("No valid card rows found in CSV data.")

            if not commanders and cards:
                commanders = [cards[0]["name"]]

            total_cards = sum(c["quantity"] for c in cards)
            return {
                "deck_name": deck_name,
                "commander": commanders,
                "cards": cards,
                "total_cards": total_cards,
                "source_type": "csv",
                "raw_text": csv_content[:1000],
            }
        except DeckParseError:
            raise
        except Exception as e:
            logger.error(f"Error parsing CSV deck: {e}", exc_info=True)
            raise DeckParseError(f"Failed to parse CSV deck list: {str(e)}")

    @staticmethod
    def parse_text(text_content: str) -> dict:
        """
        Parses ManaBox text exports, MTG Arena format, Cockatrice, and generic plain-text decklists.
        """
        try:
            cards, commanders = DeckParser._parse_raw_lines(text_content)
            if not cards:
                raise DeckParseError("Could not find any valid card lines in the provided text.")

            deck_name = "Commander Deck"
            for line in text_content.splitlines():
                line = line.strip()
                if line.startswith("//") and ("deck" in line.lower() or "name" in line.lower()) and ":" in line:
                    deck_name = line.split(":", 1)[1].strip()
                    break

            if not commanders and cards:
                commanders = [cards[0]["name"]]

            total_cards = sum(c["quantity"] for c in cards)
            return {
                "deck_name": deck_name,
                "commander": commanders,
                "cards": cards,
                "total_cards": total_cards,
                "source_type": "text",
                "raw_text": text_content[:1500],
            }
        except DeckParseError:
            raise
        except Exception as e:
            logger.error(f"Error parsing text deck: {e}", exc_info=True)
            raise DeckParseError(f"Failed to parse deck list: {str(e)}")

    @staticmethod
    def _parse_raw_lines(text: str) -> tuple[list[dict], list[str]]:
        """Parses individual lines of text, tracking sections like // Commander, // Mainboard, Sideboard, etc."""
        cards = []
        commanders = []
        current_section = "mainboard"

        line_pattern = re.compile(
            r"^(?:(\d+)[xX]?\s+)?([^\r\n()]+?)(?:\s+\(([A-Za-z0-9]+)\)\s*([A-Za-z0-9]+)?)?(?:\s+\*.*?\*)?$"
        )

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Skip any lines that look like HTML tags or script/markup
            if line.startswith("<") or line.endswith(">") or "</" in line or "<div" in line or "<span" in line or "<p" in line or "<script" in line:
                continue

            lower_line = line.lower()
            if lower_line.startswith("//") or lower_line.startswith("#"):
                header_text = lower_line.lstrip("/# ").strip()
                if "commander" in header_text:
                    current_section = "commander"
                elif "sideboard" in header_text or "maybeboard" in header_text:
                    current_section = "sideboard"
                else:
                    current_section = "mainboard"
                continue

            if lower_line in ("commander", "commanders", "command zone"):
                current_section = "commander"
                continue
            if lower_line in ("deck", "main", "mainboard", "creatures", "spells", "lands", "artifacts", "enchantments", "planeswalkers"):
                current_section = "mainboard"
                continue
            if lower_line in ("sideboard", "maybeboard", "tokens", "considering"):
                current_section = "sideboard"
                continue

            match = line_pattern.match(line)
            if match:
                qty_str, card_name, set_code, col_num = match.groups()
                qty = int(qty_str) if qty_str else 1
                clean = DeckParser._clean_card_name(card_name)

                if not clean or len(clean) < 2 or clean.lower() in ("deck", "commander", "sideboard"):
                    continue

                if current_section == "commander":
                    commanders.append(clean)

                cards.append({
                    "name": clean,
                    "quantity": qty,
                    "section": current_section,
                    "set_code": (set_code or "").upper().strip(),
                    "collector_number": (col_num or "").strip(),
                })
            else:
                clean = DeckParser._clean_card_name(line)
                if clean and len(clean) >= 3 and not clean.startswith("//"):
                    if current_section == "commander":
                        commanders.append(clean)
                    cards.append({
                        "name": clean,
                        "quantity": 1,
                        "section": current_section,
                        "set_code": "",
                        "collector_number": "",
                    })

        return cards, commanders

    @staticmethod
    def _clean_card_name(name: str) -> str:
        """Cleans card name by removing foil tags, set annotations, HTML tags, or extraneous symbols."""
        return normalize_card_name(name)
