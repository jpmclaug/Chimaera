"""CLI interface for the Timewalked-style Commander Analyzer."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .api_clients import (
    ArchidektClient,
    EdhrecClient,
    HttpJsonClient,
    parse_decklist_text,
)
from .classifier import DeckAnalysisResult, DeckClassifier

SAMPLE_DECKS: list[dict[str, str]] = [
    {
        "name": "Big Green Man (on a budget)",
        "commander": "Omnath, Locus of Mana",
        "list": """
1 Sol Ring
1 Arcane Signet
1 Llanowar Elves
1 Elvish Mystic
1 Fyndhorn Elves
1 Birds of Paradise
1 Nature's Lore
1 Three Visits
1 Cultivate
1 Kodama's Reach
1 Rampant Growth
1 Sakura-Tribe Elder
1 Nissa, Who Shakes the World
1 Sylvan Library
1 Beast Whisperer
1 Guardian Project
1 Craterhoof Behemoth
1 Finale of Devastation
1 Genesis Wave
1 Overwhelming Stampede
1 Beast Within
1 Nature's Claim
1 Boseiju, Who Endures
36 Forest
""",
    },
    {
        "name": "Upgraded Orzhov Lifedrain (Bracket 3)",
        "commander": "Vito, Thorn of the Dusk Rose",
        "list": """
1 Sol Ring
1 Arcane Signet
1 Fellwar Stone
1 Mind Stone
1 Thought Vessel
1 Demonic Tutor
1 Smothering Tithe
1 Bolas's Citadel
1 Esper Sentinel
1 Night's Whisper
1 Sign in Blood
1 Exquisite Blood
1 Sanguine Bond
1 Torment of Hailfire
1 Exsanguinate
1 Swords to Plowshares
1 Path to Exile
1 Generous Gift
1 Anguished Unmaking
35 Swamp
""",
    },
    {
        "name": "Urza PolyKraken / Scepter (High Bracket 4 / cEDH)",
        "commander": "Urza, Lord High Artificer",
        "list": """
1 Sol Ring
1 Mana Vault
1 Grim Monolith
1 Chrome Mox
1 Mox Diamond
1 Mox Opal
1 Mox Amber
1 Lotus Petal
1 Isochron Scepter
1 Dramatic Reversal
1 Basalt Monolith
1 Rings of Brighthearth
1 Rhystic Study
1 Mystic Remora
1 The One Ring
1 Sensei's Divining Top
1 Mystical Tutor
1 Whir of Invention
1 Reshape
1 Fierce Guardianship
1 Force of Will
1 Force of Negation
1 Pact of Negation
1 Mana Drain
1 Flusterstorm
1 Mental Misstep
1 Swan Song
1 Cyclonic Rift
1 Ancient Tomb
1 Urza's Saga
1 Otawara, Soaring City
28 Island
""",
    },
]


def format_analysis_report(res: DeckAnalysisResult) -> str:
    """Render a clean terminal breakdown mirroring Timewalked's UI."""
    cmdr_str = " // ".join(res.commanders) if res.commanders else "Unknown Commander"
    lines = [
        "=" * 74,
        f"  {res.deck_name}",
        f"  Commander: {cmdr_str}  ({res.total_cards} cards, avg non-land MV {res.avg_cmc_nonland:.2f})",
        "=" * 74,
        f"  ON PAPER:   Bracket {res.on_paper_bracket} · {res.on_paper_label}",
        f"  FEELS LIKE: {res.feels_like_score:.1f}   (Usually threatens a win around turn {res.estimated_win_turn})",
        "-" * 74,
        f"  {res.summary_verdict}",
        "",
        "  THE WHY — ON PAPER RULES:",
    ]
    for reason in res.on_paper_reasons:
        lines.append(f"    • {reason}")

    lines.append("")
    lines.append("  THE WHY — FEELS LIKE RECEIPTS (Base 2.0):")
    if not res.receipts:
        lines.append("    • +0.00 · Standard core deck construction")
    else:
        for r in res.receipts:
            sign = "+" if r.delta >= 0 else ""
            lines.append(f"    • {sign}{r.delta:.2f} · {r.explanation}")

    if res.game_changers:
        lines.append("")
        lines.append(f"  OFFICIAL GAME CHANGERS ({len(res.game_changers)}):")
        lines.append(f"    {', '.join(res.game_changers)}")

    if res.combos:
        lines.append("")
        lines.append(f"  DETECTED COMBOS ({len(res.combos)}):")
        for c in res.combos[:5]:
            cards_str = " + ".join(c.get("cards", []))
            prod_str = ", ".join(c.get("produces", [])[:2])
            b_tag = c.get("bracket_tag", "?")
            lines.append(f"    • {cards_str} -> {prod_str} [Combo Bracket {b_tag}]")

    pkg = res.package_counts
    lines.append("")
    lines.append(
        "  PACKAGES: "
        f"Fast Mana: {pkg.get('fast_mana', 0)} | "
        f"Ramp: {pkg.get('ramp', 0)} | "
        f"Tutors: {pkg.get('tutor', 0)} | "
        f"Draw: {pkg.get('card_draw', 0)} | "
        f"Interaction: {pkg.get('interaction', 0)} | "
        f"Finishers: {pkg.get('finisher', 0)}"
    )
    lines.append("=" * 74)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="timewalked_analyzer",
        description="Programmatic Commander Bracket & 'Feels Like' Deck Analyzer (inspired by timewalked.com)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. demo subcommand
    p_demo = sub.add_parser("demo", help="Run sample Commander decks across Brackets 2, 3, and 4/5")
    p_demo.add_argument("--offline", action="store_true", help="Use built-in card/combo DB without network calls")
    p_demo.add_argument("--json", action="store_true", help="Output JSON instead of formatted text")

    # 2. classify subcommand
    p_cls = sub.add_parser("classify", help="Classify a decklist file or Archidekt URL/ID")
    p_cls.add_argument("source", help="Path to a decklist .txt file, '-' for stdin, or an Archidekt deck URL/ID")
    p_cls.add_argument("--commander", "-c", action="append", default=[], help="Commander name(s) if not marked in file")
    p_cls.add_argument("--name", "-n", default="Custom Commander Deck", help="Deck display name")
    p_cls.add_argument("--offline", action="store_true", help="Run offline using local card/combo database")
    p_cls.add_argument("--json", action="store_true", help="Output JSON")

    # 3. commander subcommand
    p_cmdr = sub.add_parser("commander", help="Fetch average decklist & play rates from EDHREC and classify it")
    p_cmdr.add_argument("commander_name", help="Commander name (e.g., \"Atraxa, Praetors' Voice\")")
    p_cmdr.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args(argv)

    if args.command == "demo":
        classifier = DeckClassifier(offline=args.offline)
        results = []
        for sample in SAMPLE_DECKS:
            _, mainboard = parse_decklist_text(sample["list"])
            res = classifier.classify(
                commanders=[sample["commander"]],
                mainboard=mainboard,
                deck_name=sample["name"],
            )
            results.append(res)
        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2))
        else:
            for r in results:
                print(format_analysis_report(r))
                print()
        return 0

    if args.command == "classify":
        classifier = DeckClassifier(offline=args.offline)
        source: str = args.source
        if "archidekt.com" in source or source.isdigit():
            arch = ArchidektClient(HttpJsonClient(offline=args.offline))
            deck_name, commanders, mainboard = arch.fetch_deck(source)
        else:
            raw_text = sys.stdin.read() if source == "-" else pathlib.Path(source).read_text(encoding="utf-8")
            commanders, mainboard = parse_decklist_text(raw_text)
            deck_name = args.name

        if args.commander:
            commanders = args.commander

        res = classifier.classify(commanders=commanders, mainboard=mainboard, deck_name=deck_name)
        if args.json:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            print(format_analysis_report(res))
        return 0

    if args.command == "commander":
        http = HttpJsonClient(offline=False)
        edhrec = EdhrecClient(http)
        profile = edhrec.get_commander_profile(args.commander_name)
        classifier = DeckClassifier(offline=False)
        res = classifier.classify(
            commanders=[profile["commander"]],
            mainboard=profile["average_deck_mainboard"],
            deck_name=f"EDHREC Average Deck: {profile['commander']}",
        )
        if args.json:
            print(json.dumps({"edhrec_profile": profile, "classification": res.to_dict()}, indent=2))
        else:
            print(f"EDHREC Total Recorded Decks: {profile['total_decks']}")
            print(format_analysis_report(res))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
