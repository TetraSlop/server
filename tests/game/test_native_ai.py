"""Pure-policy checks and comparisons executing original TM x86 instructions.

The oracle keeps original hand/placement/score/target instructions, adjacency
tables and catalogue lookup. Only CRT rand inputs and existing presentation
boundaries are intercepted. No native process, UI, service or network is used.
"""

import json
import random
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

from tests import FIXTURES
from tmserver.game.ai import (
    BLOCKED,
    PRICE_TIERS,
    AIError,
    Cell,
    Move,
    Personality,
    choose_move,
    choose_target,
    placement_scores,
    poll_hand,
    poll_placement,
    poll_target,
)



@dataclass(frozen=True)
class Card:
    model: int = 0
    arrows: int = 0
    attack: int = 1
    kind: int = 0
    physical: int = 1
    magical: int = 1


class Draws:
    def __init__(self, values):
        self.values = list(values)
        self.used = []

    def __call__(self):
        if not self.values:
            raise AssertionError("policy consumed an unexpected random draw")
        result = self.values.pop(0)
        self.used.append(result)
        return result


def boards():
    empty = (None,) * 16
    yield "empty", empty
    for label, arrows in (("direct", 0), ("battle", 16)):
        board = list(empty)
        board[1] = Cell(Card(23, arrows), 1)
        board[6] = Cell(Card(54, 64), 1)
        board[2] = Cell(Card(162, 0), 1)  # tier16 wraps to0 in score preference1.
        board[4] = Cell(Card(149, 255), 0)
        board[15] = BLOCKED
        yield label, tuple(board)
    board = [BLOCKED] * 16
    board[9] = None
    yield "single_empty", tuple(board)
    rng = random.Random(611)
    for index in range(16):
        board = tuple(
            (
                None
                if (r := rng.randrange(10)) < 4
                else (
                    BLOCKED
                    if r == 4
                    else Cell(Card(rng.randrange(250), rng.randrange(256)), rng.randrange(2))
                )
            )
            for _ in range(16)
        )
        if all(cell is not None for cell in board):
            board = (None,) + board[1:]
        yield f"seeded_{index}", board


class NativeAIOriginalTests(unittest.TestCase):
    def test_catalogue_tiers_match_decoded_original_asset(self):
        catalog = json.loads((FIXTURES / "protocol/economy_catalog.json").read_text())
        self.assertEqual(PRICE_TIERS, tuple(row["price_tier"] for row in catalog["cards"]))


class NativeAIPolicyTests(unittest.TestCase):
    def test_tied_empty_placements_preserve_ascending_field_order(self):
        for choice in (0, 1, 15, 16, 32767):
            self.assertEqual(
                poll_placement(
                    Card(0, 255), (None,) * 16, 0, Draws([0, 0, 0, choice]), denominator=1
                ),
                choice % 16,
            )

    def test_direct_capture_and_reciprocal_combo_marks(self):
        board = [None] * 16
        board[1] = Cell(Card(0, 0), 1)
        self.assertEqual(dict(placement_scores(Card(0, 1), board, 0))[5], 1)
        board[1] = Cell(Card(0, 20), 1)  # Down reciprocal; right combo.
        board[2] = Cell(Card(0, 0), 1)
        self.assertEqual(dict(placement_scores(Card(0, 1), board, 0))[5], 2)
        self.assertEqual(dict(placement_scores(Card(0, 1), board, 0, 2))[5], 17)
        self.assertEqual(dict(placement_scores(Card(0, 1), board, 0, 4))[5], 17)
        board[2] = Cell(Card(0, 0), 0)
        self.assertEqual(dict(placement_scores(Card(0, 1), board, 0))[5], 1)

    def test_wrapped_tier_and_unmarked_cells_match_literal_scoring(self):
        board = [None] * 16
        board[1] = Cell(Card(162, 0), 1)  # Price tier16 -> marker high nibble0.
        self.assertEqual(dict(placement_scores(Card(0, 1), board, 0, 1))[5], 1)
        board[1] = Cell(Card(54, 0), 1)  # Price tier22 -> high nibble6.
        self.assertEqual(dict(placement_scores(Card(0, 1), board, 0, 1))[5], 22)
        self.assertEqual(dict(placement_scores(Card(), (None,) * 16, 0, 3))[5], 32)

    def test_choices_do_not_change_inputs_or_use_battle_stats(self):
        hand = (Card(1, 5), Card(2, 255))
        board = tuple(list(boards())[1][1])
        before = repr((hand, board))
        move = choose_move(hand, board, 0, Draws([123, 1, 22, 0, 0, 7]))
        self.assertEqual(repr((hand, board)), before)
        changed_stats = tuple(Card(c.model, c.arrows, 255, 3, 255, 255) for c in hand)
        self.assertEqual(choose_move(changed_stats, board, 0, Draws([123, 1, 22, 0, 0, 7])), move)

    def test_empty_and_blocked_boundaries_do_not_draw(self):
        self.assertIsNone(choose_move((), (None,) * 16, 0, Draws([])))
        self.assertIsNone(choose_move((Card(),), (BLOCKED,) * 16, 0, Draws([])))
        self.assertIsNone(poll_hand((), Draws([])))
        self.assertIsNone(poll_placement(Card(), (BLOCKED,) * 16, 0, Draws([])))
        self.assertIsNone(choose_target((), Draws([])))
        board = [BLOCKED] * 16
        board[6] = None
        self.assertEqual(choose_move((Card(),), board, 0, Draws([0] * 6)), Move(0, 6))

    def test_input_validation(self):
        for value in (-1, 256, True):
            with self.assertRaises(AIError):
                Personality(score_chance=value)
        for denominator in (0, -1, 0x80000000):
            with self.assertRaises(AIError):
                poll_hand((Card(),), Draws([]), denominator)
        for invalid in (-1, 32768, True):
            with self.assertRaises(AIError):
                poll_hand((Card(),), Draws([invalid]))
        for hand, board, owner in (
            ((Card(0x8009),), (None,) * 16, 0),
            ((Card(),), (None,) * 25, 0),
            ((Card(),), (None,) * 16, 2),
            ((Card(),) * 6, (None,) * 16, 0),
        ):
            with self.assertRaises(AIError):
                choose_move(hand, board, owner, Draws([]))
        for targets in ((1, 1), (-1,), (16,), tuple(range(9))):
            with self.assertRaises(AIError):
                choose_target(targets, Draws([]))


if __name__ == "__main__":
    unittest.main()
