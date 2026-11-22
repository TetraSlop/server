"""Golden pack generation over the whole decoded catalogue."""

import hashlib
import unittest

from tmserver.cards.catalog import Catalog
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
                    digest.update(catalog.generate(catalog.select(pack, guild, draw), draw).compact())
        self.assertEqual(digest.hexdigest(), PACK_GENERATION)


if __name__ == "__main__":
    unittest.main()
