"""Clients for Scryfall, Commander Spellbook, EDHREC, and Archidekt APIs with local disk caching."""

from __future__ import annotations

import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .card_db import (
    EXTRA_TURN_CARDS,
    FINISHERS,
    GAME_CHANGERS,
    KNOWN_COMBOS,
    MASS_LAND_DENIAL,
    STRONG_STAPLES,
)

USER_AGENT = "TimewalkedAnalyzerCLI/1.0 (https://timewalked.com; contact@example.com)"
CACHE_DIR = pathlib.Path.home() / ".cache" / "timewalked_analyzer"


def slugify_commander(name: str) -> str:
    """Convert a commander name (e.g. \"Atraxa, Praetors' Voice\") into an EDHREC slug."""
    front_face = name.split("//")[0].strip().lower()
    cleaned = re.sub(r"['’,]", "", front_face)
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return slug


def parse_decklist_text(raw_text: str) -> tuple[list[str], dict[str, int]]:
    """Parse raw MTGO / Arena / Moxfield / Archidekt text into (commanders, mainboard_counts)."""
    commanders: list[str] = []
    mainboard: dict[str, int] = {}
    in_commander_section = False
    in_sideboard = False

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            header = line.lstrip("#/ ").lower()
            if header.startswith("commander"):
                in_commander_section = True
                in_sideboard = False
            elif header.startswith(("deck", "mainboard", "main", "creatures", "lands", "artifacts", "instants", "sorceries", "enchantments", "planeswalkers")):
                in_commander_section = False
                in_sideboard = False
            elif header.startswith(("sideboard", "maybeboard", "considering")):
                in_sideboard = True
            continue

        low = line.lower()
        if low in ("commander", "commander:", "commanders", "commanders:"):
            in_commander_section = True
            in_sideboard = False
            continue
        if low in ("deck", "deck:", "mainboard", "mainboard:"):
            in_commander_section = False
            in_sideboard = False
            continue
        if low in ("sideboard", "sideboard:", "maybeboard", "considering"):
            in_sideboard = True
            continue
        if in_sideboard:
            continue

        # Detect inline tags like *CMDR* or [Commander{top}]
        is_cmdr_inline = bool(
            re.search(r"\*cmdr\*|\[commander.*?\]|\(commander\)", line, flags=re.IGNORECASE)
        )
        # Strip inline set codes, collector numbers, foil markers, and category brackets
        cleaned = re.sub(r"\s*\([A-Za-z0-9]{2,6}\)\s*[A-Za-z0-9\-★]*.*$", "", line)
        cleaned = re.sub(r"\s*\[.*?\]", "", cleaned)
        cleaned = re.sub(r"\s*\*.*?\*", "", cleaned)

        match = re.match(r"^(?:(\d+)x?\s+)?(.+?)$", cleaned.strip())
        if not match:
            continue
        qty = int(match.group(1) or 1)
        card_name = match.group(2).strip()
        if not card_name:
            continue

        if in_commander_section or is_cmdr_inline:
            if card_name not in commanders:
                commanders.append(card_name)
        else:
            mainboard[card_name] = mainboard.get(card_name, 0) + qty

    return commanders, mainboard


class HttpJsonClient:
    """Lightweight JSON HTTP client with local file caching."""

    def __init__(self, cache_dir: pathlib.Path = CACHE_DIR, offline: bool = False) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def request_json(
        self,
        url: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: float = 12.0,
    ) -> dict[str, Any]:
        if self.offline:
            raise urllib.error.URLError("Offline mode enabled")

        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if data_bytes is not None:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


class ScryfallClient:
    """Fetches and caches card metadata from Scryfall's /cards/collection endpoint."""

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()
        self.cache_file = self.http.cache_dir / "scryfall_cards.json"
        self._cache: dict[str, dict[str, Any]] = self._load_cache()

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_cache(self) -> None:
        try:
            self.cache_file.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        except OSError:
            pass

    def seed_cards(self, pre_resolved: list[dict[str, Any]]) -> None:
        """Seed in-memory cache from pre-resolved card dicts (e.g. Chimaera DeckAnalysis cards_data)."""
        for card in pre_resolved or []:
            c_name = (card.get("name") or "").strip()
            if not c_name:
                continue
            key = c_name.lower()
            existing = self._cache.get(key)
            if existing and existing.get("oracle_text"):
                if not existing.get("image_uri") and card.get("image_uri"):
                    existing["image_uri"] = card.get("image_uri")
                if existing.get("price_usd") is None and card.get("price_usd") is not None:
                    existing["price_usd"] = card.get("price_usd")
                continue
            slim = {
                "name": c_name,
                "cmc": float(card.get("cmc", 0.0) or 0.0),
                "type_line": card.get("type_line") or "",
                "oracle_text": card.get("oracle_text") or "",
                "mana_cost": card.get("mana_cost") or "",
                "color_identity": card.get("color_identity") or [],
                "game_changer": bool(card.get("game_changer", False) or c_name in GAME_CHANGERS),
                "edhrec_rank": card.get("edhrec_rank"),
                "legalities": card.get("legalities") or {"commander": "legal"},
                "image_uri": card.get("image_uri") or "",
                "price_usd": card.get("price_usd"),
            }
            if slim["oracle_text"] or slim["type_line"]:
                self._cache[key] = slim
                if "//" in c_name:
                    front = c_name.split("//")[0].strip().lower()
                    self._cache[front] = slim

    def resolve_cards(self, card_names: list[str]) -> dict[str, dict[str, Any]]:
        """Resolve a list of card names into Scryfall card objects (with offline heuristic fallback)."""
        unique_names = list(dict.fromkeys(card_names))
        missing = [
            n
            for n in unique_names
            if n.lower() not in self._cache or not self._cache[n.lower()].get("type_line")
        ]

        if missing and not self.http.offline:
            for i in range(0, len(missing), 75):
                batch = missing[i : i + 75]
                payload = {"identifiers": [{"name": name} for name in batch]}
                try:
                    resp = self.http.request_json(
                        "https://api.scryfall.com/cards/collection",
                        method="POST",
                        payload=payload,
                    )
                    for card in resp.get("data", []):
                        img_uris = card.get("image_uris") or {}
                        if not img_uris and card.get("card_faces"):
                            img_uris = card["card_faces"][0].get("image_uris") or {}
                        prices = card.get("prices") or {}
                        price_usd = None
                        if prices.get("usd"):
                            try:
                                price_usd = float(prices["usd"])
                            except (ValueError, TypeError):
                                price_usd = None
                        slim = {
                            "name": card.get("name"),
                            "cmc": card.get("cmc", 0.0),
                            "type_line": card.get("type_line", ""),
                            "oracle_text": card.get("oracle_text")
                            or " // ".join(
                                face.get("oracle_text", "")
                                for face in card.get("card_faces", [])
                            ),
                            "mana_cost": card.get("mana_cost", "")
                            or (
                                card.get("card_faces", [{}])[0].get("mana_cost", "")
                                if card.get("card_faces")
                                else ""
                            ),
                            "color_identity": card.get("color_identity", []),
                            "game_changer": bool(card.get("game_changer", False)),
                            "edhrec_rank": card.get("edhrec_rank"),
                            "legalities": card.get("legalities", {}),
                            "image_uri": img_uris.get("normal") or img_uris.get("large") or "",
                            "price_usd": price_usd,
                        }
                        self._cache[card["name"].lower()] = slim
                        if "//" in card["name"]:
                            front = card["name"].split("//")[0].strip().lower()
                            self._cache[front] = slim
                    if i + 75 < len(missing):
                        time.sleep(0.1)  # Respect Scryfall 10 req/sec guideline
                except Exception:
                    break
            self._save_cache()

        results: dict[str, dict[str, Any]] = {}
        for name in unique_names:
            cached = self._cache.get(name.lower())
            if cached:
                results[name] = cached
            else:
                results[name] = self._synthesize_offline_card(name)
        return results

    @staticmethod
    def _synthesize_offline_card(name: str) -> dict[str, Any]:
        """Fallback card representation when running offline or before cache population."""
        basic_lands = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes", "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest"}
        is_land = (
            name in basic_lands
            or any(
                kw in name
                for kw in (
                    "Land",
                    "Tomb",
                    "Cradle",
                    "Sanctum",
                    "Chasm",
                    "Tabernacle",
                    "Field of the Dead",
                    "Command Tower",
                    "Exotic Orchard",
                    "Boseiju",
                    "Otawara",
                    "Urza's Saga",
                    "Pool",
                    "Fountain",
                    "Crypt",
                    "Vents",
                    "Ground",
                    "Shrine",
                    "Garden",
                    "Fortress",
                    "Catacombs",
                    "Mesa",
                    "Tarn",
                    "Delta",
                    "Mire",
                    "Foothills",
                    "Strand",
                    "Heath",
                    "Rainforest",
                    "Flats",
                    " Catacomb",
                )
            )
        )
        is_gc = name in GAME_CHANGERS
        oracle_parts: list[str] = []
        type_line = "Land" if is_land else "Spell"
        cmc = 0.0 if is_land else 3.0

        # Populate accurate CMC and Oracle cues for common staples when offline
        if name in {"Chrome Mox", "Mox Diamond", "Mox Opal", "Mox Amber", "Lotus Petal", "Mana Crypt", "Jeweled Lotus", "Lion's Eye Diamond"}:
            type_line = "Artifact"
            cmc = 0.0
            oracle_parts.append("{T}: Add {C}.")
        elif name in {"Sol Ring", "Mana Vault", "Sensei's Divining Top"}:
            type_line = "Artifact"
            cmc = 1.0
            if name != "Sensei's Divining Top":
                oracle_parts.append("{T}: Add {C}{C}.")
        elif name in {"Arcane Signet", "Fellwar Stone", "Mind Stone", "Thought Vessel", "Grim Monolith", "Talisman of Dominance", "Talisman of Progress", "Talisman of Creativity", "Talisman of Indulgence", "Talisman of Conviction"}:
            type_line = "Artifact"
            cmc = 2.0
            oracle_parts.append("{T}: Add one mana of any color.")
        elif name in {"Llanowar Elves", "Elvish Mystic", "Fyndhorn Elves", "Birds of Paradise", "Delighted Halfling", "Avacyn's Pilgrim", "Ignoble Hierarch", "Noble Hierarch"}:
            type_line = "Creature — Elf Druid"
            cmc = 1.0
            oracle_parts.append("{T}: Add {G}.")
        elif name in {"Nature's Lore", "Three Visits", "Farseek", "Rampant Growth", "Sakura-Tribe Elder"}:
            cmc = 2.0
            oracle_parts.append("Search your library for a Forest or land card, put it onto the battlefield.")
        elif name in {"Cultivate", "Kodama's Reach"}:
            cmc = 3.0
            oracle_parts.append("Search your library for up to two basic land cards, put one onto the battlefield.")
        elif name in {"Vampiric Tutor", "Imperial Seal", "Mystical Tutor", "Enlightened Tutor", "Worldly Tutor", "Gamble", "Entomb"}:
            cmc = 1.0
            oracle_parts.append("Search your library for a card.")
        elif name in {"Demonic Tutor", "Diabolic Intent", "Finale of Devastation", "Sylvan Tutor", "Demonic Consultation", "Tainted Pact"}:
            cmc = 2.0
            oracle_parts.append("Search your library for a card.")
        elif name in {"Rhystic Study", "Mystic Remora", "Esper Sentinel", "Sylvan Library", "Necropotence", "The One Ring", "Consecrated Sphinx", "Brainstorm", "Ponder", "Night's Whisper", "Sign in Blood", "Beast Whisperer", "Guardian Project"}:
            cmc = 1.0 if name in {"Mystic Remora", "Esper Sentinel", "Brainstorm", "Ponder"} else 3.0
            oracle_parts.append("Draw a card.")
        elif name in {"Swords to Plowshares", "Path to Exile", "Swan Song", "Mental Misstep", "Flusterstorm", "Silence", "Red Elemental Blast", "Pyroblast", "Nature's Claim", "An Offer You Can't Refuse", "Pongify", "Rapid Hybridization"}:
            cmc = 1.0
            oracle_parts.append("Counter target spell or exile target creature.")
        elif name in {"Counterspell", "Mana Drain", "Cyclonic Rift", "Arcane Denial", "Negate", "Assassin's Trophy", "Abrupt Decay", "Beast Within", "Generous Gift", "Chaos Warp", "Deadly Rollick", "Deflecting Swat", "Fierce Guardianship", "Force of Negation", "Force of Will", "Pact of Negation"}:
            cmc = 2.0
            oracle_parts.append("Counter target spell or destroy target permanent.")
        elif name in FINISHERS:
            cmc = 7.0 if name != "Thassa's Oracle" else 2.0
            oracle_parts.append("You win the game.")
        elif name in EXTRA_TURN_CARDS:
            cmc = 5.0
            oracle_parts.append("Take an extra turn after this one.")
        elif name in MASS_LAND_DENIAL:
            cmc = 4.0
            oracle_parts.append("Destroy all lands.")

        return {
            "name": name,
            "cmc": cmc,
            "type_line": type_line,
            "oracle_text": " ".join(oracle_parts),
            "mana_cost": "",
            "color_identity": [],
            "game_changer": is_gc,
            "edhrec_rank": 100 if (is_gc or name in STRONG_STAPLES) else 2500,
            "legalities": {"commander": "legal"},
        }


class SpellbookClient:
    """Queries Commander Spellbook for combos & official combo bracket tags."""

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    def find_combos(self, commanders: list[str], mainboard: list[str]) -> dict[str, Any]:
        """Return included combos, near-combos, and Commander Spellbook's bracket tag."""
        if not self.http.offline:
            payload = {
                "commanders": [{"card": c, "quantity": 1} for c in commanders],
                "main": [{"card": m, "quantity": 1} for m in mainboard],
            }
            try:
                resp = self.http.request_json(
                    "https://backend.commanderspellbook.com/find-my-combos",
                    method="POST",
                    payload=payload,
                    timeout=15.0,
                )
                results = resp.get("results", {})
                included_raw = results.get("included", [])
                included = []
                for variant in included_raw:
                    cards = [u["card"]["name"] for u in variant.get("uses", []) if "card" in u]
                    produces = [
                        p["feature"]["name"]
                        for p in variant.get("produces", [])
                        if "feature" in p
                    ]
                    included.append(
                        {
                            "id": str(variant.get("id", "")),
                            "cards": cards,
                            "produces": produces,
                            "bracket_tag": str(variant.get("bracketTag", "3")),
                            "mana_needed": variant.get("manaNeeded", ""),
                        }
                    )
                return {
                    "source": "commander_spellbook_api",
                    "bracket_tag": results.get("bracketTag"),
                    "included": included,
                    "almost_included_count": len(results.get("almostIncluded", [])),
                }
            except Exception:
                pass

        # Fallback: local deterministic combo detection
        all_cards_lower = {c.lower(): c for c in (commanders + mainboard)}
        included_local = []
        almost_local = 0
        max_combo_bracket = 1

        for combo in KNOWN_COMBOS:
            present = [c for c in combo["cards"] if c.lower() in all_cards_lower]
            if len(present) == len(combo["cards"]):
                included_local.append(combo)
                b_tag = int(combo["bracket_tag"]) if str(combo["bracket_tag"]).isdigit() else 4
                max_combo_bracket = max(max_combo_bracket, b_tag)
            elif len(present) == len(combo["cards"]) - 1 and len(combo["cards"]) >= 2:
                almost_local += 1

        return {
            "source": "local_combo_db",
            "bracket_tag": str(max_combo_bracket) if included_local else None,
            "included": included_local,
            "almost_included_count": almost_local,
        }


class EdhrecClient:
    """Queries EDHREC's JSON endpoints for commander stats and average 100-card decklists."""

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    def get_commander_profile(self, commander_name: str) -> dict[str, Any]:
        slug = slugify_commander(commander_name)
        meta_url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
        avg_url = f"https://json.edhrec.com/pages/average-decks/{slug}.json"

        meta = self.http.request_json(meta_url)
        avg_data = self.http.request_json(avg_url)

        container = meta.get("container", {}).get("json_dict", {})
        cardlists = container.get("cardlists", [])
        top_cards: list[dict[str, Any]] = []
        for section in cardlists:
            header = section.get("header", "")
            for view in section.get("cardviews", [])[:8]:
                num_decks = view.get("num_decks", 0)
                potential = view.get("potential_decks", 1) or 1
                top_cards.append(
                    {
                        "name": view.get("name"),
                        "category": header,
                        "inclusion_rate": round((num_decks / potential) * 100, 1),
                        "synergy": view.get("synergy", 0.0),
                    }
                )

        # Parse average decklist from EDHREC's archidekt array or raw description
        avg_cards: dict[str, int] = {}
        for entry in avg_data.get("archidekt", []):
            c_name = entry.get("c")
            qty = int(entry.get("q", 1))
            if c_name and c_name.lower() != commander_name.lower():
                avg_cards[c_name] = avg_cards.get(c_name, 0) + qty

        return {
            "commander": container.get("card", {}).get("name", commander_name),
            "slug": slug,
            "total_decks": meta.get("num_decks_avg", 0),
            "top_cards": top_cards,
            "average_deck_mainboard": avg_cards,
        }


class ArchidektClient:
    """Fetches public/unlisted decks from Archidekt by URL or deck ID."""

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    @staticmethod
    def extract_deck_id(url_or_id: str) -> str | None:
        if url_or_id.isdigit():
            return url_or_id
        match = re.search(r"archidekt\.com/decks/(\d+)", url_or_id)
        return match.group(1) if match else None

    def fetch_deck(self, url_or_id: str) -> tuple[str, list[str], dict[str, int]]:
        deck_id = self.extract_deck_id(url_or_id)
        if not deck_id:
            raise ValueError(f"Could not extract Archidekt deck ID from: {url_or_id}")

        api_url = f"https://archidekt.com/api/decks/{deck_id}/"
        data = self.http.request_json(api_url)
        deck_name = data.get("name", f"Archidekt Deck #{deck_id}")
        commanders: list[str] = []
        mainboard: dict[str, int] = {}

        for item in data.get("cards", []):
            categories = [c.lower() for c in (item.get("categories") or [])]
            if "maybeboard" in categories or "sideboard" in categories:
                continue
            card_name = (
                item.get("card", {}).get("oracleCard", {}).get("name")
                or item.get("card", {}).get("name")
            )
            if not card_name:
                continue
            qty = int(item.get("quantity", 1))
            if "commander" in categories:
                commanders.append(card_name)
            else:
                mainboard[card_name] = mainboard.get(card_name, 0) + qty

        return deck_name, commanders, mainboard
