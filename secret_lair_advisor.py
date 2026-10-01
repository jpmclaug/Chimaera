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
import time
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
    get_model_for_task,
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
                    or soup.find("div", class_=lambda c: c and "article" in (c if isinstance(c, str) else " ".join(c)).lower())
                    or soup.body
                )
                text = self._extract_structured_text_from_html(article or soup) if article else soup.get_text("\n", strip=True)

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

    @staticmethod
    def _extract_structured_text_from_html(article_or_soup) -> str:
        """
        Extracts clean, structured markdown-like text from HTML/DOM node,
        preserving headings (##), list items (- 1x ...), and paragraph boundaries.
        """
        if article_or_soup is None:
            return ""

        soup_copy = BeautifulSoup(str(article_or_soup), "html.parser")
        for s in soup_copy.find_all(["script", "style", "nav", "footer", "noscript"]):
            s.decompose()

        lines = []
        for el in soup_copy.descendants:
            if not hasattr(el, "name") or not el.name:
                continue
            if el.name in ["h1", "h2", "h3", "h4"]:
                t = el.get_text(" ", strip=True)
                if t:
                    lines.append(f"## {t}")
            elif el.name == "li":
                t = el.get_text(" ", strip=True)
                if t:
                    lines.append(f"- {t}")
            elif el.name == "p":
                if not el.find(["h1", "h2", "h3", "h4", "ul", "ol", "li"]):
                    t = el.get_text(" ", strip=True)
                    if t:
                        lines.append(t)

        return "\n".join(lines)

    def parse_drops(self, text: str, api_key: str | None = None, html: str | None = None) -> tuple[list[dict], list[dict]]:
        """
        Parses drops, cards, prices, and bundles from announcement text or HTML.
        Returns a tuple of (drops_list, bundles_list).
        Falls back to Gemini intelligent parsing if deterministic parsers yield 0 drops.
        """
        # 1. Direct HTML parsing if html provided or if text contains HTML markup
        if html:
            try:
                drops, bundles = self._parse_drops_html(html)
                if len(drops) >= 1:
                    return drops, bundles
            except Exception as e:
                logger.warning(f"HTML drop parsing encountered error: {e}")
        elif text and ("<article" in text or "<h2" in text or "<html" in text or "<!doctype" in text.lower()):
            try:
                drops, bundles = self._parse_drops_html(text)
                if len(drops) >= 1:
                    return drops, bundles
            except Exception as e:
                logger.warning(f"HTML text drop parsing encountered error: {e}")

        # 2. Deterministic text parsing
        drops, bundles = self._parse_drops_deterministic(text)

        # 3. If deterministic regex failed to find valid drops, fall back to Gemini
        if len(drops) == 0 and api_key:
            logger.info("Deterministic drop parser found 0 drops. Falling back to Gemini extraction...")
            drops, bundles = self._parse_drops_with_gemini(text, api_key)

        return drops, bundles

    def _parse_drops_html(self, html_or_soup) -> tuple[list[dict], list[dict]]:
        """Extracts drops and bundles directly from HTML DOM structures."""
        soup = html_or_soup if hasattr(html_or_soup, "find_all") else BeautifulSoup(html_or_soup, "html.parser")
        article = (
            soup.find("article")
            or soup.find("div", class_=lambda c: c and "article" in (c if isinstance(c, str) else " ".join(c)).lower())
            or soup.body
            or soup
        )
        drops = []
        bundles = []

        skip_titles = (
            "footer", "social", "where to find", "statement", "bulletin",
            "company", "find a store", "sign up", "terms", "overview", "superdrop"
        )
        bundle_keywords = ("bundle", "everything", "all-in", "superdrop all-in")

        headings = article.find_all(["h2", "h3"])
        seen_drop_names = set()
        seen_bundle_names = set()

        for h in headings:
            name = h.get_text(strip=True).replace("\u00ae", "").replace("\u2122", "").replace("\u2019", "'").strip()
            if not name:
                continue
            lower = name.lower()
            if any(k in lower for k in skip_titles):
                continue

            is_bundle = any(kw in lower for kw in bundle_keywords)

            contents_items = []
            price_nonfoil = None
            price_foil = None
            single_price = None

            curr = h.next_sibling
            in_price = False

            while curr:
                if getattr(curr, "name", None) in ["h1", "h2", "h3"]:
                    break
                if hasattr(curr, "get_text"):
                    txt = curr.get_text(" ", strip=True)
                    if "price" in txt.lower():
                        in_price = True
                    if getattr(curr, "name", None) in ["ul", "ol"]:
                        for li in curr.find_all("li"):
                            li_txt = li.get_text(" ", strip=True)
                            if in_price or "$" in li_txt:
                                m = re.search(r"(\d+\.\d{2})", li_txt)
                                if m:
                                    val = float(m.group(1))
                                    if "non-foil" in li_txt.lower() or "nonfoil" in li_txt.lower():
                                        price_nonfoil = val
                                    elif "foil" in li_txt.lower():
                                        price_foil = val
                                    elif single_price is None:
                                        single_price = val
                            else:
                                contents_items.append(li_txt)
                    elif in_price or "$" in txt:
                        m = re.search(r"(\d+\.\d{2})", txt)
                        if m:
                            val = float(m.group(1))
                            if "non-foil" in txt.lower() or "nonfoil" in txt.lower():
                                price_nonfoil = val
                            elif "foil" in txt.lower():
                                price_foil = val
                            elif single_price is None:
                                single_price = val
                curr = curr.next_sibling

            if is_bundle:
                if single_price is not None:
                    if "foil" in lower and "non" not in lower:
                        price_foil = single_price
                    elif "non" in lower:
                        price_nonfoil = single_price
                    else:
                        price_nonfoil = single_price

                b_key = name.lower()
                if b_key not in seen_bundle_names:
                    seen_bundle_names.add(b_key)
                    bundles.append({
                        "bundle_name": name,
                        "price_nonfoil": price_nonfoil,
                        "price_foil": price_foil,
                        "price": single_price,
                        "contents_summary": f"{len(contents_items)} items" if contents_items else "",
                    })
            else:
                cards = []
                for item in contents_items:
                    parsed_card = self._parse_card_line(item)
                    if parsed_card:
                        cards.append(parsed_card)

                if len(cards) >= 1:
                    d_key = name.lower()
                    if d_key not in seen_drop_names:
                        seen_drop_names.add(d_key)
                        drops.append({
                            "drop_name": name,
                            "cards": cards,
                            "price_nonfoil": price_nonfoil or 29.99,
                            "price_foil": price_foil or 39.99,
                            "currency": "USD",
                        })

        return drops, bundles

    @staticmethod
    def _parse_card_line(line: str) -> dict | None:
        """Parses a single line into canonical name, flavor alias, quantity, and notes."""
        clean = line.strip()
        if not clean:
            return None

        # Ignore obvious section titles/headers or prose
        lower = clean.lower()
        if lower in ["contents", "contents:", "price", "price:", "foil", "non-foil", "usd", "release date"]:
            return None
        if len(clean) > 175 or clean.endswith("."):
            return None
        if any(lower.startswith(s) for s in ["you may notice", "please note", "for more", "don't miss", "if you", "check out", "art by", "learn more"]):
            return None
        if any(w in lower for w in ["originates from", "two-card scene", "highlighted above", "for details and terms", "while supplies last"]):
            return None

        qty = 1
        m_qty = re.match(r"^[-•*]?\s*(\d+)x\s+(.+)$", clean, re.IGNORECASE)
        m_bullet = re.match(r"^[-•*]\s+(.+)$", clean)
        if m_qty:
            qty = int(m_qty.group(1))
            clean = m_qty.group(2).strip()
        elif m_bullet:
            clean = m_bullet.group(1).strip()
        else:
            # If line doesn't start with quantity or bullet:
            # Only accept if it looks like a valid card name line
            if " as " not in lower and not re.search(r"\((?:full art|borderless|textless|double sided|reversible)\)", lower):
                if len(clean) > 50 or re.search(r"[,:;!]", clean):
                    return None

        # Check for extra notes in parenthesis at the end (e.g. "(Full Art, Textless)")
        extra_tag = ""
        m_tag = re.search(r"\s*\(([^)]+)\)$", clean)
        if m_tag:
            extra_tag = m_tag.group(1).strip()
            clean = clean[:m_tag.start()].strip()

        # Check for alias: "Card Name as 'Flavor Alias'"
        flavor_name = ""
        m_alias = re.search(r'\s+as\s+[\"“\']?(.*?)[\"”\']?$', clean, re.IGNORECASE)
        if m_alias:
            flavor_name = m_alias.group(1).strip(' "\'“”')
            canonical_name = clean[:m_alias.start()].strip()
        else:
            canonical_name = clean.strip()

        # Clean up any residual symbols
        canonical_name = canonical_name.replace("\u00ae", "").replace("\u2122", "").replace("\u2019", "'").strip()
        flavor_name = flavor_name.replace("\u00ae", "").replace("\u2122", "").replace("\u2019", "'").strip()

        if not canonical_name or len(canonical_name) < 2 or canonical_name.endswith("."):
            return None

        return {
            "canonical_name": canonical_name,
            "flavor_name": flavor_name,
            "quantity": qty,
            "extra_tag": extra_tag,
        }

    def _parse_drops_deterministic(self, text: str) -> tuple[list[dict], list[dict]]:
        """Extracts drops and bundles using structured regex patterns and content boundaries."""
        drops = []
        bundles = []

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        current_drop = None
        current_bundle = None
        mode = None  # "drop" or "bundle"
        in_contents = False

        drop_prefixes = (
            "secret lair x", "secret lair:", "drop:", "artist series:",
            "featuring:", "special guest:", "showcase:"
        )
        bundle_keywords = ("bundle", "everything", "all-in", "superdrop all-in")
        skip_titles = (
            "footer", "social", "where to find", "statement", "bulletin",
            "company", "find a store", "sign up", "terms", "overview",
            "checklist", "please note", "superdrop"
        )

        i = 0
        while i < len(lines):
            line = lines[i]
            lower_line = line.lower()

            # Track contents section
            if lower_line.startswith("contents"):
                in_contents = True
                i += 1
                continue
            elif lower_line.startswith("price") or "$" in line:
                in_contents = False

            # Check if line is a header candidate
            is_md_header = bool(re.match(r"^#{1,4}\s+", line))
            clean_title = re.sub(r"^#{1,4}\s+", "", line).replace("\u00ae", "").replace("\u2122", "").replace("\u2019", "'").strip()
            lower_title = clean_title.lower()

            has_contents_next = False
            for offset in range(1, 3):
                if i + offset < len(lines) and re.match(r"^contents\b", lines[i + offset], re.IGNORECASE):
                    has_contents_next = True
                    break

            is_disqualified = (
                lower_title.startswith("price")
                or lower_title.startswith("contents")
                or "$" in line
                or line.startswith(("-", "•", "*"))
                or bool(re.match(r"^\d+x\b", line, re.IGNORECASE))
                or any(k in lower_title for k in skip_titles)
                or len(clean_title) >= 90
                or clean_title.endswith(".")
            )

            is_header_candidate = False
            if not is_disqualified:
                if is_md_header:
                    is_header_candidate = True
                elif has_contents_next:
                    is_header_candidate = True
                elif any(lower_title.startswith(pfx) for pfx in drop_prefixes):
                    is_header_candidate = True
                elif any(kw in lower_title for kw in bundle_keywords):
                    is_header_candidate = True

            if is_header_candidate:
                is_bundle = any(kw in lower_title for kw in bundle_keywords)

                if current_drop and len(current_drop.get("cards", [])) >= 1:
                    drops.append(current_drop)
                    current_drop = None
                if current_bundle:
                    bundles.append(current_bundle)
                    current_bundle = None

                if is_bundle:
                    current_bundle = {
                        "bundle_name": clean_title,
                        "price_nonfoil": None,
                        "price_foil": None,
                        "price": None,
                        "contents_summary": "",
                    }
                    mode = "bundle"
                else:
                    current_drop = {
                        "drop_name": clean_title,
                        "cards": [],
                        "price_nonfoil": 29.99,
                        "price_foil": 39.99,
                        "currency": "USD",
                    }
                    mode = "drop"
                in_contents = False
                i += 1
                continue

            if mode == "drop" and current_drop:
                if "non-foil" in lower_line or "nonfoil" in lower_line:
                    m = re.search(r"(\d+\.\d{2})", line)
                    if m:
                        try:
                            current_drop["price_nonfoil"] = float(m.group(1))
                        except Exception:
                            pass
                elif "foil" in lower_line:
                    m = re.search(r"(\d+\.\d{2})", line)
                    if m:
                        try:
                            current_drop["price_foil"] = float(m.group(1))
                        except Exception:
                            pass
                else:
                    parsed_card = self._parse_card_line(line)
                    if parsed_card:
                        current_drop["cards"].append(parsed_card)

            elif mode == "bundle" and current_bundle:
                if "non-foil" in lower_line or "nonfoil" in lower_line:
                    m = re.search(r"(\d+\.\d{2})", line)
                    if m:
                        try:
                            current_bundle["price_nonfoil"] = float(m.group(1))
                        except Exception:
                            pass
                elif "foil" in lower_line and "non" not in lower_line:
                    m = re.search(r"(\d+\.\d{2})", line)
                    if m:
                        try:
                            current_bundle["price_foil"] = float(m.group(1))
                        except Exception:
                            pass
                elif "$" in line:
                    m = re.search(r"(\d+\.\d{2})", line)
                    if m:
                        try:
                            val = float(m.group(1))
                            b_low = current_bundle["bundle_name"].lower()
                            current_bundle["price"] = val
                            if "foil" in b_low and "non" not in b_low:
                                current_bundle["price_foil"] = val
                            elif "non" in b_low:
                                current_bundle["price_nonfoil"] = val
                            else:
                                if current_bundle["price_nonfoil"] is None:
                                    current_bundle["price_nonfoil"] = val
                        except Exception:
                            pass

            i += 1

        if current_drop and len(current_drop.get("cards", [])) >= 1:
            drops.append(current_drop)
        if current_bundle:
            bundles.append(current_bundle)

        # De-duplicate drops by name
        unique_drops = []
        seen_drop_names = set()
        for d in drops:
            key = d["drop_name"].lower()
            if key not in seen_drop_names and len(d["cards"]) >= 1:
                seen_drop_names.add(key)
                unique_drops.append(d)

        unique_bundles = []
        seen_bundle_names = set()
        for b in bundles:
            key = b["bundle_name"].lower()
            if key not in seen_bundle_names:
                seen_bundle_names.add(key)
                unique_bundles.append(b)

        return unique_drops, unique_bundles

    def _parse_drops_with_gemini(self, text: str, api_key: str) -> tuple[list[dict], list[dict]]:
        """Fallback LLM parser to extract drop JSON from irregular layouts using cost-effective flash-lite model."""
        target_model = get_model_for_task("data_extraction")
        url = f"{GEMINI_API_BASE}/{target_model}:generateContent?key={api_key}"
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
        gen_config = {"temperature": 0.1, "maxOutputTokens": 4096}
        if "gemini-3" in target_model:
            gen_config["thinkingConfig"] = {"thinkingLevel": "low"}

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        for attempt in range(2):
            try:
                resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=25)
                if resp.status_code == 200:
                    candidates = resp.json().get("candidates", [])
                    if candidates:
                        raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
                        clean = re.sub(r"\s*```$", "", clean).strip()
                        parsed = json.loads(clean)
                        return parsed.get("drops", []), parsed.get("bundles", [])
                elif resp.status_code in (429, 503) and attempt == 0:
                    time.sleep(1)
                    continue
                else:
                    logger.warning(f"Gemini drop parsing ({target_model}) returned HTTP {resp.status_code}")
                    break
            except Exception as e:
                if attempt == 0:
                    time.sleep(1)
                    continue
                logger.error(f"Gemini drop parsing fallback failed on {target_model}: {e}")

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
        self.requested_model = raw_model
        if raw_model and str(raw_model).strip().lower() in ("auto", "default", "none", ""):
            self.model = get_model_for_task("fleet_synergy")
        else:
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
                if b.get("price") and not (b.get("price_nonfoil") and b.get("price_foil")):
                    bundle_lines.append(f"  • {bname}: Bundle Price ${b['price']}")
                elif b.get("price_nonfoil") and b.get("price_foil"):
                    bundle_lines.append(f"  • {bname}: Non-foil ${b['price_nonfoil']} | Foil ${b['price_foil']}")
                elif b.get("price_foil"):
                    bundle_lines.append(f"  • {bname}: Foil ${b['price_foil']}")
                elif b.get("price_nonfoil"):
                    bundle_lines.append(f"  • {bname}: Non-foil ${b['price_nonfoil']}")
                else:
                    bundle_lines.append(f"  • {bname}: Price N/A")
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

        # Direct dispatch to optimal fleet synergy model with transient retries
        clean_key = self.api_key.strip()
        target_model = get_model_for_task("fleet_synergy", preferred_model=self.model)
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

        max_retries = 2
        last_err = ""
        last_status = 0

        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff_sec = 1.0 * attempt
                logger.info(f"Retrying Secret Lair Gemini call ({target_model}) attempt {attempt + 1}/{max_retries + 1} after {backoff_sec}s delay...")
                time.sleep(backoff_sec)

            try:
                resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=90)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        last_err = "Empty candidates"
                        last_status = 200
                        continue

                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        last_err = "Empty parts"
                        last_status = 200
                        continue

                    raw_text = parts[0].get("text", "").strip()
                    parsed = self._clean_and_parse_json(raw_text)
                    parsed["_model_used"] = target_model
                    parsed["_analyzed_at"] = datetime.now(timezone.utc).isoformat()
                    self.model = target_model
                    return parsed
                else:
                    last_status = resp.status_code
                    last_err = resp.text[:200]
                    logger.warning(f"Secret Lair model '{target_model}' failed (HTTP {resp.status_code}): {last_err}")
                    if resp.status_code in (429, 500, 503) and attempt < max_retries:
                        continue
                    break
            except Exception as e:
                last_err = str(e)
                last_status = 0
                logger.warning(f"Secret Lair model '{target_model}' exception: {e}")
                if attempt < max_retries:
                    continue
                break

        sc_str = f"HTTP {last_status}: " if last_status else ""
        raise GeminiAnalysisError(
            f"Gemini fleet analysis failed on model '{target_model}': {sc_str}{last_err}"
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
