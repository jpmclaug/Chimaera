"""
Card name normalization, Unicode accent stripping, mojibake repair, and flexible matching utilities for Chimaera MTG.
"""

import html
import re
import unicodedata
from typing import Set, List, Optional


def fix_mojibake(text: Optional[str]) -> str:
    """
    Detects and repairs UTF-8 bytes mistakenly decoded as Latin-1 or CP1252.
    For example:
        'GlÃ³in the Mighty' -> 'Glóin the Mighty'
        'MjÃ¶lnir' -> 'Mjölnir'
        'DÃ¡in' -> 'Dáin'
    """
    if not text:
        return ""
    
    clean = str(text)
    # Check for common UTF-8 mojibake signatures (Ã, Â, â€, etc.)
    if any(marker in clean for marker in ("\u00c3", "\u00c2", "\u00e2\u20ac")):
        for enc in ("latin-1", "cp1252"):
            try:
                candidate = clean.encode(enc).decode("utf-8")
                # Ensure valid non-empty result
                if candidate and "\ufffd" not in candidate:
                    clean = candidate
                    break
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass

    return clean


def strip_accents(text: Optional[str]) -> str:
    """
    Removes diacritics / combining accents from a string using NFKD Unicode normalization.
    For example:
        'Glóin the Mighty' -> 'Gloin the Mighty'
        'Mjölnir, Hammer of Thor' -> 'Mjolnir, Hammer of Thor'
        'Dáin Ironfoot' -> 'Dain Ironfoot'
        'The Balrog, Flame of Udûn' -> 'The Balrog, Flame of Udun'
    """
    if not text:
        return ""
    clean = fix_mojibake(str(text))
    nfkd = unicodedata.normalize("NFKD", clean)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_card_name(name: Optional[str], strip_diacritics: bool = False) -> str:
    """
    Cleans and standardizes a card name:
    - Repairs mojibake
    - Strips HTML tags and unescapes entities
    - Standardizes smart/curly quotes & apostrophes
    - Strips trailing foil or set annotations (e.g. *F*, *Etched*, (HOB) 99)
    - Normalizes double-faced card (DFC) slashes to standard ' // '
    - Optionally strips diacritics / accents
    """
    if not name:
        return ""

    clean = fix_mojibake(str(name))
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = html.unescape(clean).strip("\"'").strip()

    # Standardize curly quotes and apostrophes
    clean = clean.replace("\u2019", "'").replace("\u2018", "'")
    clean = clean.replace("\u201C", '"').replace("\u201D", '"')
    clean = clean.replace("\u00B4", "'").replace("`", "'")

    # Strip trailing annotation asterisks (e.g. *F*, *Etched*)
    clean = re.sub(r"\s*\*.*?\*\s*$", "", clean)

    # Standardize Double-Faced Card (DFC) / Adventure slashes
    if "/" in clean:
        parts = [p.strip() for p in re.split(r"\s*/+\s*", clean) if p.strip()]
        if len(parts) >= 2:
            clean = " // ".join(parts)

    if strip_diacritics:
        clean = strip_accents(clean)

    return clean.strip().strip("\"'").strip()


def get_card_match_keys(card_name: Optional[str]) -> Set[str]:
    """
    Generates all canonical matching keys for a card name:
    1. Lowercase original (with accents)
    2. Lowercase unaccented (ASCII-folded)
    3. Lowercase front face (if DFC / adventure)
    4. Lowercase unaccented front face
    
    Example for 'Glóin the Mighty // Easy Pickings':
        {
            'glóin the mighty // easy pickings',
            'gloin the mighty // easy pickings',
            'glóin the mighty',
            'gloin the mighty'
        }
    """
    if not card_name:
        return set()

    clean = normalize_card_name(card_name)
    if not clean:
        return set()

    keys: Set[str] = set()

    # 1. Full name lowercase
    full_lower = clean.lower()
    keys.add(full_lower)

    # 2. Full name ASCII unaccented
    full_ascii = strip_accents(full_lower)
    keys.add(full_ascii)

    # 3. Front face (before ' // ')
    if " // " in clean:
        front_clean = clean.split(" // ")[0].strip()
        front_lower = front_clean.lower()
        keys.add(front_lower)
        keys.add(strip_accents(front_lower))

    return keys


def card_names_match(name1: Optional[str], name2: Optional[str]) -> bool:
    """
    Determines if two card names refer to the same card, accounting for:
    - Diacritics/accents (e.g. 'Glóin the Mighty' vs 'Gloin the Mighty')
    - Double-faced/Adventure cards vs front face (e.g. 'Glóin the Mighty // Easy Pickings' vs 'Gloin the Mighty')
    - Mojibake (e.g. 'GlÃ³in the Mighty' vs 'Glóin the Mighty')
    - Case sensitivity and surrounding whitespace
    """
    if not name1 or not name2:
        return False

    keys1 = get_card_match_keys(name1)
    keys2 = get_card_match_keys(name2)
    return bool(keys1.intersection(keys2))
