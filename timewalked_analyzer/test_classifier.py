"""Unit tests for the Timewalked-style Commander Deck Classifier."""

from __future__ import annotations

import unittest

from .api_clients import parse_decklist_text, slugify_commander
from .classifier import DeckClassifier


class TestTimewalkedAnalyzer(unittest.TestCase):
    def test_slugify_commander(self) -> None:
        self.assertEqual(slugify_commander("Atraxa, Praetors' Voice"), "atraxa-praetors-voice")
        self.assertEqual(slugify_commander("Urza, Lord High Artificer"), "urza-lord-high-artificer")
        self.assertEqual(
            slugify_commander("Esika, God of the Tree // The Prismatic Bridge"),
            "esika-god-of-the-tree",
        )

    def test_parse_decklist_text(self) -> None:
        raw = """
        Commander
        1 Omnath, Locus of Mana (CMM) 123 *CMDR*

        Deck
        1 Sol Ring [Ramp]
        1 Craterhoof Behemoth
        35 Forest
        """
        cmdrs, main = parse_decklist_text(raw)
        self.assertEqual(cmdrs, ["Omnath, Locus of Mana"])
        self.assertEqual(main["Sol Ring"], 1)
        self.assertEqual(main["Craterhoof Behemoth"], 1)
        self.assertEqual(main["Forest"], 35)

    def test_bracket_2_plays_like_high_2(self) -> None:
        classifier = DeckClassifier(offline=True)
        _, main = parse_decklist_text(
            """
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
            1 Craterhoof Behemoth
            1 Finale of Devastation
            1 Genesis Wave
            1 Overwhelming Stampede
            36 Forest
            """
        )
        res = classifier.classify(
            commanders=["Omnath, Locus of Mana"],
            mainboard=main,
            deck_name="Big Green Man (on a budget)",
        )
        self.assertEqual(res.on_paper_bracket, 2)
        self.assertGreaterEqual(res.feels_like_score, 2.5)
        self.assertLessEqual(res.feels_like_score, 3.1)

    def test_cedh_deck_scores_bracket_5(self) -> None:
        classifier = DeckClassifier(offline=True)
        _, main = parse_decklist_text(
            """
            1 Sol Ring
            1 Mana Vault
            1 Grim Monolith
            1 Chrome Mox
            1 Mox Diamond
            1 Lion's Eye Diamond
            1 Thassa's Oracle
            1 Demonic Consultation
            1 Tainted Pact
            1 Underworld Breach
            1 Brain Freeze
            1 Demonic Tutor
            1 Vampiric Tutor
            1 Imperial Seal
            1 Mystical Tutor
            1 Rhystic Study
            1 Mystic Remora
            1 Necropotence
            1 Ad Nauseam
            1 Fierce Guardianship
            1 Force of Will
            1 Force of Negation
            1 Pact of Negation
            30 Island
            """
        )
        res = classifier.classify(
            commanders=["Tymna the Weaver", "Kraum, Ludevic's Opus"],
            mainboard=main,
            deck_name="Blue Farm cEDH",
        )
        self.assertEqual(res.on_paper_bracket, 5)
        self.assertGreaterEqual(res.feels_like_score, 4.5)
        self.assertGreaterEqual(len(res.combos), 2)


if __name__ == "__main__":
    unittest.main()
