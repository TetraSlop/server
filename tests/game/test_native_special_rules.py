"""Original x86 board-effect comparisons; no game process or desktop control."""

import unittest
from dataclasses import replace

from tests import ROOT
from tmserver.game.errors import MatchError
from tmserver.game.original.runtime import default_rules
from tmserver.game.rules import Card
from tmserver.game.special_rules import (
    Cell,
    apply_special,
    finish_turn,
    place,
    shuffle,
    spinner_chain,
    start_board,
)

BASE = ROOT
CARD = Card(5, 20, 0, 30, 40, 1, 129, 3)


class SpecialAPITests(unittest.TestCase):
    def test_start_counts_placement_and_low_property_lifetime(self):
        fields = [0] * 16
        for i, f in enumerate((1, 3, 4, 5, 6, 9, 17, 18)):
            fields[i] = f
        initial = start_board(fields)
        self.assertEqual((initial.blocks, initial.effects), (5, 3))
        placed = place(initial.cells, 1, CARD, 0, identity=("p", 3))
        self.assertEqual(
            (placed[1].model, placed[1].property, placed[1].identity), (5, 1, ("p", 3))
        )
        self.assertEqual(initial.cells[1].model, 0x8003)
        done = finish_turn(placed, 1)
        self.assertEqual(done[1].property, 0)
        self.assertEqual((done[5].direction, done[5].arrows), (1, 2))
        for prop in (2, 3, 0x11):
            board = list(placed)
            board[1] = replace(board[1], property=prop)
            self.assertEqual(finish_turn(board, 1)[1].property, prop)

    def test_CBC_is_explicit_and_generated_ownership_is_not_inventory(self):
        fields = [0] * 16
        fields[5] = 18
        b = list(start_board(fields).cells)
        b[9] = Cell.ordinary(CARD, 0, identity=7)
        result = apply_special(b, 9, 5, 0, (0,) * 8, replacement=CARD)
        c = result.cells[5]
        self.assertEqual((c.base_owner, c.owner, c.card, c.identity), (5, 0, CARD, None))
        self.assertTrue(result.continue_combat)

    def test_input_boundaries(self):
        for fields in ([0] * 15, [19] + [0] * 15, [True] + [0] * 15):
            with self.assertRaises(MatchError):
                start_board(fields)
        b = list(start_board([0] * 16).cells)
        b[5] = Cell(model=0x8009, base_owner=4, owner=4, arrows=16, direction=4)
        for power in (-1, 32768):
            with self.assertRaises(MatchError):
                spinner_chain(b, 5, 0, power)


    def test_native_AI_can_place_on_an_unoccupied_low_property_tile(self):
        from tests.game.test_native_ai import Draws
        from tmserver.game.ai import Move

        fields = [1] * 16
        fields[5] = 5
        board = start_board(fields).cells
        before = tuple(board)
        self.assertEqual(default_rules().choose_special_move((CARD,), board, 0, Draws([0] * 6)), Move(0, 5))
        self.assertEqual(board, before)

    def test_special_AI_adapter_no_move_and_validation_boundaries(self):
        from tests.game.test_native_ai import Draws
        from tmserver.game.ai import Personality
        from tmserver.game.original.image import OriginalCodeError

        empty = start_board([0] * 16).cells
        self.assertIsNone(default_rules().choose_special_move((), empty, 0, Draws([])))
        self.assertIsNone(default_rules().choose_special_move((CARD,), start_board([1] * 16).cells, 0, Draws([])))
        with self.assertRaisesRegex(OriginalCodeError, "invalid bridge input"):
            default_rules().choose_special_move((CARD,) * 6, empty, 0, Draws([]))
        for raw in (-1, True, 32768):
            with self.assertRaisesRegex(OriginalCodeError, "Random draw"):
                default_rules().choose_special_move((CARD,), empty, 0, Draws([raw]))
        move = default_rules().choose_special_move((CARD,), empty, 0, Draws([0, 0, 0, 0, 0, 5]), Personality())
        self.assertEqual((move.hand_index, move.field), (0, 5))


if __name__ == "__main__":
    unittest.main()
