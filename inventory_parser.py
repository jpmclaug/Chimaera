"""
ManaBox Inventory CSV Parser module for Chimaera MTG.
Parses collection CSV exports from ManaBox, normalizes card names & DFCs,
and generates structured row-by-row validation error reports.
"""

import csv
import html
import io
import logging
import re
from typing import Dict, Any, List, Tuple, Optional
import requests

logger = logging.getLogger(__name__)


class InventoryParseError(Exception):
    """Raised when an inventory file cannot be parsed or lacks critical headers."""
    pass


class ManaBoxInventoryParser:
    """
    Parses and validates ManaBox collection CSV exports.
    Standard ManaBox CSV columns:
    Binder Name, Name, Set code, Set name, Collector number, Foil, Rarity, Quantity,
    ManaBox ID, Scryfall ID, Purchase price, Misprint, Altered, Condition, Language, Tags
    """

    # Primary header synonyms mapped to normalized field keys
    HEADER_ALIASES = {
        "name": "name",
        "card name": "name",
        "card_name": "name",
        "card": "name",
        "set code": "set_code",
        "set_code": "set_code",
        "set": "set_code",
        "edition": "set_code",
        "set name": "set_name",
        "set_name": "set_name",
        "edition name": "set_name",
        "collector number": "collector_number",
        "collector_number": "collector_number",
        "card number": "collector_number",
        "card_number": "collector_number",
        "number": "collector_number",
        "quantity": "quantity",
        "qty": "quantity",
        "count": "quantity",
        "foil": "foil",
        "finish": "foil",
        "condition": "condition",
        "language": "language",
        "lang": "language",
        "scryfall id": "scryfall_id",
        "scryfall_id": "scryfall_id",
        "scryfallid": "scryfall_id",
        "purchase price": "purchase_price",
        "purchase_price": "purchase_price",
        "price": "purchase_price",
        "binder name": "binder_name",
        "binder_name": "binder_name",
        "binder": "binder_name",
        "rarity": "rarity",
        "tags": "tags",
    }

    @staticmethod
    def extract_gdrive_file_id(url: str) -> Optional[str]:
        """
        Extracts Google Drive file ID from various link formats or validates a raw ID:
        - https://drive.google.com/file/d/<id>/view?usp=sharing
        - https://drive.google.com/open?id=<id>
        - https://docs.google.com/spreadsheets/d/<id>/edit
        - https://drive.google.com/uc?id=<id>
        - Raw ID (25 to 55 alphanumeric characters, hyphens, underscores)
        """
        if not url:
            return None
        clean = str(url).strip()
        # Direct raw ID
        if re.match(r"^[a-zA-Z0-9_-]{25,55}$", clean):
            return clean
        # Match /d/<id>
        m = re.search(r"/d/([a-zA-Z0-9_-]+)", clean)
        if m:
            return m.group(1)
        # Match id=<id>
        m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", clean)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def download_gdrive_csv(url_or_id: str, timeout: int = 45) -> str:
        """
        Downloads CSV file content from Google Drive or direct URL.
        Handles:
        1. Google Drive files (large file virus scan confirmation, uc download)
        2. Google Sheets CSV export (/export?format=csv)
        3. Direct HTTP/HTTPS download URLs
        Validates that content is CSV rather than an HTML login or access denied page.
        """
        if not url_or_id or not str(url_or_id).strip():
            raise InventoryParseError("No Google Drive link or file ID provided.")

        clean_input = str(url_or_id).strip()
        file_id = ManaBoxInventoryParser.extract_gdrive_file_id(clean_input)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Chimaera/1.0"
        })

        content_str = None

        if file_id:
            candidate_urls = []
            if "spreadsheets" in clean_input.lower():
                candidate_urls.append(f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=csv")

            candidate_urls.extend([
                f"https://drive.usercontent.google.com/download?id={file_id}&export=download&authuser=0&confirm=t",
                f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t",
                f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=csv",
            ])

            last_err = None
            for url in candidate_urls:
                try:
                    resp = session.get(url, timeout=timeout, allow_redirects=True)
                    if resp.status_code == 200:
                        text = resp.text
                        if "Google Drive - Virus scan warning" in text or "uc-download-link" in text or "download_warning" in resp.cookies:
                            token = None
                            for k, v in resp.cookies.items():
                                if "download_warning" in k:
                                    token = v
                                    break
                            if not token:
                                token_match = re.search(r'confirm=([0-9A-Za-z_-]+)', text)
                                if token_match:
                                    token = token_match.group(1)
                            if token:
                                confirm_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm={token}"
                                resp = session.get(confirm_url, timeout=timeout, allow_redirects=True)

                        resp_text = resp.content.decode("utf-8-sig", errors="replace").strip()

                        # Verify if this is an HTML page (access denied, private file, or login required)
                        if resp_text.startswith("<!DOCTYPE") or resp_text.startswith("<html") or "<title>Google Drive - Access Denied" in resp_text or "accounts.google.com" in resp_text:
                            last_err = "Google Drive access denied. Please ensure your file's sharing permission is set to 'Anyone with the link can view'."
                            continue

                        if resp_text and ("\n" in resp_text or "," in resp_text):
                            content_str = resp_text
                            break
                except Exception as e:
                    last_err = str(e)

            if not content_str:
                if last_err:
                    raise InventoryParseError(f"Failed to download file from Google Drive: {last_err}")
                raise InventoryParseError(
                    "Could not retrieve CSV from Google Drive. Please verify the link and ensure sharing is set to 'Anyone with the link can view'."
                )
        elif clean_input.startswith("http://") or clean_input.startswith("https://"):
            try:
                resp = session.get(clean_input, timeout=timeout, allow_redirects=True)
                if resp.status_code != 200:
                    raise InventoryParseError(f"Download failed with HTTP {resp.status_code}.")
                resp_text = resp.content.decode("utf-8-sig", errors="replace").strip()
                if resp_text.startswith("<!DOCTYPE") or resp_text.startswith("<html"):
                    raise InventoryParseError("The provided URL returned a webpage instead of raw CSV file content.")
                content_str = resp_text
            except Exception as e:
                raise InventoryParseError(f"Failed to download file from URL: {str(e)}")
        else:
            raise InventoryParseError(
                "Invalid Google Drive link format. Expected a link such as: https://drive.google.com/file/d/<FILE_ID>/view?usp=sharing"
            )

        if not content_str or not content_str.strip():
            raise InventoryParseError("The downloaded Google Drive file is empty.")

        return content_str

    @staticmethod
    def normalize_card_name(name: str) -> str:
        """
        Normalizes card names:
        - Repairs mojibake
        - Strips HTML markup, extra spaces, surrounding quotes
        - Converts curly/smart apostrophes and quotes to standard ASCII
        - Strips trailing foil or set annotations like '*F*'
        - Normalizes double-faced card (DFC) slashes to standard ' // '
        """
        from card_utils import normalize_card_name as util_norm
        return util_norm(name)

    @staticmethod
    def parse(csv_content: str) -> Dict[str, Any]:
        """
        Parses ManaBox CSV string.
        Returns a dict:
        {
            "valid_cards": list[dict],
            "errors": list[dict],  # [{"row": int, "error": str, "snippet": str}]
            "total_rows": int,
            "total_quantity": int,
            "unique_names": int
        }
        """
        if not csv_content or not csv_content.strip():
            raise InventoryParseError("The uploaded CSV file is empty.")

        # Split content to check line count and preserve raw lines for snippets
        raw_lines = csv_content.splitlines()
        if not raw_lines:
            raise InventoryParseError("No readable content in CSV.")

        f = io.StringIO(csv_content)
        try:
            reader = csv.reader(f)
            header_row = next(reader, None)
        except Exception as e:
            raise InventoryParseError(f"Malformed CSV header format: {str(e)}")

        if not header_row:
            raise InventoryParseError("CSV does not contain a header row.")

        # Map header indices
        header_map: Dict[str, int] = {}
        for idx, col in enumerate(header_row):
            col_clean = str(col).strip().lower().replace('"', '').replace("'", "")
            canonical_key = ManaBoxInventoryParser.HEADER_ALIASES.get(col_clean)
            if canonical_key and canonical_key not in header_map:
                header_map[canonical_key] = idx

        # Validate that required 'name' column exists
        if "name" not in header_map:
            recognized = [str(c).strip() for c in header_row if str(c).strip()]
            raise InventoryParseError(
                f"Missing required 'Name' column in CSV headers. Found headers: {', '.join(recognized[:8])}"
            )

        valid_cards: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        unique_names_set = set()
        total_quantity = 0

        # Row 1 was header; data rows start at row 2 (1-indexed for user spreadsheet familiarity)
        current_row_num = 1
        for row in reader:
            current_row_num += 1
            if not row or not any(cell.strip() for cell in row):
                # Skip pure blank lines without flagging as fatal errors
                continue

            raw_snippet = raw_lines[current_row_num - 1] if (current_row_num - 1) < len(raw_lines) else ",".join(row)

            # Check if row has enough columns for the Name field
            name_idx = header_map["name"]
            if name_idx >= len(row):
                errors.append({
                    "row": current_row_num,
                    "error": "Row has fewer columns than expected; missing 'Name' field.",
                    "snippet": raw_snippet[:120],
                })
                continue

            raw_name = str(row[name_idx]).strip()
            if not raw_name:
                errors.append({
                    "row": current_row_num,
                    "error": "Card name is empty.",
                    "snippet": raw_snippet[:120],
                })
                continue

            normalized_name = ManaBoxInventoryParser.normalize_card_name(raw_name)
            if not normalized_name:
                errors.append({
                    "row": current_row_num,
                    "error": f"Card name '{raw_name}' could not be normalized.",
                    "snippet": raw_snippet[:120],
                })
                continue

            # Parse Quantity
            qty = 1
            if "quantity" in header_map and header_map["quantity"] < len(row):
                qty_str = str(row[header_map["quantity"]]).strip()
                if qty_str:
                    try:
                        qty = int(qty_str)
                        if qty <= 0:
                            errors.append({
                                "row": current_row_num,
                                "error": f"Quantity must be greater than zero, found '{qty_str}' for card '{normalized_name}'.",
                                "snippet": raw_snippet[:120],
                            })
                            continue
                    except ValueError:
                        errors.append({
                            "row": current_row_num,
                            "error": f"Non-numeric quantity value '{qty_str}' for card '{normalized_name}'.",
                            "snippet": raw_snippet[:120],
                        })
                        continue

            # Parse Set Code
            set_code = ""
            if "set_code" in header_map and header_map["set_code"] < len(row):
                set_code = str(row[header_map["set_code"]]).strip().upper()

            # Parse Set Name
            set_name = ""
            if "set_name" in header_map and header_map["set_name"] < len(row):
                set_name = str(row[header_map["set_name"]]).strip()

            # Parse Collector Number
            col_num = ""
            if "collector_number" in header_map and header_map["collector_number"] < len(row):
                col_num = str(row[header_map["collector_number"]]).strip()

            # Parse Foil
            foil_val = "normal"
            if "foil" in header_map and header_map["foil"] < len(row):
                raw_foil = str(row[header_map["foil"]]).strip().lower()
                if raw_foil in ("foil", "true", "1", "yes", "f"):
                    foil_val = "foil"
                elif raw_foil in ("etched", "foil etched"):
                    foil_val = "etched"
                else:
                    foil_val = "normal"

            # Parse Condition
            condition = "Near Mint"
            if "condition" in header_map and header_map["condition"] < len(row):
                cond_raw = str(row[header_map["condition"]]).strip()
                if cond_raw:
                    condition = cond_raw

            # Parse Language
            lang = "en"
            if "language" in header_map and header_map["language"] < len(row):
                lang_raw = str(row[header_map["language"]]).strip()
                if lang_raw:
                    lang = lang_raw

            # Parse Scryfall ID
            scryfall_id = ""
            if "scryfall_id" in header_map and header_map["scryfall_id"] < len(row):
                scryfall_id = str(row[header_map["scryfall_id"]]).strip()

            # Parse Purchase Price
            purchase_price = None
            if "purchase_price" in header_map and header_map["purchase_price"] < len(row):
                p_str = str(row[header_map["purchase_price"]]).replace("$", "").replace(",", "").strip()
                if p_str:
                    try:
                        purchase_price = float(p_str)
                    except ValueError:
                        pass

            # Parse Binder Name
            binder_name = ""
            if "binder_name" in header_map and header_map["binder_name"] < len(row):
                binder_name = str(row[header_map["binder_name"]]).strip()

            # Parse Rarity
            rarity = ""
            if "rarity" in header_map and header_map["rarity"] < len(row):
                rarity = str(row[header_map["rarity"]]).strip()

            valid_cards.append({
                "name": normalized_name,
                "raw_name": raw_name,
                "set_code": set_code,
                "set_name": set_name,
                "collector_number": col_num,
                "scryfall_id": scryfall_id,
                "quantity": qty,
                "foil": foil_val,
                "condition": condition,
                "language": lang,
                "purchase_price": purchase_price,
                "binder_name": binder_name,
                "rarity": rarity,
                "row_number": current_row_num,
            })
            unique_names_set.add(normalized_name.lower())
            total_quantity += qty

        if not valid_cards and not errors:
            raise InventoryParseError("No card entries found in the uploaded CSV.")

        return {
            "valid_cards": valid_cards,
            "errors": errors,
            "total_rows": current_row_num - 1,
            "total_quantity": total_quantity,
            "unique_names": len(unique_names_set),
        }
