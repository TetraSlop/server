"""Portable NPC identity and visibility contracts from the original records."""

import unittest

from tmserver.economy.service import Catalog
from tmserver.game.opponents import OpponentError, opponent, prepare_opponent


class OpponentTests(unittest.TestCase):
    def test_API_rejects_reserved_ID_and_preserves_known_NPC_names(self):
        for selector in (0, 31, -1, True):
            with self.subTest(selector=selector), self.assertRaises(OpponentError):
                opponent(selector)
        self.assertEqual(opponent(1).name, b"Flower Girl Natasha")
        self.assertEqual(opponent(3).name, b"Quina")
        self.assertEqual(opponent(30).name, b"Regent Cid")
        catalog = Catalog()
        for guild in (-1, 4, True):
            with self.subTest(guild=guild), self.assertRaises(OpponentError):
                prepare_opponent(1, catalog, lambda: 0, guild)

    def test_collection_points_control_name_visibility(self):
        self.assertEqual(opponent(1).known_name(0), b"Flower Girl Natasha")
        for selector, threshold, name in ((3, 20, b"Quina"), (30, 2000, b"Regent Cid")):
            with self.subTest(selector=selector):
                npc = opponent(selector)
                self.assertIsNone(npc.known_name(threshold - 1))
                self.assertEqual(npc.known_name(threshold), name)
                self.assertEqual(npc.known_name(65535), name)
        for points in (-1, 65536, True, 20.0, "20", None):
            with self.subTest(points=points), self.assertRaises(OpponentError):
                opponent(3).known_name(points)


if __name__ == "__main__":
    unittest.main()
