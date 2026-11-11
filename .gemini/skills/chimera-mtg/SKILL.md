---
name: chimera-mtg
description: Query and manage the Chimaera Magic: The Gathering database (card inventory, surveillance watchlist, active retail deals, and Commander deck analyses) via the Chimaera Model Context Protocol (MCP) server locally or on Google Cloud Run.
---

# Chimaera MTG Database & MCP Skill

Use this skill when the user asks questions about their Magic: The Gathering collection inventory, tracked watchlist singles, active price deals (TCGplayer, Mighty Meeple, eBay, MicroCenter, Best Buy), or saved Commander deck analyses in **Chimaera**.

## Available MCP Tools

The Chimaera MCP engine ([chimera_mcp.py](file:///c:/Users/jpmcl/Python%20Development/Chimera/chimera_mcp.py)) exposes 13 tools with full bulk upload and batch input support:

1. `get_inventory_summary(top_n=15)`: Returns total card quantity, unique printings, total USD market value, foil breakdown, binder breakdown, color identity distribution, and top valuable cards.
2. `check_inventory_cards(cards, card_names, card_name, text, csv_content, binder_name, include_details, match_mode)`: Fast batch or single ownership verification across inventory.
3. `search_inventory(query, set_code, foil, condition, binder_name, type_line, color_identity, min_price, limit)`: Searches owned cards in `UserInventoryCard`. Automatically routes bulk card inputs to `check_inventory_cards`.
4. `upload_inventory(cards, card_names, card_name, text, csv_content, gdrive_url, mode="merge", binder_name, condition, foil)`: Bulk uploads or imports cards into collection inventory from lists, decklists, ManaBox CSV, or Google Drive link.
5. `get_watchlist(query, cards, card_names, card_name, text, tag, tags, untagged_only, deals_only, in_stock_only, mm_in_stock_only, in_binder, color, card_type, finish, min_price, max_price, sort_by, group_by, include_vendor_details, limit=500)`: Lists monitored cards on the user's `WatchlistItem` registry with baseline market and statistical vendor median prices, individual vendor quotes (TCGplayer, Mighty Meeple, eBay), deal statuses, inventory ownership, portfolio summary metrics, or sort/filter/group by price, tag, color, or type. Up to 1000 items (default 500) to return full watchlists. Aliases: `search_watchlist`, `query_watchlist`, `list_watchlist`, `get_watchlist_summary`.
6. `get_active_deals(category="all")`: Returns active deals across single-card watchlists, MicroCenter Charlotte (`MicrocenterItem`), and Best Buy (`BestBuyItem`).
7. `get_deck_analyses(deck_id, deck_ids, query, queries, include_cards)`: Lists saved Commander decks or retrieves full AI tactical breakdowns, card ratings, stats, and upgrades for single or multiple decks.
8. `get_deck_cards(query, queries, deck_id, deck_ids, group_by_type)`: Returns all cards in one or multiple Commander decks categorized by card type and formatted as clean text decklists.
9. `upload_decks(urls, url, decks, text, csv_content, deck_name, commander_name, is_pauper)`: Bulk imports or saves Commander decks into the Vault from ManaBox/Moxfield/Archidekt/Scryfall URLs, text decklists, or CSV exports.
10. `search_card_prices(cards, card_names, card_name, text, csv_content, set_code)`: Queries live Scryfall pricing for a single card or bulk list of cards, cross-referencing inventory ownership and watchlist status.
11. `add_to_watchlist(cards, card_names, card_name, text, csv_content, target_price, target_strategy, set_code, finish, tag)`: Bulk adds or updates cards on the surveillance watchlist from card arrays, decklists, or CSV.
12. `reconcile_watchlist(text, cards, card_names, card_name, card_ids, add_to_inventory, binder_name, condition)`: Bulk removes acquired cards from watchlist using TCGplayer order text, card lists, or IDs, with optional collection inventory ingestion.
13. `check_card_deck_fit(cards, card_names, card_name, text, csv_content, deck_id, query)`: Evaluates how one or multiple candidate cards fit across the user's saved Commander decks, checking ownership, current inclusion, and upgrade recommendations.

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
