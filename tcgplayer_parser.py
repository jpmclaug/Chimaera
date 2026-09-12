"""
TCGplayer Purchase Order Parser module for Chimaera MTG.
Parses card purchase manifests copied from TCGplayer order history, confirmation emails,
and invoices, extracting quantities, canonical card names, set names, conditions, and finishes.
"""

import re
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any, Tuple
from card_utils import normalize_card_name, card_names_match, fix_mojibake


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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TCGPlayerPurchaseParser:
    """Parses text containing TCGplayer card purchases."""

    @staticmethod
    def parse(text: str) -> List[TCGPlayerCardItem]:
        """
        Parses raw text into a list of TCGPlayerCardItem objects.
        Handles headers, tab-delimited, space-delimited, and multi-part card descriptions.
        """
        if not text:
            return []

        text = fix_mojibake(text)
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        items: List[TCGPlayerCardItem] = []

        for line in lines:
            parsed = TCGPlayerPurchaseParser.parse_line(line)
            if parsed:
                items.append(parsed)

        return items

    @staticmethod
    def parse_line(line: str) -> Optional[TCGPlayerCardItem]:
        """Parses a single line of TCGplayer purchase manifest."""
        clean_line = line.strip()
        if not clean_line:
            return None

        # Ignore header rows
        lower_line = clean_line.lower()
        if (
            lower_line.startswith("qty")
            or lower_line.startswith("quantity")
            or lower_line.startswith("product name")
            or lower_line == "qty\tdescription"
        ):
            return None

        # Extract Quantity and Description
        qty = 1
        desc = clean_line

        # Case 1: Tab-separated e.g. "1\tMagic - Aether Revolt - Metallic Rebuke - Near Mint"
        tab_match = re.match(r"^(\d+)\s*\t\s*(.+)$", clean_line)
        if tab_match:
            qty = int(tab_match.group(1))
            desc = tab_match.group(2).strip()
        else:
            # Case 2: Multi-space separated e.g. "1   Magic - Aether Revolt..."
            multi_space = re.match(r"^(\d+)\s{2,}(.+)$", clean_line)
            if multi_space:
                qty = int(multi_space.group(1))
                desc = multi_space.group(2).strip()
            else:
                # Case 3: Leading quantity followed by "x" or space e.g. "1x Magic - ..." or "1 Magic - ..."
                qty_lead = re.match(r"^(\d+)\s*x?\s+(.+)$", clean_line)
                if qty_lead:
                    qty = int(qty_lead.group(1))
                    desc = qty_lead.group(2).strip()

        # Remove surrounding quotes if from CSV/TSV copy
        desc = desc.strip("\"'").strip()
        if not desc:
            return None

        # Parse Description into Set, Card Name, Treatment, Finish, Condition
        parts = [p.strip() for p in desc.split(" - ") if p.strip()]

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
                condition = KNOWN_CONDITIONS[remaining[-1].lower()]
                remaining = remaining[:-1]

            # Check for finish / treatment at the end of remaining parts
            cleaned_remaining = []
            for p in remaining:
                p_lower = p.lower()
                if p_lower in FINISH_KEYWORDS:
                    finish = FINISH_KEYWORDS[p_lower]
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

        # Extract collector number or variant in parentheses from card name
        # e.g. "Clockwork Percussionist (0130)" -> col "0130"
        # e.g. "Swamp (274)" -> col "274"
        # e.g. "Abdel Adrian, Gorion's Ward (Showcase)" -> treatment "Showcase"
        collector_number = ""
        p_match = re.search(r"\(([^)]+)\)", card_raw)
        if p_match:
            paren_val = p_match.group(1).strip()
            # If purely digits or formatted collector number like "0130", "274", "A-24"
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
        )
