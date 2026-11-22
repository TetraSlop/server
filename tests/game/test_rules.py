"""Battle and growth arithmetic and its input domain."""

import json
import random
import struct
import sys
import unittest
from dataclasses import astuple, replace
from itertools import product
from pathlib import Path

from tests import ROOT
from tmserver.game.rules import RulesError
from tmserver.game.match import RULES
from tmserver.game.rules import (
    CRTLCG,
    BattleSide,
    Card,
    GrowthBaseline,
)

BASE = ROOT


class RulesAPITests(unittest.TestCase):
    def test_rng_is_explicit_restorable_and_uint32(self):
        rng = CRTLCG(1)
        self.assertEqual([rng() for _ in range(5)], [41, 18467, 6334, 26500, 19169])
        state = rng.state
        expected = [rng() for _ in range(20)]
        rng.state = state
        self.assertEqual([rng() for _ in range(20)], expected)
        self.assertEqual(CRTLCG(-1).state, 0xFFFFFFFF)
        self.assertEqual(CRTLCG(0x100000001).state, 1)

    def test_ties_are_preserved_not_returned_as_defender_wins(self):
        c = Card(0, 10, 0, 10, 10, 1, 0)
        result = RULES.calculate_battle(c, c, iter([8, 8, 8, 8]).__next__)
        self.assertEqual(result.attacker.final, result.defender.final)
        self.assertEqual(result.raw_draws, (8, 8, 8, 8))
        self.assertEqual(astuple(result.attacker), (10, 8, 1, 1))

    def test_zero_fallback_occurs_once_even_if_still_tied(self):
        c = Card(0, 0, 0, 0, 0, 0, 0)
        result = RULES.calculate_battle(c, c, iter([0, 0, 0, 0, 2, 4]).__next__)
        self.assertEqual(result.attacker.start, 1)
        self.assertEqual(result.attacker.final, result.defender.final)
        self.assertEqual(result.raw_draws, (0, 0, 0, 0, 2, 4))

    def test_stat_selector_ties_and_full_property_bytes(self):
        c = Card(0, 50, 3, 50, 50, 1, 0)
        result = RULES.calculate_battle(
            c, c, iter([3, 2, 1, 2, 3, 4]).__next__, attacker_property=0x51, defender_property=0x32
        )
        self.assertEqual((result.attacker.selector, result.defender.selector), (8, 8))
        self.assertEqual((result.attacker.multiplier, result.defender.multiplier), (5, 4))
        self.assertEqual(len(result.raw_draws), 6)

    def test_growth_normalizes_bytes_and_recomputes_even_without_increment(self):
        c = Card(7, 100, 3, 80, 60, 0, 129, 4)
        b = GrowthBaseline(100, 80, 60, 20, 100)
        result = RULES.grow_card(c, b, 257, 256)
        self.assertEqual(result.card, replace(c, points=20))
        self.assertIsNone(result.changed_field)
        self.assertEqual(result.sale_value, 112)
        self.assertEqual(c.points, 0)  # Immutable input.
        self.assertEqual(RULES.grow_card(replace(c, kind=0), b, 256, 259).card.kind, 2)

    def test_growth_preserves_over_cap_stats_arrows_and_deck(self):
        c = Card(0, 255, 1, 255, 255, 1, 255, 49)
        b = GrowthBaseline(10, 20, 30, 255, 17500)
        for r in range(3):
            result = RULES.grow_card(c, b, 1, r)
            self.assertEqual(
                (result.card.attack, result.card.physical, result.card.magical), (255, 255, 255)
            )
            self.assertEqual((result.card.arrows, result.card.deck), (255, 49))
            self.assertIsNone(result.changed_field)
            self.assertEqual(result.card.points, (765 * 255 // 60) & 255)

    def test_invalid_domains_are_explicit(self):
        c = Card(0, 1, 0, 1, 1, 1, 0)
        default = RULES
        for effects in ({"attacker_modifier": 128}, {"defender_property": 256}):
            with self.assertRaisesRegex(RulesError, "outside the client.s domain"):
                default.calculate_battle(c, c, lambda: 0, **effects)
        with self.assertRaisesRegex(RulesError, "Random draw"):
            default.calculate_battle(c, c, lambda: -1)
        with self.assertRaisesRegex(RulesError, "outside the client.s domain"):
            default.grow_card(c, GrowthBaseline(0, 1, 1, 1, 10), 0, 0)

    def test_existing_card_adapters(self):
        from tmserver.cards.model import Card as EconomyCard
        from tmserver.game.models import Card as MatchCard

        values = (5, 20, 1, 30, 40, 2, 129, 4)
        m, e = MatchCard(*values), EconomyCard(*values)
        b = GrowthBaseline(100, 80, 60, 20, 100)
        expected = RULES.grow_card(m, b, 1, 0)
        self.assertEqual(RULES.grow_card(e, b, 1, 0), expected)
        self.assertEqual(MatchCard(*expected.card.full()).attack, 21)
        self.assertEqual(EconomyCard(*expected.card.full()).deck, 4)
        self.assertEqual(RULES.calculate_battle(m, e, iter([1, 2, 3, 4]).__next__).attacker.selector, 1)


if __name__ == "__main__":
    unittest.main()
