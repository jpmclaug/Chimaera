"""
Chimera Model Context Protocol (MCP) Server Engine.

Provides stateless Streamable HTTP, Server-Sent Events (SSE), and stdio MCP support
so Google Gemini Mobile App (via Gemini Connected Apps), Custom Gems, and Antigravity
skills can query and interact with the Chimera PostgreSQL / SQLite database on Google Cloud Run.
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from card_utils import (
    card_names_match,
    get_card_match_keys,
    normalize_card_name,
    strip_accents,
)
from models import (
    BestBuyItem,
    CardAddAnalysis,
    DeckAnalysis,
    MicrocenterItem,
    SecretLairAnalysis,
    SystemSetting,
    User,
    UserInventoryCard,
    VendorPrice,
    WatchlistItem,
    db,
)

logger = logging.getLogger(__name__)

MCP_SERVER_NAME = "chimera-mtg-mcp"
MCP_SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07"]
DEFAULT_PROTOCOL_VERSION = "2025-03-26"


MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_inventory_summary",
        "description": (
            "Returns a comprehensive summary of the user's Magic: The Gathering card collection "
            "stored in Chimera, including total card count, unique printings, total USD market value, "
            "foil vs non-foil breakdown, binder distribution, color identity breakdown, and top valuable cards."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of highest-value cards to include in the summary (default 15, max 50).",
                    "default": 15,
                }
            },
            "required": [],
        },
    },
    {
        "name": "check_inventory_cards",
        "description": (
            "Quickly checks whether a single card or multiple Magic: The Gathering cards are present in the user's "
            "inventory collection. Accepts a list of card names, an array of card objects, a single card name, or a "
            "raw decklist string. Returns which cards are owned, total quantities owned, foil/non-foil availability, "
            "binder locations, USD market valuations, and which cards are missing from the collection."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of card names to check in inventory (e.g. ['Rhystic Study', 'Sol Ring', 'Cyclonic Rift']).",
                },
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alias for 'cards' - list of card names to check.",
                },
                "card_name": {
                    "type": "string",
                    "description": "A single card name to check (e.g. 'Rhystic Study').",
                },
                "text": {
                    "type": "string",
                    "description": "Decklist or multi-line text containing card names (e.g. '1 Sol Ring\\n2 Rhystic Study').",
                },
                "query": {
                    "type": "string",
                    "description": "Optional search string or comma-separated list of card names.",
                },
                "binder_name": {
                    "type": "string",
                    "description": "Optional binder/folder filter to restrict ownership verification to a specific binder.",
                },
                "include_details": {
                    "type": "boolean",
                    "description": "Whether to include full itemized printing records (set, condition, foil, binder, price) for each matched card. Defaults to true.",
                    "default": True,
                },
                "match_mode": {
                    "type": "string",
                    "enum": ["smart", "exact"],
                    "description": "Matching algorithm: 'smart' (default, handles accents, DFC split cards, and punctuation) or 'exact'.",
                    "default": "smart",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_inventory",
        "description": (
            "Searches the user's Magic: The Gathering card collection/inventory by card name, "
            "set code, condition, foil finish, binder name, type line, oracle text, or color identity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Card name or partial name to search for (e.g. 'Rhystic Study', 'Sol Ring', 'Dragon').",
                },
                "set_code": {
                    "type": "string",
                    "description": "Optional set code filter (e.g. 'CMM', 'MH3', 'LTR').",
                },
                "foil": {
                    "type": "string",
                    "description": "Optional finish filter: 'normal', 'foil', or 'etched'.",
                },
                "condition": {
                    "type": "string",
                    "description": "Optional condition filter (e.g. 'Near Mint', 'NM', 'LP').",
                },
                "binder_name": {
                    "type": "string",
                    "description": "Optional binder/folder name filter.",
                },
                "type_line": {
                    "type": "string",
                    "description": "Optional card type filter (e.g. 'Creature', 'Artifact', 'Land', 'Instant').",
                },
                "color_identity": {
                    "type": "string",
                    "description": "Optional color identity filter (e.g. 'U', 'B', 'W,U').",
                },
                "min_price": {
                    "type": "number",
                    "description": "Optional minimum USD price per card.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matching inventory entries to return (default 25, max 100).",
                    "default": 25,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_watchlist",
        "description": (
            "Retrieves the user's monitored MTG watchlist cards, including target acquisition prices, "
            "current lowest in-stock vendor prices across TCGplayer, Mighty Meeple, and eBay, "
            "price ratings, and whether the card is currently an active deal. Supports filtering by "
            "a single card or a batch of card names, tags, or decklist text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional card name filter.",
                },
                "cards": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of card names to filter on the watchlist.",
                },
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alias for 'cards' - list of card names to filter.",
                },
                "text": {
                    "type": "string",
                    "description": "Optional decklist or multi-line text to filter watchlist items.",
                },
                "tag": {
                    "type": "string",
                    "description": "Optional watchlist tag filter.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of tags to filter (returns cards matching any specified tag).",
                },
                "deals_only": {
                    "type": "boolean",
                    "description": "If true, only returns watchlist cards currently at or below their target price.",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of watchlist items to return (default 50, max 200).",
                    "default": 50,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_active_deals",
        "description": (
            "Retrieves all currently active MTG deals across single-card watchlists (TCGplayer, Mighty Meeple, eBay), "
            "MicroCenter local store inventory, and Best Buy retail stock."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional source filter: 'all', 'singles', 'microcenter', or 'bestbuy'.",
                    "default": "all",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_deck_analyses",
        "description": (
            "Lists the user's saved Commander decks and Gemini tactical deck analyses, or retrieves "
            "detailed analysis, card ratings, mana curve stats, weaknesses, tactics, and upgrade "
            "recommendations for a single deck or multiple decks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deck_id": {
                    "type": "integer",
                    "description": "Optional specific DeckAnalysis ID to inspect in full detail.",
                },
                "deck_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional list of DeckAnalysis IDs to inspect in batch.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional deck name or commander name to search for (e.g. 'Ur-Dragon', 'Sauron').",
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of deck or commander names to search/retrieve in batch.",
                },
                "include_cards": {
                    "type": "boolean",
                    "description": "If true when inspecting decks, includes the full parsed card list (defaults to True).",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_deck_cards",
        "description": (
            "Returns cards in one or multiple Commander decks, including quantities, type lines, mana costs, "
            "CMC, USD prices, and color identity. Cards are returned grouped by category (Commander, "
            "Creatures, Instants, Sorceries, Artifacts, Enchantments, Planeswalkers, Lands) and as "
            "clean formatted text decklists."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Name of the deck or commander to retrieve (e.g. 'Ur-Dragon', 'Sauron'). Leave blank to get the user's latest deck.",
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of deck names or commanders to retrieve in batch.",
                },
                "deck_id": {
                    "type": "integer",
                    "description": "Optional specific DeckAnalysis ID.",
                },
                "deck_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional list of DeckAnalysis IDs to retrieve in batch.",
                },
                "group_by_type": {
                    "type": "boolean",
                    "description": "Whether to group cards by category (Commander, Creatures, Instants, Sorceries, Artifacts, Enchantments, Planeswalkers, Lands). Defaults to True.",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_card_prices",
        "description": (
            "Looks up live Scryfall market pricing and oracle metadata for one or multiple Magic: The Gathering cards, "
            "and cross-references whether the user already owns copies in inventory or tracks them on their watchlist. "
            "Accepts a single card name, an array of card names, a decklist text, or CSV content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object"}
                        ]
                    },
                    "description": "List of card names (e.g. ['Rhystic Study', 'Sol Ring']) or card objects to price in batch.",
                },
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alias for 'cards' - list of card names to look up.",
                },
                "card_name": {
                    "type": "string",
                    "description": "Exact or partial Magic: The Gathering card name (or semicolon-separated card names) to look up.",
                },
                "text": {
                    "type": "string",
                    "description": "Decklist or multi-line text containing card names to price in batch.",
                },
                "csv_content": {
                    "type": "string",
                    "description": "Optional CSV content containing card names to price in batch.",
                },
                "set_code": {
                    "type": "string",
                    "description": "Optional 3-4 letter set code (e.g. 'MH3', 'LTR').",
                },
            },
            "required": [],
        },
    },
    {
        "name": "add_to_watchlist",
        "description": (
            "Adds one or multiple Magic: The Gathering cards to the user's Chimera surveillance watchlist "
            "with optional target buy prices, set codes, finishes, and tags. Accepts a list of card names, "
            "an array of card objects, raw decklist text, ManaBox CSV content, or a single card name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object"}
                        ]
                    },
                    "description": "List of card names (e.g. ['Smothering Tithe', 'Rhystic Study']) or objects with per-card settings (card_name, target_price, set_code, finish, tag).",
                },
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alias for 'cards' - list of card names to add to the watchlist.",
                },
                "card_name": {
                    "type": "string",
                    "description": "A single card name, or semicolon-separated card list (e.g. 'Smothering Tithe' or 'Smothering Tithe; Esper Sentinel').",
                },
                "text": {
                    "type": "string",
                    "description": "Decklist or multi-line text containing card names to add.",
                },
                "csv_content": {
                    "type": "string",
                    "description": "Optional CSV spreadsheet content containing cards to add to the watchlist.",
                },
                "target_price": {
                    "type": "number",
                    "description": "Target USD acquisition price threshold for deal alerts. Applied to all added cards that do not specify an individual target price.",
                },
                "target_strategy": {
                    "type": "string",
                    "enum": ["none", "good_deal", "great_deal"],
                    "description": "Optional automatic target price strategy: 'good_deal' (10% discount off market) or 'great_deal' (20% discount off market).",
                },
                "set_code": {
                    "type": "string",
                    "description": "Optional set code (leave empty or 'ANY' to monitor lowest price across all sets).",
                },
                "finish": {
                    "type": "string",
                    "description": "Card finish: 'nonfoil', 'foil', or 'etched' (default 'nonfoil').",
                    "default": "nonfoil",
                },
                "tag": {
                    "type": "string",
                    "description": "Optional category tag (e.g. 'Staples', 'Ur-Dragon Upgrades'). Applied to all added cards that do not specify a tag.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_card_deck_fit",
        "description": (
            "Evaluates how one or multiple Magic: The Gathering cards fit across the user's saved Commander decks: "
            "checks color identity compatibility, whether each card is already in any deck, whether it appears "
            "in saved upgrade recommendations or Card Add / Secret Lair analyses, and whether the user owns copies. "
            "Accepts a single card name, a list of card names or objects, decklist text, or CSV content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object"}
                        ]
                    },
                    "description": "List of card names (e.g. ['Smothering Tithe', 'Rhystic Study']) or objects to evaluate across all saved decks.",
                },
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alias for 'cards' - list of card names to evaluate.",
                },
                "card_name": {
                    "type": "string",
                    "description": "Name of a card (or semicolon-separated list) to check across the user's Commander fleet.",
                },
                "text": {
                    "type": "string",
                    "description": "Decklist or multi-line text containing card names to evaluate in batch.",
                },
                "csv_content": {
                    "type": "string",
                    "description": "Optional CSV content containing card names to evaluate in batch.",
                },
                "deck_id": {
                    "type": "integer",
                    "description": "Optional specific DeckAnalysis ID to restrict the evaluation to a single deck.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional deck or commander name filter to restrict the evaluation.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "upload_inventory",
        "description": (
            "Bulk uploads or imports Magic: The Gathering cards into the user's collection inventory in Chimera. "
            "Accepts a list of card objects/names, raw decklist text, ManaBox CSV content, TCGplayer purchase order text, "
            "or a public Google Drive CSV share link. Defaults to 'merge' mode (adds/increments quantities to existing cards) "
            "and also supports 'replace' mode (clears existing inventory before importing)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object"}
                        ]
                    },
                    "description": "List of card names or objects with card fields (name, quantity, set_code, collector_number, foil, condition, binder_name, purchase_price).",
                },
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alias for 'cards' - list of card names to import.",
                },
                "card_name": {
                    "type": "string",
                    "description": "Single card name, or semicolon-delimited list of card names to import.",
                },
                "text": {
                    "type": "string",
                    "description": "Decklist, multi-line card text, or TCGplayer purchase manifest text.",
                },
                "csv_content": {
                    "type": "string",
                    "description": "Raw ManaBox collection CSV export content.",
                },
                "gdrive_url": {
                    "type": "string",
                    "description": "Public Google Drive share URL or file ID to a ManaBox CSV export.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["merge", "replace"],
                    "description": "Import mode: 'merge' (default, adds quantities to existing inventory) or 'replace' (purges existing inventory first).",
                    "default": "merge",
                },
                "binder_name": {
                    "type": "string",
                    "description": "Default binder/folder name to assign to imported cards (e.g. 'Trade Binder', 'Commander Staples').",
                },
                "condition": {
                    "type": "string",
                    "description": "Default condition for imported cards (e.g. 'Near Mint', 'Lightly Played'). Defaults to 'Near Mint'.",
                    "default": "Near Mint",
                },
                "foil": {
                    "type": "string",
                    "description": "Default finish for imported cards: 'normal', 'foil', or 'etched'. Defaults to 'normal'.",
                    "default": "normal",
                },
            },
            "required": [],
        },
    },
    {
        "name": "upload_decks",
        "description": (
            "Bulk uploads or imports one or multiple Magic: The Gathering Commander decks into the user's Chimera Deck Vault. "
            "Accepts ManaBox, Moxfield, Archidekt, Scryfall, or MTGGoldfish URLs, an array of deck objects, raw decklist text, "
            "or CSV deck exports. Enriches all cards with Scryfall metadata and computes full tactical deck analysis metrics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of public deck URLs to import (ManaBox, Moxfield, Archidekt, Scryfall, MTGGoldfish).",
                },
                "url": {
                    "type": "string",
                    "description": "Single deck URL to import.",
                },
                "decks": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of deck objects containing {deck_name, commander, url, text, csv_content, cards, is_pauper}.",
                },
                "text": {
                    "type": "string",
                    "description": "Decklist or multi-line text for a deck to import.",
                },
                "csv_content": {
                    "type": "string",
                    "description": "CSV content of a deck export to import.",
                },
                "deck_name": {
                    "type": "string",
                    "description": "Optional name for the deck when importing from text/csv.",
                },
                "commander_name": {
                    "type": "string",
                    "description": "Optional commander name when importing from text/csv.",
                },
                "is_pauper": {
                    "type": "boolean",
                    "description": "Whether the deck is Pauper Commander (PDH). Defaults to auto-detect.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "reconcile_watchlist",
        "description": (
            "Bulk removes or reconciles cards from the user's surveillance watchlist that were purchased or acquired. "
            "Accepts a pasted TCGplayer order history manifest, a list of card names, raw text, or specific watchlist IDs. "
            "Optionally ingests acquired cards directly into the user's collection inventory (UserInventoryCard)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "TCGplayer purchase manifest text, decklist, or multi-line card text to reconcile against watchlist.",
                },
                "cards": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object"}
                        ]
                    },
                    "description": "List of card names or card objects that were acquired to remove from the watchlist.",
                },
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alias for 'cards' - list of card names acquired.",
                },
                "card_name": {
                    "type": "string",
                    "description": "Single card name or semicolon-separated list of card names to reconcile.",
                },
                "card_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional list of specific WatchlistItem IDs to de-register.",
                },
                "add_to_inventory": {
                    "type": "boolean",
                    "description": "If true, also adds/increments the acquired cards in the user's collection inventory. Defaults to false.",
                    "default": False,
                },
                "binder_name": {
                    "type": "string",
                    "description": "Binder location to assign when add_to_inventory is true (default 'Acquired').",
                    "default": "Acquired",
                },
                "condition": {
                    "type": "string",
                    "description": "Default card condition when adding to inventory (default 'Near Mint').",
                    "default": "Near Mint",
                },
            },
            "required": [],
        },
    },
]


def resolve_mcp_user(token: str | None = None, config_api_key: str | None = None, admin_email: str | None = None) -> User | None:
    """
    Resolves the authenticated User for an MCP request.
    1. Matches a User by their `api_token`.
    2. If `token` matches `CHIMERA_API_KEY` from config, resolves the primary admin user (or first active user).
    """
    clean_token = (token or "").strip()
    if not clean_token:
        return None

    # 1. Check user-specific api_token in database
    user = User.query.filter_by(api_token=clean_token, is_active=True).first()
    if user:
        return user

    # 2. Check master CHIMERA_API_KEY fallback
    if config_api_key and clean_token == config_api_key:
        if admin_email:
            admin_user = User.query.filter(
                db.func.lower(User.email) == admin_email.lower(),
                User.is_active.is_(True),
            ).first()
            if admin_user:
                return admin_user
        admin_user = User.query.filter_by(is_admin=True, is_active=True).order_by(User.id.asc()).first()
        if admin_user:
            return admin_user
        return User.query.filter_by(is_active=True).order_by(User.id.asc()).first()

    return None


def _execute_get_inventory_summary(user: User, args: dict[str, Any]) -> dict[str, Any]:
    top_n = min(max(int(args.get("top_n", 15) or 15), 1), 50)
    cards = UserInventoryCard.query.filter_by(user_id=user.id).all()

    total_qty = 0
    total_value = 0.0
    foil_qty = 0
    nonfoil_qty = 0
    binders: dict[str, int] = {}
    colors: dict[str, int] = {}

    enriched_list: list[dict[str, Any]] = []
    for c in cards:
        qty = int(c.quantity or 1)
        is_foil = bool(c.foil and c.foil.lower() in ("foil", "etched"))
        unit_price = (
            c.price_usd_foil
            if (is_foil and c.price_usd_foil is not None)
            else (c.price_usd or 0.0)
        )
        line_val = round(unit_price * qty, 2)
        total_qty += qty
        total_value += line_val
        if is_foil:
            foil_qty += qty
        else:
            nonfoil_qty += qty

        binder = (c.binder_name or "Unassigned").strip() or "Unassigned"
        binders[binder] = binders.get(binder, 0) + qty

        c_colors = c.get_color_identity_list()
        if not c_colors:
            colors["Colorless"] = colors.get("Colorless", 0) + qty
        else:
            for col in c_colors:
                colors[col] = colors.get(col, 0) + qty

        enriched_list.append({
            "name": c.name,
            "set_code": (c.set_code or "").upper(),
            "quantity": qty,
            "foil": c.foil or "normal",
            "condition": c.condition or "NM",
            "unit_price_usd": round(unit_price, 2),
            "total_value_usd": line_val,
            "binder_name": c.binder_name or "",
        })

    enriched_list.sort(key=lambda x: (x["unit_price_usd"], x["total_value_usd"]), reverse=True)

    return {
        "user_email": user.email,
        "total_cards_quantity": total_qty,
        "unique_inventory_entries": len(cards),
        "total_value_usd": round(total_value, 2),
        "foil_cards_quantity": foil_qty,
        "nonfoil_cards_quantity": nonfoil_qty,
        "binders_breakdown": binders,
        "color_identity_breakdown": colors,
        "top_valuable_cards": enriched_list[:top_n],
    }


def _parse_card_input_list(args: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extracts and standardizes requested card items from various MCP argument combinations:
    - args["cards"]: list of strings, list of dicts, or comma/newline delimited string
    - args["card_names"]: list of strings or comma/newline delimited string
    - args["card_name"]: single string
    - args["query"]: single string or comma/newline delimited string
    - args["text"]: decklist, multi-line string, or TCGplayer purchase manifest
    - args["csv_content"]: ManaBox or standard MTG CSV export content
    """
    # 0. Check for CSV content first
    csv_text = args.get("csv_content")
    if not csv_text and isinstance(args.get("text"), str):
        candidate_header = args["text"].splitlines()[0].lower() if args["text"].splitlines() else ""
        if "," in candidate_header and ("name" in candidate_header or "binder" in candidate_header or "scryfall" in candidate_header):
            csv_text = args["text"]

    if csv_text and isinstance(csv_text, str) and csv_text.strip():
        try:
            from inventory_parser import ManaBoxInventoryParser
            parsed_csv = ManaBoxInventoryParser.parse(csv_text)
            extracted_csv_cards = []
            for item in parsed_csv.get("valid_cards", []):
                name = item.get("name", "").strip()
                if name:
                    extracted_csv_cards.append({
                        "raw_query": item.get("raw_name") or name,
                        "clean_name": normalize_card_name(name),
                        "quantity": max(1, int(item.get("quantity", 1) or 1)),
                        "set_code": (item.get("set_code") or "").upper().strip() or None,
                        "collector_number": str(item.get("collector_number") or "").strip() or None,
                        "foil": item.get("foil") or "normal",
                        "condition": item.get("condition") or "Near Mint",
                        "binder_name": item.get("binder_name") or "",
                        "purchase_price": item.get("purchase_price"),
                    })
            if extracted_csv_cards:
                return extracted_csv_cards
        except Exception:
            pass

    # 1. Check for TCGplayer purchase format in text
    text_content = args.get("text")
    if text_content and isinstance(text_content, str) and text_content.strip():
        try:
            from tcgplayer_parser import TCGPlayerPurchaseParser
            tcg_items = TCGPlayerPurchaseParser.parse(text_content)
            if tcg_items and len(tcg_items) > 0:
                extracted_tcg = []
                for item in tcg_items:
                    extracted_tcg.append({
                        "raw_query": item.raw_line or item.card_name,
                        "clean_name": normalize_card_name(item.card_name),
                        "quantity": max(1, int(item.quantity or 1)),
                        "set_name": item.set_name,
                        "collector_number": item.collector_number or None,
                        "condition": item.condition or "Near Mint",
                        "foil": item.finish or "nonfoil",
                    })
                if extracted_tcg:
                    return extracted_tcg
        except Exception:
            pass

    raw_candidates: list[Any] = []

    # 2. 'cards' or 'card_names'
    for key in ("cards", "card_names"):
        val = args.get(key)
        if val is not None:
            if isinstance(val, list):
                raw_candidates.extend(val)
            elif isinstance(val, str) and val.strip():
                raw_candidates.append(val)

    # 3. 'card_name'
    single_name = args.get("card_name")
    if single_name and isinstance(single_name, str) and single_name.strip():
        raw_candidates.append(single_name)

    # 4. 'text' or 'query'
    if not raw_candidates:
        for key in ("text", "query"):
            val = args.get(key)
            if val and isinstance(val, str) and val.strip():
                raw_candidates.append(val)

    line_pattern = re.compile(
        r"^(?:(\d+)[xX]?\s+)?([^\r\n()]+?)(?:\s+\(([A-Za-z0-9]+)\)\s*([A-Za-z0-9]+)?)?(?:\s+\*.*?\*)?$"
    )

    extracted_items: list[dict[str, Any]] = []

    for item in raw_candidates:
        if isinstance(item, dict):
            name = (item.get("name") or item.get("card_name") or "").strip()
            qty = int(item.get("quantity", 1) or 1)
            if name:
                extracted_items.append({
                    "raw_query": name,
                    "clean_name": normalize_card_name(name),
                    "quantity": max(1, qty),
                    "set_code": (item.get("set_code") or "").upper().strip() or None,
                    "collector_number": str(item.get("collector_number") or "").strip() or None,
                    "foil": item.get("foil") or item.get("finish") or "normal",
                    "condition": item.get("condition") or "Near Mint",
                    "binder_name": item.get("binder_name") or "",
                    "purchase_price": item.get("purchase_price"),
                })
        elif isinstance(item, str):
            clean_str = item.strip()
            if not clean_str:
                continue

            lines = [ln.strip() for ln in clean_str.splitlines() if ln.strip()]
            if len(lines) > 1:
                for line in lines:
                    if line.startswith(("//", "#")) or line.lower() in ("deck", "commander", "mainboard", "sideboard"):
                        continue
                    m = line_pattern.match(line)
                    if m:
                        qty_str, cname, scode, colnum = m.groups()
                        qty = int(qty_str) if qty_str else 1
                        cleaned = normalize_card_name(cname)
                        if cleaned and len(cleaned) >= 2:
                            extracted_items.append({
                                "raw_query": line,
                                "clean_name": cleaned,
                                "quantity": max(1, qty),
                                "set_code": (scode or "").upper().strip() or None,
                                "collector_number": (colnum or "").strip() or None,
                            })
                    else:
                        cleaned = normalize_card_name(line)
                        if cleaned and len(cleaned) >= 2:
                            extracted_items.append({
                                "raw_query": line,
                                "clean_name": cleaned,
                                "quantity": 1,
                            })
            else:
                single_line = lines[0]
                # If only one candidate was provided and it has commas separating what look like cards
                if len(raw_candidates) == 1 and "," in single_line:
                    is_legendary_prefix = any(
                        single_line.lower().startswith(p)
                        for p in (
                            "jace,", "teferi,", "chandra,", "urza,", "karn,",
                            "liliana,", "nissa,", "nicol bolas,", "omnath,", "gisela,"
                        )
                    )
                    comma_parts = [p.strip() for p in single_line.split(",") if p.strip()]
                    if not is_legendary_prefix and len(comma_parts) > 1:
                        for part in comma_parts:
                            m = line_pattern.match(part)
                            if m:
                                qty_str, cname, scode, colnum = m.groups()
                                qty = int(qty_str) if qty_str else 1
                                cleaned = normalize_card_name(cname)
                                if cleaned:
                                    extracted_items.append({
                                        "raw_query": part,
                                        "clean_name": cleaned,
                                        "quantity": max(1, qty),
                                        "set_code": (scode or "").upper().strip() or None,
                                        "collector_number": (colnum or "").strip() or None,
                                    })
                            else:
                                cleaned = normalize_card_name(part)
                                if cleaned:
                                    extracted_items.append({
                                        "raw_query": part,
                                        "clean_name": cleaned,
                                        "quantity": 1,
                                    })
                        continue

                m = line_pattern.match(single_line)
                if m:
                    qty_str, cname, scode, colnum = m.groups()
                    qty = int(qty_str) if qty_str else 1
                    cleaned = normalize_card_name(cname)
                    if cleaned:
                        extracted_items.append({
                            "raw_query": single_line,
                            "clean_name": cleaned,
                            "quantity": max(1, qty),
                            "set_code": (scode or "").upper().strip() or None,
                            "collector_number": (colnum or "").strip() or None,
                        })
                else:
                    cleaned = normalize_card_name(single_line)
                    if cleaned:
                        extracted_items.append({
                            "raw_query": single_line,
                            "clean_name": cleaned,
                            "quantity": 1,
                        })

    return extracted_items


def _execute_check_inventory_cards(user: User, args: dict[str, Any]) -> dict[str, Any]:
    """
    Checks ownership and availability in the user's inventory for one or multiple cards.
    Accepts single cards, lists of card names/objects, or decklist text.
    """
    extracted_items = _parse_card_input_list(args)
    if not extracted_items:
        return {
            "total_queried": 0,
            "found_count": 0,
            "missing_count": 0,
            "all_owned": False,
            "total_owned_quantity": 0,
            "total_found_value_usd": 0.0,
            "owned_card_names": [],
            "missing_card_names": [],
            "found_cards": [],
            "missing_cards": [],
            "cards": [],
            "message": "No cards provided to check. Pass 'cards': ['Card Name 1', 'Card Name 2'] or 'card_name': 'Card Name'.",
        }

    include_details = bool(args.get("include_details", True))
    match_mode = (args.get("match_mode") or "smart").strip().lower()
    binder_name = (args.get("binder_name") or "").strip()

    q = UserInventoryCard.query.filter_by(user_id=user.id)
    if binder_name:
        q = q.filter(UserInventoryCard.binder_name.ilike(f"%{binder_name}%"))
    inventory_cards = q.all()

    # Build fast in-memory lookup indices
    exact_map: dict[str, list[UserInventoryCard]] = {}
    key_map: dict[str, list[UserInventoryCard]] = {}

    for card in inventory_cards:
        c_name = card.name or ""
        c_clean = normalize_card_name(c_name)
        c_lower = c_clean.lower()
        exact_map.setdefault(c_lower, []).append(card)

        for k in get_card_match_keys(c_clean):
            key_map.setdefault(k, []).append(card)

        if card.raw_name:
            r_clean = normalize_card_name(card.raw_name)
            if r_clean and r_clean.lower() != c_lower:
                exact_map.setdefault(r_clean.lower(), []).append(card)
                for k in get_card_match_keys(r_clean):
                    key_map.setdefault(k, []).append(card)

    all_cards: list[dict[str, Any]] = []
    found_cards: list[dict[str, Any]] = []
    missing_cards: list[dict[str, Any]] = []
    owned_names_set: set[str] = set()
    missing_names: list[str] = []

    total_owned_qty = 0
    total_found_val = 0.0

    for req in extracted_items:
        clean_name = req["clean_name"]
        clean_lower = clean_name.lower()
        req_qty = req["quantity"]
        raw_q = req["raw_query"]

        matching_rows: list[UserInventoryCard] = []

        # 1. Exact lowercase match
        if clean_lower in exact_map:
            matching_rows = exact_map[clean_lower]
        # 2. Smart match via canonical keys (accents, DFC front face, mojibake)
        elif match_mode != "exact":
            query_keys = get_card_match_keys(clean_name)
            for k in query_keys:
                if k in key_map:
                    matching_rows = key_map[k]
                    break

            # 3. Fallback: card_names_match loop
            if not matching_rows:
                for card in inventory_cards:
                    if card_names_match(card.name, clean_name):
                        matching_rows = [card]
                        break

        # Deduplicate rows by ID
        unique_matches: list[UserInventoryCard] = []
        seen_ids = set()
        for r in matching_rows:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                unique_matches.append(r)

        in_inventory = len(unique_matches) > 0
        total_qty = sum(int(c.quantity or 1) for c in unique_matches) if in_inventory else 0
        has_foil = any(bool(c.foil and c.foil.lower() in ("foil", "etched")) for c in unique_matches)
        has_nonfoil = any(not (c.foil and c.foil.lower() in ("foil", "etched")) for c in unique_matches)
        binders = sorted(list({(c.binder_name or "Unassigned").strip() or "Unassigned" for c in unique_matches})) if in_inventory else []

        card_total_val = 0.0
        items_list = []
        for c in unique_matches:
            qty = int(c.quantity or 1)
            is_foil = bool(c.foil and c.foil.lower() in ("foil", "etched"))
            unit_price = (
                c.price_usd_foil
                if (is_foil and c.price_usd_foil is not None)
                else (c.price_usd or 0.0)
            )
            line_val = round(unit_price * qty, 2)
            card_total_val += line_val

            if include_details:
                items_list.append({
                    "id": c.id,
                    "name": c.name,
                    "set_code": (c.set_code or "").upper(),
                    "set_name": c.set_name or "",
                    "collector_number": c.collector_number or "",
                    "quantity": qty,
                    "foil": c.foil or "normal",
                    "condition": c.condition or "NM",
                    "binder_name": (c.binder_name or "Unassigned").strip() or "Unassigned",
                    "price_usd": c.price_usd,
                    "price_usd_foil": c.price_usd_foil,
                    "effective_price": round(unit_price, 2),
                    "total_value_usd": line_val,
                })

        matched_name = unique_matches[0].name if in_inventory else None
        has_sufficient = total_qty >= req_qty if in_inventory else False
        missing_qty = max(0, req_qty - total_qty)

        entry: dict[str, Any] = {
            "query": raw_q,
            "clean_name": clean_name,
            "matched_name": matched_name,
            "in_inventory": in_inventory,
            "total_quantity": total_qty,
            "requested_quantity": req_qty,
            "has_sufficient_quantity": has_sufficient,
            "missing_quantity": missing_qty,
            "has_foil": has_foil,
            "has_nonfoil": has_nonfoil,
            "binders": binders,
            "total_value_usd": round(card_total_val, 2),
        }
        if include_details:
            entry["inventory_items"] = items_list

        all_cards.append(entry)

        if in_inventory:
            found_cards.append(entry)
            owned_names_set.add(matched_name or clean_name)
            total_owned_qty += total_qty
            total_found_val += card_total_val
        else:
            missing_cards.append(entry)
            missing_names.append(raw_q)

    return {
        "total_queried": len(extracted_items),
        "found_count": len(found_cards),
        "missing_count": len(missing_cards),
        "all_owned": len(missing_cards) == 0 and len(extracted_items) > 0,
        "total_owned_quantity": total_owned_qty,
        "total_found_value_usd": round(total_found_val, 2),
        "owned_card_names": sorted(list(owned_names_set)),
        "missing_card_names": missing_names,
        "found_cards": found_cards,
        "missing_cards": missing_cards,
        "cards": all_cards,
    }


def _execute_search_inventory(user: User, args: dict[str, Any]) -> dict[str, Any]:
    # If caller passed multiple cards or a list, seamlessly route to check_inventory_cards
    if args.get("cards") or args.get("card_names"):
        return _execute_check_inventory_cards(user, args)

    query_str = (args.get("query") or args.get("card_name") or "").strip()
    if "\n" in query_str:
        return _execute_check_inventory_cards(user, args)

    limit = min(max(int(args.get("limit", 25) or 25), 1), 100)
    q = UserInventoryCard.query.filter_by(user_id=user.id)
    if query_str:
        q = q.filter(
            db.or_(
                UserInventoryCard.name.ilike(f"%{query_str}%"),
                UserInventoryCard.raw_name.ilike(f"%{query_str}%"),
                UserInventoryCard.oracle_text.ilike(f"%{query_str}%"),
            )
        )

    set_code = (args.get("set_code") or "").strip()
    if set_code:
        q = q.filter(UserInventoryCard.set_code.ilike(set_code))

    foil = (args.get("foil") or "").strip()
    if foil:
        q = q.filter(UserInventoryCard.foil.ilike(f"%{foil}%"))

    condition = (args.get("condition") or "").strip()
    if condition:
        q = q.filter(UserInventoryCard.condition.ilike(f"%{condition}%"))

    binder_name = (args.get("binder_name") or "").strip()
    if binder_name:
        q = q.filter(UserInventoryCard.binder_name.ilike(f"%{binder_name}%"))

    type_line = (args.get("type_line") or "").strip()
    if type_line:
        q = q.filter(UserInventoryCard.type_line.ilike(f"%{type_line}%"))

    color_identity = (args.get("color_identity") or "").strip().upper()
    if color_identity:
        for col in [c.strip() for c in color_identity.split(",") if c.strip()]:
            q = q.filter(UserInventoryCard.color_identity.ilike(f"%{col}%"))

    matches = q.all()

    min_price = args.get("min_price")
    results = []
    for c in matches:
        d = c.to_dict(include_metadata=True)
        eff_price = d.get("effective_price") or 0.0
        if min_price is not None and eff_price < float(min_price):
            continue
        results.append(d)

    results.sort(key=lambda x: (x.get("effective_price") or 0.0, x.get("quantity") or 0), reverse=True)
    total_qty = sum(r.get("quantity", 0) for r in results)

    return {
        "query": query_str or None,
        "total_matching_entries": len(results),
        "total_matching_quantity": total_qty,
        "returned_count": min(len(results), limit),
        "cards": results[:limit],
    }


def _execute_get_watchlist(user: User, args: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(args.get("limit", 50) or 50), 1), 200)
    deals_only = bool(args.get("deals_only", False))
    query_str = (args.get("query") or "").strip()
    tag_str = (args.get("tag") or "").strip()
    tags_list = [t.strip() for t in args.get("tags", []) if isinstance(t, str) and t.strip()]

    # Check if bulk card names filter passed
    filter_cards = _parse_card_input_list(args)
    filter_names_lower = {c["clean_name"].lower() for c in filter_cards} if filter_cards else set()

    q = WatchlistItem.query.filter_by(user_id=user.id)
    if query_str:
        q = q.filter(WatchlistItem.name.ilike(f"%{query_str}%"))
    if tag_str:
        q = q.filter(WatchlistItem.tag.ilike(f"%{tag_str}%"))
    if tags_list:
        tag_filters = [WatchlistItem.tag.ilike(f"%{tg}%") for tg in tags_list]
        q = q.filter(db.or_(*tag_filters))

    items = q.order_by(WatchlistItem.name.asc()).all()
    serialized = []
    for item in items:
        if deals_only and not item.is_deal:
            continue
        if filter_names_lower:
            i_name = (item.name or "").lower()
            if not any(card_names_match(i_name, target) for target in filter_names_lower):
                continue
        serialized.append(item.to_dict())

    return {
        "total_matching": len(serialized),
        "deals_count": sum(1 for i in serialized if i.get("is_deal")),
        "returned_count": min(len(serialized), limit),
        "watchlist": serialized[:limit],
    }


def _execute_get_active_deals(user: User, args: dict[str, Any]) -> dict[str, Any]:
    category = (args.get("category") or "all").strip().lower()
    result: dict[str, Any] = {}

    if category in ("all", "singles", "watchlist"):
        watchlist_items = WatchlistItem.query.filter_by(user_id=user.id).all()
        singles_deals = [item.to_dict() for item in watchlist_items if item.is_deal]
        singles_deals.sort(key=lambda x: x.get("savings_percent", 0.0), reverse=True)
        result["singles_deals"] = singles_deals
        result["singles_deals_count"] = len(singles_deals)

    if category in ("all", "microcenter"):
        mc_items = MicrocenterItem.query.filter_by(is_active=True, in_stock=True).all()
        mc_deals = [item.to_dict(include_history=False) for item in mc_items if item.is_deal]
        result["microcenter_deals"] = mc_deals
        result["microcenter_in_stock_count"] = len(mc_items)

    if category in ("all", "bestbuy"):
        bb_items = BestBuyItem.query.filter_by(is_active=True, in_stock=True).all()
        bb_deals = [item.to_dict(include_history=False) for item in bb_items if item.is_deal]
        result["bestbuy_deals"] = bb_deals
        result["bestbuy_in_stock_count"] = len(bb_items)

    result["last_registry_poll"] = SystemSetting.get_val("last_poll_time")
    return result


def _execute_get_deck_analyses(user: User, args: dict[str, Any]) -> dict[str, Any]:
    deck_id = args.get("deck_id")
    deck_ids = args.get("deck_ids")
    query_str = (args.get("query") or args.get("deck_name") or args.get("commander_name") or "").strip()
    queries = [q.strip() for q in args.get("queries", []) if isinstance(q, str) and q.strip()]
    include_cards = bool(args.get("include_cards", True))

    q = DeckAnalysis.query.filter_by(user_id=user.id)

    # 1. Single deck by ID
    if deck_id is not None and not deck_ids:
        deck = q.filter_by(id=int(deck_id)).first()
        if not deck:
            return {"error": f"No Commander deck found with ID {deck_id}."}
        full_data = deck.to_dict(include_full=True)
        if not include_cards:
            full_data.pop("cards_data", None)
            full_data.pop("raw_decklist", None)
        return {"deck": full_data}

    # 2. Batch deck IDs
    if deck_ids and isinstance(deck_ids, list):
        try:
            int_ids = [int(i) for i in deck_ids]
            decks = q.filter(DeckAnalysis.id.in_(int_ids)).order_by(DeckAnalysis.updated_at.desc()).all()
            full_decks = []
            for d in decks:
                fdata = d.to_dict(include_full=True)
                if not include_cards:
                    fdata.pop("cards_data", None)
                    fdata.pop("raw_decklist", None)
                full_decks.append(fdata)
            return {
                "total_decks": len(full_decks),
                "decks": full_decks,
            }
        except (ValueError, TypeError):
            pass

    # 3. Batch queries
    if queries:
        query_filters = []
        for qterm in queries:
            query_filters.append(
                db.or_(
                    DeckAnalysis.deck_name.ilike(f"%{qterm}%"),
                    DeckAnalysis.commander_name.ilike(f"%{qterm}%"),
                    DeckAnalysis.archetype.ilike(f"%{qterm}%"),
                )
            )
        decks = q.filter(db.or_(*query_filters)).order_by(DeckAnalysis.updated_at.desc()).all()
        summaries = []
        for d in decks:
            summary = d.to_dict(include_full=include_cards)
            analysis = d.get_analysis()
            if analysis:
                summary["strategic_summary"] = analysis.get("strategic_summary") or analysis.get("summary")
                summary["win_conditions"] = analysis.get("win_conditions", [])
                summary["key_weaknesses"] = analysis.get("weaknesses") or analysis.get("key_weaknesses", [])
            summaries.append(summary)
        return {
            "total_decks": len(summaries),
            "decks": summaries,
        }

    if query_str:
        q = q.filter(
            db.or_(
                DeckAnalysis.deck_name.ilike(f"%{query_str}%"),
                DeckAnalysis.commander_name.ilike(f"%{query_str}%"),
                DeckAnalysis.archetype.ilike(f"%{query_str}%"),
            )
        )

    decks = q.order_by(DeckAnalysis.updated_at.desc()).all()
    if len(decks) == 1 and query_str:
        full_data = decks[0].to_dict(include_full=True)
        if not include_cards:
            full_data.pop("cards_data", None)
            full_data.pop("raw_decklist", None)
        return {"deck": full_data}

    summaries = []
    for d in decks:
        summary = d.to_dict(include_full=False)
        analysis = d.get_analysis()
        if analysis:
            summary["strategic_summary"] = analysis.get("strategic_summary") or analysis.get("summary")
            summary["win_conditions"] = analysis.get("win_conditions", [])
            summary["key_weaknesses"] = analysis.get("weaknesses") or analysis.get("key_weaknesses", [])
        summaries.append(summary)

    return {
        "total_decks": len(summaries),
        "decks": summaries,
    }


def _build_deck_cards_response(deck: DeckAnalysis, group_by_type: bool) -> dict[str, Any]:
    cards = deck.get_parsed_cards()
    commander_names = [c.strip() for c in (deck.commander_name or "").split(",") if c.strip()]

    type_categories: dict[str, list[dict[str, Any]]] = {
        "Commander": [],
        "Creatures": [],
        "Instants": [],
        "Sorceries": [],
        "Artifacts": [],
        "Enchantments": [],
        "Planeswalkers": [],
        "Battles": [],
        "Lands": [],
        "Other": [],
    }

    clean_cards_list = []
    text_lines = []

    for c in cards:
        name = c.get("name") or "Unknown"
        qty = int(c.get("quantity", 1) or 1)
        type_line = c.get("type_line") or ""
        t_lower = type_line.lower()

        card_info = {
            "name": name,
            "quantity": qty,
            "type_line": type_line,
            "mana_cost": c.get("mana_cost") or "",
            "cmc": c.get("cmc", 0),
            "price_usd": c.get("price_usd"),
            "set_code": (c.get("set_code") or "").upper(),
            "color_identity": c.get("color_identity") or [],
            "oracle_text": c.get("oracle_text") or "",
        }
        clean_cards_list.append(card_info)
        text_lines.append(f"{qty} {name}")

        if any(name.lower() == cmd.lower() for cmd in commander_names):
            type_categories["Commander"].append(card_info)
        elif "creature" in t_lower:
            type_categories["Creatures"].append(card_info)
        elif "instant" in t_lower:
            type_categories["Instants"].append(card_info)
        elif "sorcery" in t_lower:
            type_categories["Sorceries"].append(card_info)
        elif "artifact" in t_lower:
            type_categories["Artifacts"].append(card_info)
        elif "enchantment" in t_lower:
            type_categories["Enchantments"].append(card_info)
        elif "planeswalker" in t_lower:
            type_categories["Planeswalkers"].append(card_info)
        elif "battle" in t_lower:
            type_categories["Battles"].append(card_info)
        elif "land" in t_lower:
            type_categories["Lands"].append(card_info)
        else:
            type_categories["Other"].append(card_info)

    filtered_categories = {cat: items for cat, items in type_categories.items() if items}
    category_counts = {cat: sum(item["quantity"] for item in items) for cat, items in filtered_categories.items()}

    resp = {
        "deck_id": deck.id,
        "deck_name": deck.deck_name,
        "commander_name": deck.commander_name,
        "total_cards": sum(c.get("quantity", 1) for c in clean_cards_list),
        "total_value_usd": deck.total_value,
        "avg_cmc": deck.avg_cmc,
        "color_identity": deck.get_color_identity_list(),
        "category_counts": category_counts,
        "text_decklist": "\n".join(text_lines),
    }
    if group_by_type:
        resp["cards_by_type"] = filtered_categories
    else:
        resp["cards"] = clean_cards_list
    return resp


def _execute_get_deck_cards(user: User, args: dict[str, Any]) -> dict[str, Any]:
    deck_id = args.get("deck_id")
    deck_ids = args.get("deck_ids")
    query_str = (args.get("query") or args.get("deck_name") or args.get("commander_name") or "").strip()
    queries = [q.strip() for q in args.get("queries", []) if isinstance(q, str) and q.strip()]
    group_by_type = bool(args.get("group_by_type", True))

    q = DeckAnalysis.query.filter_by(user_id=user.id)

    # 1. Batch deck IDs
    if deck_ids and isinstance(deck_ids, list):
        try:
            int_ids = [int(i) for i in deck_ids]
            decks = q.filter(DeckAnalysis.id.in_(int_ids)).order_by(DeckAnalysis.updated_at.desc()).all()
            deck_results = [_build_deck_cards_response(d, group_by_type) for d in decks]
            return {
                "total_decks": len(deck_results),
                "decks": deck_results,
            }
        except (ValueError, TypeError):
            pass

    # 2. Batch queries
    if queries:
        query_filters = []
        for qterm in queries:
            query_filters.append(
                db.or_(
                    DeckAnalysis.deck_name.ilike(f"%{qterm}%"),
                    DeckAnalysis.commander_name.ilike(f"%{qterm}%"),
                )
            )
        decks = q.filter(db.or_(*query_filters)).order_by(DeckAnalysis.updated_at.desc()).all()
        deck_results = [_build_deck_cards_response(d, group_by_type) for d in decks]
        return {
            "total_decks": len(deck_results),
            "decks": deck_results,
        }

    # 3. Single deck
    deck = None
    if deck_id is not None:
        deck = q.filter_by(id=int(deck_id)).first()
    elif query_str:
        deck = q.filter(
            db.or_(
                DeckAnalysis.deck_name.ilike(f"%{query_str}%"),
                DeckAnalysis.commander_name.ilike(f"%{query_str}%"),
            )
        ).order_by(DeckAnalysis.updated_at.desc()).first()
    else:
        deck = q.order_by(DeckAnalysis.updated_at.desc()).first()

    if not deck:
        avail_decks = [
            {"id": d.id, "name": d.deck_name, "commander": d.commander_name}
            for d in q.order_by(DeckAnalysis.deck_name.asc()).all()
        ]
        return {
            "error": f"No Commander deck found matching '{query_str or deck_id}'.",
            "available_decks": avail_decks,
        }

    return _build_deck_cards_response(deck, group_by_type)


def _execute_search_card_prices(user: User, args: dict[str, Any]) -> dict[str, Any]:
    extracted_items = _parse_card_input_list(args)
    set_code = (args.get("set_code") or "").strip()

    # Fallback to legacy single card_name arg if parser found nothing
    if not extracted_items:
        legacy_name = (args.get("card_name") or "").strip()
        if legacy_name:
            extracted_items = [{"raw_query": legacy_name, "clean_name": normalize_card_name(legacy_name), "quantity": 1}]

    if not extracted_items:
        return {"error": "card_name is required (or pass 'cards' array / 'text')."}

    from providers.scryfall import ScryfallProvider
    provider = ScryfallProvider()

    # Pre-fetch user's entire inventory & watchlist for fast matching
    owned_cards = UserInventoryCard.query.filter_by(user_id=user.id).all()
    watched_items = WatchlistItem.query.filter_by(user_id=user.id).all()

    # 1. Single-card query: retain full backward-compatible schema
    if len(extracted_items) == 1 and not args.get("cards") and not args.get("card_names"):
        single = extracted_items[0]
        card_name = single["clean_name"]
        matching_owned = [c for c in owned_cards if card_names_match(c.name, card_name)]
        matching_watched = [w for w in watched_items if card_names_match(w.name, card_name)]

        scryfall_data = None
        lowest_printing = None
        try:
            scryfall_data = provider.get_card_named(card_name)
            if not set_code or set_code.upper() == "ANY":
                lowest_printing = provider.get_cheapest_tcgplayer_price(card_name)
        except Exception as exc:
            logger.warning(f"MCP search_card_prices Scryfall lookup error for '{card_name}': {exc}")

        return {
            "queried_card": single["raw_query"],
            "scryfall_card": scryfall_data,
            "lowest_market_printing": lowest_printing,
            "owned_in_inventory": [c.to_dict(include_metadata=False) for c in matching_owned],
            "total_owned_quantity": sum(c.quantity for c in matching_owned),
            "tracked_on_watchlist": [w.to_dict() for w in matching_watched],
        }

    # 2. Multi-card batch query
    names_to_fetch = [item["clean_name"] for item in extracted_items]
    scryfall_map: dict[str, Any] = {}
    try:
        scryfall_map, _ = provider.get_cards_collection(names_to_fetch)
    except Exception as exc:
        logger.warning(f"MCP batch search_card_prices Scryfall error: {exc}")

    results = []
    total_market_val = 0.0

    for item in extracted_items:
        c_clean = item["clean_name"]
        raw_q = item["raw_query"]
        req_qty = item.get("quantity", 1)

        c_meta = scryfall_map.get(c_clean.lower())
        if not c_meta:
            for k in get_card_match_keys(c_clean):
                if k in scryfall_map:
                    c_meta = scryfall_map[k]
                    break
        if not c_meta:
            try:
                c_meta = provider.get_card_named(c_clean)
            except Exception:
                c_meta = None

        prices = c_meta.get("prices", {}) if isinstance(c_meta, dict) else {}
        usd_price = None
        if prices.get("usd"):
            try:
                usd_price = float(prices["usd"])
            except (ValueError, TypeError):
                pass

        usd_foil = None
        if prices.get("usd_foil"):
            try:
                usd_foil = float(prices["usd_foil"])
            except (ValueError, TypeError):
                pass

        if usd_price is not None:
            total_market_val += usd_price * req_qty

        matching_owned = [c for c in owned_cards if card_names_match(c.name, c_clean)]
        matching_watched = [w for w in watched_items if card_names_match(w.name, c_clean)]

        results.append({
            "query": raw_q,
            "card_name": c_clean,
            "matched_name": c_meta.get("name") if isinstance(c_meta, dict) else c_clean,
            "set_code": (c_meta.get("set_code") or set_code or "").upper(),
            "price_usd": usd_price,
            "price_usd_foil": usd_foil,
            "type_line": c_meta.get("type_line") if isinstance(c_meta, dict) else None,
            "mana_cost": c_meta.get("mana_cost") if isinstance(c_meta, dict) else None,
            "cmc": c_meta.get("cmc") if isinstance(c_meta, dict) else None,
            "color_identity": c_meta.get("color_identity") if isinstance(c_meta, dict) else [],
            "image_uri": c_meta.get("image_uri") if isinstance(c_meta, dict) else None,
            "owned_quantity": sum(c.quantity for c in matching_owned),
            "owned_in_inventory": [c.to_dict(include_metadata=False) for c in matching_owned],
            "tracked_on_watchlist": [w.to_dict() for w in matching_watched],
            "is_tracked": len(matching_watched) > 0,
        })

    return {
        "total_queried": len(extracted_items),
        "total_estimated_market_value_usd": round(total_market_val, 2),
        "results": results,
        "cards": results,
    }


def _parse_watchlist_card_entries(args: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extracts and standardizes requested card items for watchlist monitoring from diverse argument shapes:
    - args["cards"]: list of strings, list of dicts, or semicolon/newline/comma delimited string
    - args["card_names"]: list of strings or delimited string
    - args["card_name"]: single string or semicolon-separated string
    - args["text"]: decklist or multi-line string
    - args["csv_content"]: CSV spreadsheet content
    """
    # Check for CSV content first
    csv_text = args.get("csv_content")
    if not csv_text and isinstance(args.get("text"), str):
        candidate_header = args["text"].splitlines()[0].lower() if args["text"].splitlines() else ""
        if "," in candidate_header and ("name" in candidate_header or "binder" in candidate_header or "scryfall" in candidate_header):
            csv_text = args["text"]

    default_target_price = args.get("target_price")
    if default_target_price is not None:
        try:
            default_target_price = round(float(default_target_price), 2)
        except (ValueError, TypeError):
            default_target_price = None

    default_target_strategy = (args.get("target_strategy") or "none").strip().lower()

    default_set_code = (args.get("set_code") or "").strip().upper()
    if default_set_code in ("", "ANY"):
        default_set_code = None

    default_finish = (args.get("finish") or "nonfoil").strip().lower()
    if default_finish not in ("nonfoil", "foil", "etched"):
        default_finish = "nonfoil"

    default_tag = (args.get("tag") or "").strip() or None

    if csv_text and isinstance(csv_text, str) and csv_text.strip():
        try:
            from inventory_parser import ManaBoxInventoryParser
            parsed_csv = ManaBoxInventoryParser.parse(csv_text)
            extracted_csv = []
            for item in parsed_csv.get("valid_cards", []):
                name = item.get("name", "").strip()
                if name:
                    extracted_csv.append({
                        "raw_name": item.get("raw_name") or name,
                        "clean_name": normalize_card_name(name),
                        "target_price": item.get("purchase_price") or default_target_price,
                        "target_strategy": default_target_strategy,
                        "set_code": (item.get("set_code") or default_set_code or "").upper().strip() or None,
                        "finish": item.get("foil") or default_finish,
                        "tag": default_tag,
                    })
            if extracted_csv:
                return extracted_csv
        except Exception:
            pass

    raw_candidates: list[Any] = []
    for key in ("cards", "card_names"):
        val = args.get(key)
        if val is not None:
            if isinstance(val, list):
                raw_candidates.extend(val)
            elif isinstance(val, str) and val.strip():
                raw_candidates.append(val)

    single_name = args.get("card_name")
    if single_name and isinstance(single_name, str) and single_name.strip():
        raw_candidates.append(single_name)

    text_val = args.get("text")
    if text_val and isinstance(text_val, str) and text_val.strip():
        raw_candidates.append(text_val)

    line_pattern = re.compile(
        r"^(?:(\d+)[xX]?\s+)?([^\r\n()]+?)(?:\s+\(([A-Za-z0-9]+)\)\s*([A-Za-z0-9]+)?)?(?:\s+\*.*?\*)?$"
    )

    extracted_entries: list[dict[str, Any]] = []

    for item in raw_candidates:
        if isinstance(item, dict):
            c_name = (item.get("card_name") or item.get("name") or "").strip()
            if not c_name:
                continue

            t_price = item.get("target_price")
            if t_price is not None:
                try:
                    t_price = round(float(t_price), 2)
                except (ValueError, TypeError):
                    t_price = default_target_price
            else:
                t_price = default_target_price

            t_strat = (item.get("target_strategy") or default_target_strategy or "none").strip().lower()

            s_code = (item.get("set_code") or default_set_code or "").strip().upper()
            if s_code in ("", "ANY"):
                s_code = None

            fin = (item.get("finish") or default_finish).strip().lower()
            if fin not in ("nonfoil", "foil", "etched"):
                fin = "nonfoil"

            tg = (item.get("tag") or default_tag or "").strip() or None

            extracted_entries.append({
                "raw_name": c_name,
                "clean_name": normalize_card_name(c_name),
                "target_price": t_price,
                "target_strategy": t_strat,
                "set_code": s_code,
                "finish": fin,
                "tag": tg,
            })
        elif isinstance(item, str):
            clean_str = item.strip()
            if not clean_str:
                continue

            # Check if semicolon or newline delimited
            if ";" in clean_str or "\n" in clean_str:
                lines = [ln.strip() for ln in clean_str.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
                for line in lines:
                    parts = [p.strip() for p in line.split(";") if p.strip()]
                    for part in parts:
                        if part.startswith(("//", "#")) or part.lower() in ("deck", "commander", "mainboard", "sideboard"):
                            continue
                        m = line_pattern.match(part)
                        if m:
                            _, cname, scode, _ = m.groups()
                            resolved_set = (scode or default_set_code or "").strip().upper() or None
                            if resolved_set in ("", "ANY"):
                                resolved_set = None
                            c_clean = normalize_card_name(cname or part)
                            if c_clean:
                                extracted_entries.append({
                                    "raw_name": part,
                                    "clean_name": c_clean,
                                    "target_price": default_target_price,
                                    "target_strategy": default_target_strategy,
                                    "set_code": resolved_set,
                                    "finish": default_finish,
                                    "tag": default_tag,
                                })
                        else:
                            c_clean = normalize_card_name(part)
                            if c_clean:
                                extracted_entries.append({
                                    "raw_name": part,
                                    "clean_name": c_clean,
                                    "target_price": default_target_price,
                                    "target_strategy": default_target_strategy,
                                    "set_code": default_set_code,
                                    "finish": default_finish,
                                    "tag": default_tag,
                                })
            else:
                # Single line string: could still be comma-separated if multiple cards were passed in a single string,
                # but preserve legendary names containing commas like 'Omnath, Locus of Creation'
                if len(raw_candidates) == 1 and "," in clean_str:
                    is_legendary_prefix = any(
                        clean_str.lower().startswith(p)
                        for p in (
                            "jace,", "teferi,", "chandra,", "urza,", "karn,",
                            "liliana,", "nissa,", "nicol bolas,", "omnath,", "gisela,"
                        )
                    )
                    comma_parts = [p.strip() for p in clean_str.split(",") if p.strip()]
                    if not is_legendary_prefix and len(comma_parts) > 1:
                        for part in comma_parts:
                            m = line_pattern.match(part)
                            if m:
                                _, cname, scode, _ = m.groups()
                                resolved_set = (scode or default_set_code or "").strip().upper() or None
                                if resolved_set in ("", "ANY"):
                                    resolved_set = None
                                c_clean = normalize_card_name(cname or part)
                                if c_clean:
                                    extracted_entries.append({
                                        "raw_name": part,
                                        "clean_name": c_clean,
                                        "target_price": default_target_price,
                                        "target_strategy": default_target_strategy,
                                        "set_code": resolved_set,
                                        "finish": default_finish,
                                        "tag": default_tag,
                                    })
                            else:
                                c_clean = normalize_card_name(part)
                                if c_clean:
                                    extracted_entries.append({
                                        "raw_name": part,
                                        "clean_name": c_clean,
                                        "target_price": default_target_price,
                                        "target_strategy": default_target_strategy,
                                        "set_code": default_set_code,
                                        "finish": default_finish,
                                        "tag": default_tag,
                                    })
                        continue

                m = line_pattern.match(clean_str)
                if m:
                    _, cname, scode, _ = m.groups()
                    resolved_set = (scode or default_set_code or "").strip().upper() or None
                    if resolved_set in ("", "ANY"):
                        resolved_set = None
                    c_clean = normalize_card_name(cname or clean_str)
                    if c_clean:
                        extracted_entries.append({
                            "raw_name": clean_str,
                            "clean_name": c_clean,
                            "target_price": default_target_price,
                            "target_strategy": default_target_strategy,
                            "set_code": resolved_set,
                            "finish": default_finish,
                            "tag": default_tag,
                        })
                else:
                    c_clean = normalize_card_name(clean_str)
                    if c_clean:
                        extracted_entries.append({
                            "raw_name": clean_str,
                            "clean_name": c_clean,
                            "target_price": default_target_price,
                            "target_strategy": default_target_strategy,
                            "set_code": default_set_code,
                            "finish": default_finish,
                            "tag": default_tag,
                        })

    # Deduplicate entries by (clean_name.lower(), set_code, finish), preserving order and merging updates
    deduped_map: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    for entry in extracted_entries:
        key = (entry["clean_name"].lower(), entry["set_code"], entry["finish"])
        if key in deduped_map:
            existing_ent = deduped_map[key]
            if entry["target_price"] is not None:
                existing_ent["target_price"] = entry["target_price"]
            if entry["tag"] is not None:
                existing_ent["tag"] = entry["tag"]
            if entry["target_strategy"] != "none":
                existing_ent["target_strategy"] = entry["target_strategy"]
        else:
            deduped_map[key] = entry

    return list(deduped_map.values())


def _execute_add_to_watchlist(user: User, args: dict[str, Any]) -> dict[str, Any]:
    entries = _parse_watchlist_card_entries(args)
    if not entries:
        return {"error": "card_name is required (or provide a 'cards' array)."}

    # Scryfall metadata enrichment
    sf_meta: dict[str, dict[str, Any]] = {}
    try:
        from providers.scryfall import ScryfallProvider
        provider = ScryfallProvider()

        if len(entries) == 1:
            clean_q = entries[0]["clean_name"]
            card_info = provider.get_card_named(clean_q)
            if card_info:
                sf_meta[clean_q.lower()] = card_info
        else:
            names_to_fetch = [e["clean_name"] for e in entries]
            found_map, _ = provider.get_cards_collection(names_to_fetch)
            for e in entries:
                clean_q = e["clean_name"]
                c_info = found_map.get(clean_q.lower())
                if not c_info:
                    for k in get_card_match_keys(clean_q):
                        if k in found_map:
                            c_info = found_map[k]
                            break
                if not c_info:
                    try:
                        c_info = provider.get_card_named(clean_q)
                    except Exception:
                        c_info = None
                if c_info:
                    sf_meta[clean_q.lower()] = c_info
    except Exception as exc:
        logger.warning(f"MCP add_to_watchlist Scryfall enrichment warning: {exc}")

    added_items: list[WatchlistItem] = []
    updated_items: list[WatchlistItem] = []
    all_processed_items: list[WatchlistItem] = []

    for entry in entries:
        clean_name = entry["clean_name"]
        set_code = entry["set_code"]
        finish = entry["finish"]
        tag = entry["tag"]
        target_price = entry["target_price"]
        target_strategy = entry["target_strategy"]

        resolved_name = clean_name
        scryfall_id = None
        collector_number = None
        image_uri = None
        tcg_price = None
        tcg_url = None
        type_line = None
        color_identity = None
        colors = None
        mana_cost = None
        cmc = None

        card_info = sf_meta.get(clean_name.lower())
        if card_info:
            resolved_name = card_info.get("name") or clean_name
            scryfall_id = card_info.get("scryfall_id") or card_info.get("id")
            if set_code:
                set_code = (card_info.get("set_code") or set_code).upper()
                collector_number = card_info.get("collector_number")
            image_uri = card_info.get("image_uri")
            type_line = card_info.get("type_line")
            cid = card_info.get("color_identity")
            if cid is not None:
                color_identity = ",".join(cid) if isinstance(cid, (list, tuple)) else str(cid)
            cols = card_info.get("colors")
            if cols is not None:
                colors = ",".join(cols) if isinstance(cols, (list, tuple)) else str(cols)
            mana_cost = card_info.get("mana_cost")
            cmc = card_info.get("cmc")

            prices = card_info.get("prices") or {}
            if finish == "foil":
                tcg_price = prices.get("usd_foil") or card_info.get("usd_foil")
            elif finish == "etched":
                tcg_price = prices.get("usd_etched") or card_info.get("usd_etched")
            else:
                tcg_price = prices.get("usd") or card_info.get("usd")
            tcg_url = card_info.get("tcgplayer_url")

        # Automatic deal target calculation if strategy requested and target_price absent
        if target_price is None and target_strategy in ("good", "good_deal", "good_deal_10", "great", "great_deal", "great_deal_20"):
            if tcg_price is not None:
                try:
                    num = float(tcg_price)
                    if num > 0:
                        discount = 0.80 if "great" in target_strategy else 0.90
                        target_price = round(num * discount, 2)
                except (ValueError, TypeError):
                    pass

        # Look up existing watchlist entry for user
        if set_code is None:
            existing = WatchlistItem.query.filter(
                WatchlistItem.user_id == user.id,
                WatchlistItem.name.ilike(resolved_name),
                (WatchlistItem.set_code == None) | (WatchlistItem.set_code == "") | (WatchlistItem.set_code == "ANY"),
                WatchlistItem.finish == finish,
            ).first()
        else:
            existing = WatchlistItem.query.filter(
                WatchlistItem.user_id == user.id,
                WatchlistItem.name.ilike(resolved_name),
                WatchlistItem.set_code == set_code,
                WatchlistItem.finish == finish,
            ).first()

        if existing:
            if target_price is not None:
                existing.target_price = target_price
            if tag is not None:
                existing.tag = tag
            if type_line and not getattr(existing, "type_line", None):
                existing.type_line = type_line
            if color_identity and getattr(existing, "color_identity", None) is None:
                existing.color_identity = color_identity
            if colors and getattr(existing, "colors", None) is None:
                existing.colors = colors
            if mana_cost and not getattr(existing, "mana_cost", None):
                existing.mana_cost = mana_cost
            if cmc is not None and getattr(existing, "cmc", None) is None:
                existing.cmc = cmc
            updated_items.append(existing)
            all_processed_items.append(existing)
        else:
            new_item = WatchlistItem(
                user_id=user.id,
                name=resolved_name,
                scryfall_id=scryfall_id,
                set_code=set_code,
                collector_number=collector_number,
                image_uri=image_uri,
                finish=finish,
                type_line=type_line,
                color_identity=color_identity,
                colors=colors,
                mana_cost=mana_cost,
                cmc=cmc,
                target_price=target_price,
                notify_mm_stock=True,
                tag=tag,
            )
            db.session.add(new_item)
            db.session.flush()

            if tcg_price is not None:
                try:
                    vp = VendorPrice(
                        watchlist_id=new_item.id,
                        vendor_name="TCGplayer",
                        price=float(tcg_price),
                        condition="NM",
                        in_stock=True,
                        product_url=tcg_url,
                    )
                    db.session.add(vp)
                except Exception:
                    pass

            added_items.append(new_item)
            all_processed_items.append(new_item)

    db.session.commit()

    # Single-card backward compatibility response
    if len(entries) == 1:
        if added_items:
            item = added_items[0]
            return {
                "status": "created",
                "message": f"Added '{item.name}' to your Chimera watchlist.",
                "item": item.to_dict(),
                "total_requested": 1,
                "added_count": 1,
                "updated_count": 0,
                "added_items": [item.to_dict()],
                "updated_items": [],
                "items": [item.to_dict()],
            }
        else:
            item = updated_items[0]
            return {
                "status": "updated_existing",
                "message": f"Updated existing watchlist entry for '{item.name}'.",
                "item": item.to_dict(),
                "total_requested": 1,
                "added_count": 0,
                "updated_count": 1,
                "added_items": [],
                "updated_items": [item.to_dict()],
                "items": [item.to_dict()],
            }

    status = "created" if not updated_items else ("updated_existing" if not added_items else "completed")
    return {
        "status": status,
        "message": f"Successfully processed {len(entries)} watchlist targets ({len(added_items)} added, {len(updated_items)} updated).",
        "total_requested": len(entries),
        "added_count": len(added_items),
        "updated_count": len(updated_items),
        "added_items": [i.to_dict() for i in added_items],
        "updated_items": [i.to_dict() for i in updated_items],
        "items": [i.to_dict() for i in all_processed_items],
        "item": all_processed_items[0].to_dict() if all_processed_items else None,
    }


def _evaluate_single_card_deck_fit(card_name: str, raw_query: str, user: User, decks: list[DeckAnalysis], owned_cards: list[UserInventoryCard], add_analyses: list[CardAddAnalysis]) -> dict[str, Any]:
    card_lower = card_name.lower()
    matching_owned = [c for c in owned_cards if card_names_match(c.name, card_name)]

    deck_matches = []
    in_any_deck = False

    for d in decks:
        parsed_cards = d.get_parsed_cards()
        in_deck_card = next(
            (c for c in parsed_cards if card_names_match(c.get("name") or "", card_name)),
            None,
        )
        if in_deck_card:
            in_any_deck = True

        analysis = d.get_analysis()
        card_rating = None
        for r in analysis.get("card_ratings", []):
            if card_names_match(r.get("name") or r.get("card_name") or "", card_name):
                card_rating = r
                break

        upgrades = d.get_upgrades()
        upgrade_mention = None
        if isinstance(upgrades, dict):
            for tier_key, tier_val in upgrades.items():
                if isinstance(tier_val, list):
                    for u in tier_val:
                        if isinstance(u, dict):
                            add_name = (u.get("add") or u.get("card_name") or u.get("name") or "").strip()
                            cut_name = (u.get("cut") or u.get("replace") or "").strip()
                            if card_names_match(add_name, card_name) or card_names_match(cut_name, card_name):
                                upgrade_mention = {"tier": tier_key, "entry": u}
                                break

        deck_matches.append({
            "deck_id": d.id,
            "deck_name": d.deck_name,
            "commander_name": d.commander_name,
            "color_identity": d.get_color_identity_list(),
            "archetype": d.archetype,
            "power_level": d.power_level,
            "already_in_deck": bool(in_deck_card),
            "quantity_in_deck": in_deck_card.get("quantity", 1) if in_deck_card else 0,
            "ai_rating_in_deck": card_rating,
            "upgrade_recommendation": upgrade_mention,
        })

    prior_evaluations = []
    for ca in add_analyses:
        for c in ca.get_cards():
            if card_names_match(c.get("name") or "", card_name):
                prior_evaluations.append({
                    "analysis_id": ca.id,
                    "title": ca.title,
                    "created_at": ca.created_at.isoformat() if ca.created_at else None,
                    "analysis": ca.get_analysis(),
                })
                break

    return {
        "query": raw_query,
        "card_name": card_name,
        "owned_copies": sum(c.quantity for c in matching_owned),
        "in_any_deck": in_any_deck,
        "inventory_entries": [c.to_dict(include_metadata=False) for c in matching_owned],
        "decks_evaluated": len(deck_matches),
        "deck_fit_breakdown": deck_matches,
        "prior_card_add_evaluations": prior_evaluations,
    }


def _execute_check_card_deck_fit(user: User, args: dict[str, Any]) -> dict[str, Any]:
    extracted_items = _parse_card_input_list(args)
    if not extracted_items:
        legacy_name = (args.get("card_name") or "").strip()
        if legacy_name:
            extracted_items = [{"raw_query": legacy_name, "clean_name": normalize_card_name(legacy_name), "quantity": 1}]

    if not extracted_items:
        return {"error": "card_name is required (or pass 'cards' array / 'text')."}

    # Load decks and inventory once
    deck_id = args.get("deck_id")
    query_str = (args.get("query") or "").strip()

    q = DeckAnalysis.query.filter_by(user_id=user.id)
    if deck_id is not None:
        q = q.filter_by(id=int(deck_id))
    elif query_str:
        q = q.filter(
            db.or_(
                DeckAnalysis.deck_name.ilike(f"%{query_str}%"),
                DeckAnalysis.commander_name.ilike(f"%{query_str}%"),
            )
        )
    decks = q.order_by(DeckAnalysis.updated_at.desc()).all()
    owned_cards = UserInventoryCard.query.filter_by(user_id=user.id).all()
    add_analyses = CardAddAnalysis.query.filter_by(user_id=user.id).order_by(CardAddAnalysis.created_at.desc()).limit(20).all()

    # 1. Single card query without batch wrapper
    if len(extracted_items) == 1 and not args.get("cards") and not args.get("card_names"):
        first = extracted_items[0]
        res = _evaluate_single_card_deck_fit(first["clean_name"], first["raw_query"], user, decks, owned_cards, add_analyses)
        return res

    # 2. Batch cards query
    results = []
    for item in extracted_items:
        c_res = _evaluate_single_card_deck_fit(item["clean_name"], item["raw_query"], user, decks, owned_cards, add_analyses)
        results.append(c_res)

    return {
        "total_cards_evaluated": len(results),
        "decks_count": len(decks),
        "cards": results,
        "results": results,
    }


def _execute_upload_inventory(user: User, args: dict[str, Any]) -> dict[str, Any]:
    """
    Bulk uploads / imports cards into user's collection inventory (UserInventoryCard).
    Accepts:
    - gdrive_url: Google Drive CSV share link
    - csv_content: ManaBox CSV string
    - text: Decklist text or TCGplayer purchase order text
    - cards: List of card dicts or card name strings
    - mode: 'merge' (default) or 'replace'
    """
    mode = (args.get("mode") or "merge").strip().lower()
    if mode not in ("merge", "replace"):
        mode = "merge"

    default_binder = (args.get("binder_name") or "").strip()
    default_condition = (args.get("condition") or "Near Mint").strip()
    default_foil = (args.get("foil") or "normal").strip().lower()
    if default_foil not in ("normal", "foil", "etched"):
        default_foil = "normal"

    from inventory_parser import ManaBoxInventoryParser, InventoryParseError
    from inventory_manager import InventoryManager

    csv_content = ""
    gdrive_url = (args.get("gdrive_url") or "").strip()

    if gdrive_url:
        try:
            csv_content = ManaBoxInventoryParser.download_gdrive_csv(gdrive_url)
            user.inventory_gdrive_url = gdrive_url
            db.session.commit()
        except InventoryParseError as e:
            return {"error": f"Google Drive download error: {str(e)}"}
        except Exception as e:
            logger.error(f"MCP upload_inventory GDrive error: {e}", exc_info=True)
            return {"error": f"Failed to download Google Drive CSV: {str(e)}"}
    elif args.get("csv_content"):
        csv_content = str(args["csv_content"]).strip()

    parsed_cards: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if csv_content:
        try:
            parsed = ManaBoxInventoryParser.parse(csv_content)
            parsed_cards = parsed.get("valid_cards", [])
            errors = parsed.get("errors", [])
        except InventoryParseError as e:
            return {"error": f"CSV parse error: {str(e)}"}
        except Exception as e:
            return {"error": f"Failed to parse CSV: {str(e)}"}
    else:
        # Check text or cards array via _parse_card_input_list
        raw_items = _parse_card_input_list(args)
        if not raw_items:
            return {"error": "No cards, decklist text, CSV content, or Google Drive URL provided to upload."}

        for item in raw_items:
            c_name = item.get("clean_name") or normalize_card_name(item.get("raw_query") or "")
            if not c_name:
                continue
            parsed_cards.append({
                "name": c_name,
                "raw_name": item.get("raw_query") or c_name,
                "set_code": (item.get("set_code") or "").upper().strip(),
                "set_name": item.get("set_name") or "",
                "collector_number": str(item.get("collector_number") or "").strip(),
                "scryfall_id": item.get("scryfall_id"),
                "quantity": max(1, int(item.get("quantity", 1) or 1)),
                "foil": item.get("foil") or default_foil,
                "condition": item.get("condition") or default_condition,
                "language": item.get("language") or "en",
                "purchase_price": item.get("purchase_price"),
                "binder_name": item.get("binder_name") or default_binder,
            })

    if not parsed_cards:
        return {"error": "No valid cards could be parsed from input.", "errors": errors}

    inv_mgr = InventoryManager()
    import_result = inv_mgr.import_inventory(
        user_id=user.id,
        parsed_cards=parsed_cards,
        mode=mode,
    )

    return {
        "success": True,
        "mode": mode,
        "cards_parsed_count": len(parsed_cards),
        "added_count": import_result.get("added_count", 0),
        "updated_count": import_result.get("updated_count", 0),
        "total_cards_quantity": import_result.get("total_cards", 0),
        "unique_cards_count": import_result.get("unique_cards", 0),
        "total_collection_value_usd": import_result.get("total_value", 0.0),
        "errors": errors,
        "message": f"Successfully imported {len(parsed_cards)} cards into inventory ({mode} mode).",
    }


def _execute_upload_decks(user: User, args: dict[str, Any]) -> dict[str, Any]:
    """
    Bulk uploads or imports Commander decks into user's Deck Vault.
    Accepts:
    - urls: list of deck URLs (ManaBox, Moxfield, Archidekt, Scryfall, MTGGoldfish)
    - url: single deck URL
    - decks: list of deck objects ({deck_name, commander, url, text, csv_content, cards, is_pauper})
    - text / csv_content: decklist text or CSV
    - deck_name, commander_name, is_pauper: metadata for single deck import
    """
    from deck_parser import DeckParser, DeckParseError
    from deck_analyzer import DeckAnalyzer
    from providers.scryfall import ScryfallProvider

    raw_urls: list[str] = []
    if args.get("urls") and isinstance(args["urls"], list):
        raw_urls.extend([str(u).strip() for u in args["urls"] if str(u).strip()])
    if args.get("url") and isinstance(args["url"], str) and args["url"].strip():
        raw_urls.append(args["url"].strip())

    deck_objects = args.get("decks") if isinstance(args.get("decks"), list) else []

    scryfall = ScryfallProvider()
    analyzer = DeckAnalyzer(scryfall_provider=scryfall)

    imported_decks: list[DeckAnalysis] = []
    failed_items: list[dict[str, Any]] = []

    # 1. Process URL imports
    for url in raw_urls:
        try:
            parsed = DeckParser.parse(url, source_type="auto")
            card_names = [c["name"] for c in parsed.get("cards", [])]
            scryfall_map, _ = scryfall.get_cards_collection(card_names)

            enriched_cards = []
            for c in parsed.get("cards", []):
                cname = normalize_card_name(c["name"])
                meta = scryfall_map.get(cname.lower(), {})
                if not meta:
                    for k in get_card_match_keys(cname):
                        if k in scryfall_map:
                            meta = scryfall_map[k]
                            break

                img_uri = meta.get("image_uri") or meta.get("small_image_uri") or c.get("image_uri")
                art_crop = meta.get("art_crop_uri") or img_uri
                price_usd = meta.get("prices", {}).get("usd") or c.get("price_usd")
                card_cmc = meta.get("cmc") if meta.get("cmc") is not None else (c.get("cmc") if c.get("cmc") is not None else 0)

                enriched_cards.append({
                    "name": cname,
                    "quantity": c.get("quantity", 1),
                    "section": c.get("section", "mainboard"),
                    "set_code": c.get("set_code") or meta.get("set_code", ""),
                    "collector_number": c.get("collector_number") or meta.get("collector_number", ""),
                    "image_uri": img_uri,
                    "small_image_uri": meta.get("small_image_uri") or img_uri,
                    "art_crop_uri": art_crop,
                    "mana_cost": meta.get("mana_cost", "") or c.get("mana_cost", ""),
                    "cmc": card_cmc,
                    "type_line": meta.get("type_line", "Unknown"),
                    "oracle_text": meta.get("oracle_text", ""),
                    "colors": meta.get("colors", []),
                    "color_identity": meta.get("color_identity", []),
                    "rarity": meta.get("rarity", ""),
                    "price_usd": price_usd,
                    "price_usd_foil": meta.get("prices", {}).get("usd_foil"),
                })

            cmdr_art = parsed.get("commander_art")
            if not cmdr_art:
                for cmd_name in parsed.get("commander", []):
                    clean_cmd = normalize_card_name(cmd_name)
                    cmd_meta = scryfall_map.get(clean_cmd.lower(), {})
                    if cmd_meta.get("art_crop_uri") or cmd_meta.get("image_uri"):
                        cmdr_art = cmd_meta.get("art_crop_uri") or cmd_meta.get("image_uri")
                        break

            is_pauper_flag = bool(parsed.get("is_pauper") or parsed.get("deck_format") == "pauper_commander")
            analyzed = analyzer.analyze({
                "deck_name": parsed.get("deck_name") or "Commander Deck",
                "commander": parsed.get("commander", []),
                "commander_art": cmdr_art,
                "cards": enriched_cards,
                "source_type": parsed.get("source_type", "url"),
                "raw_text": parsed.get("raw_text", ""),
                "is_pauper": is_pauper_flag,
                "deck_format": "pauper_commander" if is_pauper_flag else "commander",
            })

            stats = analyzed.get("stats", {})
            cmdr_name = ", ".join(analyzed.get("commander", [])) or "Commander"
            color_id_str = ",".join(stats.get("color_identity", []))

            entry = DeckAnalysis(
                user_id=user.id,
                deck_name=analyzed.get("deck_name", "Commander Deck"),
                commander_name=cmdr_name,
                commander_art=cmdr_art,
                source_url=url,
                source_type=parsed.get("source_type", "url"),
                raw_decklist=parsed.get("raw_text", ""),
                cards_data=json.dumps(analyzed.get("cards", [])),
                stats_json=json.dumps(stats),
                total_cards=analyzed.get("total_cards", 100),
                total_value=stats.get("total_value"),
                avg_cmc=stats.get("avg_cmc"),
                color_identity=color_id_str,
                is_pauper=is_pauper_flag,
                deck_format="pauper_commander" if is_pauper_flag else "commander",
            )
            db.session.add(entry)
            imported_decks.append(entry)
        except Exception as e:
            logger.warning(f"MCP upload_decks error for URL {url}: {e}")
            failed_items.append({"source": url, "error": str(e)})

    # 2. Process deck objects or raw text/csv
    if deck_objects:
        candidate_decks = deck_objects
    elif args.get("text") or args.get("csv_content"):
        candidate_decks = [{
            "deck_name": args.get("deck_name") or "Commander Deck",
            "commander_name": args.get("commander_name"),
            "text": args.get("text"),
            "csv_content": args.get("csv_content"),
            "is_pauper": args.get("is_pauper"),
        }]
    else:
        candidate_decks = []

    for dobj in candidate_decks:
        try:
            d_source = dobj.get("text") or dobj.get("csv_content") or dobj.get("url") or ""
            d_cards = dobj.get("cards")
            is_pauper_val = dobj.get("is_pauper")

            if d_source:
                parsed = DeckParser.parse(d_source, source_type="auto", is_pauper=is_pauper_val)
            elif d_cards and isinstance(d_cards, list):
                parsed = {
                    "deck_name": dobj.get("deck_name") or "Commander Deck",
                    "commander": [dobj["commander_name"]] if dobj.get("commander_name") else [],
                    "cards": [{"name": (c.get("name") if isinstance(c, dict) else str(c)), "quantity": (c.get("quantity", 1) if isinstance(c, dict) else 1)} for c in d_cards],
                    "total_cards": sum((c.get("quantity", 1) if isinstance(c, dict) else 1) for c in d_cards),
                    "source_type": "object",
                    "raw_text": "",
                    "is_pauper": bool(is_pauper_val),
                }
            else:
                continue

            if dobj.get("deck_name"):
                parsed["deck_name"] = dobj["deck_name"]
            if dobj.get("commander_name"):
                parsed["commander"] = [dobj["commander_name"]]

            card_names = [c["name"] for c in parsed.get("cards", [])]
            scryfall_map, _ = scryfall.get_cards_collection(card_names)

            enriched_cards = []
            for c in parsed.get("cards", []):
                cname = normalize_card_name(c["name"])
                meta = scryfall_map.get(cname.lower(), {})
                if not meta:
                    for k in get_card_match_keys(cname):
                        if k in scryfall_map:
                            meta = scryfall_map[k]
                            break

                img_uri = meta.get("image_uri") or meta.get("small_image_uri") or c.get("image_uri")
                art_crop = meta.get("art_crop_uri") or img_uri
                price_usd = meta.get("prices", {}).get("usd") or c.get("price_usd")
                card_cmc = meta.get("cmc") if meta.get("cmc") is not None else (c.get("cmc") if c.get("cmc") is not None else 0)

                enriched_cards.append({
                    "name": cname,
                    "quantity": c.get("quantity", 1),
                    "section": c.get("section", "mainboard"),
                    "set_code": c.get("set_code") or meta.get("set_code", ""),
                    "collector_number": c.get("collector_number") or meta.get("collector_number", ""),
                    "image_uri": img_uri,
                    "small_image_uri": meta.get("small_image_uri") or img_uri,
                    "art_crop_uri": art_crop,
                    "mana_cost": meta.get("mana_cost", "") or c.get("mana_cost", ""),
                    "cmc": card_cmc,
                    "type_line": meta.get("type_line", "Unknown"),
                    "oracle_text": meta.get("oracle_text", ""),
                    "colors": meta.get("colors", []),
                    "color_identity": meta.get("color_identity", []),
                    "rarity": meta.get("rarity", ""),
                    "price_usd": price_usd,
                    "price_usd_foil": meta.get("prices", {}).get("usd_foil"),
                })

            cmdr_art = parsed.get("commander_art")
            if not cmdr_art:
                for cmd_name in parsed.get("commander", []):
                    clean_cmd = normalize_card_name(cmd_name)
                    cmd_meta = scryfall_map.get(clean_cmd.lower(), {})
                    if cmd_meta.get("art_crop_uri") or cmd_meta.get("image_uri"):
                        cmdr_art = cmd_meta.get("art_crop_uri") or cmd_meta.get("image_uri")
                        break

            is_pauper_flag = bool(parsed.get("is_pauper") or parsed.get("deck_format") == "pauper_commander")
            analyzed = analyzer.analyze({
                "deck_name": parsed.get("deck_name") or "Commander Deck",
                "commander": parsed.get("commander", []),
                "commander_art": cmdr_art,
                "cards": enriched_cards,
                "source_type": parsed.get("source_type", "text"),
                "raw_text": parsed.get("raw_text", ""),
                "is_pauper": is_pauper_flag,
                "deck_format": "pauper_commander" if is_pauper_flag else "commander",
            })

            stats = analyzed.get("stats", {})
            cmdr_name = ", ".join(analyzed.get("commander", [])) or "Commander"
            color_id_str = ",".join(stats.get("color_identity", []))

            entry = DeckAnalysis(
                user_id=user.id,
                deck_name=analyzed.get("deck_name", "Commander Deck"),
                commander_name=cmdr_name,
                commander_art=cmdr_art,
                source_url=dobj.get("url"),
                source_type=parsed.get("source_type", "text"),
                raw_decklist=parsed.get("raw_text", ""),
                cards_data=json.dumps(analyzed.get("cards", [])),
                stats_json=json.dumps(stats),
                total_cards=analyzed.get("total_cards", 100),
                total_value=stats.get("total_value"),
                avg_cmc=stats.get("avg_cmc"),
                color_identity=color_id_str,
                is_pauper=is_pauper_flag,
                deck_format="pauper_commander" if is_pauper_flag else "commander",
            )
            db.session.add(entry)
            imported_decks.append(entry)
        except Exception as e:
            logger.warning(f"MCP upload_decks error for object {dobj}: {e}")
            failed_items.append({"source": dobj.get("deck_name") or str(dobj)[:50], "error": str(e)})

    if imported_decks:
        db.session.commit()

    if not imported_decks and failed_items:
        return {
            "error": "Failed to import any Commander decks.",
            "failed_count": len(failed_items),
            "failed": failed_items,
        }

    return {
        "success": True,
        "imported_count": len(imported_decks),
        "failed_count": len(failed_items),
        "decks": [d.to_dict(include_full=False) for d in imported_decks],
        "failed": failed_items,
        "message": f"Successfully imported {len(imported_decks)} Commander deck(s) into your Vault.",
    }


def _execute_reconcile_watchlist(user: User, args: dict[str, Any]) -> dict[str, Any]:
    """
    Bulk removes or reconciles cards from user's watchlist that were acquired/purchased.
    Optionally ingests them into UserInventoryCard if add_to_inventory=True.
    """
    card_ids = args.get("card_ids") if isinstance(args.get("card_ids"), list) else []
    add_to_inventory = bool(args.get("add_to_inventory", False))
    binder_name = (args.get("binder_name") or "Acquired").strip() or "Acquired"
    condition = (args.get("condition") or "Near Mint").strip() or "Near Mint"

    user_targets = WatchlistItem.query.filter_by(user_id=user.id).all()
    if not user_targets:
        return {
            "total_requested": 0,
            "removed_count": 0,
            "inventory_added_count": 0,
            "removed_items": [],
            "message": "Watchlist is currently empty; no items to reconcile.",
        }

    matched_targets: dict[int, WatchlistItem] = {}
    purchased_entries: list[dict[str, Any]] = []

    # 1. Direct card_ids
    if card_ids:
        for cid in card_ids:
            try:
                target = next((t for t in user_targets if t.id == int(cid)), None)
                if target:
                    matched_targets[target.id] = target
                    purchased_entries.append({
                        "name": target.name,
                        "set_code": target.set_code,
                        "collector_number": target.collector_number,
                        "foil": target.finish or "nonfoil",
                        "quantity": 1,
                    })
            except (ValueError, TypeError):
                pass

    # 2. Text / cards input
    extracted_items = _parse_card_input_list(args)
    for item in extracted_items:
        clean_name = item["clean_name"]
        item_qty = item.get("quantity", 1)
        purchased_entries.append(item)
        for target in user_targets:
            if card_names_match(target.name, clean_name):
                matched_targets[target.id] = target

    if not matched_targets and not extracted_items and not card_ids:
        return {"error": "No purchase text, card names, or card_ids provided to reconcile."}

    removed_items_serialized = [t.to_dict() for t in matched_targets.values()]

    # Delete watchlist rows (SQLAlchemy cascade deletes associated vendor_price records)
    if matched_targets:
        for t in matched_targets.values():
            db.session.delete(t)

    # Ingest into inventory if requested
    inventory_added_count = 0
    if add_to_inventory and purchased_entries:
        from inventory_manager import InventoryManager
        inv_cards = []
        for p in purchased_entries:
            cname = p.get("clean_name") or p.get("raw_query") or p.get("name")
            if not cname:
                continue
            inv_cards.append({
                "name": cname,
                "raw_name": p.get("raw_query") or cname,
                "set_code": p.get("set_code") or "",
                "collector_number": p.get("collector_number") or "",
                "quantity": p.get("quantity", 1),
                "foil": p.get("foil") or "normal",
                "condition": p.get("condition") or condition,
                "binder_name": p.get("binder_name") or binder_name,
            })
        if inv_cards:
            inv_mgr = InventoryManager()
            inv_mgr.import_inventory(user_id=user.id, parsed_cards=inv_cards, mode="merge")
            inventory_added_count = len(inv_cards)

    db.session.commit()

    return {
        "success": True,
        "removed_count": len(matched_targets),
        "removed_items": removed_items_serialized,
        "inventory_added_count": inventory_added_count,
        "message": f"Successfully de-registered {len(matched_targets)} watchlist card(s)." + (f" Ingested {inventory_added_count} item(s) into inventory under '{binder_name}'." if add_to_inventory else ""),
    }


TOOL_HANDLERS = {
    "get_inventory_summary": _execute_get_inventory_summary,
    "check_inventory_cards": _execute_check_inventory_cards,
    "check_cards_in_inventory": _execute_check_inventory_cards,
    "batch_check_inventory": _execute_check_inventory_cards,
    "search_inventory": _execute_search_inventory,
    "get_watchlist": _execute_get_watchlist,
    "get_active_deals": _execute_get_active_deals,
    "get_deck_analyses": _execute_get_deck_analyses,
    "get_deck_cards": _execute_get_deck_cards,
    "search_card_prices": _execute_search_card_prices,
    "bulk_search_card_prices": _execute_search_card_prices,
    "add_to_watchlist": _execute_add_to_watchlist,
    "add_cards_to_watchlist": _execute_add_to_watchlist,
    "bulk_add_to_watchlist": _execute_add_to_watchlist,
    "check_card_deck_fit": _execute_check_card_deck_fit,
    "bulk_check_card_deck_fit": _execute_check_card_deck_fit,
    "upload_inventory": _execute_upload_inventory,
    "add_to_inventory": _execute_upload_inventory,
    "bulk_upload_inventory": _execute_upload_inventory,
    "import_inventory": _execute_upload_inventory,
    "upload_decks": _execute_upload_decks,
    "import_decks": _execute_upload_decks,
    "bulk_import_decks": _execute_upload_decks,
    "save_deck": _execute_upload_decks,
    "reconcile_watchlist": _execute_reconcile_watchlist,
    "remove_from_watchlist": _execute_reconcile_watchlist,
    "bulk_remove_watchlist": _execute_reconcile_watchlist,
    "reconcile_purchases": _execute_reconcile_watchlist,
}


def execute_mcp_tool(user: User, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Executes a named Chimera MCP tool for the given User and returns an MCP CallToolResult dict.
    """
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": f"Unknown tool: '{tool_name}'"}),
                }
            ],
            "isError": True,
        }

    try:
        result_payload = handler(user, arguments or {})
        is_error = bool(isinstance(result_payload, dict) and "error" in result_payload and len(result_payload) == 1)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result_payload, indent=2, default=str),
                }
            ],
            "isError": is_error,
        }
    except Exception as exc:
        logger.error(f"Error executing MCP tool '{tool_name}': {exc}", exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": f"Tool execution error: {str(exc)}"}),
                }
            ],
            "isError": True,
        }


def handle_mcp_jsonrpc_message(
    message: dict[str, Any],
    user: User,
) -> dict[str, Any] | None:
    """
    Processes a single JSON-RPC 2.0 MCP message and returns a JSON-RPC 2.0 response dict
    (or None if the message is a JSON-RPC notification without an 'id').
    """
    if not isinstance(message, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request: expected JSON object."},
        }

    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    # Notifications (e.g. notifications/initialized, notifications/cancelled) produce no response
    if is_notification or (isinstance(method, str) and method.startswith("notifications/")):
        return None

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        negotiated_version = (
            requested_version
            if requested_version in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION
        )
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": negotiated_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {
                    "name": MCP_SERVER_NAME,
                    "version": MCP_SERVER_VERSION,
                },
                "instructions": (
                    "Chimera MTG Market Surveillance & Collection Intelligence MCP Server. "
                    "Use these tools to quickly check single or batch card inventory ownership with check_inventory_cards, "
                    "search card details with search_inventory, inspect collection totals with get_inventory_summary, "
                    "check watched single-card prices and active retail deals across TCGplayer, "
                    "Mighty Meeple, eBay, MicroCenter, and Best Buy, inspect Commander deck "
                    "analyses and upgrade recommendations, or add cards to the watchlist."
                ),
            },
        }

    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {},
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": MCP_TOOLS,
            },
        }

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not tool_name:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": "Missing required parameter: 'name'."},
            }
        tool_result = execute_mcp_tool(user, tool_name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": tool_result,
        }

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"resources": []},
        }

    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "prompts": [
                    {
                        "name": "chimera_daily_briefing",
                        "description": "Summarize active MTG deals, watchlist alerts, and collection highlights.",
                        "arguments": [],
                    }
                ]
            },
        }

    if method == "prompts/get":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "description": "Chimera MTG Daily Briefing Prompt",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                "Please check my active MTG deals using get_active_deals and summarize "
                                "my collection status using get_inventory_summary."
                            ),
                        },
                    }
                ],
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: '{method}'",
        },
    }


def handle_mcp_payload(payload: Any, user: User) -> tuple[Any, int]:
    """
    Handles either a single JSON-RPC 2.0 request object or a batch list of objects.
    Returns (response_body_or_None, http_status_code).
    """
    if isinstance(payload, list):
        if not payload:
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: empty batch."},
            }, 400
        responses = []
        for item in payload:
            resp = handle_mcp_jsonrpc_message(item, user)
            if resp is not None:
                responses.append(resp)
        if not responses:
            return None, 202
        return responses, 200

    resp = handle_mcp_jsonrpc_message(payload, user)
    if resp is None:
        return None, 202
    return resp, 200


def run_stdio_server():
    """
    Runs the Chimera MCP Server over standard input/output (stdio) for local CLI / Antigravity usage.
    """
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        admin_email = flask_app.config.get("ADMIN_EMAIL", "")
        user = None
        if admin_email:
            user = User.query.filter(db.func.lower(User.email) == admin_email.lower()).first()
        if not user:
            user = User.query.filter_by(is_admin=True).order_by(User.id.asc()).first()
        if not user:
            user = User.query.order_by(User.id.asc()).first()
        if not user:
            user = User(email=admin_email or "admin@chimera.local", name="Chimera Admin", is_admin=True, is_active=True)
            user.ensure_api_token()
            db.session.add(user)
            db.session.commit()

        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
                continue

            resp_body, _ = handle_mcp_payload(msg, user)
            if resp_body is not None:
                sys.stdout.write(json.dumps(resp_body) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        run_stdio_server()
    else:
        print("Chimera MCP Server module. Run with `--stdio` for stdio mode or access `/mcp` via Flask/Cloud Run.")
