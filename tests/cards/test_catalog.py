"""Golden pack generation over the whole decoded catalogue."""

import hashlib
import unittest

from tmserver.cards.catalog import Catalog
from tmserver.cards.model import Card
from tmserver.game.rules import CRTLCG

# SHA-256 of every pack and guild drawing ten cards from CRTLCG(seed=pack*4+guild).
PACK_GENERATION = "663f4e19d65ddbec069361ea1ce5367b26ac677bf1fe5f78d23354219560eeb9"


class CatalogGenerationTests(unittest.TestCase):
    def test_every_pack_and_guild_matches_golden_generation(self):
        catalog = Catalog()
        digest = hashlib.sha256()
        for pack in range(len(catalog.packs)):
            for guild in range(4):
                draw = CRTLCG(pack * 4 + guild)
                for _ in range(10):
                    digest.update(
                        catalog.generate(catalog.select(pack, guild, draw), draw).compact()
                    )
        self.assertEqual(digest.hexdigest(), PACK_GENERATION)


# (model, attack, kind, physical, magical, client sale value). The client's
# stored-card routine at 24-bit precision, for cards where the exact rational
# formula, the bridge at 53 or 64 bits, or the growth routine's summation order
# at 24 bits gives another value.
CLIENT_SALE_VALUES = (
    # below the exact rational value
    (1, 2, 3, 3, 4, 19),
    (9, 9, 1, 7, 4, 19),
    (23, 55, 1, 25, 31, 5299),
    (25, 16, 1, 27, 67, 494),
    (29, 12, 1, 12, 16, 52),
    (35, 27, 1, 14, 14, 134),
    (43, 27, 3, 19, 19, 104),
    (54, 55, 2, 50, 66, 8124),
    # above it
    (50, 26, 1, 40, 14, 348),
    (53, 47, 1, 25, 73, 1551),
    (64, 43, 3, 21, 37, 677),
    (65, 64, 3, 51, 31, 1046),
    (68, 43, 2, 19, 42, 598),
    (82, 38, 2, 32, 61, 1073),
    (96, 41, 2, 17, 29, 586),
    (103, 29, 3, 22, 34, 606),
    # equal to it, one below at 53 bits
    (16, 10, 2, 11, 14, 45),
    (23, 55, 1, 25, 34, 5450),
    (25, 18, 1, 27, 67, 510),
    (28, 15, 3, 13, 4, 45),
    # equal to it, one below at 64 bits or in the growth order
    (1, 4, 3, 3, 2, 20),
    (10, 4, 2, 4, 9, 20),
    (23, 55, 1, 27, 30, 5350),
    (28, 17, 3, 9, 6, 46),
    (28, 17, 3, 11, 5, 46),
)


class CatalogSaleValueTests(unittest.TestCase):
    def test_sale_value_matches_client_where_other_arithmetic_differs(self):
        catalog = Catalog()
        for model, attack, kind, physical, magical, value in CLIENT_SALE_VALUES:
            card = Card(model, attack, kind, physical, magical, 0, 1)
            with self.subTest(card=card):
                self.assertEqual(catalog.sale_value(card), value)


if __name__ == "__main__":
    unittest.main()
