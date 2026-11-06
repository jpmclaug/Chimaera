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

__all__ = [
    "ArchidektClient",
    "BRACKET_NAMES",
    "DeckAnalysisResult",
    "DeckClassifier",
    "EXTRA_TURN_CARDS",
    "EdhrecClient",
    "FINISHERS",
    "GAME_CHANGERS",
    "HttpJsonClient",
    "KNOWN_COMBOS",
    "MASS_LAND_DENIAL",
    "SAMPLE_DECKS",
    "STRONG_STAPLES",
    "ScoreReceipt",
    "ScryfallClient",
    "SpellbookClient",
    "format_analysis_report",
    "parse_decklist_text",
    "slugify_commander",
]
