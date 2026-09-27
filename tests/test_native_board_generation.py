"""Exact local board-generation oracle and bounded pure API checks."""

import sys
import unittest
from pathlib import Path

from tmserver.native_board_generation import BoardGenerationError, generate_board
from tmserver.native_rules import CRTLCG


class BoardGenerationTests(unittest.TestCase):

    def test_bad_inputs_validate_before_randomness_and_default_original_has_walls(self):
        def forbidden():
            raise AssertionError("unexpected random draw")

        for n in (0, 1, 4, True):
            with self.assertRaises(BoardGenerationError):
                generate_board(n, forbidden)
        for field in ("st", "cb", "ca"):
            for value in (-1, 2, True):
                with self.assertRaises(BoardGenerationError):
                    generate_board(2, forbidden, **{field: value})
        for value in (-1, 32768, True):
            with self.assertRaises(BoardGenerationError):
                generate_board(2, lambda: value)
        generated = generate_board(2, CRTLCG(1))
        self.assertGreater(generated.blocks, 0)
        self.assertEqual(generated.effects, 0)
        self.assertTrue(set(generated.fields) <= {0, 1, 2})


if __name__ == "__main__":
    unittest.main()
