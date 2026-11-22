"""Portable NPC identity contracts from the original records."""

import unittest

from tmserver.game.opponents import OpponentError, opponent


class OpponentTests(unittest.TestCase):
    def test_API_rejects_reserved_ID_and_preserves_known_NPC_names(self):
        for selector in (0, 31, -1, True):
            with self.subTest(selector=selector), self.assertRaises(OpponentError):
                opponent(selector)
        self.assertEqual(opponent(1).name, b"Flower Girl Natasha")
        self.assertEqual(opponent(3).name, b"Quina")
        self.assertEqual(opponent(30).name, b"Regent Cid")


if __name__ == "__main__":
    unittest.main()
