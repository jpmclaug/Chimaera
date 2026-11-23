"""
Shared sealed MTG product title matcher for multi-retailer surveillance
(Card Kingdom sealed pricelist, Shopify Global Catalog / Shop app, etc.).

Reuses classify_sealed_product() from providers.tcgplayer_sealed so category
and booster-subtype rules stay consistent across all providers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .tcgplayer_sealed import classify_sealed_product


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    score: float
    reason: str


# Boilerplate prefixes / noise tokens stripped when comparing set names
_BOILERPLATE_PATTERNS = [
    r"\bmagic\s*:\s*the\s+gathering\b",
    r"\bmagic\s+the\s+gathering\b",
    r"\buniverses\s+beyond\b",
    r"\bwizards\s+of\s+the\s+coast\b",
    r"\bmtg\b",
    r"\benglish\b",
    r"\bsealed\b",
    r"\bfactory\s+sealed\b",
    r"\bbrand\s+new\b",
    r"\bnew\b",
    r"\bhot\s+sale\b",
    r"\btrading\s+card\s+game\b",
    r"\btrading\s+cards?\b",
    r"\btcg\b",
    r"\bcca\b",
]

# Product-form keywords stripped when isolating the set name
_PRODUCT_FORM_PATTERNS = [
    r"\bsleeved\b",
    r"\bomega\b",
    r"\bsample\b",
    r"\bcollector(?:'s|s)?\b",
    r"\bplay\b",
    r"\bset\b",
    r"\bdraft\b",
    r"\bjumpstart\b",
    r"\bbooster\b",
    r"\bboosters\b",
    r"\bdisplay\b",
    r"\bbox\b",
    r"\bpacks?\b",
    r"\bbundle\b",
    r"\bfat\s+pack\b",
    r"\bprerelease\b",
    r"\bpre-release\b",
    r"\bkit\b",
    r"\bblister\b",
    r"\bhanger\b",
    r"\bgift\b",
    r"\bcompleat\b",
    r"\bdeck\b",
    r"\bprecon\b",
    r"\bvalue\b",
    r"\b\d+\s*(?:packs?|cards?|boosters?)\b",
]

_STOPWORDS = {"the", "of", "and", "in", "for", "a", "an", "to", "with", "on", "at", "by"}

# Hard-reject patterns unless explicitly present in the watched item name
_REJECT_PATTERNS = [
    (r"\bcase\b", "case"),
    (r"\blot\s+of\b", "lot"),
    (r"\bempty\b", "empty"),
    (r"\bopened\b", "opened"),
    (r"\bno\s+packs?\b", "no_packs"),
    (r"\bbox\s+only\b", "box_only"),
    (r"\bproxy\b", "proxy"),
    (r"\bart\s+card\b", "art_card"),
    (r"\bsingle\s+card\b", "single_card"),
    (r"\bplaymat\b", "accessory"),
    (r"\bdeck\s+box\b", "accessory"),
    (r"\bbinder\b", "accessory"),
    (r"\bportfolio\b", "accessory"),
    (r"\bultra\s*pro\b", "accessory"),
    (r"\bdragon\s+shield\b", "accessory"),
    (r"\bcard\s+sleeves\b", "accessory"),
    (r"\bsleeves\b", "sleeves_accessory"),
    (r"\bpromo\s+pack\b", "promo_pack"),
    (r"\bwelcome\s+booster\b", "welcome_booster"),
    (r"\bsample\s+pack\b", "sample_pack"),
    (r"\btheme\s+booster\b", "theme_booster"),
    (r"\bcollector\s+sample\b", "sample_pack"),
    (r"\bbundle\s+of\b", "multi_bundle"),
    (r"\bset\s+of\s+\d+\b", "multi_set"),
    (r"\bcombo\b", "combo"),
    (r"\b(?:japanese|jpn|french|german|italian|spanish|korean|chinese|portuguese|russian)\b", "non_english"),
]

# Multi-unit multiplier patterns like "x2", "2x", "(2)", "2-pack", "3 pack" (for counts 2..24)
_MULTI_UNIT_PATTERNS = [
    r"\b(?:[2-9]|1[0-9]|2[0-4])\s*[xX]\b",
    r"\b[xX]\s*(?:[2-9]|1[0-9]|2[0-4])\b",
    r"\b(?:[2-9]|1[0-2])\s*-\s*pack\b",
    r"\b(?:[2-9]|1[0-2])\s+packs?\s+lot\b",
    r"\b(?:[2-9]|1[0-2])\s+boxes?\b",
    r"\b(?:[2-9]|1[0-2])\s+displays?\b",
]


def _normalize_text(text: str) -> str:
    s = (text or "").lower()
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"[/|•·]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _canonical_category_and_subtype(title: str, handle: str = "") -> tuple[str, str]:
    """
    Returns (category, subtype) using classify_sealed_product, with a fallback
    to checking the Shopify URL handle when the title omits the subtype
    (e.g. title 'Wilds of Eldraine Booster Box', handle '...-set-booster-box').
    """
    cat, _, subtype = classify_sealed_product(title)
    if handle:
        handle_text = handle.replace("-", " ").replace("_", " ")
        h_cat, _, h_sub = classify_sealed_product(handle_text)
        if subtype == "Standard" and h_sub != "Standard":
            subtype = h_sub
        if cat == "case_other" and h_cat != "case_other":
            cat = h_cat
    return cat, subtype


def _extract_set_tokens(text: str) -> set[str]:
    """
    Strips boilerplate and sealed product-form words to isolate significant
    set-identifying tokens (e.g. {'wilds', 'eldraine'}, {'modern', 'horizons', '3'}).
    """
    s = _normalize_text(text)
    for pat in _BOILERPLATE_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)
    for pat in _PRODUCT_FORM_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)
    tokens = re.findall(r"[a-z0-9]+", s)
    return {t for t in tokens if t not in _STOPWORDS and (len(t) >= 2 or t.isdigit())}


def _has_sleeved_marker(text: str) -> bool:
    t = _normalize_text(text)
    return bool(re.search(r"\b(?:sleeved|blister|hanger)\b", t))


def _has_omega_marker(text: str) -> bool:
    t = _normalize_text(text)
    return bool(re.search(r"\bomega\b", t))


def _has_gift_or_compleat(text: str) -> bool:
    t = _normalize_text(text)
    return bool(re.search(r"\b(?:gift|compleat)\b", t))


def _has_aftermath(text: str) -> bool:
    return bool(re.search(r"\baftermath\b", _normalize_text(text)))


def _has_commander_word(text: str) -> bool:
    return bool(re.search(r"\bcommander\b", _normalize_text(text)))


def build_search_query(item_name: str) -> str:
    """
    Builds a clean search query string for external retailers (Shopify Catalog)
    from a TCGplayer sealed watchlist item name.
    """
    name = (item_name or "").strip()
    if not name:
        return ""
    # Replace ' - ' separator with space and normalize Display -> Booster Box/Display cleanly
    cleaned = re.sub(r"\s+-\s+", " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if "magic" not in cleaned.lower() and "mtg" not in cleaned.lower():
        cleaned = f"MTG {cleaned}"
    return cleaned


def match_sealed(
    listing_title: str,
    item_name: str,
    *,
    listing_edition: str = "",
    listing_handle: str = "",
) -> MatchResult:
    """
    Determines whether a retailer's sealed listing matches a watched sealed MTG item.

    Args:
        listing_title: Product title from the retailer (e.g. Card Kingdom or Shopify).
        item_name: WatchlistItem.name (e.g. 'Wilds of Eldraine - Set Booster Display').
        listing_edition: Optional edition/set field (e.g. Card Kingdom's `edition`).
        listing_handle: Optional URL slug/handle for subtype disambiguation.

    Returns:
        MatchResult(matched, score, reason)
    """
    if not listing_title or not item_name:
        return MatchResult(False, 0.0, "empty_input")

    title_norm = _normalize_text(listing_title)
    item_norm = _normalize_text(item_name)
    combined_listing = f"{listing_edition} {listing_title}".strip() if listing_edition else listing_title

    # 1. Hard rejections (unless the watched item itself explicitly contains that keyword)
    for pat, reason in _REJECT_PATTERNS:
        if re.search(pat, title_norm) and not re.search(pat, item_norm):
            return MatchResult(False, 0.0, f"rejected:{reason}")

    # Reject multi-unit listings (2x, x3, 2-pack, 6 boxes) unless watched item has it
    for pat in _MULTI_UNIT_PATTERNS:
        if re.search(pat, title_norm) and not re.search(pat, item_norm):
            return MatchResult(False, 0.0, "rejected:multi_unit")

    # Reject single "sleeve" when item is not a sleeved pack
    if not _has_sleeved_marker(item_norm) and re.search(r"\bsleeve\b", title_norm):
        return MatchResult(False, 0.0, "rejected:sleeve_accessory")

    # 2. Sub-set disambiguation: Aftermath vs base March of the Machine; Commander vs non-Commander
    if _has_aftermath(combined_listing) != _has_aftermath(item_name):
        return MatchResult(False, 0.0, "mismatch:aftermath")

    if _has_commander_word(combined_listing) != _has_commander_word(item_name):
        return MatchResult(False, 0.0, "mismatch:commander")

    # 3. Category & booster subtype equivalence
    item_cat, item_sub = _canonical_category_and_subtype(item_name)
    list_cat, list_sub = _canonical_category_and_subtype(listing_title, handle=listing_handle)

    if item_cat != list_cat:
        return MatchResult(False, 0.0, f"mismatch:category({item_cat}!={list_cat})")

    if item_sub != list_sub:
        return MatchResult(False, 0.0, f"mismatch:subtype({item_sub}!={list_sub})")

    # 4. Variant modifiers: Sleeved packs, Omega packs, Gift/Compleat bundles
    if item_cat == "booster_pack":
        if _has_sleeved_marker(item_name) != _has_sleeved_marker(listing_title):
            return MatchResult(False, 0.0, "mismatch:sleeved")
        if _has_omega_marker(item_name) != _has_omega_marker(listing_title):
            return MatchResult(False, 0.0, "mismatch:omega")

    if item_cat == "bundle":
        if _has_gift_or_compleat(item_name) != _has_gift_or_compleat(listing_title):
            return MatchResult(False, 0.0, "mismatch:gift_or_compleat")

    # 5. Set-token matching: every significant token of the watched set must appear in the listing
    item_tokens = _extract_set_tokens(item_name)
    listing_tokens = _extract_set_tokens(combined_listing)

    if not item_tokens:
        return MatchResult(False, 0.0, "no_set_tokens")

    missing = item_tokens - listing_tokens
    if missing:
        return MatchResult(False, 0.0, f"missing_set_tokens:{','.join(sorted(missing))}")

    # Penalize excessive extra set tokens (e.g. a different set sharing a word like "Modern Horizons 3" vs "Modern Horizons 2")
    extra = listing_tokens - item_tokens
    # Reject conflicting numeric tokens (e.g. "2" vs "3" in Modern Horizons or Mystery Booster)
    item_nums = {t for t in item_tokens if t.isdigit()}
    extra_nums = {t for t in extra if t.isdigit()}
    if extra_nums and extra_nums != item_nums:
        # Ignore common pack-count numbers that may linger (e.g. 30, 36, 12, 15, 24, 360)
        non_pack_nums = extra_nums - {"6", "9", "12", "14", "15", "18", "20", "24", "30", "36", "180", "360"}
        if non_pack_nums:
            return MatchResult(False, 0.0, f"conflicting_numbers:{','.join(sorted(non_pack_nums))}")

    # Jaccard-like score biased toward full recall of item_tokens
    overlap = len(item_tokens)
    union = len(item_tokens | listing_tokens)
    score = round(overlap / max(1, union), 3)

    # Require reasonable precision (avoid wildly unrelated titles that happen to contain 1 generic word)
    if len(item_tokens) == 1 and len(extra) > 4:
        return MatchResult(False, score, "too_many_extra_tokens")

    return MatchResult(True, score, "matched")
