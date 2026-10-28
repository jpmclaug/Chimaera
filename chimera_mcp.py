"""
Chimera Model Context Protocol (MCP) Server Engine.

Provides stateless Streamable HTTP, Server-Sent Events (SSE), and stdio MCP support
so Google Gemini Mobile App (via Gemini Connected Apps), Custom Gems, and Antigravity
skills can query and interact with the Chimera PostgreSQL / SQLite database on Google Cloud Run.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

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
            "price ratings, and whether the card is currently an active deal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional card name filter.",
                },
                "tag": {
                    "type": "string",
                    "description": "Optional watchlist tag filter.",
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
            "recommendations for a specific deck."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deck_id": {
                    "type": "integer",
                    "description": "Optional specific DeckAnalysis ID to inspect in full detail.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional deck name or commander name to search for (e.g. 'Ur-Dragon', 'Sauron').",
                },
                "include_cards": {
                    "type": "boolean",
                    "description": "If true when inspecting a deck, includes the full parsed card list.",
                    "default": False,
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_card_prices",
        "description": (
            "Looks up live Scryfall market pricing and oracle metadata for any Magic: The Gathering card, "
            "and cross-references whether the user already owns the card in inventory or tracks it on their watchlist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "card_name": {
                    "type": "string",
                    "description": "Exact or partial Magic: The Gathering card name to look up.",
                },
                "set_code": {
                    "type": "string",
                    "description": "Optional 3-4 letter set code (e.g. 'MH3', 'LTR').",
                },
            },
            "required": ["card_name"],
        },
    },
    {
        "name": "add_to_watchlist",
        "description": (
            "Adds a Magic: The Gathering card to the user's Chimera surveillance watchlist with an optional "
            "target buy price, set code, finish, and tag."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "card_name": {
                    "type": "string",
                    "description": "Name of the MTG card to add to the watchlist.",
                },
                "target_price": {
                    "type": "number",
                    "description": "Target USD acquisition price threshold for deal alerts.",
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
                    "description": "Optional category tag (e.g. 'Staples', 'Ur-Dragon Upgrades').",
                },
            },
            "required": ["card_name"],
        },
    },
    {
        "name": "check_card_deck_fit",
        "description": (
            "Evaluates how a specific Magic: The Gathering card fits across the user's saved Commander decks: "
            "checks color identity compatibility, whether the card is already in any deck, whether it appears "
            "in saved upgrade recommendations or Card Add / Secret Lair analyses, and whether the user owns copies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "card_name": {
                    "type": "string",
                    "description": "Name of the card to check across the user's Commander fleet.",
                }
            },
            "required": ["card_name"],
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


def _execute_search_inventory(user: User, args: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(args.get("limit", 25) or 25), 1), 100)
    q = UserInventoryCard.query.filter_by(user_id=user.id)

    query_str = (args.get("query") or args.get("card_name") or "").strip()
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

    q = WatchlistItem.query.filter_by(user_id=user.id)
    if query_str:
        q = q.filter(WatchlistItem.name.ilike(f"%{query_str}%"))
    if tag_str:
        q = q.filter(WatchlistItem.tag.ilike(f"%{tag_str}%"))

    items = q.order_by(WatchlistItem.name.asc()).all()
    serialized = []
    for item in items:
        if deals_only and not item.is_deal:
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
    query_str = (args.get("query") or "").strip()
    include_cards = bool(args.get("include_cards", False))

    q = DeckAnalysis.query.filter_by(user_id=user.id)
    if deck_id is not None:
        deck = q.filter_by(id=int(deck_id)).first()
        if not deck:
            return {"error": f"No Commander deck found with ID {deck_id}."}
        full_data = deck.to_dict(include_full=True)
        if not include_cards:
            full_data.pop("cards_data", None)
            full_data.pop("raw_decklist", None)
        return {"deck": full_data}

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


def _execute_search_card_prices(user: User, args: dict[str, Any]) -> dict[str, Any]:
    card_name = (args.get("card_name") or "").strip()
    set_code = (args.get("set_code") or "").strip()
    if not card_name:
        return {"error": "card_name is required."}

    # Check user's inventory & watchlist first
    owned = UserInventoryCard.query.filter(
        UserInventoryCard.user_id == user.id,
        UserInventoryCard.name.ilike(f"%{card_name}%"),
    ).all()

    watched = WatchlistItem.query.filter(
        WatchlistItem.user_id == user.id,
        WatchlistItem.name.ilike(f"%{card_name}%"),
    ).all()

    scryfall_data = None
    lowest_printing = None
    try:
        from providers.scryfall import ScryfallProvider
        provider = ScryfallProvider()
        scryfall_data = provider.get_card_named(card_name)
        if not set_code or set_code.upper() == "ANY":
            lowest_printing = provider.get_cheapest_tcgplayer_price(card_name)
    except Exception as exc:
        logger.warning(f"MCP search_card_prices Scryfall lookup error for '{card_name}': {exc}")

    return {
        "queried_card": card_name,
        "scryfall_card": scryfall_data,
        "lowest_market_printing": lowest_printing,
        "owned_in_inventory": [c.to_dict(include_metadata=False) for c in owned],
        "total_owned_quantity": sum(c.quantity for c in owned),
        "tracked_on_watchlist": [w.to_dict() for w in watched],
    }


def _execute_add_to_watchlist(user: User, args: dict[str, Any]) -> dict[str, Any]:
    card_name = (args.get("card_name") or "").strip()
    if not card_name:
        return {"error": "card_name is required."}

    target_price = args.get("target_price")
    if target_price is not None:
        try:
            target_price = round(float(target_price), 2)
        except (ValueError, TypeError):
            target_price = None

    set_code = (args.get("set_code") or "").strip().upper()
    if set_code in ("", "ANY"):
        set_code = None

    finish = (args.get("finish") or "nonfoil").strip().lower()
    if finish not in ("nonfoil", "foil", "etched"):
        finish = "nonfoil"

    tag = (args.get("tag") or "").strip() or None

    resolved_name = card_name
    scryfall_id = None
    collector_number = None
    image_uri = None
    tcg_price = None
    tcg_url = None

    try:
        from providers.scryfall import ScryfallProvider
        provider = ScryfallProvider()
        card_info = provider.get_card_named(card_name)
        if card_info:
            resolved_name = card_info.get("name") or card_name
            scryfall_id = card_info.get("scryfall_id") or card_info.get("id")
            if set_code:
                set_code = (card_info.get("set_code") or set_code).upper()
                collector_number = card_info.get("collector_number")
            image_uri = card_info.get("image_uri")
            prices = card_info.get("prices") or {}
            if finish == "foil":
                tcg_price = prices.get("usd_foil") or card_info.get("usd_foil")
            elif finish == "etched":
                tcg_price = prices.get("usd_etched") or card_info.get("usd_etched")
            else:
                tcg_price = prices.get("usd") or card_info.get("usd")
            tcg_url = card_info.get("tcgplayer_url")
    except Exception as exc:
        logger.warning(f"MCP add_to_watchlist Scryfall enrichment warning for '{card_name}': {exc}")

    existing = WatchlistItem.query.filter(
        WatchlistItem.user_id == user.id,
        db.func.lower(WatchlistItem.name) == resolved_name.lower(),
        WatchlistItem.set_code == set_code,
        WatchlistItem.finish == finish,
    ).first()

    if existing:
        if target_price is not None:
            existing.target_price = target_price
        if tag is not None:
            existing.tag = tag
        db.session.commit()
        return {
            "status": "updated_existing",
            "message": f"Updated existing watchlist entry for '{resolved_name}'.",
            "item": existing.to_dict(),
        }

    new_item = WatchlistItem(
        user_id=user.id,
        name=resolved_name,
        scryfall_id=scryfall_id,
        set_code=set_code,
        collector_number=collector_number,
        image_uri=image_uri,
        finish=finish,
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

    db.session.commit()
    return {
        "status": "created",
        "message": f"Added '{resolved_name}' to your Chimera watchlist.",
        "item": new_item.to_dict(),
    }


def _execute_check_card_deck_fit(user: User, args: dict[str, Any]) -> dict[str, Any]:
    card_name = (args.get("card_name") or "").strip()
    if not card_name:
        return {"error": "card_name is required."}

    card_lower = card_name.lower()

    # Check inventory ownership
    owned_cards = UserInventoryCard.query.filter(
        UserInventoryCard.user_id == user.id,
        UserInventoryCard.name.ilike(f"%{card_name}%"),
    ).all()

    decks = DeckAnalysis.query.filter_by(user_id=user.id).order_by(DeckAnalysis.updated_at.desc()).all()
    deck_matches = []

    for d in decks:
        parsed_cards = d.get_parsed_cards()
        in_deck_card = next(
            (c for c in parsed_cards if (c.get("name") or "").strip().lower() == card_lower),
            None,
        )

        # Check if card is mentioned in deck's AI card ratings or upgrades
        analysis = d.get_analysis()
        card_rating = None
        for r in analysis.get("card_ratings", []):
            if (r.get("name") or r.get("card_name") or "").strip().lower() == card_lower:
                card_rating = r
                break

        upgrades = d.get_upgrades()
        upgrade_mention = None
        if isinstance(upgrades, dict):
            for tier_key, tier_val in upgrades.items():
                if isinstance(tier_val, list):
                    for u in tier_val:
                        if isinstance(u, dict):
                            add_name = (u.get("add") or u.get("card_name") or u.get("name") or "").strip().lower()
                            cut_name = (u.get("cut") or u.get("replace") or "").strip().lower()
                            if card_lower in (add_name, cut_name):
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

    # Check saved CardAddAnalysis records
    add_analyses = CardAddAnalysis.query.filter_by(user_id=user.id).order_by(CardAddAnalysis.created_at.desc()).limit(20).all()
    prior_evaluations = []
    for ca in add_analyses:
        for c in ca.get_cards():
            if card_lower in (c.get("name") or "").strip().lower():
                prior_evaluations.append({
                    "analysis_id": ca.id,
                    "title": ca.title,
                    "created_at": ca.created_at.isoformat() if ca.created_at else None,
                    "analysis": ca.get_analysis(),
                })
                break

    return {
        "card_name": card_name,
        "owned_copies": sum(c.quantity for c in owned_cards),
        "inventory_entries": [c.to_dict(include_metadata=False) for c in owned_cards],
        "decks_evaluated": len(deck_matches),
        "deck_fit_breakdown": deck_matches,
        "prior_card_add_evaluations": prior_evaluations,
    }


TOOL_HANDLERS = {
    "get_inventory_summary": _execute_get_inventory_summary,
    "search_inventory": _execute_search_inventory,
    "get_watchlist": _execute_get_watchlist,
    "get_active_deals": _execute_get_active_deals,
    "get_deck_analyses": _execute_get_deck_analyses,
    "search_card_prices": _execute_search_card_prices,
    "add_to_watchlist": _execute_add_to_watchlist,
    "check_card_deck_fit": _execute_check_card_deck_fit,
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
                    "Use these tools to search the user's Magic: The Gathering card inventory, "
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
