"""Timewalked-style Commander Deck Analyzer & Bracket Classifier."""

from .api_clients import (
    ArchidektClient,
    EdhrecClient,
    HttpJsonClient,
    ScryfallClient,
    SpellbookClient,
    parse_decklist_text,
    slugify_commander,
)
from .card_db import BRACKET_NAMES, EXTRA_TURN_CARDS, FINISHERS, GAME_CHANGERS, KNOWN_COMBOS, MASS_LAND_DENIAL, STRONG_STAPLES
from .classifier import DeckAnalysisResult, DeckClassifier, ScoreReceipt
from .cli import SAMPLE_DECKS, format_analysis_report
from .upgrades import (
    DEFAULT_RATIO_TARGETS,
    RATIO_CATEGORY_LABELS,
    SuggestedUpgradeEngine,
    UpgradeSuggestion,
    classify_timewalked_categories,
    compute_ratio_breakdown,
    ratio_state_for,
)

__all__ = [
    "ArchidektClient",
    "BRACKET_NAMES",
    "DEFAULT_RATIO_TARGETS",
    "DeckAnalysisResult",
    "DeckClassifier",
    "EXTRA_TURN_CARDS",
    "EdhrecClient",
    "FINISHERS",
    "GAME_CHANGERS",
    "HttpJsonClient",
    "KNOWN_COMBOS",
    "MASS_LAND_DENIAL",
    "RATIO_CATEGORY_LABELS",
    "SAMPLE_DECKS",
    "STRONG_STAPLES",
    "ScoreReceipt",
    "ScryfallClient",
    "SpellbookClient",
    "SuggestedUpgradeEngine",
    "UpgradeSuggestion",
    "classify_timewalked_categories",
    "compute_ratio_breakdown",
    "format_analysis_report",
    "parse_decklist_text",
    "ratio_state_for",
    "slugify_commander",
]
