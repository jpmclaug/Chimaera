"""
Secret Lair Commander Deck Value & Synergy Advisor for Chimaera MTG.
Scrapes Secret Lair preview announcements, resolves live Scryfall market valuations,
and leverages Google Gemini to determine tactical deck fit, upgrade recommendations,
suggested cuts, and ranked drop buying advice for the user's Commander fleet.
"""

import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

from providers.scryfall import ScryfallProvider
from card_utils import normalize_card_name, fix_mojibake, strip_accents
from gemini_analyzer import (
    GEMINI_API_BASE,
    DEFAULT_MODEL,
    MODEL_TIER_SEQUENCE,
    MODEL_FALLBACK_MAP,
    GeminiAnalysisError,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class SecretLairScraper:
    """Scrapes and parses Secret Lair preview announcements, articles, or raw text."""

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def fetch_announcement(self, url_or_text: str) -> dict:
        """
        Fetches an announcement by URL or directly accepts raw text.
        Returns a dict with title, banner_image, source_url, and cleaned text.
        """
        clean_input = url_or_text.strip()
        if clean_input.startswith("http://") or clean_input.startswith("https://"):
            try:
                resp = self.session.get(clean_input, timeout=15)
                resp.raise_for_status()
                html = resp.text
                soup = BeautifulSoup(html, "html.parser")

                # Extract title
                title = ""
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    title = og_title["content"].strip()
                elif soup.title and soup.title.string:
                    title = soup.title.string.strip()
                else:
                    h1 = soup.find("h1")
                    title = h1.get_text(strip=True) if h1 else "Secret Lair Superdrop"

                # Clean title
                title = re.sub(r"\s*\|\s*Magic:\s*The\s*Gathering.*$", "", title, flags=re.IGNORECASE).strip()

                # Extract banner image
                banner_image = ""
                og_img = soup.find("meta", property="og:image")
                if og_img and og_img.get("content"):
                    banner_image = og_img["content"].strip()

                # Extract body text
                article = (
                    soup.find("article")
                    or soup.find("div", class_=lambda c: c and "article" in c.lower())
                    or soup.body
                )
                text = article.get_text("\n", strip=True) if article else soup.get_text("\n", strip=True)

                return {
                    "source_url": clean_input,
                    "title": title or "Secret Lair Announcement",
                    "banner_image": banner_image,
                    "html": html,
                    "text": text,
                }
            except Exception as e:
                logger.error(f"Error fetching Secret Lair URL '{clean_input}': {e}")
                raise ValueError(f"Failed to fetch announcement from link: {str(e)}")
        else:
            # Raw text provided
            first_line = clean_input.splitlines()[0][:100] if clean_input else "Secret Lair Custom Preview"
            return {
                "source_url": "",
                "title": first_line.strip("#* "),
                "banner_image": "",
                "html": "",
                "text": clean_input,
            }

    def parse_drops(self, text: str, api_key: str | None = None) -> tuple[list[dict], list[dict]]:
        """
        Parses drops, cards, prices, and bundles from announcement text.
        Returns a tuple of (drops_list, bundles_list).
        Falls back to Gemini intelligent parsing if deterministic regex yields insufficient drops.
        """
        drops, bundles = self._parse_drops_deterministic(text)

        # If deterministic regex failed to find valid drops, fall back to Gemini
        if len(drops) == 0 and api_key:
            logger.info("Deterministic drop parser found 0 drops. Falling back to Gemini extraction...")
            drops, bundles = self._parse_drops_with_gemini(text, api_key)

        return drops, bundles

    @staticmethod
    def _parse_card_line(line: str) -> dict | None:
        """Parses a single line into canonical name, flavor alias, quantity, and notes."""
        clean = line.strip()
        if not clean:
            return None

        # Ignore obvious section titles/headers
        lower = clean.lower()
        if lower in ["contents", "contents:", "price", "price:", "foil", "non-foil", "usd", "release date"]:
            return None

        qty = 1
        m_qty = re.match(r"^(\d+)x\s+(.+)$", clean, re.IGNORECASE)
        if m_qty:
            qty = int(m_qty.group(1))
            clean = m_qty.group(2).strip()
        elif re.match(r"^[-•*]\s+(.+)$", clean):
            clean = re.sub(r"^[-•*]\s+", "", clean).strip()
        else:
            # If line doesn't start with 1x or a bullet, accept only if it has " as " or special card tag
            if " as " not in clean.lower() and not re.search(r"\((?:full art|borderless|textless|double sided)\)", clean, re.IGNORECASE):
                return None

        # Check for extra notes in parenthesis at the end (e.g. "(Full Art, Textless)")
        extra_tag = ""
        m_tag = re.search(r"\s*\(([^)]+)\)$", clean)
        if m_tag:
            extra_tag = m_tag.group(1).strip()
            clean = clean[:m_tag.start()].strip()

        # Check for alias: "Card Name as 'Flavor Alias'"
        flavor_name = ""
        m_alias = re.search(r"\s+as\s+[\"“']?(.*?)[\"”']?$", clean, re.IGNORECASE)
        if m_alias:
            flavor_name = m_alias.group(1).strip(' "\'“”')
            canonical_name = clean[:m_alias.start()].strip()
        else:
            canonical_name = clean.strip()

        # Clean up any residual symbols
        canonical_name = canonical_name.replace("®", "").replace("™", "").replace("’", "'").strip()
        flavor_name = flavor_name.replace("®", "").replace("™", "").replace("’", "'").strip()

        if not canonical_name or len(canonical_name) < 2:
            return None

        return {
            "canonical_name": canonical_name,
            "flavor_name": flavor_name,
            "quantity": qty,
            "extra_tag": extra_tag,
        }

    def _parse_drops_deterministic(self, text: str) -> tuple[list[dict], list[dict]]:
        """Extracts drops and bundles using structured regex patterns."""
        drops = []
        bundles = []

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        current_drop = None
        current_bundle = None
        mode = None  # "drop" or "bundle"

        drop_header_regex = re.compile(
            r"^(?:###?\s*)?(Secret Lair x [^\n]+|Secret Lair:\s*[^\n]+|Drop:\s*[^\n]+)",
            re.IGNORECASE,
        )
        bundle_header_regex = re.compile(
            r"^(?:###?\s*)?([^\n]+(?:Bundle|Everything|Superdrop All-in)[^\n]*)",
            re.IGNORECASE,
        )

        for line in lines:
            drop_match = drop_header_regex.match(line)
            if drop_match:
                drop_name = drop_match.group(1).strip().replace("®", "").replace("™", "").replace("’", "'").strip()
                # Skip article headers that are not individual drops
                if not any(kw in drop_name.lower() for kw in ["footer", "social", "where to find", "statement", "superdrop in the universe"]):
                    if current_drop and len(current_drop.get("cards", [])) >= 2:
                        drops.append(current_drop)
                    current_drop = {
                        "drop_name": drop_name,
                        "cards": [],
                        "price_nonfoil": 29.99,
                        "price_foil": 39.99,
                        "currency": "USD",
                    }
                    mode = "drop"
                    continue

            bundle_match = bundle_header_regex.match(line)
            if bundle_match and ("bundle" in line.lower() or "everything" in line.lower()):
                if current_drop and len(current_drop.get("cards", [])) >= 2:
                    drops.append(current_drop)
                    current_drop = None
                if current_bundle:
                    bundles.append(current_bundle)
                b_name = bundle_match.group(1).strip().replace("®", "").replace("™", "").replace("’", "'")
                current_bundle = {
                    "bundle_name": b_name,
                    "price_nonfoil": None,
                    "price_foil": None,
                    "contents_summary": "",
                }
                mode = "bundle"
                continue

            if mode == "drop" and current_drop:
                parsed_card = self._parse_card_line(line)
                if parsed_card:
                    current_drop["cards"].append(parsed_card)

                if "non-foil:" in line.lower() or "nonfoil:" in line.lower():
                    m = re.search(r"\$?([0-9]+\.[0-9]{2})", line)
                    if m:
                        try:
                            current_drop["price_nonfoil"] = float(m.group(1))
                        except Exception:
                            pass
                elif "foil:" in line.lower():
                    m = re.search(r"\$?([0-9]+\.[0-9]{2})", line)
                    if m:
                        try:
                            current_drop["price_foil"] = float(m.group(1))
                        except Exception:
                            pass

            if mode == "bundle" and current_bundle:
                if "non-foil:" in line.lower() or "nonfoil:" in line.lower():
                    m = re.search(r"\$?([0-9]+\.[0-9]{2})", line)
                    if m:
                        current_bundle["price_nonfoil"] = float(m.group(1))
                elif "foil:" in line.lower():
                    m = re.search(r"\$?([0-9]+\.[0-9]{2})", line)
                    if m:
                        current_bundle["price_foil"] = float(m.group(1))
                elif "$" in line:
                    m = re.search(r"\$?([0-9]+\.[0-9]{2})", line)
                    if m and current_bundle["price_nonfoil"] is None:
                        current_bundle["price_nonfoil"] = float(m.group(1))

        if current_drop and len(current_drop.get("cards", [])) >= 2:
            drops.append(current_drop)
        if current_bundle:
            bundles.append(current_bundle)

        # De-duplicate drops by name
        unique_drops = []
        seen_drop_names = set()
        for d in drops:
            key = d["drop_name"].lower()
            if key not in seen_drop_names and len(d["cards"]) >= 2:
                seen_drop_names.add(key)
                unique_drops.append(d)

        return unique_drops, bundles

    def _parse_drops_with_gemini(self, text: str, api_key: str) -> tuple[list[dict], list[dict]]:
        """Fallback LLM parser to extract drop JSON from irregular layouts."""
        url = f"{GEMINI_API_BASE}/{DEFAULT_MODEL}:generateContent?key={api_key}"
        prompt = f"""You are a specialized MTG Secret Lair data extractor.
Extract all Secret Lair drops, cards, prices, and bundles from this announcement text into strict JSON.

ANNOUNCEMENT TEXT:
{text[:12000]}

REQUIRED SCHEMA:
{{
  "drops": [
    {{
      "drop_name": "Exact Secret Lair Drop Title",
      "price_nonfoil": 29.99,
      "price_foil": 39.99,
      "cards": [
        {{
          "canonical_name": "Original Magic Card Name (e.g. Winota, Joiner of Forces)",
          "flavor_name": "Secret Lair reskin title if any (e.g. He-Man, Champion of Eternia)",
          "quantity": 1
        }}
      ]
    }}
  ],
  "bundles": [
    {{
      "bundle_name": "Bundle Title",
      "price_nonfoil": 119.99,
      "price_foil": 159.99
    }}
  ]
}}
Respond with JSON only. Do not wrap with markdown code fences.
"""
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
        }
        try:
            resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=20)
            if resp.status_code == 200:
                candidates = resp.json().get("candidates", [])
                if candidates:
                    raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
                    clean = re.sub(r"\s*```$", "", clean).strip()
                    parsed = json.loads(clean)
                    return parsed.get("drops", []), parsed.get("bundles", [])
        except Exception as e:
            logger.error(f"Gemini drop parsing fallback failed: {e}")

        return [], []


class SecretLairFinancialEvaluator:
    """Queries Scryfall to compute live singles market value and Expected Value (EV) per drop."""

    def __init__(self, scryfall_provider: ScryfallProvider | None = None):
        self.scryfall = scryfall_provider or ScryfallProvider()

    def enrich_drops_with_scryfall(self, drops: list[dict]) -> list[dict]:
        """
        Enriches each card in each drop with Scryfall metadata and calculates
        the drop's Expected Value (EV) vs its retail price.
        """
        if not drops:
            return []

        # Collect unique canonical card names
        all_card_names = []
        for d in drops:
            for c in d.get("cards", []):
                name = c.get("canonical_name", "").strip()
                if name and name not in all_card_names:
                    all_card_names.append(name)

        # Batch lookup cards via Scryfall
        scryfall_map, _ = self.scryfall.get_cards_collection(all_card_names)

        enriched_drops = []
        for drop in drops:
            enriched_cards = []
            total_nonfoil_val = 0.0
            total_foil_val = 0.0

            for c in drop.get("cards", []):
                canonical = c.get("canonical_name", "").strip()
                qty = c.get("quantity", 1)
                meta = scryfall_map.get(canonical.lower(), {})

                prices = meta.get("prices", {})
                p_usd = prices.get("usd")
                p_usd_foil = prices.get("usd_foil") or p_usd

                val_nonfoil = float(p_usd) if p_usd is not None else 0.0
                val_foil = float(p_usd_foil) if p_usd_foil is not None else val_nonfoil

                if val_nonfoil == 0.0:
                    try:
                        cheap = self.scryfall.get_cheapest_tcgplayer_price(canonical)
                        if cheap and cheap.get("price", 0) > 0:
                            val_nonfoil = float(cheap["price"])
                            val_foil = val_nonfoil
                    except Exception:
                        pass

                total_nonfoil_val += val_nonfoil * qty
                total_foil_val += val_foil * qty

                enriched_card = dict(c)
                enriched_card.update({
                    "scryfall_id": meta.get("id"),
                    "mana_cost": meta.get("mana_cost", ""),
                    "cmc": meta.get("cmc", 0.0),
                    "type_line": meta.get("type_line", ""),
                    "colors": meta.get("colors", []),
                    "color_identity": meta.get("color_identity", []),
                    "oracle_text": meta.get("oracle_text", ""),
                    "image_uri": meta.get("image_uri", ""),
                    "art_crop_uri": meta.get("art_crop_uri", ""),
                    "price_usd": round(val_nonfoil, 2) if val_nonfoil > 0 else None,
                    "price_usd_foil": round(val_foil, 2) if val_foil > 0 else None,
                    "tcgplayer_url": meta.get("tcgplayer_url", ""),
                })
                enriched_cards.append(enriched_card)

            price_nf = drop.get("price_nonfoil") or 29.99
            price_f = drop.get("price_foil") or 39.99

            net_ev_nf = round(total_nonfoil_val - price_nf, 2)
            ev_ratio_nf = round(total_nonfoil_val / price_nf, 2) if price_nf > 0 else 1.0
            ev_pct_nf = round(((total_nonfoil_val - price_nf) / price_nf) * 100, 1) if price_nf > 0 else 0.0

            # Determine EV Status
            if ev_ratio_nf >= 1.5:
                ev_status = "Exceptional EV"
            elif ev_ratio_nf >= 1.0:
                ev_status = "Positive EV"
            elif ev_ratio_nf >= 0.75:
                ev_status = "Fair / Near Par"
            else:
                ev_status = "Collector / Art Premium"

            enriched_drop = dict(drop)
            enriched_drop.update({
                "cards": enriched_cards,
                "singles_value_nonfoil": round(total_nonfoil_val, 2),
                "singles_value_foil": round(total_foil_val, 2),
                "net_ev_nonfoil": net_ev_nf,
                "ev_ratio_nonfoil": ev_ratio_nf,
                "ev_pct_nonfoil": ev_pct_nf,
                "ev_status": ev_status,
            })
            enriched_drops.append(enriched_drop)

        return enriched_drops


class SecretLairGeminiAdvisor:
    """Uses Google Gemini to perform tactical Commander fleet synergy evaluations."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        raw_model = model or os.getenv("GEMINI_DEFAULT_MODEL", DEFAULT_MODEL)
        self.model = MODEL_FALLBACK_MAP.get(raw_model, raw_model)

    def analyze_fleet_synergy(
        self,
        superdrop_title: str,
        drops: list[dict],
        bundles: list[dict],
        commander_decks: list[dict],
        custom_instructions: str = "",
    ) -> dict:
        """
        Sends structured Secret Lair drops and user's commander decks to Gemini.
        Returns tactical recommendation, deck fit matrix, cuts, and ranked best drops to buy.
        """
        if not self.api_key:
            raise GeminiAnalysisError(
                "Gemini API Key is required for Secret Lair Advisor. Please configure your key in settings."
            )

        if not drops:
            raise GeminiAnalysisError("No Secret Lair drops found to analyze.")

        if not commander_decks:
            raise GeminiAnalysisError("No Commander decks selected to analyze against.")

        # Format Drops Summary for Prompt
        drop_blocks = []
        for i, d in enumerate(drops, 1):
            d_name = d.get("drop_name", f"Drop #{i}")
            p_nf = d.get("price_nonfoil", 29.99)
            p_f = d.get("price_foil", 39.99)
            singles_val = d.get("singles_value_nonfoil", 0.0)
            ev_status = d.get("ev_status", "N/A")

            card_lines = []
            for c in d.get("cards", []):
                canon = c.get("canonical_name", "Card")
                flavor = f' as "{c["flavor_name"]}"' if c.get("flavor_name") else ""
                t_line = f" [{c.get('type_line', '')}]" if c.get("type_line") else ""
                mana = f" ({c.get('mana_cost', '')})" if c.get("mana_cost") else ""
                colors = f" CI:[{','.join(c.get('color_identity', []))}]"
                price = f" ~${c.get('price_usd')}" if c.get("price_usd") is not None else ""
                card_lines.append(f"    • {canon}{flavor}{mana}{t_line}{colors}{price}")

            drop_blocks.append(
                f"DROP #{i}: {d_name}\n"
                f"  Price: Non-foil ${p_nf} | Foil ${p_f} | TCGplayer Market Singles Total: ${singles_val:.2f} ({ev_status})\n"
                f"  Cards in Drop:\n" + "\n".join(card_lines)
            )

        drops_prompt_text = "\n\n".join(drop_blocks)

        # Format Commander Decks Summary for Prompt
        deck_blocks = []
        for deck in commander_decks:
            did = deck.get("id")
            dname = deck.get("deck_name", "Commander Deck")
            cmdrs = deck.get("commander_name", "Unspecified")
            colors = deck.get("color_identity", "")
            archetype = deck.get("archetype", "Commander Synergy")
            cards = deck.get("cards", [])
            existing_card_names = [c["name"] for c in cards]

            deck_blocks.append(
                f"DECK ID [{did}]: \"{dname}\"\n"
                f"  Commander(s): {cmdrs}\n"
                f"  Color Identity: [{colors}]\n"
                f"  Archetype: {archetype}\n"
                f"  Current Deck Card Names Sample: {', '.join(existing_card_names[:45])} ... ({len(existing_card_names)} cards total)"
            )

        decks_prompt_text = "\n\n".join(deck_blocks)

        # Format Bundles Summary
        bundle_text = ""
        if bundles:
            bundle_lines = []
            for b in bundles:
                bname = b.get("bundle_name", "Bundle")
                pnf = f"${b['price_nonfoil']}" if b.get("price_nonfoil") else "N/A"
                pf = f"${b['price_foil']}" if b.get("price_foil") else "N/A"
                bundle_lines.append(f"  • {bname}: Non-foil {pnf} | Foil {pf}")
            bundle_text = "AVAILABLE BUNDLES:\n" + "\n".join(bundle_lines)

        system_instruction = (
            "You are an elite Magic: The Gathering Commander (EDH) tactical strategist, financial value analyst, "
            "and deck optimization engine. You evaluate Secret Lair drops with mathematical and clinical precision. "
            "Rules:\n"
            "1. Commander Color Identity Rule: A card CANNOT be put into a Commander deck if the card's color identity has colors outside the commander's color identity.\n"
            "2. Reskinned Cards: Cards with flavor aliases (e.g., Winota as He-Man) are functionally identical to their canonical card name. Check whether the deck already runs this card.\n"
            "3. Output ONLY valid, strict JSON matching the exact required schema."
        )

        user_prompt = f"""Evaluate this Secret Lair Superdrop against the player's active Commander deck fleet.

SUPERDROP TITLE: {superdrop_title}

SECRET LAIR DROPS CONTENT & SINGLES VALUATIONS:
{drops_prompt_text}

{bundle_text}

PLAYER'S ACTIVE COMMANDER DECKS:
{decks_prompt_text}

{f"USER CUSTOM INSTRUCTIONS: {custom_instructions}" if custom_instructions else ""}

TASK REQUIREMENTS:
1. EXECUTIVE INTEL & BEST DROPS TO BUY:
   - Rank the drops from #1 (best purchase) to last based on a blend of Fleet Synergy (how many decks want the cards and how impactful they are) and Financial EV (market singles vs drop cost).
   - Assign each drop a Recommendation Tier: 'Must Buy (S-Tier)', 'High Value (A-Tier)', 'Situational / Niche (B-Tier)', or 'Skip (C-Tier)'.
   - Provide an overall composite score (1-100), fleet synergy score (1-100), and financial value score (1-100).
   - Provide a clear, actionable summary rationale of why to buy or skip each drop.
2. BUNDLE EVALUATION:
   - Compare buying the bundle vs buying individual high-priority drops. Determine if the bundle is worth it for this player.
3. DECK-BY-DECK FIT BREAKDOWN:
   - For EACH of the player's commander decks, list ALL cards from the drops that are legal and beneficial.
   - For each card, specify:
     * 'card_name': Canonical card name
     * 'flavor_name': Reskin/alias title if applicable
     * 'from_drop': Exact drop title
     * 'fit_verdict': 'Essential Upgrade', 'High Synergy', 'Alternative Win-Con', 'Art/Flavor Upgrade', or 'Redundant / Suboptimal'
     * 'role': e.g. 'Finisher / Win-Con', 'Synergy Engine', 'Ramp / Rocks', 'Spot Removal', 'Card Advantage', 'Protection', 'Commander'
     * 'synergy_rating': Score from 1.0 to 10.0 for this specific deck
     * 'is_already_in_deck': boolean (true if deck already runs the card)
     * 'rationale': Detailed tactical explanation citing the commander and synergies
     * 'suggested_cut': Specific card currently in that deck to cut for this upgrade (or 'None - Art Swap' if already in deck)
4. NEW COMMANDER OPPORTUNITIES:
   - Identify any legendary creatures in the drops that could lead brand new Commander decks (e.g. He-Man/Winota, Skeletor/Tinybones).

CRITICAL INSTRUCTION: Respond ONLY with a raw JSON object (no markdown surrounding code fences) strictly adhering to this schema:
{{
  "superdrop_title": "{superdrop_title}",
  "executive_summary": "High-level tactical briefing on this Superdrop for the player's fleet...",
  "best_drops_to_buy": [
    {{
      "rank": 1,
      "drop_name": "Drop Title",
      "recommendation_tier": "Must Buy (S-Tier)",
      "composite_score": 92,
      "fleet_synergy_score": 95,
      "financial_value_score": 88,
      "target_decks_count": 3,
      "top_fit_decks": ["Deck Name A", "Deck Name B"],
      "key_cards": ["Card 1", "Card 2"],
      "summary_rationale": "Why this drop is #1..."
    }}
  ],
  "bundle_analysis": {{
    "verdict": "Individual Drops Recommended",
    "recommended_option": "Buy Drop 1 and Drop 2 individually",
    "rationale": "Buying individual drops costs $59.98 vs $119.99 bundle, saving $60 while getting 100% of the cards that fit your fleet."
  }},
  "deck_breakdowns": [
    {{
      "deck_id": 1,
      "deck_name": "Deck Name",
      "commander_name": "Commander Name",
      "color_identity": ["R", "W"],
      "total_fitting_cards": 2,
      "applicable_cards": [
        {{
          "card_name": "Bruenor Battlehammer",
          "flavor_name": "Man-at-Arms, Master Tactician",
          "from_drop": "Drop Title",
          "fit_verdict": "Essential Upgrade",
          "role": "Synergy Engine",
          "synergy_rating": 9.7,
          "is_already_in_deck": false,
          "rationale": "Synergy rationale...",
          "suggested_cut": "Card to Cut"
        }}
      ]
    }}
  ],
  "new_commander_opportunities": [
    {{
      "card_name": "Winota, Joiner of Forces",
      "flavor_name": "He-Man, Champion of Eternia",
      "colors": ["R", "W"],
      "from_drop": "Drop Title",
      "archetype": "Boros Human/Non-Human Aggro",
      "rationale": "High-tier commander option..."
    }}
  ]
}}
"""

        # Dispatch through model sequence with automatic fallback
        clean_key = self.api_key.strip()
        models_to_try = [self.model] + [m for m in MODEL_TIER_SEQUENCE if m != self.model]

        attempt_logs = []
        for target_model in models_to_try:
            attempt_ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            url = f"{GEMINI_API_BASE}/{target_model}:generateContent?key={clean_key}"

            gen_config = {
                "temperature": 0.2,
                "maxOutputTokens": 8192,
            }
            if "gemini-3" in target_model:
                gen_config["thinkingConfig"] = {"thinkingLevel": "low"}

            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": system_instruction + "\n\n" + user_prompt}
                        ],
                    }
                ],
                "generationConfig": gen_config,
            }

            try:
                resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=90)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        attempt_logs.append(f"{target_model}: Empty candidates")
                        continue

                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        attempt_logs.append(f"{target_model}: Empty parts")
                        continue

                    raw_text = parts[0].get("text", "").strip()
                    parsed = self._clean_and_parse_json(raw_text)
                    parsed["_model_used"] = target_model
                    parsed["_analyzed_at"] = datetime.now(timezone.utc).isoformat()
                    return parsed
                else:
                    err_msg = resp.text[:150]
                    attempt_logs.append(f"{target_model} (HTTP {resp.status_code}): {err_msg}")
                    logger.warning(f"Model '{target_model}' failed: {err_msg}. Trying fallback...")
            except Exception as e:
                attempt_logs.append(f"{target_model}: {str(e)}")
                logger.warning(f"Model '{target_model}' exception: {e}. Trying fallback...")

        raise GeminiAnalysisError(
            f"All Gemini models failed for Secret Lair analysis. Logs: {'; '.join(attempt_logs)}"
        )

    def _clean_and_parse_json(self, raw: str) -> dict:
        """Strips markdown fences and safely parses JSON."""
        clean = raw.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s*```$", "", clean)
        clean = clean.strip()

        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            start = clean.find("{")
            end = clean.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(clean[start : end + 1])
                except Exception:
                    pass
            raise GeminiAnalysisError(f"Could not parse Gemini JSON response. Snippet: {raw[:250]}")
