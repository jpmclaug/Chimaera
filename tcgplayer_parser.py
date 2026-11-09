"""
TCGplayer Purchase Order Parser & Gmail IMAP Auto-Fetcher module for Chimaera MTG.
Parses card purchase manifests copied from TCGplayer order history, confirmation emails,
and invoices, and fetches order confirmation emails directly via Gmail IMAP.
"""

import email
import imaplib
import logging
import re
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict, Any, Tuple

from bs4 import BeautifulSoup, Tag
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
    "seller\t",
    "sold by",
    "shipped by",
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
    "===",
)

ORDER_NUMBER_REGEX = re.compile(
    r"(?:Order\s*(?:Number|#|ID)?\s*[:#\t]\s*([A-Z0-9]{4,12}(?:-[A-Z0-9]{2,12})+)|\b((?=[0-9A-F-]*\d)[0-9A-F]{6,12}-[0-9A-F]{4,10}(?:-[0-9A-F]{3,10})?|\d{6,12}-\d{4,10}(?:-\d{4,10})?)\b)",
    re.IGNORECASE,
)

CANONICAL_PACKAGE_HEADER_REGEX = re.compile(r"^===\s*(.+?)\s*===$")

SELLER_INLINE_REGEXES = [
    re.compile(
        r"(?:Package\s+from\s+Seller|Package\s+\d+\s*(?:of\s*\d+\s*)?[-:–]\s*Seller|Seller\s+in\s+Focus|Seller|Sold\s+by|Package\s+from|Shipped\s+by|Fulfilled\s+by|Merchant|Store)\s*(?:[:\-–]|\t+)\s*([^\n\r<|]{2,65})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:Sold\s+by|Package\s+from|Shipped\s+by|Fulfilled\s+by)\s+([A-Za-z0-9][^\n\r<|:]{1,55})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:order|package|items?)\s+from\s+([A-Za-z0-9][^\n\r<|]{1,50}?)\s+(?:has\s+shipped|have\s+shipped|is\s+on\s+its\s+way|was\s+shipped|in\s+order)",
        re.IGNORECASE,
    ),
]

SELLER_STOPWORDS = {
    "tcgplayer",
    "tcgplayer.com",
    "tcgplayer marketplace",
    "order summary",
    "order details",
    "shipping address",
    "billing address",
    "payment method",
    "your order",
    "track package",
    "contact seller",
    "quantity",
    "description",
    "price",
    "total",
    "subtotal",
}


def _clean_seller_name(raw_seller: str) -> str:
    if not raw_seller:
        return ""
    s = re.sub(r"\s+", " ", raw_seller).strip()
    # Strip trailing boilerplate fragments that sometimes share a line with the seller name
    s = re.split(
        r"\s+(?:Order\s*(?:Number|#)|Estimated\s+Delivery|Tracking\s*(?:Number|#)|Shipping\s+Method|has\s+shipped|is\s+on\s+its\s+way|View\s+Order|Contact\s+Seller|\|)\b",
        s,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    s = s.strip(":-–|# \t\"'")
    if not s or len(s) < 2 or len(s) > 65:
        return ""
    if s.lower() in SELLER_STOPWORDS:
        return ""
    if ORDER_NUMBER_REGEX.fullmatch(s):
        return ""
    if re.match(r"^\$?\d+(?:\.\d{2})?$", s):
        return ""
    return s


def _extract_order_numbers(text: str) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    for m in ORDER_NUMBER_REGEX.finditer(text):
        val = (m.group(1) or m.group(2) or "").strip()
        if val and val not in found:
            found.append(val)
    return found


def _extract_seller_names(text: str) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    for rgx in SELLER_INLINE_REGEXES:
        for m in rgx.finditer(text):
            cleaned = _clean_seller_name(m.group(1))
            if cleaned and cleaned not in found:
                found.append(cleaned)
    if not found and re.search(r"\b(?:TCGplayer\s+Direct|Direct\s+by\s+TCGplayer)\b", text, re.IGNORECASE):
        found.append("TCGplayer Direct")
    return found


def _extract_date_str(text: str) -> str:
    if not text:
        return ""
    m = re.search(
        r"(?:Order\s+Date|Date)\s*[:\-]\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return ""


def _is_generic_order_number(order_num: str) -> bool:
    if not order_num:
        return True
    u = order_num.strip().upper()
    return u.startswith("ORD-") or u.startswith("TCG-") or u.startswith("PKG-")


def _is_generic_seller(seller: str) -> bool:
    if not seller:
        return True
    s = seller.strip().lower()
    return (
        s in ("tcgplayer marketplace", "tcgplayer", "unknown seller", "marketplace")
        or s.startswith("package ")
        or s.startswith("tcgplayer package")
    )


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
    seller: str = ""
    order_date: str = ""
    package_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def format_item_manifest_line(it: TCGPlayerCardItem) -> str:
    """Formats a single TCGPlayerCardItem as a canonical tab-separated manifest line."""
    set_part = f"{it.set_name} - " if it.set_name else ""
    cond_part = f" - {it.condition}" if it.condition else ""
    price_part = f"\t${it.price:.2f}" if it.price is not None else ""
    return f"{it.quantity}\tMagic - {set_part}{it.raw_name}{cond_part}{price_part}"


def format_package_header(order_number: str = "", seller: str = "", order_date: str = "") -> str:
    """Formats a canonical package section header line for manifest_text."""
    parts = []
    if order_number:
        parts.append(f"Order #{order_number}")
    if seller:
        parts.append(f"Seller: {seller}")
    if order_date:
        parts.append(f"Date: {order_date}")
    if not parts:
        return ""
    return f"=== {' | '.join(parts)} ==="


def split_items_by_alphabetical_boundaries(items: List[TCGPlayerCardItem], min_total_items: int = 10, min_segment_size: int = 4) -> List[List[TCGPlayerCardItem]]:
    """
    Detects concatenated TCGplayer tables when explicit headers were stripped.
    TCGplayer tables are sorted alphabetically by (set_name, card_name). When multiple
    tables are concatenated, (set_name, card_name) resets backwards at each table boundary.
    Only splits if the input has at least `min_total_items` and every resulting segment
    has at least `min_segment_size` items, preventing false splits on small hand-typed lists.
    """
    if len(items) < min_total_items:
        return [items]

    def _sort_key(it: TCGPlayerCardItem) -> Tuple[str, str]:
        return ((it.set_name or "").lower().strip(), (it.card_name or "").lower().strip())

    reset_indices: List[int] = [0]
    for idx in range(1, len(items)):
        if _sort_key(items[idx]) < _sort_key(items[idx - 1]):
            reset_indices.append(idx)

    if len(reset_indices) <= 1:
        return [items]

    # Check if overall list is predominantly sorted (at least 80% in-order transitions)
    in_order_ratio = 1.0 - ((len(reset_indices) - 1) / max(1, len(items) - 1))
    if in_order_ratio < 0.80:
        return [items]

    segments: List[List[TCGPlayerCardItem]] = []
    for i, start_idx in enumerate(reset_indices):
        end_idx = reset_indices[i + 1] if (i + 1) < len(reset_indices) else len(items)
        seg = items[start_idx:end_idx]
        if segments and len(seg) < min_segment_size:
            # Merge tiny trailing/anomalous segment into previous segment
            segments[-1].extend(seg)
        else:
            segments.append(seg)

    # If the very first segment was smaller than min_segment_size, only keep it separate if >= 3 items
    if len(segments) >= 2 and len(segments[0]) < 3:
        segments[1] = segments[0] + segments[1]
        segments.pop(0)

    return segments if len(segments) > 1 else [items]


def deduplicate_package_groups(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicates and reconciles TCGplayer package groups:
    1. Merges identical package multisets (e.g. duplicate email fetches or 1-package Order + Shipping email),
       preserving the most specific order_number and seller name.
    2. Subtracts individual seller package multisets from consolidated summary/superset tables
       (e.g. TCGplayer Direct summary tables or full-order confirmation tables when individual
       seller breakdown tables are also present).
       - If subtracting constituent sub-packages leaves 0 items, the redundant summary table is dropped.
       - If subtracting constituent sub-packages leaves remaining items (e.g. 107 - 40 = 67 items),
         only the remaining unsplit items are kept in that package.
    """
    if not groups or len(groups) <= 1:
        return groups

    def _item_key(it: TCGPlayerCardItem) -> Tuple[str, str]:
        return (normalize_card_name(it.card_name).lower(), (it.set_name or "").lower().strip())

    def _build_counter(item_list: List[TCGPlayerCardItem]) -> Counter:
        c: Counter = Counter()
        for it in item_list:
            c[_item_key(it)] += int(it.quantity or 1)
        return c

    # Step 1: Merge exact-match multisets
    merged_groups: List[Dict[str, Any]] = []
    for g in groups:
        g_items: List[TCGPlayerCardItem] = list(g.get("items") or [])
        if not g_items:
            continue
        g_counter = _build_counter(g_items)
        matched_existing = None
        for existing in merged_groups:
            ex_counter = _build_counter(existing["items"])
            if g_counter == ex_counter:
                # Same cards & quantities; check if same order_number or at least one has generic order_number
                o1 = (existing.get("order_number") or "").strip()
                o2 = (g.get("order_number") or "").strip()
                if o1 == o2 or _is_generic_order_number(o1) or _is_generic_order_number(o2):
                    matched_existing = existing
                    break

        if matched_existing is not None:
            # Enrich order_number, seller, order_date from whichever is more specific
            if _is_generic_order_number(matched_existing.get("order_number", "")) and not _is_generic_order_number(g.get("order_number", "")):
                matched_existing["order_number"] = g["order_number"]
            if _is_generic_seller(matched_existing.get("seller", "")) and not _is_generic_seller(g.get("seller", "")):
                matched_existing["seller"] = g["seller"]
            if not matched_existing.get("order_date") and g.get("order_date"):
                matched_existing["order_date"] = g["order_date"]
        else:
            merged_groups.append({
                **g,
                "items": [
                    TCGPlayerCardItem(**it.to_dict()) if isinstance(it, TCGPlayerCardItem) else TCGPlayerCardItem(**it)
                    for it in g_items
                ],
            })

    if len(merged_groups) <= 1:
        return merged_groups

    # Step 2: Superset / Summary Table Subtraction
    # Sort indices by number of unique items ascending so smaller packages are subtracted from larger supersets
    indices_by_size = sorted(range(len(merged_groups)), key=lambda i: (len(merged_groups[i]["items"]), sum(it.quantity for it in merged_groups[i]["items"])))
    dropped_indices = set()

    for pos, big_idx in enumerate(indices_by_size):
        if big_idx in dropped_indices:
            continue
        big_group = merged_groups[big_idx]
        big_items: List[TCGPlayerCardItem] = big_group["items"]
        if len(big_items) < 6:
            continue

        remaining_counter = _build_counter(big_items)
        subtracted_any = False

        # Consider candidate smaller packages
        for small_idx in indices_by_size[:pos]:
            if small_idx in dropped_indices:
                continue
            small_group = merged_groups[small_idx]
            small_items: List[TCGPlayerCardItem] = small_group["items"]
            if not small_items or len(small_items) >= len(big_items):
                continue

            o_big = (big_group.get("order_number") or "").strip()
            o_small = (small_group.get("order_number") or "").strip()
            same_order = bool(o_big and o_small and o_big == o_small)
            compatible_order = same_order or _is_generic_order_number(o_big) or _is_generic_order_number(o_small)

            # Require either same explicit order number or at least 3 unique cards in small_group
            if not compatible_order:
                continue
            if not same_order and len(small_items) < 3:
                continue

            small_counter = _build_counter(small_items)
            # Check if 100% of small_counter is contained in remaining_counter
            if all(remaining_counter[k] >= qty for k, qty in small_counter.items()):
                for k, qty in small_counter.items():
                    remaining_counter[k] -= qty
                    if remaining_counter[k] <= 0:
                        del remaining_counter[k]
                subtracted_any = True
                # Propagate real order_number / order_date between big and small if one was generic
                if _is_generic_order_number(o_small) and not _is_generic_order_number(o_big):
                    small_group["order_number"] = o_big
                elif _is_generic_order_number(o_big) and not _is_generic_order_number(o_small):
                    big_group["order_number"] = o_small
                if not small_group.get("order_date") and big_group.get("order_date"):
                    small_group["order_date"] = big_group["order_date"]

        if subtracted_any:
            total_left = sum(remaining_counter.values())
            if total_left <= 0:
                # 100% of big_group was accounted for by individual sub-packages! Drop big_group.
                dropped_indices.add(big_idx)
            else:
                # Keep only the unsubtracted remainder items in big_group
                reduced_items: List[TCGPlayerCardItem] = []
                need_counter = remaining_counter.copy()
                for it in big_items:
                    k = _item_key(it)
                    avail = need_counter.get(k, 0)
                    if avail <= 0:
                        continue
                    take_qty = min(int(it.quantity or 1), avail)
                    need_counter[k] -= take_qty
                    it_copy = TCGPlayerCardItem(**it.to_dict())
                    it_copy.quantity = take_qty
                    reduced_items.append(it_copy)
                big_group["items"] = reduced_items

    result_groups: List[Dict[str, Any]] = []
    for idx, grp in enumerate(merged_groups):
        if idx in dropped_indices or not grp.get("items"):
            continue
        result_groups.append(grp)

    # Stamp clean metadata onto each group's items
    for pkg_idx, grp in enumerate(result_groups):
        o_num = (grp.get("order_number") or "").strip()
        s_name = (grp.get("seller") or "").strip()
        o_date = (grp.get("order_date") or "").strip()
        for it in grp["items"]:
            it.package_index = pkg_idx
            if o_num:
                it.order_number = o_num
            if s_name:
                it.seller = s_name
            if o_date:
                it.order_date = o_date

    return result_groups


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
    def parse_into_packages(text: str, strict_email_mode: bool = False) -> List[Dict[str, Any]]:
        """
        Parses raw text into distinct package groups (split by Order #, Seller, table headers,
        or alphabetical table resets) and deduplicates superset summary tables.
        """
        items = TCGPlayerPurchaseParser.parse(text, strict_email_mode=strict_email_mode)
        if not items:
            return []

        # Group contiguous items by (order_number, seller, package_index)
        raw_groups: List[Dict[str, Any]] = []
        for it in items:
            key = (it.order_number or "", it.seller or "", it.package_index)
            if not raw_groups or raw_groups[-1]["_key"] != key:
                raw_groups.append({
                    "_key": key,
                    "order_number": it.order_number or "",
                    "seller": it.seller or "",
                    "order_date": it.order_date or "",
                    "items": [it],
                })
            else:
                raw_groups[-1]["items"].append(it)

        # Within any large group that lacks explicit seller splits, check for alphabetical table resets
        split_groups: List[Dict[str, Any]] = []
        for grp in raw_groups:
            grp_items = grp["items"]
            if len(grp_items) >= 10 and _is_generic_seller(grp.get("seller", "")):
                alpha_segs = split_items_by_alphabetical_boundaries(grp_items, min_total_items=10, min_segment_size=4)
                if len(alpha_segs) > 1:
                    for seg_idx, seg_items in enumerate(alpha_segs):
                        split_groups.append({
                            "order_number": grp["order_number"],
                            "seller": grp["seller"],
                            "order_date": grp["order_date"],
                            "items": seg_items,
                        })
                    continue
            split_groups.append({
                "order_number": grp["order_number"],
                "seller": grp["seller"],
                "order_date": grp["order_date"],
                "items": grp_items,
            })

        deduped = deduplicate_package_groups(split_groups)

        # Ensure every package has a distinguishable seller label when multiple packages exist
        if len(deduped) > 1:
            for idx, grp in enumerate(deduped, start=1):
                if _is_generic_seller(grp.get("seller", "")):
                    qty_sum = sum(it.quantity for it in grp["items"])
                    if len(grp["items"]) >= 25 or qty_sum >= 30:
                        grp["seller"] = f"TCGplayer Direct (Package {idx})"
                    else:
                        grp["seller"] = f"Marketplace Seller (Package {idx})"
                    for it in grp["items"]:
                        it.seller = grp["seller"]

        return deduped

    @staticmethod
    def parse(text: str, strict_email_mode: bool = False) -> List[TCGPlayerCardItem]:
        """
        Parses raw text into a list of TCGPlayerCardItem objects.
        Handles canonical `=== Order #... | Seller: ... ===` headers, inline/two-line
        Order & Seller headers, tab-delimited, space-delimited, and multi-part card descriptions.
        """
        if not text:
            return []

        text = fix_mojibake(text)
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        items: List[TCGPlayerCardItem] = []

        current_order_number = ""
        current_seller = ""
        current_order_date = ""
        current_package_index = 0
        has_seen_items_in_current_pkg = False
        pending_label: Optional[str] = None
        recent_non_item_lines: List[str] = []

        for line in lines:
            clean_s = line.strip()
            if not clean_s:
                pending_label = None
                continue

            # 1. Check canonical `=== Order #... | Seller: ... | Date: ... ===` package header
            canon_m = CANONICAL_PACKAGE_HEADER_REGEX.match(clean_s)
            if canon_m:
                if has_seen_items_in_current_pkg:
                    current_package_index += 1
                    has_seen_items_in_current_pkg = False
                header_body = canon_m.group(1)
                for part in header_body.split("|"):
                    p = part.strip()
                    if p.lower().startswith("order"):
                        ords = _extract_order_numbers(p)
                        if ords:
                            current_order_number = ords[0]
                        else:
                            val = re.sub(r"^order\s*#?\s*:?\s*", "", p, flags=re.IGNORECASE).strip()
                            if val:
                                current_order_number = val
                    elif p.lower().startswith("seller"):
                        val = re.sub(r"^seller\s*:?\s*", "", p, flags=re.IGNORECASE).strip()
                        if val:
                            current_seller = val
                    elif p.lower().startswith("date"):
                        val = re.sub(r"^date\s*:?\s*", "", p, flags=re.IGNORECASE).strip()
                        if val:
                            current_order_date = val
                pending_label = None
                recent_non_item_lines.clear()
                continue

            # 2. Check two-line label/value headers (e.g. "ORDER NUMBER" followed by value on next line)
            upper_s = clean_s.upper().rstrip(":")
            if upper_s in ("ORDER NUMBER", "ORDER #", "ORDER ID"):
                pending_label = "ORDER_NUMBER"
                continue
            if upper_s in ("SELLER", "SELLER IN FOCUS", "SOLD BY", "PACKAGE FROM", "SHIPPED BY", "MERCHANT"):
                pending_label = "SELLER"
                continue
            if upper_s in ("ORDER DATE", "DATE"):
                pending_label = "ORDER_DATE"
                continue

            # Check if line is "Contact Seller" or "Leave Feedback" right below a bare seller name
            if upper_s in ("CONTACT SELLER", "LEAVE FEEDBACK") and recent_non_item_lines:
                prev_cand = _clean_seller_name(recent_non_item_lines[-1])
                if prev_cand and not _extract_order_numbers(prev_cand) and len(prev_cand.split()) <= 6:
                    if prev_cand != current_seller and has_seen_items_in_current_pkg:
                        current_package_index += 1
                        has_seen_items_in_current_pkg = False
                    current_seller = prev_cand
                continue

            if pending_label:
                lbl = pending_label
                pending_label = None
                if lbl == "ORDER_NUMBER":
                    ords = _extract_order_numbers(clean_s)
                    new_ord = ords[0] if ords else clean_s
                    if new_ord != current_order_number and has_seen_items_in_current_pkg:
                        current_package_index += 1
                        has_seen_items_in_current_pkg = False
                        current_seller = ""
                    current_order_number = new_ord
                    continue
                elif lbl == "SELLER":
                    cleaned_sel = _clean_seller_name(clean_s)
                    if cleaned_sel:
                        if cleaned_sel != current_seller and has_seen_items_in_current_pkg:
                            current_package_index += 1
                            has_seen_items_in_current_pkg = False
                        current_seller = cleaned_sel
                        continue
                elif lbl == "ORDER_DATE":
                    current_order_date = clean_s[:30]
                    continue

            # 3. Check table column header boundary ("Qty\tDescription" or "Quantity\tProduct Name")
            lower_s = clean_s.lower()
            if lower_s in ("qty\tdescription", "quantity\tdescription", "quantity\tproduct name", "qty\tproduct name") or re.match(r"^(?:qty|quantity)\s+(?:description|product\s+name|item)\b", lower_s):
                if has_seen_items_in_current_pkg:
                    current_package_index += 1
                    has_seen_items_in_current_pkg = False
                # Look behind in recent_non_item_lines for a bare seller name right above the table header
                for prev_line in reversed(recent_non_item_lines[-3:]):
                    s_cands = _extract_seller_names(prev_line)
                    if s_cands:
                        current_seller = s_cands[0]
                        break
                    cleaned_cand = _clean_seller_name(prev_line)
                    if cleaned_cand and not _extract_order_numbers(prev_line) and len(cleaned_cand.split()) <= 6:
                        current_seller = cleaned_cand
                        break
                recent_non_item_lines.clear()
                continue

            # 4. Check inline Order Number / Seller / Date on this line
            line_ords = _extract_order_numbers(clean_s)
            if line_ords and ("order" in lower_s or "-" in line_ords[0]):
                new_ord = line_ords[0]
                if new_ord != current_order_number and has_seen_items_in_current_pkg:
                    current_package_index += 1
                    has_seen_items_in_current_pkg = False
                    current_seller = ""
                current_order_number = new_ord

            line_sellers = _extract_seller_names(clean_s)
            if line_sellers:
                new_sel = line_sellers[0]
                if new_sel != current_seller and has_seen_items_in_current_pkg:
                    current_package_index += 1
                    has_seen_items_in_current_pkg = False
                current_seller = new_sel

            line_date = _extract_date_str(clean_s)
            if line_date:
                current_order_date = line_date

            # 5. Attempt to parse line as a card item
            parsed = TCGPlayerPurchaseParser.parse_line(line, strict_email_mode=strict_email_mode)
            if parsed:
                parsed.order_number = current_order_number
                parsed.seller = current_seller
                parsed.order_date = current_order_date
                parsed.package_index = current_package_index
                has_seen_items_in_current_pkg = True
                recent_non_item_lines.clear()
                items.append(parsed)
            else:
                recent_non_item_lines.append(clean_s)

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
    def _extract_table_context(table_tag: Tag, default_order_number: str = "", default_seller: str = "") -> Tuple[str, str]:
        """
        Walks backwards in DOM from `table_tag` (and checks non-item banner rows inside `table_tag`)
        to find the most specific Order Number and Seller Name for this item table.
        """
        found_order = ""
        found_seller = ""

        # 1. Check banner/header rows inside the table itself
        for tr in table_tag.find_all("tr"):
            if tr.find("tr") is not None:
                continue
            row_text = tr.get_text(" ", strip=True)
            if not row_text:
                continue
            # Skip actual card rows
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"]) if td.get_text(" ", strip=True)]
            if len(cells) >= 2 and TCGPlayerPurchaseParser.parse_line("\t".join(cells), strict_email_mode=True):
                continue
            if not found_order:
                ords = _extract_order_numbers(row_text)
                if ords:
                    found_order = ords[0]
            if not found_seller:
                sels = _extract_seller_names(row_text)
                if sels:
                    found_seller = sels[0]

        # 2. Walk preceding elements in document order (up to the previous item table or 25 elements back)
        inspected = 0
        for prev in table_tag.find_all_previous(["p", "div", "span", "td", "th", "h2", "h3", "h4", "strong", "b", "a", "table"]):
            if prev is table_tag:
                continue
            # Stop if we hit a previous leaf item table
            if prev.name == "table" and prev not in table_tag.parents:
                has_cards = False
                for tr in prev.find_all("tr"):
                    if tr.find("tr") is None:
                        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"]) if td.get_text(" ", strip=True)]
                        if len(cells) >= 2 and TCGPlayerPurchaseParser.parse_line("\t".join(cells), strict_email_mode=True):
                            has_cards = True
                            break
                if has_cards:
                    break
                continue

            # Skip huge container divs/tables that wrap the entire email
            if prev in table_tag.parents:
                continue

            txt = prev.get_text(" ", strip=True)
            if not txt or len(txt) > 350:
                continue

            if not found_order:
                ords = _extract_order_numbers(txt)
                if ords:
                    found_order = ords[0]
            if not found_seller:
                sels = _extract_seller_names(txt)
                if sels:
                    found_seller = sels[0]

            inspected += 1
            if (found_order and found_seller) or inspected >= 30:
                break

        return (found_order or default_order_number, found_seller or default_seller)

    @staticmethod
    def parse_email_html(html_content: str) -> Tuple[List[TCGPlayerCardItem], List[str], List[str]]:
        """
        Parses a TCGplayer order confirmation or shipping HTML email body.
        Extracts each leaf item `<table>` with its own Order Number and Seller context,
        deduplicating redundant summary tables when per-seller breakdown tables are present.
        Returns: (items, order_numbers, seller_names)
        """
        if not html_content:
            return [], [], []

        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        # Use space join per block line so inline `<td><strong>Seller:</strong> <span>Name</span></td>` stays on one line
        full_text_lines = []
        for block in soup.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4"]):
            if block.name in ("div", "tr") and block.find(["p", "div", "tr"]) is not None:
                continue
            line_txt = block.get_text(" ", strip=True)
            if line_txt:
                full_text_lines.append(line_txt)
        full_text = "\n".join(full_text_lines) if full_text_lines else soup.get_text(" ", strip=True)

        order_numbers = _extract_order_numbers(full_text)
        seller_names = _extract_seller_names(full_text)
        email_date_str = _extract_date_str(full_text)

        default_order_num = order_numbers[0] if order_numbers else ""
        default_seller = seller_names[0] if len(seller_names) == 1 else ""

        # Strategy 1: Find leaf item tables so each table's package boundary & seller context is preserved
        table_groups: List[Dict[str, Any]] = []
        for tbl_idx, tbl in enumerate(soup.find_all("table")):
            # Skip outer layout wrapper tables that contain nested tables with card items
            tbl_items: List[TCGPlayerCardItem] = []
            for tr in tbl.find_all("tr"):
                if tr.find("tr") is not None:
                    continue
                # Ensure this <tr> belongs directly to `tbl` (not a nested child `<table>`)
                parent_tbl = tr.find_parent("table")
                if parent_tbl is not tbl:
                    continue
                cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                cells = [c for c in cells if c]
                if len(cells) >= 2:
                    row_line = "\t".join(cells)
                    parsed = TCGPlayerPurchaseParser.parse_line(row_line, strict_email_mode=True)
                    if parsed:
                        tbl_items.append(parsed)

            if tbl_items:
                t_ord, t_sel = TCGPlayerPurchaseParser._extract_table_context(
                    tbl,
                    default_order_number=default_order_num,
                    default_seller=default_seller,
                )
                if t_ord and t_ord not in order_numbers:
                    order_numbers.append(t_ord)
                if t_sel and t_sel not in seller_names:
                    seller_names.append(t_sel)

                for it in tbl_items:
                    it.order_number = t_ord
                    it.seller = t_sel
                    it.order_date = email_date_str
                    it.package_index = len(table_groups)

                table_groups.append({
                    "order_number": t_ord,
                    "seller": t_sel,
                    "order_date": email_date_str,
                    "items": tbl_items,
                })

        if table_groups:
            # If seller_names were found in the email header in exact 1-to-1 count with untagged tables, pair them up
            untagged_groups = [g for g in table_groups if _is_generic_seller(g.get("seller", ""))]
            specific_sellers = [s for s in seller_names if not _is_generic_seller(s)]
            if len(untagged_groups) == len(specific_sellers) and len(specific_sellers) > 0:
                for grp, s_name in zip(untagged_groups, specific_sellers):
                    grp["seller"] = s_name
                    for it in grp["items"]:
                        it.seller = s_name

            deduped_groups = deduplicate_package_groups(table_groups)
            items: List[TCGPlayerCardItem] = []
            for pkg_idx, grp in enumerate(deduped_groups):
                for it in grp["items"]:
                    it.package_index = pkg_idx
                    items.append(it)
            return items, order_numbers, seller_names

        # Strategy 2: Fallback to plain-text line-by-line parsing if no HTML table rows matched
        items = TCGPlayerPurchaseParser.parse(full_text, strict_email_mode=True)
        for it in items:
            if not it.order_number and default_order_num:
                it.order_number = default_order_num
            if not it.seller and default_seller:
                it.seller = default_seller
            if not it.order_date and email_date_str:
                it.order_date = email_date_str

        return items, order_numbers, seller_names


class TCGPlayerEmailFetcher:
    """
    Connects to Gmail IMAP (imap.gmail.com:993) using an email and Google App Password
    to automatically fetch and parse TCGplayer Order Confirmation and Shipping emails.
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
        """
        Parses a raw RFC822 email message into a structured TCGplayer order dictionary,
        including per-package splits in `packages` and canonical headers in `manifest_text`.
        """
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

        # Extract order number and seller from subject if present
        for o in _extract_order_numbers(subject):
            if o not in order_numbers:
                order_numbers.append(o)
        for s in _extract_seller_names(subject):
            if s not in seller_names:
                seller_names.append(s)

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
            for s in _extract_seller_names(combined_text):
                if s not in seller_names:
                    seller_names.append(s)
            items = TCGPlayerPurchaseParser.parse(combined_text, strict_email_mode=True)

        if not items:
            return None

        primary_order_number = order_numbers[0] if order_numbers else f"TCG-{abs(hash(message_id or subject)) % 1000000:06d}"
        primary_seller = seller_names[0] if len(seller_names) == 1 else ""

        for it in items:
            if not it.order_number:
                it.order_number = primary_order_number
            if not it.seller and primary_seller:
                it.seller = primary_seller
            if not it.order_date and order_date_str:
                it.order_date = order_date_str

        # Build package groups within this email
        pkg_groups: List[Dict[str, Any]] = []
        for it in items:
            key = (it.order_number or primary_order_number, it.seller or "", it.package_index)
            if not pkg_groups or pkg_groups[-1]["_key"] != key:
                pkg_groups.append({
                    "_key": key,
                    "order_number": it.order_number or primary_order_number,
                    "seller": it.seller or primary_seller or "",
                    "order_date": it.order_date or order_date_str,
                    "order_timestamp": order_dt.isoformat() if order_dt else None,
                    "email_message_id": message_id,
                    "subject": subject or f"TCGplayer Order {primary_order_number}",
                    "from": from_hdr,
                    "items": [it],
                })
            else:
                pkg_groups[-1]["items"].append(it)

        pkg_groups = deduplicate_package_groups(pkg_groups)

        # Build per-package manifest blocks with canonical `=== Order #... | Seller: ... | Date: ... ===` headers
        packages_out: List[Dict[str, Any]] = []
        all_manifest_blocks: List[str] = []
        flattened_items: List[Dict[str, Any]] = []

        for idx, grp in enumerate(pkg_groups, start=1):
            g_ord = grp.get("order_number") or primary_order_number
            g_sel = grp.get("seller") or ""
            if not g_sel:
                if len(pkg_groups) > 1:
                    g_sel = f"Package {idx} of {len(pkg_groups)}"
                elif seller_names:
                    g_sel = ", ".join(seller_names[:3])
                else:
                    g_sel = "TCGplayer Marketplace"
            g_date = grp.get("order_date") or order_date_str
            g_items: List[TCGPlayerCardItem] = grp["items"]
            for it in g_items:
                it.order_number = g_ord
                it.seller = g_sel
                it.order_date = g_date
                flattened_items.append(it.to_dict())

            hdr = format_package_header(order_number=g_ord, seller=g_sel, order_date=g_date)
            lines = ([hdr] if hdr else []) + [format_item_manifest_line(it) for it in g_items]
            pkg_manifest = "\n".join(lines)
            all_manifest_blocks.append(pkg_manifest)

            packages_out.append({
                "order_number": g_ord,
                "all_order_numbers": [g_ord],
                "email_message_id": f"{message_id}#pkg{idx}" if len(pkg_groups) > 1 else message_id,
                "subject": subject or f"TCGplayer Order {g_ord}",
                "from": from_hdr,
                "order_date_str": g_date,
                "order_timestamp": order_dt.isoformat() if order_dt else None,
                "seller_summary": g_sel,
                "total_quantity": sum(it.quantity for it in g_items),
                "unique_cards_count": len(g_items),
                "items": [it.to_dict() for it in g_items],
                "manifest_text": pkg_manifest,
            })

        seller_summary_str = (
            ", ".join(seller_names[:3])
            if seller_names
            else (packages_out[0]["seller_summary"] if packages_out else "TCGplayer Marketplace")
        )

        return {
            "order_number": primary_order_number,
            "all_order_numbers": order_numbers or [primary_order_number],
            "email_message_id": message_id,
            "subject": subject or f"TCGplayer Order {primary_order_number}",
            "from": from_hdr,
            "order_date_str": order_date_str,
            "order_timestamp": order_dt.isoformat() if order_dt else None,
            "seller_summary": seller_summary_str,
            "total_quantity": sum(p["total_quantity"] for p in packages_out),
            "unique_cards_count": sum(p["unique_cards_count"] for p in packages_out),
            "items": flattened_items,
            "manifest_text": "\n".join(all_manifest_blocks),
            "packages": packages_out,
        }

    def fetch_recent_orders(self, days_back: int = 30, max_emails: int = 20) -> Dict[str, Any]:
        """
        Connects to Gmail IMAP, searches for recent TCGplayer order and shipping emails,
        deduplicates consolidated order emails against individual seller shipping emails,
        and returns each distinct (Order #, Seller) package with its own manifest header.
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

        raw_package_groups: List[Dict[str, Any]] = []
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
                        parsed_email = self.parse_raw_email_bytes(raw_bytes)
                        if parsed_email and parsed_email.get("packages"):
                            for pkg in parsed_email["packages"]:
                                raw_package_groups.append({
                                    "order_number": pkg["order_number"],
                                    "seller": pkg["seller_summary"],
                                    "order_date": pkg["order_date_str"],
                                    "order_timestamp": pkg.get("order_timestamp"),
                                    "email_message_id": pkg.get("email_message_id"),
                                    "subject": pkg.get("subject"),
                                    "from": pkg.get("from"),
                                    "items": [TCGPlayerCardItem(**it) for it in pkg["items"]],
                                })
                        break
        finally:
            if should_close and conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass

        # Deduplicate across all fetched emails (reconciling Order Confirmation vs. Seller Shipping emails)
        deduped_groups = deduplicate_package_groups(raw_package_groups)

        # Assign clean seller names if multiple packages share an order or lack seller names
        orders: List[Dict[str, Any]] = []
        all_items: List[Dict[str, Any]] = []
        all_manifest_lines: List[str] = ["Qty\tDescription"]

        for idx, grp in enumerate(deduped_groups, start=1):
            g_ord = grp.get("order_number") or f"TCG-PKG-{idx:03d}"
            g_sel = grp.get("seller") or "TCGplayer Marketplace"
            if _is_generic_seller(g_sel) and len(deduped_groups) > 1:
                qty_sum = sum(it.quantity for it in grp["items"])
                if len(grp["items"]) >= 25 or qty_sum >= 30:
                    g_sel = f"TCGplayer Direct (Package {idx})"
                else:
                    g_sel = f"Seller Package {idx}"
            g_date = grp.get("order_date") or ""
            g_items: List[TCGPlayerCardItem] = grp["items"]

            for it in g_items:
                it.order_number = g_ord
                it.seller = g_sel
                it.order_date = g_date
                it.package_index = idx - 1

            hdr = format_package_header(order_number=g_ord, seller=g_sel, order_date=g_date)
            pkg_lines = ([hdr] if hdr else []) + [format_item_manifest_line(it) for it in g_items]
            pkg_manifest = "\n".join(pkg_lines)

            item_dicts = [it.to_dict() for it in g_items]
            all_items.extend(item_dicts)
            all_manifest_lines.append(pkg_manifest)

            orders.append({
                "order_number": g_ord,
                "all_order_numbers": [g_ord],
                "email_message_id": grp.get("email_message_id") or f"pkg-{idx}",
                "subject": grp.get("subject") or f"TCGplayer Order {g_ord} ({g_sel})",
                "from": grp.get("from") or "sales@tcgplayer.com",
                "order_date_str": g_date,
                "order_timestamp": grp.get("order_timestamp"),
                "seller_summary": g_sel,
                "total_quantity": sum(it.quantity for it in g_items),
                "unique_cards_count": len(g_items),
                "items": item_dicts,
                "manifest_text": pkg_manifest,
            })

        return {
            "orders_found": len(orders),
            "total_cards_quantity": sum(o["total_quantity"] for o in orders),
            "orders": orders,
            "items": all_items,
            "manifest_text": "\n".join(all_manifest_lines) if orders else "",
        }
