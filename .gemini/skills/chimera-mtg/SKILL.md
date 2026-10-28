---
name: chimera-mtg
description: Query and manage the Chimaera Magic: The Gathering database (card inventory, surveillance watchlist, active retail deals, and Commander deck analyses) via the Chimaera Model Context Protocol (MCP) server locally or on Google Cloud Run.
---

# Chimaera MTG Database & MCP Skill

Use this skill when the user asks questions about their Magic: The Gathering collection inventory, tracked watchlist singles, active price deals (TCGplayer, Mighty Meeple, eBay, MicroCenter, Best Buy), or saved Commander deck analyses in **Chimaera**.

## Available MCP Tools

The Chimaera MCP engine ([chimera_mcp.py](file:///c:/Users/jpmcl/Python%20Development/Chimera/chimera_mcp.py)) exposes 8 tools:

1. `get_inventory_summary(top_n=15)`: Returns total card quantity, unique printings, total USD market value, foil breakdown, binder breakdown, color identity distribution, and top valuable cards.
2. `search_inventory(query, set_code, foil, condition, binder_name, type_line, color_identity, min_price, limit)`: Searches owned cards in `UserInventoryCard`.
3. `get_watchlist(query, tag, deals_only, limit)`: Lists monitored cards on the user's `WatchlistItem` registry with current vendor prices and target thresholds.
4. `get_active_deals(category="all")`: Returns active deals across single-card watchlists, MicroCenter Charlotte (`MicrocenterItem`), and Best Buy (`BestBuyItem`).
5. `get_deck_analyses(deck_id, query, include_cards)`: Lists saved Commander decks or retrieves full AI tactical breakdown, card ratings, stats, and upgrades for a deck.
6. `search_card_prices(card_name, set_code)`: Queries live Scryfall pricing and checks inventory ownership + watchlist status.
7. `add_to_watchlist(card_name, target_price, set_code, finish, tag)`: Adds or updates a card on the user's surveillance watchlist.
8. `check_card_deck_fit(card_name)`: Evaluates whether a card is already in the user's Commander decks, recommended in saved upgrade plans, or owned in inventory.

## Connecting via Google Cloud Run (Gemini Mobile App & Connected Apps)

1. Deploy Chimaera to Google Cloud Run (using the project's `Dockerfile`).
2. Visit `https://<your-cloud-run-service>.run.app/gemini-hub` and copy your **Authenticated MCP URL**:
   ```text
   https://<your-cloud-run-service>.run.app/mcp?token=<YOUR_API_TOKEN>
   ```
3. In `gemini.google.com` -> **Settings** -> **Connected Apps** -> **Custom apps** -> **Add a custom app**, enter the URL and name it `Chimera`.
4. In the **Gemini Mobile App**, mention `@Chimera` in any prompt.

## Local CLI / Stdio Invocation

To run the MCP server locally over stdio:
```bash
python chimera_mcp.py --stdio
```
