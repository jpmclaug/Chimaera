"""
Migration & Repair Script: Cleans mojibake and resolves Scryfall metadata for accented cards across all decks.
"""

import json
import logging
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, db
from models import DeckAnalysis, UserInventoryCard, WatchlistItem
from providers.scryfall import ScryfallProvider
from deck_analyzer import DeckAnalyzer
from card_utils import fix_mojibake, strip_accents, normalize_card_name, get_card_match_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_accent_mojibake")


def repair_decks():
    logger.info("Starting deck mojibake and accent metadata repair...")
    scryfall = ScryfallProvider()
    analyzer = DeckAnalyzer()

    with app.app_context():
        decks = DeckAnalysis.query.all()
        logger.info(f"Scanning {len(decks)} decks...")

        for d in decks:
            modified = False
            cards = d.get_parsed_cards()
            cards_modified = False

            # 1. Clean deck name & commander name
            fixed_deck_name = fix_mojibake(d.deck_name)
            if fixed_deck_name != d.deck_name:
                logger.info(f"Deck {d.id}: Renaming deck '{d.deck_name}' -> '{fixed_deck_name}'")
                d.deck_name = fixed_deck_name
                modified = True

            if d.commander_name:
                fixed_cmdr = fix_mojibake(d.commander_name)
                if fixed_cmdr != d.commander_name:
                    logger.info(f"Deck {d.id}: Renaming commander '{d.commander_name}' -> '{fixed_cmdr}'")
                    d.commander_name = fixed_cmdr
                    modified = True

            # 2. Check cards for mojibake or missing Scryfall metadata
            names_needing_enrichment = []
            for c in cards:
                orig_name = c.get("name", "")
                fixed_name = fix_mojibake(orig_name)
                if fixed_name != orig_name:
                    logger.info(f"Deck {d.id}: Card mojibake fixed: '{orig_name}' -> '{fixed_name}'")
                    c["name"] = fixed_name
                    cards_modified = True

                # Check if card needs enrichment
                type_line = c.get("type_line", "")
                mana_cost = c.get("mana_cost", "")
                if type_line in ("Unknown", "", None) or not mana_cost:
                    names_needing_enrichment.append(c["name"])

            # 3. Batch enrich cards missing metadata
            if names_needing_enrichment:
                logger.info(f"Deck {d.id} ({d.deck_name}): Re-enriching {len(names_needing_enrichment)} cards from Scryfall: {names_needing_enrichment}")
                found_map, not_found = scryfall.get_cards_collection(names_needing_enrichment, fallback_named=True)

                for c in cards:
                    c_name = c.get("name", "")
                    meta = found_map.get(c_name.lower())
                    if not meta:
                        for k in get_card_match_keys(c_name):
                            if k in found_map:
                                meta = found_map[k]
                                break

                    if meta:
                        c["type_line"] = meta.get("type_line") or c.get("type_line")
                        c["mana_cost"] = meta.get("mana_cost") or c.get("mana_cost")
                        if meta.get("cmc") is not None:
                            c["cmc"] = meta.get("cmc")
                        if meta.get("colors"):
                            c["colors"] = meta.get("colors")
                        if meta.get("color_identity"):
                            c["color_identity"] = meta.get("color_identity")
                        if meta.get("oracle_text"):
                            c["oracle_text"] = meta.get("oracle_text")
                        if meta.get("image_uri") and not c.get("image_uri"):
                            c["image_uri"] = meta.get("image_uri")
                        if meta.get("small_image_uri") and not c.get("small_image_uri"):
                            c["small_image_uri"] = meta.get("small_image_uri")
                        if meta.get("art_crop_uri") and not c.get("art_crop_uri"):
                            c["art_crop_uri"] = meta.get("art_crop_uri")
                        if meta.get("rarity") and not c.get("rarity"):
                            c["rarity"] = meta.get("rarity")
                        if meta.get("tcgplayer_url") and not c.get("tcgplayer_url"):
                            c["tcgplayer_url"] = meta.get("tcgplayer_url")
                        if meta.get("card_faces") and not c.get("card_faces"):
                            c["card_faces"] = meta.get("card_faces")
                        if meta.get("keywords") and not c.get("keywords"):
                            c["keywords"] = meta.get("keywords")
                        if c.get("price_usd") is None and meta.get("prices", {}).get("usd"):
                            try:
                                c["price_usd"] = float(meta["prices"]["usd"])
                            except Exception:
                                pass
                        cards_modified = True

            # 4. Save and recompute stats if cards were modified
            if cards_modified:
                cmdrs = [c.strip() for c in (d.commander_name or "").split(",") if c.strip()]
                try:
                    telemetry = analyzer.analyze({"cards": cards, "deck_name": d.deck_name, "commander": cmdrs})
                    computed_stats = telemetry.get("stats", {})
                    d.stats_json = json.dumps(computed_stats)
                    if computed_stats.get("total_value"):
                        d.total_value = float(computed_stats["total_value"])
                    if computed_stats.get("nonland_amv"):
                        d.avg_cmc = float(computed_stats["nonland_amv"])
                    if computed_stats.get("color_identity"):
                        d.color_identity = ",".join(computed_stats["color_identity"])
                except Exception as e:
                    logger.error(f"Error recomputing stats for deck {d.id}: {e}")

                d.cards_data = json.dumps(cards)
                modified = True

            if modified:
                db.session.add(d)
                logger.info(f"Deck {d.id} ({d.deck_name}) updated.")

        # 5. Check UserInventoryCard and WatchlistItem
        for ic in UserInventoryCard.query.all():
            fixed_name = fix_mojibake(ic.name)
            if fixed_name != ic.name:
                logger.info(f"UserInventoryCard {ic.id}: '{ic.name}' -> '{fixed_name}'")
                ic.name = fixed_name
                db.session.add(ic)

        for w in WatchlistItem.query.all():
            fixed_name = fix_mojibake(w.name)
            if fixed_name != w.name:
                logger.info(f"WatchlistItem {w.id}: '{w.name}' -> '{fixed_name}'")
                w.name = fixed_name
                db.session.add(w)

        db.session.commit()
        logger.info("Mojibake and accent metadata repair completed successfully.")


if __name__ == "__main__":
    repair_decks()
