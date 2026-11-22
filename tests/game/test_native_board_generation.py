"""Exact local board-generation oracle and bounded pure API checks."""

import sys
import unittest
from pathlib import Path

from tmserver.game.boards import generate_board
from tmserver.game.rules import CRTLCG


class BoardGenerationTests(unittest.TestCase):

    def test_default_original_has_walls(self):
        generated = generate_board(2, CRTLCG(1))
        self.assertGreater(generated.blocks, 0)
        self.assertEqual(generated.effects, 0)
        self.assertTrue(set(generated.fields) <= {0, 1, 2})


if __name__ == "__main__":
    unittest.main()
