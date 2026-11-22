"""Catalogue points and sale value against the original growth routine."""

import random
import unittest

from tmserver.cards.catalog import Catalog
from tmserver.game.original.runtime import default_rules
from tmserver.game.rules import Card, GrowthBaseline


class NativeCardValueTests(unittest.TestCase):
    def test_points_and_sale_value_match_original_growth(self):
        catalog = Catalog()
        rng = random.Random(3)
        low = 0
        for model, row in enumerate(catalog.cards):
            baseline = GrowthBaseline.from_catalog(row, catalog.prices)
            for mode in range(4):
                stats = [rng.randrange(1, row[f] + 1) for f in ("attack", "physical_defense",
                                                               "magical_defense")]
                card = Card(model, stats[0], rng.randrange(4), *stats[1:], 0, 1)
                result = default_rules().grow_card(card, baseline, mode, rng.randrange(256))
                grown = result.card
                with self.subTest(card=card, mode=mode):
                    self.assertEqual(
                        grown.points,
                        catalog.points(model, grown.attack, grown.physical, grown.magical),
                    )
                    # x87 truncation lands one below an exact integer sale value
                    # for a few cards; the catalogue keeps the exact rational.
                    self.assertIn(catalog.sale_value(grown) - result.sale_value, (0, 1))
                    low += catalog.sale_value(grown) != result.sale_value
        self.assertEqual(low, 2)


if __name__ == "__main__":
    unittest.main()
