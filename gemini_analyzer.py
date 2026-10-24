"""
Gemini Commander Deck Analyzer module for Chimaera MTG.
Integrates with Google Gemini API to produce tactical Commander deck analyses,
card-by-card 1-10 ratings, strategic summaries, win-conditions, and upgrade recommendations.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
import requests

try:
    import zoneinfo
    EASTERN_TZ = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    import datetime as dt
    EASTERN_TZ = dt.timezone(dt.timedelta(hours=-5), name="EST")


def get_est_timestamp_str(dt_val: datetime | None = None) -> str:
    """Returns formatted datetime string in Eastern Time with EST label."""
    now = dt_val or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    est_dt = now.astimezone(EASTERN_TZ)
    return est_dt.strftime("%Y-%m-%d %H:%M:%S EST")


logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.8-flash"

SUPPORTED_MODELS = [
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash (Deeper Guidance)",
        "description": "1M context flagship Flash model for deeper strategic guidance, deck analysis, and fleet synergy.",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash-Lite (Quick Analysis)",
        "description": "Fastest, lowest-cost 3.5 model for quick tactical analysis and high-throughput evaluations.",
    },
]

MODEL_TIER_SEQUENCE = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]

ALLOWED_MODEL_IDS = {m["id"] for m in SUPPORTED_MODELS}

TASK_MODEL_MAP = {
    "deck_analysis": "gemini-3.8-flash",
    "deck_tactics": "gemini-3.8-flash",
    "fleet_synergy": "gemini-3.8-flash",
    "card_evaluation": "gemini-3.5-flash-lite",
    "data_extraction": "gemini-3.5-flash-lite",
    "api_test": "gemini-3.5-flash-lite",
}

MODEL_FALLBACK_MAP = {
    "gemini-3.8": "gemini-3.8-flash",
    "gemini-3.8-pro": "gemini-3.8-flash",
    "3.8": "gemini-3.8-flash",
    "gemini-3.7-flash": "gemini-3.8-flash",
    "gemini-3.7": "gemini-3.8-flash",
    "3.7": "gemini-3.8-flash",
    "gemini-3.6-flash": "gemini-3.8-flash",
    "gemini-3.6": "gemini-3.8-flash",
    "gemini-3.5-flash": "gemini-3.5-flash-lite",
    "gemini-3.5": "gemini-3.5-flash-lite",
    "3.5": "gemini-3.5-flash-lite",
    "3.5-lite": "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.5-pro": "gemini-3.8-flash",
    "gemini-3.1-pro-preview": "gemini-3.8-flash",
    "gemini-2.5-flash": "gemini-3.8-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.0-flash": "gemini-3.8-flash",
    "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
    "gemini-1.5-pro": "gemini-3.8-flash",
    "gemini-1.5-flash": "gemini-3.5-flash-lite",
    "gemini-pro": "gemini-3.8-flash",
}


def get_model_for_task(task_type: str, preferred_model: str | None = None) -> str:
    """
    Resolves the appropriate Gemini model (strictly one of the two supported models:
    gemini-3.8-flash for deeper guidance or gemini-3.5-flash-lite for quick analysis).
    - If preferred_model is 'auto', None, or empty, routes to the task-optimized model.
    - Otherwise, honors explicit user model selection (mapping legacy aliases to supported models).
    """
    if preferred_model and str(preferred_model).strip().lower() not in ("auto", "default", "none", ""):
        clean_pref = str(preferred_model).strip()
        resolved = MODEL_FALLBACK_MAP.get(clean_pref, clean_pref)
        if resolved in ALLOWED_MODEL_IDS:
            return resolved
    return TASK_MODEL_MAP.get(task_type, DEFAULT_MODEL)


TACTICS_SECTIONS = {
    "opening_hand": {
        "title": "Opening Hand & Mulligan Guide",
        "description": "What to look for in an opening hand, keep/mulligan criteria, key cards, and red flags.",
        "icon": "✋",
    },
    "combos": {
        "title": "Combos & Synergy Lines",
        "description": "Infinite loops, game-winning combos, and high-synergy lines present in the deck with step-by-step instructions.",
        "icon": "⚡",
    },
    "game_plan": {
        "title": "Game Plan by Phase",
        "description": "Turn-by-turn priorities across early, mid, and late game, sequencing, and pivot signals.",
        "icon": "🗺️",
    },
    "threats": {
        "title": "Threats & Interaction",
        "description": "Removal priorities, threat coverage, bad matchups, and playing around board wipes.",
        "icon": "🎯",
    },
    "commander_usage": {
        "title": "Commander Usage & Timing",
        "description": "Ideal cast timing, protection options, recast thresholds, and commander tax management.",
        "icon": "👑",
    },
}


class GeminiAnalysisError(Exception):
    """Raised when Gemini API analysis fails."""
    pass


class GeminiAnalyzer:
    """Handles prompt construction, API dispatch to Google Gemini, and structured JSON parsing."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        raw_model = model or os.getenv("GEMINI_DEFAULT_MODEL", DEFAULT_MODEL)
        self.requested_model = raw_model
        self.model = get_model_for_task("deck_analysis", preferred_model=raw_model)

    @staticmethod
    def get_available_models(api_key: str | None = None) -> list[dict]:
        """Returns the curated list of supported models for Gemini Strategic Intel."""
        return list(SUPPORTED_MODELS)

    @staticmethod
    def test_api_key(api_key: str, model: str | None = None) -> tuple[bool, str]:
        """Tests whether a Gemini API key is valid using the quick analysis model by default."""
        if not api_key or not str(api_key).strip():
            return False, "Gemini API key is required."

        clean_key = str(api_key).strip()
        # For health check / ping, use lightweight model (gemini-3.5-flash-lite) by default,
        # or the explicitly requested model if specified
        test_model = get_model_for_task("api_test", preferred_model=model)
        url = f"{GEMINI_API_BASE}/{test_model}:generateContent?key={clean_key}"
        gen_config: dict = {
            "maxOutputTokens": 10,
        }
        if "gemini-3" in test_model:
            gen_config["thinkingConfig"] = {"thinkingLevel": "low"}

        payload = {
            "contents": [
                {"parts": [{"text": "Reply with only the word: OK"}]}
            ],
            "generationConfig": gen_config,
        }

        # Direct execution with short retry on transient errors
        for attempt in range(2):
            try:
                resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
                if resp.status_code == 200:
                    return True, "API Key successfully verified."
                elif resp.status_code in (429, 503) and attempt == 0:
                    time.sleep(1)
                    continue
                else:
                    try:
                        err_json = resp.json()
                        msg = err_json.get("error", {}).get("message", f"HTTP {resp.status_code}")
                    except Exception:
                        msg = f"HTTP {resp.status_code}: {resp.text[:150]}"
                    return False, f"Gemini API Error ({test_model}): {msg}"
            except Exception as e:
                if attempt == 0:
                    time.sleep(1)
                    continue
                return False, f"Connection failed ({test_model}): {str(e)}"

        return False, f"Gemini API ping timed out or failed ({test_model})."

    def analyze_deck(
        self,
        deck_data: dict,
        scryfall_metadata: dict | None = None,
        custom_instructions: str = "",
    ) -> dict:
        """
        Submits full Commander deck details to Gemini and parses the structured tactical analysis.
        """
        if not self.api_key:
            raise GeminiAnalysisError(
                "Gemini API Key is not configured. Please enter your Gemini API key in the settings modal or set GEMINI_API_KEY in .env."
            )

        deck_name = deck_data.get("deck_name", "Commander Deck")
        commanders = deck_data.get("commander", [])
        cards = deck_data.get("cards", [])

        if not cards:
            raise GeminiAnalysisError("Deck contains no cards to analyze.")

        # Build card list summary for prompt
        card_lines = []
        for c in cards:
            c_name = c["name"]
            qty = c.get("quantity", 1)
            section = c.get("section", "mainboard")
            meta = (scryfall_metadata or {}).get(c_name.lower(), {})
            type_line = meta.get("type_line", "")
            mana_cost = meta.get("mana_cost", "")
            price = meta.get("prices", {}).get("usd", "")

            extra_str = f" [{type_line}]" if type_line else ""
            if mana_cost:
                extra_str += f" ({mana_cost})"
            if price:
                extra_str += f" ~${price}"

            prefix = "[COMMANDER] " if section == "commander" or c_name in commanders else ""
            card_lines.append(f"{prefix}{qty}x {c_name}{extra_str}")

        decklist_prompt_text = "\n".join(card_lines)

        is_pauper = bool(
            deck_data.get("is_pauper")
            or deck_data.get("deck_format") == "pauper_commander"
        )

        cid_list = deck_data.get("color_identity") or []
        if not cid_list and "stats" in deck_data and isinstance(deck_data["stats"], dict):
            cid_list = deck_data["stats"].get("color_identity", [])
        cid_str = ", ".join(cid_list) if cid_list else "Colorless"

        if is_pauper:
            system_instruction = (
                "You are an elite Magic: The Gathering Pauper Commander (PDH / Pauper EDH) tactical deck analyst, tournament judge, "
                "and deck-building architect. You evaluate decks with clinical precision, strategic depth, and high authority. "
                "CRITICAL FORMAT RULES FOR PAUPER COMMANDER: "
                "1. The Commander must be an UNCOMMON creature (it does NOT have to be legendary). "
                "2. The 99 cards in the library must have ALL been printed at COMMON rarity in paper MTG or MTGO. "
                "3. Mystic Remora and Rhystic Study are BANNED in Pauper Commander. "
                "4. ALL upgrade suggestions ('card_in') MUST be 100% legal in Pauper Commander (strictly COMMON cards; no uncommons, rares, or mythics allowed in the 99). "
                f"5. ALL upgrade suggestions ('card_in') MUST strictly conform to the Commander's color identity [{cid_str}]. "
                "You must output ONLY valid JSON matching the exact required schema."
            )
            format_header = f"FORMAT: Pauper Commander (PDH / Pauper EDH) | COMMANDER COLOR IDENTITY: [{cid_str}]"
            upgrade_constraint = " (CRITICAL PAUPER COMMANDER CONSTRAINT: Every single 'card_in' MUST be printed at COMMON rarity. Do NOT recommend Rares, Mythics, or Uncommons for the 99-card deck, and do NOT recommend Rhystic Study or Mystic Remora.)"
        else:
            system_instruction = (
                "You are an elite Magic: The Gathering Commander (EDH) tactical deck analyst, tournament judge, "
                "and deck-building architect. You evaluate decks with clinical precision, strategic depth, and high authority. "
                f"CRITICAL COMMANDER RULE: Every card upgrade ('card_in') MUST strictly match the designated Commander's color identity [{cid_str}]. Never recommend cards containing mana symbols or hybrid mana outside [{cid_str}]. "
                "You must output ONLY valid JSON matching the exact required schema."
            )
            format_header = f"FORMAT: Regular Commander (EDH) | COMMANDER COLOR IDENTITY: [{cid_str}]"
            upgrade_constraint = ""

        user_prompt = f"""Analyze this Magic: The Gathering Commander deck in full clinical detail.

{format_header}
DECK NAME: {deck_name}
DESIGNATED COMMANDER(S): {', '.join(commanders) if commanders else 'Not explicitly specified'}
COMMANDER COLOR IDENTITY: [{cid_str}]
TOTAL CARD COUNT: {sum(c.get('quantity', 1) for c in cards)}

DECK LIST:
{decklist_prompt_text}

{f"USER NOTES / CUSTOM INSTRUCTIONS: {custom_instructions}" if custom_instructions else ""}

TASK REQUIREMENTS:
1. OVERALL STRATEGY & IDENTITY:
   - Identify the deck's primary archetype, tempo/speed, gameplan, and estimated power level (1.0 to 10.0 scale, e.g. 7.5).
   - Assign a Power Bracket: 'Casual (1-4)', 'Focused (5-6)', 'Optimized (7-8)', 'High-Power (8-9)', or 'cEDH (9-10)'.
   - Evaluate mana base health, ramp package, color balance, and curve.

2. WIN CONDITIONS & COMBOS:
   - Identify primary win conditions (combat damage, combo lines, commander damage, aristocrats drain, alternate win-cons, etc.).
   - List the exact key cards needed, execution steps, and resilience/speed rating.

3. CARD-BY-CARD EFFECTIVENESS RATINGS & PURPOSE:
   - For EVERY unique card in the deck (including Commander and mainboard), provide:
     * 'card_name': Exact card name
     * 'quantity': Number of copies
     * 'rating': Effectiveness score on a 1.0 to 10.0 scale specifically within THIS deck's synergy and strategy (10 = absolute pillar/staple for this commander, 7 = strong synergizer, 5 = functional filler, 1-4 = suboptimal/cut candidate).
     * 'role': One of ['Commander', 'Ramp', 'Card Advantage', 'Spot Removal', 'Board Wipe', 'Finisher / Win-Con', 'Enabler / Synergy Engine', 'Protection / Counterspell', 'Tutor', 'Utility', 'Land']
     * 'purpose': Detailed tactical explanation of why this card is included, what role it plays, and how it interacts with the commander and other key cards.
     * 'verdict': One of ['Core Staple', 'Strong Synergizer', 'Solid Role Player', 'Potential Cut']

4. PROPOSED CARD UPGRADES & SWAPS:
   - Suggest 4 to 8 high-impact card upgrades.{upgrade_constraint}
   - CRITICAL COMMANDER COLOR IDENTITY RULE: Every suggested 'card_in' MUST strictly match the Commander's color identity [{cid_str}]. For example, in a Mono-Green deck, ALL suggestions MUST be Green or Colorless. Never recommend cards containing mana symbols or hybrid mana outside [{cid_str}] (e.g., no Blue, Black, Red, or White cards for a Mono-Green deck).
   - STRATEGY ALIGNMENT: Recommend upgrades that offer the STRONGEST BUFFS to what this deck is actually doing (amplifying core engines, enablers, payoffs, or finishers) or solving critical curve, draw, and interaction deficits.
   - For each upgrade, specify 'card_in' (the recommended addition), 'card_out' (the card to cut from the current list), 'category' ('Power', 'Synergy', 'Mana Base', 'Protection', 'Speed', 'Budget'), 'rationale' (clear explanation of why this swap improves speed, consistency, or power), 'estimated_impact' ('High', 'Medium', 'Low'), and 'color_identity' (array of color letters e.g. ["G"] or []).

5. CUT RECOMMENDATIONS:
   - List the 3 to 6 weakest cards in the deck with reasons why they should be replaced.

CRITICAL INSTRUCTION: You must respond ONLY with a raw JSON object (no markdown surrounding code fences if possible, or standard json) adhering strictly to this schema:
{{
  "deck_name": "{deck_name}",
  "commander": ["{commanders[0] if commanders else ''}"],
  "partner_or_companion": null,
  "color_identity": {json.dumps(cid_list)},
  "archetype": "string",
  "estimated_power_level": 7.5,
  "power_bracket": "Optimized (7-8)",
  "overall_summary": "string",
  "mana_base_analysis": "string",
  "key_synergies": [
    {{"name": "string", "cards": ["string"], "description": "string"}}
  ],
  "win_conditions": [
    {{
      "title": "string",
      "type": "string",
      "description": "string",
      "key_cards": ["string"],
      "difficulty_or_speed": "string"
    }}
  ],
  "card_ratings": [
    {{
      "card_name": "string",
      "quantity": 1,
      "rating": 9.0,
      "role": "string",
      "purpose": "string",
      "verdict": "Core Staple"
    }}
  ],
  "upgrades": [
    {{
      "card_in": "string",
      "card_out": "string",
      "category": "string",
      "rationale": "string",
      "estimated_impact": "High",
      "color_identity": ["G"]
    }}
  ],
  "cut_recommendations": [
    {{
      "card_name": "string",
      "reason": "string"
    }}
  ]
}}
"""

        target_model = get_model_for_task("deck_analysis", preferred_model=self.model)
        parsed_json, model_used = self._call_gemini_json(
            system_instruction=system_instruction,
            user_prompt=user_prompt,
            task_type="deck_analysis",
            max_output_tokens=16384,
            preferred_model=target_model,
        )

        # Ensure essential keys exist
        parsed_json.setdefault("deck_name", deck_name)
        parsed_json.setdefault("commander", commanders)
        parsed_json.setdefault("card_ratings", [])
        parsed_json.setdefault("win_conditions", [])
        # Filter off-color hallucinated upgrades if color identity is specified
        raw_upgrades = parsed_json.get("upgrades", [])
        if cid_list and isinstance(raw_upgrades, list):
            allowed_set = {c.upper() for c in cid_list if c}
            filtered_upgrades = []
            for u in raw_upgrades:
                u_cid = u.get("color_identity")
                if isinstance(u_cid, (list, set)):
                    u_pips = {c.upper() for c in u_cid if c and c.upper() in ("W", "U", "B", "R", "G")}
                    if not u_pips.issubset(allowed_set):
                        logger.warning(
                            f"Dropping off-color Gemini upgrade recommendation: '{u.get('card_in')}' "
                            f"(colors: {sorted(list(u_pips))} not in deck colors {sorted(list(allowed_set))})"
                        )
                        continue
                filtered_upgrades.append(u)
            parsed_json["upgrades"] = filtered_upgrades
        else:
            parsed_json.setdefault("upgrades", [])

        parsed_json.setdefault("cut_recommendations", [])
        parsed_json.setdefault("overall_summary", "Deck analysis complete.")
        parsed_json["_model_used"] = model_used
        self.model = model_used

        return parsed_json

    def _call_gemini_json(
        self,
        system_instruction: str,
        user_prompt: str,
        task_type: str = "deck_analysis",
        max_output_tokens: int = 16384,
        preferred_model: str | None = None,
    ) -> tuple[dict, str]:
        """
        Submits prompt to Google Gemini in JSON response mode with retries and exponential backoff.
        Returns tuple of (parsed_json, target_model).
        """
        if not self.api_key:
            raise GeminiAnalysisError(
                "Gemini API Key is not configured. Please enter your Gemini API key in the settings modal or set GEMINI_API_KEY in .env."
            )

        target_model = get_model_for_task(task_type, preferred_model=preferred_model or self.model)
        attempt_ts = get_est_timestamp_str()
        url = f"{GEMINI_API_BASE}/{target_model}:generateContent?key={self.api_key}"
        logger.info(f"Submitting task '{task_type}' to Gemini ({target_model}) at {attempt_ts}...")

        base_gen_config = {
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        }
        if "gemini-3" in target_model:
            base_gen_config["thinkingConfig"] = {"thinkingLevel": "low"}

        model_payload = {
            "contents": [
                {"role": "user", "parts": [{"text": user_prompt}]}
            ],
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "generationConfig": base_gen_config,
        }

        model_timeout = 60 if "gemini-3.8" in target_model else 45
        max_retries = 2
        last_err = ""
        last_status = 0

        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff_sec = 1.0 * attempt
                logger.info(f"Retrying Gemini call ({target_model}) attempt {attempt + 1}/{max_retries + 1} after {backoff_sec}s delay...")
                time.sleep(backoff_sec)

            try:
                resp = requests.post(url, json=model_payload, headers={"Content-Type": "application/json"}, timeout=model_timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        last_err = "Gemini returned no response candidates."
                        last_status = 200
                        continue

                    content_parts = candidates[0].get("content", {}).get("parts", [])
                    if not content_parts:
                        last_err = "Gemini response contained empty content."
                        last_status = 200
                        continue

                    raw_text = content_parts[0].get("text", "").strip()
                    parsed_json = self._clean_and_parse_json(raw_text)
                    return parsed_json, target_model

                last_status = resp.status_code
                try:
                    err_json = resp.json()
                    last_err = err_json.get("error", {}).get("message", "")
                except Exception:
                    last_err = resp.text[:200]

                logger.warning(f"Model '{target_model}' request returned HTTP {resp.status_code}: {last_err}")
                if resp.status_code in (429, 500, 503) and attempt < max_retries:
                    continue
                break

            except requests.RequestException as req_err:
                last_err = f"Network error: {str(req_err)}"
                last_status = 0
                if attempt < max_retries:
                    logger.warning(f"Model '{target_model}' network failure: {req_err}. Retrying...")
                    continue
            except GeminiAnalysisError as parse_err:
                last_err = f"Output parsing error: {str(parse_err)}"
                last_status = 200
                if attempt < max_retries:
                    continue
                break

        sc_str = f"HTTP {last_status}: " if last_status else ""
        err_report = f"Gemini analysis failed on model '{target_model}': {sc_str}{last_err}"
        logger.error(err_report)
        raise GeminiAnalysisError(err_report)

    @staticmethod
    def _build_decklist_prompt_text(
        cards: list[dict],
        commanders: list[str],
        scryfall_metadata: dict | None = None,
        include_oracle: bool = True,
    ) -> str:
        """Formats cards list into a detailed text inventory with type, mana, and trimmed oracle text."""
        card_lines = []
        for c in cards:
            c_name = c.get("name", "")
            qty = c.get("quantity", 1)
            section = c.get("section", "mainboard")
            meta = (scryfall_metadata or {}).get(c_name.lower(), {})
            type_line = meta.get("type_line") or c.get("type_line", "")
            mana_cost = meta.get("mana_cost") or c.get("mana_cost", "")

            extra_str = f" [{type_line}]" if type_line else ""
            if mana_cost:
                extra_str += f" ({mana_cost})"

            if include_oracle and "land" not in type_line.lower():
                raw_oracle = meta.get("oracle_text") or c.get("oracle_text", "")
                if raw_oracle:
                    clean_oracle = re.sub(r"\s+", " ", raw_oracle.strip())
                    if len(clean_oracle) > 180:
                        clean_oracle = clean_oracle[:180] + "..."
                    extra_str += f" | {clean_oracle}"

            prefix = "[COMMANDER] " if section == "commander" or c_name in commanders else ""
            card_lines.append(f"{prefix}{qty}x {c_name}{extra_str}")
        return "\n".join(card_lines)

    @staticmethod
    def _validate_cards_in_deck(obj: any, deck_card_names_norm: set[str], cmdrs_norm: set[str]) -> any:
        """Recursively walks parsed JSON to flag MTG card references with an in_deck boolean."""
        if isinstance(obj, dict):
            # If dict has a single card reference key
            for card_key in ("card_name", "card"):
                if card_key in obj and isinstance(obj[card_key], str):
                    cname = obj[card_key].strip().lower()
                    is_in = (
                        cname in deck_card_names_norm
                        or cname in cmdrs_norm
                        or any(cname == dn.split(" // ")[0].strip() for dn in deck_card_names_norm)
                    )
                    obj["in_deck"] = is_in
                    break

            # If dict has 'cards' list e.g. in combo
            if "cards" in obj and isinstance(obj["cards"], list):
                obj["cards_detail"] = []
                for c in obj["cards"]:
                    if isinstance(c, str):
                        cn = c.strip().lower()
                        is_in = (
                            cn in deck_card_names_norm
                            or cn in cmdrs_norm
                            or any(cn == dn.split(" // ")[0].strip() for dn in deck_card_names_norm)
                        )
                        obj["cards_detail"].append({"name": c, "in_deck": is_in})

            # If dict has 'hand' list in example_keeps
            if "hand" in obj and isinstance(obj["hand"], list):
                obj["hand_detail"] = []
                for c in obj["hand"]:
                    if isinstance(c, str):
                        cn = c.strip().lower()
                        is_in = (
                            cn in deck_card_names_norm
                            or cn in cmdrs_norm
                            or any(cn == dn.split(" // ")[0].strip() for dn in deck_card_names_norm)
                        )
                        obj["hand_detail"].append({"name": c, "in_deck": is_in})

            # If dict has 'answers_in_deck' list
            if "answers_in_deck" in obj and isinstance(obj["answers_in_deck"], list):
                obj["answers_detail"] = []
                for c in obj["answers_in_deck"]:
                    if isinstance(c, str):
                        cn = c.strip().lower()
                        is_in = (
                            cn in deck_card_names_norm
                            or cn in cmdrs_norm
                            or any(cn == dn.split(" // ")[0].strip() for dn in deck_card_names_norm)
                        )
                        obj["answers_detail"].append({"name": c, "in_deck": is_in})

            for k, v in list(obj.items()):
                if k not in ("cards_detail", "hand_detail", "answers_detail"):
                    obj[k] = GeminiAnalyzer._validate_cards_in_deck(v, deck_card_names_norm, cmdrs_norm)
            return obj
        elif isinstance(obj, list):
            return [GeminiAnalyzer._validate_cards_in_deck(item, deck_card_names_norm, cmdrs_norm) for item in obj]
        return obj

    def generate_tactics_section(
        self,
        section: str,
        deck_data: dict,
        scryfall_metadata: dict | None = None,
        extra_context: str = "",
        preferred_model: str | None = None,
    ) -> dict:
        """
        Generates tactical advice for a specific section ('opening_hand', 'combos', 'game_plan',
        'threats', 'commander_usage') using Gemini AI grounded in the provided deck list.
        """
        if section not in TACTICS_SECTIONS:
            allowed = ", ".join(TACTICS_SECTIONS.keys())
            raise GeminiAnalysisError(f"Unknown tactics section '{section}'. Allowed: {allowed}")

        cards = deck_data.get("cards", [])
        if not cards:
            raise GeminiAnalysisError("Deck contains no cards to analyze for tactics.")

        deck_name = deck_data.get("deck_name", "Commander Deck")
        commanders = deck_data.get("commander", [])
        if isinstance(commanders, str):
            commanders = [c.strip() for c in commanders.split(",") if c.strip()]

        cid_list = deck_data.get("color_identity") or []
        if not cid_list and "stats" in deck_data and isinstance(deck_data["stats"], dict):
            cid_list = deck_data["stats"].get("color_identity", [])
        cid_str = ", ".join(cid_list) if cid_list else "Colorless"

        is_pauper = bool(
            deck_data.get("is_pauper")
            or deck_data.get("deck_format") == "pauper_commander"
        )

        decklist_text = self._build_decklist_prompt_text(
            cards=cards,
            commanders=commanders,
            scryfall_metadata=scryfall_metadata,
            include_oracle=True,
        )

        format_str = "Pauper Commander (PDH / Pauper EDH)" if is_pauper else "Regular Commander (EDH)"
        pauper_rules = (
            "CRITICAL FORMAT RULES: This is Pauper Commander. Commander is an Uncommon creature; "
            "the 99-card deck consists strictly of Common cards. Rhystic Study and Mystic Remora are banned. "
            if is_pauper else ""
        )

        system_instruction = (
            f"You are an elite Magic: The Gathering Commander gameplay coach, judge, and tactical tournament strategist. "
            f"You provide clinical, high-authority advice tailored strictly to the user's specific deck list and Commander. "
            f"{pauper_rules}"
            f"CRITICAL CONSTRAINT: You must ONLY reference cards that actually exist in this deck list, "
            f"except when explicitly discussing near-combos or opponent archetypes. "
            f"You must respond ONLY with raw valid JSON adhering strictly to the required schema."
        )

        section_prompts = {
            "opening_hand": f"""Provide an in-depth Opening Hand & Mulligan Guide for this Commander deck.

FORMAT: {format_str} | COMMANDER COLOR IDENTITY: [{cid_str}]
DECK: {deck_name}
COMMANDER: {', '.join(commanders) if commanders else 'Not specified'}

DECK LIST:
{decklist_text}

{f"ADDITIONAL CONTEXT: {extra_context}" if extra_context else ""}

TASK REQUIREMENTS:
1. 'summary': Clear 2-3 sentence overview of what this deck fundamentally needs in its opening hand.
2. 'keep_criteria': 3 to 5 actionable bullet points defining an ideal keepable 7-card hand.
3. 'priority_cards': 4 to 7 high-priority cards in this deck list that dramatically improve an opening hand, with 'card_name' and 'why'.
4. 'red_flags': 3 to 5 trap hands or warning signs that warrant an immediate mulligan.
5. 'ideal_land_count': The sweet-spot opening land count (e.g. '3-4 lands').
6. 'example_keeps': 2 to 3 sample 7-card hands using real cards from this deck list, with 'hand' (array of 7 card names), 'verdict' ('Keep' or 'Mulligan'), and tactical 'reason'.
7. 'mulligan_tips': 2 to 4 tips regarding aggressive vs. conservative mulligans and using the free Commander mulligan.

Respond ONLY with valid JSON:
{{
  "summary": "string",
  "keep_criteria": ["string"],
  "priority_cards": [{{"card_name": "string", "why": "string"}}],
  "red_flags": ["string"],
  "ideal_land_count": "3-4",
  "example_keeps": [{{"hand": ["string"], "verdict": "Keep", "reason": "string"}}],
  "mulligan_tips": ["string"]
}}""",

            "combos": f"""Identify all infinite combos, high-potency synergy loops, and near-combos for this Commander deck.

FORMAT: {format_str} | COMMANDER COLOR IDENTITY: [{cid_str}]
DECK: {deck_name}
COMMANDER: {', '.join(commanders) if commanders else 'Not specified'}

DECK LIST:
{decklist_text}

{f"KNOWN COMBO GROUNDING DATA: {extra_context}" if extra_context else ""}

TASK REQUIREMENTS:
1. 'summary': Overview of the deck's combo posture (primary win-con vs. incidental value vs. non-combo synergy).
2. 'combos': Every complete combo, infinite loop, or game-ending engine where ALL pieces are present in this deck list.
   For each combo, provide:
   - 'name': Descriptive name of the line
   - 'type': One of ['Infinite Combo', 'Game-Ending Synergy', 'Value Engine', 'Combat Lock']
   - 'cards': Exact card names in this deck needed to execute
   - 'steps': Step-by-step ordered list of instructions to execute the combo
   - 'result': Exact outcome (e.g. 'Infinite Mana', 'Infinite Damage', 'Infinite Card Draw')
   - 'tutors_or_redundancy': Tutors or backup pieces in this deck that locate or substitute for pieces
   - 'vulnerabilities': How opponents can disrupt or interact with this line
   (If this deck runs zero infinite loops, document its 2 to 4 highest-impact non-infinite synergistic loops and board combos).
3. 'near_combos': 2 to 4 recognized combos where this deck already runs 2+ pieces and is missing only 1 card.
   - 'name': Combo name
   - 'cards_in_deck': Array of card names currently in the deck
   - 'missing': The single missing card name
   - 'result': Outcome if completed

Respond ONLY with valid JSON:
{{
  "summary": "string",
  "combos": [
    {{
      "name": "string",
      "type": "string",
      "cards": ["string"],
      "steps": ["string"],
      "result": "string",
      "tutors_or_redundancy": ["string"],
      "vulnerabilities": "string"
    }}
  ],
  "near_combos": [
    {{
      "name": "string",
      "cards_in_deck": ["string"],
      "missing": "string",
      "result": "string"
    }}
  ]
}}""",

            "game_plan": f"""Construct a phased, turn-by-turn game plan for this Commander deck.

FORMAT: {format_str} | COMMANDER COLOR IDENTITY: [{cid_str}]
DECK: {deck_name}
COMMANDER: {', '.join(commanders) if commanders else 'Not specified'}

DECK LIST:
{decklist_text}

{f"ADDITIONAL CONTEXT: {extra_context}" if extra_context else ""}

TASK REQUIREMENTS:
1. 'summary': Executive strategic thesis and sequencing philosophy.
2. 'phases': Exactly 3 phase breakdowns:
   - 'Early Game (Turns 1-3)': ramp, fixing, early board state, avoiding overcommitment.
   - 'Mid Game (Turns 4-6)': deploying the commander, establishing engines, policing threats.
   - 'Late Game (Turns 7+)': closing the game, breaking parity, navigating board wipes.
   Each phase must include:
   - 'phase': Name of phase
   - 'goals': 2 to 4 actionable objectives
   - 'key_plays': 3 to 5 specific cards or sequences from this deck list to execute
   - 'avoid': 2 to 3 common tactical mistakes in this phase
3. 'pivot_signals': 2 to 4 specific board signals indicating when to shift gears (e.g. shift from defensive control to all-in alpha strike, or hold up interaction against an impending combo).

Respond ONLY with valid JSON:
{{
  "summary": "string",
  "phases": [
    {{"phase": "Early Game (Turns 1-3)", "goals": ["string"], "key_plays": ["string"], "avoid": ["string"]}},
    {{"phase": "Mid Game (Turns 4-6)", "goals": ["string"], "key_plays": ["string"], "avoid": ["string"]}},
    {{"phase": "Late Game (Turns 7+)", "goals": ["string"], "key_plays": ["string"], "avoid": ["string"]}}
  ],
  "pivot_signals": ["string"]
}}""",

            "threats": f"""Provide a defensive interaction, threat assessment, and wipe management guide for this Commander deck.

FORMAT: {format_str} | COMMANDER COLOR IDENTITY: [{cid_str}]
DECK: {deck_name}
COMMANDER: {', '.join(commanders) if commanders else 'Not specified'}

DECK LIST:
{decklist_text}

{f"ADDITIONAL CONTEXT: {extra_context}" if extra_context else ""}

TASK REQUIREMENTS:
1. 'summary': The deck's interaction posture (proactive vs. reactive vs. tap-out).
2. 'removal_priorities': 3 to 5 critical opposing threat categories to save interaction for, with 'threat', 'why', and 'answers_in_deck' (specific removal cards present in this deck list).
3. 'bad_matchups': 2 to 4 challenging opponent archetypes (e.g. Fast Spell Combo, Stax / Tax, Graveyard Reanimator, Go-Wide Tokens), with 'archetype', 'why' it poses a threat, and 'how_to_adapt'.
4. 'board_wipe_plan': Specific instructions on how this deck plays around, recovers from, or executes board wipes.
5. 'hold_up_tips': 2 to 4 practical guidelines on when to hold open mana for instant-speed interaction vs. spending mana on development.

Respond ONLY with valid JSON:
{{
  "summary": "string",
  "removal_priorities": [{{"threat": "string", "why": "string", "answers_in_deck": ["string"]}}],
  "bad_matchups": [{{"archetype": "string", "why": "string", "how_to_adapt": "string"}}],
  "board_wipe_plan": "string",
  "hold_up_tips": ["string"]
}}""",

            "commander_usage": f"""Provide optimal Commander usage, timing, and protection guidelines for this Commander deck.

FORMAT: {format_str} | COMMANDER COLOR IDENTITY: [{cid_str}]
DECK: {deck_name}
COMMANDER: {', '.join(commanders) if commanders else 'Not specified'}

DECK LIST:
{decklist_text}

{f"ADDITIONAL CONTEXT: {extra_context}" if extra_context else ""}

TASK REQUIREMENTS:
1. 'summary': The commander's strategic role (e.g. centerpiece combo engine, value engine, finisher, utility).
2. 'ideal_cast_turn': When to deploy the commander (e.g. 'Turn 3 with haste / Turn 4 with protection open').
3. 'cast_timing_tips': 3 to 5 golden rules on when and how to deploy the commander.
4. 'protection_options': 3 to 6 specific cards in this deck list that protect, recur, or shield the commander, with 'card_name' and 'how'.
5. 'recast_threshold': Clear guidance on commander tax management and when to stop recasting from the command zone.
6. 'when_not_to_cast': 2 to 4 scenarios where it is a mistake to cast the commander.

Respond ONLY with valid JSON:
{{
  "summary": "string",
  "ideal_cast_turn": "string",
  "cast_timing_tips": ["string"],
  "protection_options": [{{"card_name": "string", "how": "string"}}],
  "recast_threshold": "string",
  "when_not_to_cast": ["string"]
}}""",
        }

        user_prompt = section_prompts[section]

        parsed_json, model_used = self._call_gemini_json(
            system_instruction=system_instruction,
            user_prompt=user_prompt,
            task_type="deck_tactics",
            max_output_tokens=8192,
            preferred_model=preferred_model or self.model,
        )

        # Build normalized card name lookup sets for hallucination checks
        deck_card_names_norm = {c.get("name", "").strip().lower() for c in cards if c.get("name")}
        cmdrs_norm = {c.strip().lower() for c in commanders if c.strip()}

        validated_json = self._validate_cards_in_deck(parsed_json, deck_card_names_norm, cmdrs_norm)
        validated_json["_section"] = section
        validated_json["_model_used"] = model_used

        return validated_json

    def _clean_and_parse_json(self, raw: str) -> dict:
        """Strips markdown code fences and cleans json before parsing."""
        clean = raw.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s*```$", "", clean)

        clean = clean.strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.warning(f"Initial JSON decode failed, attempting bracket slice extraction: {e}")
            start = clean.find("{")
            end = clean.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(clean[start : end + 1])
                except Exception:
                    pass
            raise GeminiAnalysisError(f"Could not parse Gemini JSON response: {str(e)}. Raw output: {raw[:300]}")
