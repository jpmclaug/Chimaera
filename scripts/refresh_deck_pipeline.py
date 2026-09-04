"""
Deck Telemetry & AI Analysis Pipeline Refresh Script.
Iterates over all stored DeckAnalysis records in the database:
1. Re-computes complete 61-key telemetry metrics via DeckAnalyzer.
2. Backfills missing Gemini AI strategic analyses for unanalyzed decks (e.g. Deck 13).
3. Persists refreshed stats_json, analysis_json, and metadata to PostgreSQL.
"""

import os
import sys
import json
import logging
from datetime import datetime

# Set up project path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app import create_app
from models import db, DeckAnalysis, SystemSetting, utc_now
from deck_analyzer import DeckAnalyzer
from gemini_analyzer import GeminiAnalyzer, GeminiAnalysisError
from providers.scryfall import ScryfallProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("refresh_deck_pipeline")


def refresh_all_decks(force_ai: bool = False, model_override: str | None = None):
    app = create_app()
    with app.app_context():
        decks = DeckAnalysis.query.order_by(DeckAnalysis.id.asc()).all()
        logger.info(f"Loaded {len(decks)} saved Commander decks from database.")

        analyzer = DeckAnalyzer()
        scryfall = ScryfallProvider()

        # Check Gemini API Key
        db_key = SystemSetting.get_val("gemini_api_key")
        effective_key = (
            app.config.get("GEMINI_API_KEY")
            or os.getenv("GEMINI_API_KEY", "").strip()
            or (db_key.strip() if db_key else "")
        )
        default_model = (
            model_override
            or SystemSetting.get_val("gemini_default_model")
            or app.config.get("GEMINI_DEFAULT_MODEL", "gemini-3.6-flash")
        )
        if default_model == "gemini-3.5-flash-lite":
            default_model = "gemini-3.6-flash"

        logger.info(f"Gemini API Key present: {bool(effective_key)} | Effective Model: {default_model}")

        refreshed_stats_count = 0
        refreshed_ai_count = 0

        print("\n" + "=" * 90)
        print(f"{'ID':<4} | {'DECK NAME':<35} | {'TELEMETRY':<14} | {'AI ANALYSIS':<16} | {'STATUS':<10}")
        print("=" * 90)

        for deck in decks:
            cards = deck.get_parsed_cards()
            if not cards:
                logger.warning(f"Deck #{deck.id} '{deck.deck_name}' has no parsed cards. Skipping.")
                continue

            cmdrs = [c.strip() for c in (deck.commander_name or "").split(",") if c.strip()]
            
            # 1. Evaluate Telemetry
            existing_stats = {}
            if deck.stats_json:
                try:
                    existing_stats = json.loads(deck.stats_json)
                except Exception:
                    existing_stats = {}

            needs_stats_recompute = (
                not existing_stats
                or len(existing_stats) < 50
                or "land_drop_probabilities" not in existing_stats
                or "mana_sinks" not in existing_stats
            )

            if needs_stats_recompute:
                logger.info(f"Re-computing full telemetry for Deck #{deck.id} '{deck.deck_name}'...")
                analyzed = analyzer.analyze({
                    "deck_name": deck.deck_name,
                    "commander": cmdrs,
                    "cards": cards,
                })
                new_stats = analyzed.get("stats", {})
                deck.stats_json = json.dumps(new_stats)
                deck.total_value = new_stats.get("total_value", deck.total_value)
                deck.avg_cmc = new_stats.get("avg_cmc", deck.avg_cmc)
                if not deck.archetype:
                    deck.archetype = new_stats.get("archetype")
                deck.updated_at = utc_now()
                refreshed_stats_count += 1
                stats_status = f"Updated ({len(new_stats)}k)"
            else:
                stats_status = f"Current ({len(existing_stats)}k)"

            # 2. Evaluate AI Analysis
            has_ai = deck.has_ai_analysis
            needs_ai = (not has_ai) or force_ai

            if needs_ai and effective_key:
                logger.info(f"Generating Gemini AI analysis for Deck #{deck.id} '{deck.deck_name}' with {default_model}...")
                try:
                    stats_for_ai = json.loads(deck.stats_json)
                    card_names = [c["name"] for c in cards]
                    scryfall_map, _ = scryfall.get_cards_collection(card_names)

                    payload = {
                        "deck_name": deck.deck_name,
                        "commander": cmdrs,
                        "cards": cards,
                        "total_cards": deck.total_cards,
                        "raw_text": deck.raw_decklist or "",
                        "stats": stats_for_ai,
                    }

                    ai = GeminiAnalyzer(api_key=effective_key, model=default_model)
                    analysis = ai.analyze_deck(
                        deck_data=payload,
                        scryfall_metadata=scryfall_map,
                    )

                    # Enrich ratings
                    if "card_ratings" in analysis and isinstance(analysis["card_ratings"], list):
                        for item in analysis["card_ratings"]:
                            c_name = item.get("card_name", "")
                            meta = scryfall_map.get(c_name.lower(), {})
                            item["image_uri"] = meta.get("image_uri") or meta.get("small_image_uri")
                            item["small_image_uri"] = meta.get("small_image_uri")
                            item["mana_cost"] = meta.get("mana_cost", "")
                            item["type_line"] = meta.get("type_line", "")
                            item["cmc"] = meta.get("cmc", 0)
                            item["price_usd"] = meta.get("prices", {}).get("usd")
                            item["tcgplayer_url"] = meta.get("tcgplayer_url")

                    # Enrich upgrades
                    if "upgrades" in analysis and isinstance(analysis["upgrades"], list):
                        upgrade_names = [u.get("card_in", "") for u in analysis["upgrades"] if u.get("card_in")]
                        upgrade_names += [u.get("card_out", "") for u in analysis["upgrades"] if u.get("card_out")]
                        extra_meta, _ = scryfall.get_cards_collection(upgrade_names)

                        for u in analysis["upgrades"]:
                            c_in = u.get("card_in", "")
                            c_out = u.get("card_out", "")
                            in_m = extra_meta.get(c_in.lower()) or scryfall_map.get(c_in.lower(), {})
                            out_m = extra_meta.get(c_out.lower()) or scryfall_map.get(c_out.lower(), {})
                            u["card_in_image"] = in_m.get("image_uri") or in_m.get("small_image_uri")
                            u["card_in_price"] = in_m.get("prices", {}).get("usd")
                            u["card_in_mana"] = in_m.get("mana_cost", "")
                            u["card_in_type"] = in_m.get("type_line", "")
                            u["card_in_tcg"] = in_m.get("tcgplayer_url")
                            u["card_out_image"] = out_m.get("image_uri") or out_m.get("small_image_uri")
                            u["card_out_price"] = out_m.get("prices", {}).get("usd")
                            u["card_out_mana"] = out_m.get("mana_cost", "")

                    power_level = analysis.get("estimated_power_level")
                    if power_level:
                        try:
                            power_level = float(power_level)
                        except Exception:
                            power_level = None

                    actual_model = analysis.get("_model_used") or ai.model or default_model
                    deck.analysis_json = json.dumps(analysis)
                    deck.model_used = actual_model
                    deck.power_level = power_level
                    deck.power_bracket = analysis.get("power_bracket")
                    deck.archetype = analysis.get("archetype") or deck.archetype
                    deck.updated_at = utc_now()
                    refreshed_ai_count += 1
                    ai_status = f"Generated ({actual_model})"
                except Exception as e:
                    logger.error(f"Failed to generate AI analysis for Deck #{deck.id}: {e}")
                    ai_status = "Failed (Error)"
            else:
                ai_status = f"Existing ({deck.model_used or 'AI'})" if has_ai else "Pending (No AI)"

            db.session.commit()
            print(f"{deck.id:<4} | {deck.deck_name[:35]:<35} | {stats_status:<14} | {ai_status:<16} | {deck.status:<10}")

        print("=" * 90)
        logger.info(f"Refresh completed: {refreshed_stats_count} decks telemetry backfilled, {refreshed_ai_count} AI analyses generated.")


if __name__ == "__main__":
    force = "--force-ai" in sys.argv
    cli_model = None
    for arg in sys.argv:
        if arg.startswith("--model="):
            cli_model = arg.split("=", 1)[1].strip()
    refresh_all_decks(force_ai=force, model_override=cli_model)
