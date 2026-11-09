"""
TCGplayer Purchase Order Parser & Gmail IMAP Auto-Fetcher module for Chimaera MTG.
Parses card purchase manifests copied from TCGplayer order history, confirmation emails,
and invoices, and fetches order confirmation emails directly via Gmail IMAP.
"""

import email
import imaplib
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict, Any, Tuple

from bs4 import BeautifulSoup
from card_utils import normalize_card_name, card_names_match, fix_mojibake

logger = logging.getLogger(__name__)


KNOWN_CONDITIONS = {
    "near mint": "Near Mint",
    "lightly played": "Lightly Played",
    "moderately played": "Moderately Played",
    "heavily played": "Heavily Played",
    "damaged": "Damaged",
    "mint": "Mint",
    "unopened": "Unopened",
    "sealed": "Sealed",
    "nm": "Near Mint",
    "lp": "Lightly Played",
    "mp": "Moderately Played",
    "hp": "Heavily Played",
    "dmg": "Damaged",
    "near mint foil": "Near Mint",
    "lightly played foil": "Lightly Played",
    "moderately played foil": "Moderately Played",
    "heavily played foil": "Heavily Played",
    "damaged foil": "Damaged",
    "near mint holofoil": "Near Mint",
    "lightly played holofoil": "Lightly Played",
}

CONDITION_WITH_FOIL = {
    "near mint foil",
    "lightly played foil",
    "moderately played foil",
    "heavily played foil",
    "damaged foil",
    "near mint holofoil",
    "lightly played holofoil",
}

FINISH_KEYWORDS = {
    "foil": "foil",
    "foil etched": "etched",
    "etched foil": "etched",
    "etched": "etched",
    "non-foil": "nonfoil",
    "nonfoil": "nonfoil",
    "holofoil": "foil",
    "step-and-compleat foil": "foil",
    "halo foil": "foil",
    "confetti foil": "foil",
    "galaxy foil": "foil",
    "textured foil": "foil",
    "rainbow foil": "foil",
    "oil slick raised foil": "foil",
}

GAME_PREFIXES = {
    "magic",
    "magic: the gathering",
    "magic the gathering",
    "mtg",
}

BOILERPLATE_PREFIXES = (
    "qty",
    "quantity",
    "product name",
    "item\t",
    "items\t",
    "description\t",
    "subtotal",
    "sub-total",
    "shipping",
    "estimated tax",
    "sales tax",
    "tax:",
    "tax\t",
    "order total",
    "total:",
    "total\t",
    "order number",
    "order #",
    "order date",
    "order summary",
    "shipping address",
    "billing address",
    "payment method",
    "track package",
    "track your",
    "view order",
    "contact seller",
    "thank you",
    "thanks for",
    "hello ",
    "hi ",
    "dear ",
    "your order",
    "we've received",
    "package ",
    "seller:",
    "sold by",
    "fulfilled by",
    "estimated delivery",
    "need help",
    "questions?",
    "unsubscribe",
    "privacy policy",
    "terms of service",
    "tcgplayer, inc",
    "http://",
    "https://",
)

ORDER_NUMBER_REGEX = re.compile(
    r"(?:Order\s*(?:Number|#|ID)?\s*[:#]\s*([A-Z0-9]{4,12}(?:-[A-Z0-9]{2,12})+)|\b(\d[0-9A-F]{5,9}-[0-9A-F]{4,8}(?:-[0-9A-F]{3,8})?|\d{6,12}-\d{4,10}(?:-\d{4,10})?)\b)",
    re.IGNORECASE,
)


def _extract_order_numbers(text: str) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    for m in ORDER_NUMBER_REGEX.finditer(text):
        val = (m.group(1) or m.group(2) or "").strip()
        if val and val not in found:
            found.append(val)
    return found


@dataclass
class TCGPlayerCardItem:
    quantity: int
    raw_name: str
    card_name: str
    set_name: str = ""
    collector_number: str = ""
    condition: str = ""
    finish: str = "nonfoil"
    treatment: str = ""
    raw_line: str = ""
    price: Optional[float] = None
    order_number: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TCGPlayerPurchaseParser:
    """Parses text and HTML containing TCGplayer card purchases."""

    @staticmethod
    def is_likely_tcgplayer_manifest(text: str) -> bool:
        """Returns True if text contains recognizable TCGplayer purchase manifest rows."""
        if not text or not isinstance(text, str):
            return False
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if re.match(r"^\d+\s*(?:\t|\s{2,}|x?\s+)(?:Magic|MTG)\s*-\s*", s, re.IGNORECASE):
                return True
            if " - " in s and any(cond in s.lower() for cond in ("near mint", "lightly played", "moderately played", "heavily played", "damaged")):
                return True
        return False

    @staticmethod
    def parse(text: str, strict_email_mode: bool = False) -> List[TCGPlayerCardItem]:
        """
        Parses raw text into a list of TCGPlayerCardItem objects.
        Handles headers, tab-delimited, space-delimited, and multi-part card descriptions.
        """
        if not text:
            return []

        text = fix_mojibake(text)
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        items: List[TCGPlayerCardItem] = []
        current_order_number = ""

        for line in lines:
            line_ords = _extract_order_numbers(line)
            if line_ords and ("order" in line.lower() or "-" in line_ords[0]):
                current_order_number = line_ords[0]

            parsed = TCGPlayerPurchaseParser.parse_line(line, strict_email_mode=strict_email_mode)
            if parsed:
                if current_order_number and not parsed.order_number:
                    parsed.order_number = current_order_number
                items.append(parsed)

        return items

    @staticmethod
    def parse_line(line: str, strict_email_mode: bool = False) -> Optional[TCGPlayerCardItem]:
        """Parses a single line of TCGplayer purchase manifest."""
        clean_line = line.strip()
        if not clean_line:
            return None

        # Ignore header and boilerplate rows
        lower_line = clean_line.lower()
        if any(lower_line.startswith(prefix) for prefix in BOILERPLATE_PREFIXES) or lower_line == "qty\tdescription":
            return None

        # Ignore lines that are purely prices, dates, or order numbers
        if re.match(r"^\$?\d+(?:\.\d{2})?$", clean_line):
            return None
        if ORDER_NUMBER_REGEX.fullmatch(clean_line):
            return None

        # Extract trailing price columns (e.g. "\t$0.49\t$0.49" or "   $1.25" or " - $0.49")
        extracted_price: Optional[float] = None
        price_trail = re.search(r"(?:\t+|\s{2,}|\s+-\s+)\$\s*(\d+(?:\.\d{2})?)(?:\s*\t*\s*\$\s*\d+(?:\.\d{2})?)*\s*$", clean_line)
        if price_trail:
            try:
                extracted_price = float(price_trail.group(1))
            except ValueError:
                extracted_price = None
            clean_line_no_price = clean_line[:price_trail.start()].strip()
        else:
            clean_line_no_price = clean_line

        # Extract Quantity and Description
        qty = 1
        desc = clean_line_no_price
        has_explicit_qty = False

        # Case 1: Tab-separated e.g. "1\tMagic - Aether Revolt - Metallic Rebuke - Near Mint"
        tab_match = re.match(r"^(\d+)\s*\t\s*(.+)$", clean_line_no_price)
        if tab_match:
            qty = int(tab_match.group(1))
            desc = tab_match.group(2).strip()
            has_explicit_qty = True
        else:
            # Case 2: Multi-space separated e.g. "1   Magic - Aether Revolt..."
            multi_space = re.match(r"^(\d+)\s{2,}(.+)$", clean_line_no_price)
            if multi_space:
                qty = int(multi_space.group(1))
                desc = multi_space.group(2).strip()
                has_explicit_qty = True
            else:
                # Case 3: Leading quantity followed by "x" or space e.g. "1x Magic - ..." or "1 Magic - ..."
                qty_lead = re.match(r"^(\d+)\s*x?\s+(.+)$", clean_line_no_price)
                if qty_lead:
                    qty = int(qty_lead.group(1))
                    desc = qty_lead.group(2).strip()
                    has_explicit_qty = True

        # Remove surrounding quotes if from CSV/TSV copy
        desc = desc.strip("\"'").strip()
        if not desc:
            return None

        # Strip any remaining tab-separated trailing columns (such as Condition in a separate column or Price)
        if "\t" in desc and " - " not in desc:
            tab_cols = [c.strip() for c in desc.split("\t") if c.strip()]
            desc = " - ".join(tab_cols)
        elif "\t" in desc:
            tab_cols = [c.strip() for c in desc.split("\t") if c.strip() and not re.match(r"^\$?\d+\.\d{2}$", c.strip())]
            desc = " - ".join(tab_cols)

        # Parse Description into Set, Card Name, Treatment, Finish, Condition
        parts = [p.strip() for p in desc.split(" - ") if p.strip()]

        if strict_email_mode:
            # In full-email plain-text mode, require either " - " with a known condition/game prefix or explicit quantity + Magic prefix
            has_game_prefix = len(parts) >= 2 and parts[0].lower() in GAME_PREFIXES
            has_condition = any(p.lower() in KNOWN_CONDITIONS for p in parts)
            if not (has_game_prefix or (len(parts) >= 2 and has_condition) or (has_explicit_qty and len(parts) >= 2)):
                return None

        set_name = ""
        card_raw = desc
        finish = "nonfoil"
        treatment = ""
        condition = ""

        if len(parts) >= 2:
            # Check if first part is game prefix (e.g. "Magic")
            start_idx = 0
            if parts[0].lower() in GAME_PREFIXES:
                start_idx = 1

            remaining = parts[start_idx:]
            if not remaining:
                remaining = parts

            # Extract condition from the end if matched
            if remaining and remaining[-1].lower() in KNOWN_CONDITIONS:
                cond_key = remaining[-1].lower()
                condition = KNOWN_CONDITIONS[cond_key]
                if cond_key in CONDITION_WITH_FOIL:
                    finish = "foil"
                remaining = remaining[:-1]

            # Check for finish / treatment at the end of remaining parts
            cleaned_remaining = []
            for p in remaining:
                p_lower = p.lower()
                if p_lower in FINISH_KEYWORDS:
                    finish = FINISH_KEYWORDS[p_lower]
                elif p_lower in KNOWN_CONDITIONS and not condition:
                    condition = KNOWN_CONDITIONS[p_lower]
                    if p_lower in CONDITION_WITH_FOIL:
                        finish = "foil"
                elif p_lower in ("foil", "non-foil", "nonfoil", "etched"):
                    finish = "foil" if "foil" in p_lower else ("etched" if "etched" in p_lower else "nonfoil")
                elif p_lower in ("full art", "showcase", "borderless", "extended art", "retro frame"):
                    treatment = p.strip()
                else:
                    cleaned_remaining.append(p)

            if len(cleaned_remaining) >= 2:
                set_name = cleaned_remaining[0]
                card_raw = " - ".join(cleaned_remaining[1:])
            elif len(cleaned_remaining) == 1:
                card_raw = cleaned_remaining[0]
            else:
                card_raw = desc
        else:
            # Plain single token e.g. "Metallic Rebuke"
            card_raw = desc

        # Reject sealed booster boxes/packs or non-card tokens if in strict mode
        lower_raw = card_raw.lower()
        if any(lower_raw.startswith(bp) for bp in BOILERPLATE_PREFIXES):
            return None

        # Extract collector number or variant in parentheses from card name
        collector_number = ""
        p_match = re.search(r"\(([^)]+)\)", card_raw)
        if p_match:
            paren_val = p_match.group(1).strip()
            if re.match(r"^[A-Za-z0-9#-]{1,6}$", paren_val):
                collector_number = paren_val.lstrip("#")
            else:
                if not treatment:
                    treatment = paren_val

        # Strip all parentheses for canonical card matching
        clean_name = re.sub(r"\s*\([^)]*\)", "", card_raw).strip()
        clean_name = normalize_card_name(clean_name)

        if not clean_name:
            return None

        return TCGPlayerCardItem(
            quantity=max(1, qty),
            raw_name=card_raw,
            card_name=clean_name,
            set_name=set_name,
            collector_number=collector_number,
            condition=condition,
            finish=finish,
            treatment=treatment,
            raw_line=clean_line,
            price=extracted_price,
        )

    @staticmethod
    def parse_email_html(html_content: str) -> Tuple[List[TCGPlayerCardItem], List[str], List[str]]:
        """
        Parses a TCGplayer order confirmation HTML email body.
        Returns: (items, order_numbers, seller_names)
        """
        if not html_content:
            return [], [], []

        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        full_text = soup.get_text("\n", strip=True)
        order_numbers = _extract_order_numbers(full_text)

        seller_names: List[str] = []
        for s_match in re.finditer(r"(?:Seller|Sold by|Package from)\s*[:\-]\s*([^\n\r<]{2,60})", full_text, re.IGNORECASE):
            s_name = s_match.group(1).strip()
            if s_name and s_name not in seller_names:
                seller_names.append(s_name)

        items: List[TCGPlayerCardItem] = []

        # Strategy 1: Inspect all HTML table rows (<tr>) and join cells with tabs
        for tr in soup.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                # Avoid nested wrapper tables that contain nested <tr>
                if tr.find("tr") is not None:
                    continue
                row_line = "\t".join(cells)
                parsed = TCGPlayerPurchaseParser.parse_line(row_line, strict_email_mode=True)
                if parsed:
                    if order_numbers:
                        parsed.order_number = order_numbers[0]
                    items.append(parsed)

        # Strategy 2: If no table rows matched, check line-by-line in extracted text with strict_email_mode
        if not items:
            items = TCGPlayerPurchaseParser.parse(full_text, strict_email_mode=True)
            if order_numbers:
                for it in items:
                    if not it.order_number:
                        it.order_number = order_numbers[0]

        return items, order_numbers, seller_names


class TCGPlayerEmailFetcher:
    """
    Connects to Gmail IMAP (imap.gmail.com:993) using an email and Google App Password
    to automatically fetch and parse TCGplayer Order Confirmation emails.
    """

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993

    def __init__(self, email_address: str, app_password: str, imap_conn: Any = None):
        self.email_address = (email_address or "").strip()
        # Strip spaces from 16-char Google App Password (e.g. "abcd efgh ijkl mnop" -> "abcdefghijklmnop")
        self.app_password = re.sub(r"\s+", "", (app_password or "").strip())
        self._injected_conn = imap_conn

    @staticmethod
    def _decode_mime_header(raw_header: Optional[str]) -> str:
        if not raw_header:
            return ""
        try:
            parts = decode_header(raw_header)
            decoded = []
            for content, charset in parts:
                if isinstance(content, bytes):
                    decoded.append(content.decode(charset or "utf-8", errors="replace"))
                else:
                    decoded.append(str(content))
            return "".join(decoded).strip()
        except Exception:
            return str(raw_header).strip()

    @classmethod
    def parse_raw_email_bytes(cls, raw_bytes: bytes) -> Optional[Dict[str, Any]]:
        """Parses a raw RFC822 email message into a structured TCGplayer order dictionary."""
        if not raw_bytes:
            return None

        msg = email.message_from_bytes(raw_bytes)
        subject = cls._decode_mime_header(msg.get("Subject"))
        from_hdr = cls._decode_mime_header(msg.get("From"))
        message_id = (msg.get("Message-ID") or "").strip()
        date_hdr = (msg.get("Date") or "").strip()

        order_date_str = date_hdr
        order_dt = None
        if date_hdr:
            try:
                order_dt = parsedate_to_datetime(date_hdr)
                order_date_str = order_dt.strftime("%b %d, %Y")
            except Exception:
                order_date_str = date_hdr[:25]

        html_parts: List[str] = []
        text_parts: List[str] = []

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                cdisp = str(part.get("Content-Disposition") or "")
                if "attachment" in cdisp.lower():
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                decoded_text = payload.decode(charset, errors="replace")
                if ctype == "text/html":
                    html_parts.append(decoded_text)
                elif ctype == "text/plain":
                    text_parts.append(decoded_text)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                decoded_text = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    html_parts.append(decoded_text)
                else:
                    text_parts.append(decoded_text)

        items: List[TCGPlayerCardItem] = []
        order_numbers: List[str] = []
        seller_names: List[str] = []

        # Extract order number from subject if present
        for o in _extract_order_numbers(subject):
            if o not in order_numbers:
                order_numbers.append(o)

        for html_body in html_parts:
            h_items, h_ords, h_sellers = TCGPlayerPurchaseParser.parse_email_html(html_body)
            if h_items:
                items.extend(h_items)
            for o in h_ords:
                if o not in order_numbers:
                    order_numbers.append(o)
            for s in h_sellers:
                if s not in seller_names:
                    seller_names.append(s)

        if not items and text_parts:
            combined_text = "\n".join(text_parts)
            for o in _extract_order_numbers(combined_text):
                if o not in order_numbers:
                    order_numbers.append(o)
            items = TCGPlayerPurchaseParser.parse(combined_text, strict_email_mode=True)

        if not items:
            return None

        primary_order_number = order_numbers[0] if order_numbers else f"TCG-{abs(hash(message_id or subject)) % 1000000:06d}"
        for it in items:
            if not it.order_number:
                it.order_number = primary_order_number

        manifest_lines = [
            f"{it.quantity}\tMagic - {it.set_name + ' - ' if it.set_name else ''}{it.raw_name}{' - ' + it.condition if it.condition else ''}"
            for it in items
        ]

        return {
            "order_number": primary_order_number,
            "all_order_numbers": order_numbers or [primary_order_number],
            "email_message_id": message_id,
            "subject": subject or f"TCGplayer Order {primary_order_number}",
            "from": from_hdr,
            "order_date_str": order_date_str,
            "order_timestamp": order_dt.isoformat() if order_dt else None,
            "seller_summary": ", ".join(seller_names[:3]) if seller_names else "TCGplayer Marketplace",
            "total_quantity": sum(it.quantity for it in items),
            "unique_cards_count": len(items),
            "items": [it.to_dict() for it in items],
            "manifest_text": "\n".join(manifest_lines),
        }

    def fetch_recent_orders(self, days_back: int = 30, max_emails: int = 20) -> Dict[str, Any]:
        """
        Connects to Gmail IMAP, searches for recent TCGplayer order emails,
        and returns parsed orders and a combined purchase manifest.
        """
        if not self.email_address or not self.app_password:
            raise ValueError("Gmail address and 16-character Google App Password are required.")

        days_back = max(1, min(365, int(days_back or 30)))
        max_emails = max(1, min(50, int(max_emails or 20)))

        since_dt = datetime.now(timezone.utc) - timedelta(days=days_back)
        since_str = since_dt.strftime("%d-%b-%Y")

        conn = self._injected_conn
        should_close = False
        if conn is None:
            try:
                conn = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
                conn.login(self.email_address, self.app_password)
                should_close = True
            except imaplib.IMAP4.error as auth_err:
                raise ValueError(
                    f"Gmail IMAP authentication failed for {self.email_address}. "
                    "Verify that IMAP is enabled in Gmail and you are using a 16-character Google App Password."
                ) from auth_err
            except Exception as conn_err:
                raise ValueError(f"Could not connect to Gmail IMAP server: {conn_err}") from conn_err

        orders: List[Dict[str, Any]] = []
        try:
            # Select All Mail or INBOX
            status, _ = conn.select('"[Gmail]/All Mail"', readonly=True)
            if status != "OK":
                conn.select("INBOX", readonly=True)

            # Search for TCGplayer emails since date
            search_criterion = f'(SINCE "{since_str}" SUBJECT "TCGplayer")'
            status, msg_nums = conn.search(None, search_criterion)
            msg_ids = msg_nums[0].split() if (status == "OK" and msg_nums and msg_nums[0]) else []

            # Also search FROM tcgplayer.com in case subject doesn't contain "TCGplayer"
            status2, msg_nums2 = conn.search(None, f'(SINCE "{since_str}" FROM "tcgplayer.com")')
            if status2 == "OK" and msg_nums2 and msg_nums2[0]:
                for mid in msg_nums2[0].split():
                    if mid not in msg_ids:
                        msg_ids.append(mid)

            # Most recent emails first
            recent_ids = list(reversed(msg_ids))[:max_emails]

            for mid in recent_ids:
                f_status, msg_data = conn.fetch(mid, "(RFC822)")
                if f_status != "OK" or not msg_data:
                    continue
                for response_part in msg_data:
                    if isinstance(response_part, tuple) and len(response_part) >= 2:
                        raw_bytes = response_part[1]
                        parsed_order = self.parse_raw_email_bytes(raw_bytes)
                        if parsed_order and parsed_order.get("items"):
                            orders.append(parsed_order)
                        break
        finally:
            if should_close and conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass

        all_items: List[Dict[str, Any]] = []
        all_manifest_lines: List[str] = ["Qty\tDescription"]
        for o in orders:
            all_items.extend(o["items"])
            if o.get("manifest_text"):
                all_manifest_lines.append(o["manifest_text"])

        return {
            "orders_found": len(orders),
            "total_cards_quantity": sum(o["total_quantity"] for o in orders),
            "orders": orders,
            "items": all_items,
            "manifest_text": "\n".join(all_manifest_lines) if orders else "",
        }
