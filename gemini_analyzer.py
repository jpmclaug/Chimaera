"""
Gemini Commander Deck Analyzer module for Chimaera MTG.
Integrates with Google Gemini API to produce tactical Commander deck analyses,
card-by-card 1-10 ratings, strategic summaries, win-conditions, and upgrade recommendations.
"""

import json
import logging
import os
import re
import requests

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.7-flash"
SUPPORTED_MODELS = [
    {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash", "description": "High speed, high accuracy tactical MTG evaluations."},
]

MODEL_FALLBACK_MAP = {
    "gemini-2.5-pro": "gemini-3.7-flash",
    "gemini-3.1-pro-preview": "gemini-3.7-flash",
    "gemini-2.5-flash": "gemini-3.7-flash",
    "gemini-2.0-flash": "gemini-3.7-flash",
    "gemini-1.5-pro": "gemini-3.7-flash",
    "gemini-1.5-flash": "gemini-3.7-flash",
    "gemini-pro": "gemini-3.7-flash",
}


class GeminiAnalysisError(Exception):
    """Raised when Gemini API analysis fails."""
    pass


class GeminiAnalyzer:
    """Handles prompt construction, API dispatch to Google Gemini, and structured JSON parsing."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        # Default strictly to Gemini 3.7 Flash (user selection disabled)
        self.model = DEFAULT_MODEL

    @staticmethod
    def get_available_models(api_key: str | None = None) -> list[dict]:
        """Returns the supported Gemini models (locked to Gemini 3.7 Flash)."""
        return SUPPORTED_MODELS

    @staticmethod
    def test_api_key(api_key: str, model: str = DEFAULT_MODEL) -> tuple[bool, str]:
        """Tests whether a Gemini API key is valid by sending a ping request."""
        if not api_key or not str(api_key).strip():
            return False, "Gemini API key is required."

        clean_key = str(api_key).strip()
        test_model = MODEL_FALLBACK_MAP.get(model, model)
        url = f"{GEMINI_API_BASE}/{test_model}:generateContent?key={clean_key}"
        payload = {
            "contents": [
                {"parts": [{"text": "Reply with only the word: OK"}]}
            ],
            "generationConfig": {
                "maxOutputTokens": 10,
                "temperature": 0.1,
            }
        }

        try:
            resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            if resp.status_code == 200:
                return True, "API Key successfully verified."
            else:
                try:
                    err_json = resp.json()
                    msg = err_json.get("error", {}).get("message", f"HTTP {resp.status_code}")
                except Exception:
                    msg = f"HTTP {resp.status_code}: {resp.text[:150]}"

                # If the specific model failed due to availability, try fallback to default flash model
                if ("no longer available" in msg.lower() or "not found" in msg.lower()) and test_model != DEFAULT_MODEL:
                    fallback_url = f"{GEMINI_API_BASE}/{DEFAULT_MODEL}:generateContent?key={clean_key}"
                    fallback_resp = requests.post(fallback_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
                    if fallback_resp.status_code == 200:
                        return True, f"API key is valid. Note: '{test_model}' was deprecated, so Chimaera will use '{DEFAULT_MODEL}'."

                return False, f"Gemini API Error: {msg}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

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

        system_instruction = (
            "You are an elite Magic: The Gathering Commander (EDH) tactical deck analyst, tournament judge, "
            "and deck-building architect. You evaluate decks with clinical precision, strategic depth, and high authority. "
            "You must output ONLY valid JSON matching the exact required schema."
        )

        user_prompt = f"""Analyze this Magic: The Gathering Commander (EDH) deck in full clinical detail.

DECK NAME: {deck_name}
DESIGNATED COMMANDER(S): {', '.join(commanders) if commanders else 'Not explicitly specified'}
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
   - Suggest 4 to 8 high-impact card upgrades.
   - For each upgrade, specify 'card_in' (the recommended addition), 'card_out' (the card to cut from the current list), 'category' ('Power', 'Synergy', 'Mana Base', 'Protection', 'Speed', 'Budget'), 'rationale' (clear explanation of why this swap improves speed, consistency, or power), and 'estimated_impact' ('High', 'Medium', 'Low').

5. CUT RECOMMENDATIONS:
   - List the 3 to 6 weakest cards in the deck with reasons why they should be replaced.

CRITICAL INSTRUCTION: You must respond ONLY with a raw JSON object (no markdown surrounding code fences if possible, or standard json) adhering strictly to this schema:
{{
  "deck_name": "{deck_name}",
  "commander": ["{commanders[0] if commanders else ''}"],
  "partner_or_companion": null,
  "color_identity": ["W", "U", "B", "R", "G"],
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
      "estimated_impact": "High"
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

        url = f"{GEMINI_API_BASE}/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": user_prompt}]}
            ],
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 16384,
                "responseMimeType": "application/json",
            }
        }

        try:
            target_model = self.model
            url = f"{GEMINI_API_BASE}/{target_model}:generateContent?key={self.api_key}"
            logger.info(f"Submitting deck '{deck_name}' to Gemini ({target_model})...")
            resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=120)

            # Auto-fallback if the specific model is deprecated or unavailable
            if resp.status_code != 200:
                err_msg = ""
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error", {}).get("message", "")
                except Exception:
                    err_msg = resp.text[:200]

                if ("no longer available" in err_msg.lower() or "not found" in err_msg.lower()) and target_model != DEFAULT_MODEL:
                    logger.warning(f"Model '{target_model}' unavailable ({err_msg}). Automatically falling back to '{DEFAULT_MODEL}'...")
                    fallback_url = f"{GEMINI_API_BASE}/{DEFAULT_MODEL}:generateContent?key={self.api_key}"
                    resp = requests.post(fallback_url, json=payload, headers={"Content-Type": "application/json"}, timeout=120)

            if resp.status_code != 200:
                err_msg = f"Gemini API returned HTTP {resp.status_code}"
                try:
                    err_json = resp.json()
                    err_msg += f": {err_json.get('error', {}).get('message', '')}"
                except Exception:
                    err_msg += f": {resp.text[:200]}"
                raise GeminiAnalysisError(err_msg)

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise GeminiAnalysisError("Gemini returned no response candidates.")

            content_parts = candidates[0].get("content", {}).get("parts", [])
            if not content_parts:
                raise GeminiAnalysisError("Gemini response contained empty content.")

            raw_text = content_parts[0].get("text", "").strip()
            parsed_json = self._clean_and_parse_json(raw_text)

            # Ensure essential keys exist
            parsed_json.setdefault("deck_name", deck_name)
            parsed_json.setdefault("commander", commanders)
            parsed_json.setdefault("card_ratings", [])
            parsed_json.setdefault("win_conditions", [])
            parsed_json.setdefault("upgrades", [])
            parsed_json.setdefault("cut_recommendations", [])
            parsed_json.setdefault("overall_summary", "Deck analysis complete.")

            return parsed_json

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
