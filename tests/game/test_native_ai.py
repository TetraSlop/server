"""Native COM decision input boundaries and immutable caller state."""

import unittest
from dataclasses import dataclass

from tmserver.game.ai import (
    BLOCKED,
    AIError,
    Cell,
    Move,
    Personality,
)
from tmserver.game.original.runtime import default_rules


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


class NativeAIPolicyTests(unittest.TestCase):
    def test_tied_empty_native_placements_keep_ascending_field_order(self):
        for choice in (0, 1, 15, 16, 32767):
            with self.subTest(choice=choice):
                self.assertEqual(
                    default_rules().choose_move((Card(0, 255),), (None,) * 16, 0, Draws([0, 0, 0, 0, 0, choice])),
                    Move(0, choice % 16),
                )

    def test_choices_do_not_change_inputs_or_use_battle_stats(self):
        hand = (Card(1, 5), Card(2, 255))
        cells = [None] * 16
        cells[1] = Cell(Card(23, 0), 1)
        cells[6] = Cell(Card(54, 64), 1)
        cells[2] = Cell(Card(162, 0), 1)
        cells[4] = Cell(Card(149, 255), 0)
        cells[15] = BLOCKED
        board = tuple(cells)
        before = repr((hand, board))
        move = default_rules().choose_move(hand, board, 0, Draws([123, 1, 22, 0, 0, 7]))
        self.assertEqual(repr((hand, board)), before)
        changed_stats = tuple(Card(c.model, c.arrows, 255, 3, 255, 255) for c in hand)
        self.assertEqual(default_rules().choose_move(changed_stats, board, 0, Draws([123, 1, 22, 0, 0, 7])), move)

    def test_empty_and_blocked_boundaries_do_not_draw(self):
        self.assertIsNone(default_rules().choose_move((), (None,) * 16, 0, Draws([])))
        self.assertIsNone(default_rules().choose_move((Card(),), (BLOCKED,) * 16, 0, Draws([])))
        self.assertIsNone(default_rules().choose_target((), Draws([])))
        board = [BLOCKED] * 16
        board[6] = None
        self.assertEqual(default_rules().choose_move((Card(),), board, 0, Draws([0] * 6)), Move(0, 6))

    def test_input_validation(self):
        for value in (-1, 256, True):
            with self.assertRaises(AIError):
                Personality(score_chance=value)
        for invalid in (-1, 32768, True):
            with self.assertRaises(AIError):
                default_rules().choose_move((Card(),), (None,) * 16, 0, Draws([invalid]))
        for hand, board, owner in (
            ((Card(0x8009),), (None,) * 16, 0),
            ((Card(),), (None,) * 24, 0),
            ((Card(),), (None,) * 16, 2),
            ((Card(),) * 6, (None,) * 16, 0),
        ):
            with self.assertRaises(AIError):
                default_rules().choose_move(hand, board, owner, Draws([]))
        for targets in ((1, 1), (-1,), (16,), tuple(range(9))):
            with self.assertRaises(AIError):
                default_rules().choose_target(targets, Draws([]))


if __name__ == "__main__":
    unittest.main()
