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
DEFAULT_MODEL = "auto"

SUPPORTED_MODELS = [
    {"id": "auto", "name": "Auto (Cost & Value Optimized)", "description": "Automatically routes each prompt to the most cost-effective Gemini model for that task."},
    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash (Deep Intel)", "description": "Next-generation ultra-high accuracy and speed tactical MTG evaluations."},
    {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash (Balanced)", "description": "High speed, high accuracy tactical MTG evaluations."},
    {"id": "gemini-3.6-flash", "name": "Gemini 3.6 Flash", "description": "High performance low latency MTG analysis."},
    {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "description": "Fast tactical Commander evaluations."},
    {"id": "gemini-3.5-flash-lite", "name": "Gemini 3.5 Flash-Lite (Fast / Lowest Cost)", "description": "Ultra lightweight, low latency model for utility tasks."},
]

MODEL_TIER_SEQUENCE = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

TASK_MODEL_MAP = {
    "deck_analysis": "gemini-3.8-flash",
    "fleet_synergy": "gemini-3.8-flash",
    "card_evaluation": "gemini-3.7-flash",
    "data_extraction": "gemini-3.5-flash-lite",
    "api_test": "gemini-3.5-flash-lite",
}

MODEL_FALLBACK_MAP = {
    "gemini-3.8": "gemini-3.8-flash",
    "gemini-3.8-pro": "gemini-3.8-flash",
    "3.8": "gemini-3.8-flash",
    "gemini-3.7": "gemini-3.7-flash",
    "3.7": "gemini-3.7-flash",
    "gemini-2.5-pro": "gemini-3.7-flash",
    "gemini-3.1-pro-preview": "gemini-3.7-flash",
    "gemini-2.5-flash": "gemini-3.5-flash",
    "gemini-2.0-flash": "gemini-3.6-flash",
    "gemini-1.5-pro": "gemini-3.5-flash",
    "gemini-1.5-flash": "gemini-3.5-flash-lite",
    "gemini-pro": "gemini-3.5-flash",
}


def get_model_for_task(task_type: str, preferred_model: str | None = None) -> str:
    """
    Resolves the most cost-effective and appropriate Gemini model for a given task.
    - If preferred_model is 'auto', None, or empty, routes to the task-optimized model.
    - Otherwise, honors explicit user model selection (mapping legacy aliases if needed).
    """
    if preferred_model and str(preferred_model).strip().lower() not in ("auto", "default", "none", ""):
        clean_pref = str(preferred_model).strip()
        return MODEL_FALLBACK_MAP.get(clean_pref, clean_pref)
    return TASK_MODEL_MAP.get(task_type, "gemini-3.8-flash")


class GeminiAnalysisError(Exception):
    """Raised when Gemini API analysis fails."""
    pass


class GeminiAnalyzer:
    """Handles prompt construction, API dispatch to Google Gemini, and structured JSON parsing."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        raw_model = model or os.getenv("GEMINI_DEFAULT_MODEL", DEFAULT_MODEL)
        self.requested_model = raw_model
        if raw_model and str(raw_model).strip().lower() in ("auto", "default", "none", ""):
            self.model = TASK_MODEL_MAP.get("deck_analysis", "gemini-3.8-flash")
        else:
            self.model = MODEL_FALLBACK_MAP.get(raw_model, raw_model)

    @staticmethod
    def get_available_models(api_key: str | None = None) -> list[dict]:
        """Returns the curated list of supported models for Gemini Strategic Intel."""
        return list(SUPPORTED_MODELS)

    @staticmethod
    def test_api_key(api_key: str, model: str = DEFAULT_MODEL) -> tuple[bool, str]:
        """Tests whether a Gemini API key is valid using the most cost-effective model."""
        if not api_key or not str(api_key).strip():
            return False, "Gemini API key is required."

        clean_key = str(api_key).strip()
        # For health check / ping, use lightweight model (gemini-3.5-flash-lite) if auto/default,
        # or the explicitly requested model if specified
        test_model = get_model_for_task("api_test", preferred_model=model)
        url = f"{GEMINI_API_BASE}/{test_model}:generateContent?key={clean_key}"
        gen_config = {
            "maxOutputTokens": 10,
            "temperature": 0.1,
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

        # Base generation config
        base_gen_config = {
            "temperature": 0.2,
            "maxOutputTokens": 16384,
            "responseMimeType": "application/json",
        }

        target_model = get_model_for_task("deck_analysis", preferred_model=self.model)
        attempt_ts = get_est_timestamp_str()
        url = f"{GEMINI_API_BASE}/{target_model}:generateContent?key={self.api_key}"
        logger.info(f"Submitting deck '{deck_name}' to Gemini ({target_model}) at {attempt_ts}...")

        # Apply low thinking level for Gemini 3 models to prevent high-latency timeouts
        model_gen_config = dict(base_gen_config)
        if "gemini-3" in target_model:
            model_gen_config["thinkingConfig"] = {"thinkingLevel": "low"}

        model_payload = {
            "contents": [
                {"role": "user", "parts": [{"text": user_prompt}]}
            ],
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "generationConfig": model_gen_config,
        }

        # Adaptive timeout: 60s for 3.8, 45s for 3.7/3.6/3.5
        model_timeout = 60 if "gemini-3.8" in target_model else 45

        # Direct execution with transient retries (exponential backoff) on target_model
        max_retries = 2
        last_err = ""
        last_status = 0

        try:
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
                        parsed_json["_model_used"] = target_model
                        self.model = target_model

                        return parsed_json

                    # Handle non-200 responses
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

        except GeminiAnalysisError:
            raise
        except Exception as e:
            logger.error(f"Error during Gemini deck analysis: {e}", exc_info=True)
            raise GeminiAnalysisError(f"Deck analysis failed: {str(e)}")

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
