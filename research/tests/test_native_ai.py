"""Pure-policy checks and comparisons executing original TM x86 instructions.

The oracle keeps original hand/placement/score/target instructions, adjacency
tables and catalogue lookup. Only CRT rand inputs and existing presentation
boundaries are intercepted. No native process, UI, service or network is used.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import random
import sys
import unittest

from native_ai import (AIError, BLOCKED, Cell, Move, Personality, PRICE_TIERS,
                       choose_move, choose_target, placement_scores,
                       poll_hand, poll_placement, poll_target)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'protocol_map'))
from match_timeout_vectors import TimerVM
from unicorn.x86_const import UC_X86_REG_ESP


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
            raise AssertionError('policy consumed an unexpected random draw')
        result = self.values.pop(0)
        self.used.append(result)
        return result


class OriginalAI(TimerVM):
    def __init__(self):
        super().__init__()
        self.w32(0x102b6364, 0x20370000)
        catalog = json.loads((ROOT / 'protocol_map/economy_catalog.json').read_text())
        self.u.mem_write(0x20370000, b''.join(bytes.fromhex(row['raw_hex']) for row in catalog['cards']))

    def hook(self, u, address, size, data):
        if address == 0x101e2e44:
            if not self.random:
                raise AssertionError('original consumed an unexpected random draw')
            result = self.random.pop(0)
            self.rng.append(result)
            self.ret(u.reg_read(UC_X86_REG_ESP), result)
        else:
            super().hook(u, address, size, data)

    def seed(self, hand, board, owner, draws=(), denominator=1, personality=Personality()):
        self.random, self.rng = list(draws), []
        self.w32(0x102ff568, denominator)
        g, p = self.game, self.player
        self.w8(g + 0x178, owner)
        self.w8(g + 0x171, 1)
        self.w8(g + 0x16f, 0x22)
        self.w8(g + 0x17a, 255)
        self.w8(g + 0x179, 255)
        for i, occupant in enumerate(board):
            if occupant is None:
                self.card(i, id=0xffff, owner=8, arrows=0)
            elif occupant is BLOCKED:
                self.card(i, id=0x8000, owner=9, arrows=0)
            else:
                self.card(i, id=occupant.card.model, owner=occupant.owner, arrows=occupant.card.arrows)
        self.w8(p + 0x19f + owner * 0x30, len(hand))
        self.w8(p + 0x19a + owner * 0x30, 1)
        for offset, value in ((0x184, personality.score_chance), (0x185, personality.preference),
                              (0x186, personality.preference_chance)):
            self.w8(p + offset + owner * 0x30, value)
        for i, card in enumerate(hand):
            address = p + 0x20c + (owner * 5 + i) * 0x9c
            self.w16(address + 14, card.model)
            self.w8(address + 13, card.arrows)

    def hand(self, owner):
        self.branch(0x100c42de, [0x100c434f], {'EAX': owner, 'ESI': self.game, 'EBP': 0})
        selected = self.r8(self.game + 0x17a)
        return None if selected == 255 else selected

    def placement(self):
        ready = self.call(0x100d68d0, this=self.game)
        return self.r8(self.game + 0x179) if ready else None

    def score(self, field, preference):
        self.u.mem_write(self.game + 0x227, bytes(16))
        return self.call(0x100d6aa0, [field, preference, 1], this=self.game) & 255

    def target(self, candidates, owner):
        self.w8(self.game + 0x17d, len(candidates))
        self.u.mem_write(self.game + 0x1dc, bytes(candidates))
        self.branch(0x100ceb2b, [0x100ceb96], {'EAX': owner, 'EBP': self.game, 'ESI': 0})
        selected = self.r8(self.game + 0x17a)
        return None if selected == 255 else selected


def boards():
    empty = (None,) * 16
    yield 'empty', empty
    for label, arrows in (('direct', 0), ('battle', 16)):
        board = list(empty)
        board[1] = Cell(Card(23, arrows), 1)
        board[6] = Cell(Card(54, 64), 1)
        board[2] = Cell(Card(162, 0), 1)  # tier16 wraps to0 in score preference1.
        board[4] = Cell(Card(149, 255), 0)
        board[15] = BLOCKED
        yield label, tuple(board)
    board = [BLOCKED] * 16
    board[9] = None
    yield 'single_empty', tuple(board)
    rng = random.Random(611)
    for index in range(16):
        board = tuple(None if (r := rng.randrange(10)) < 4 else BLOCKED if r == 4
                      else Cell(Card(rng.randrange(250), rng.randrange(256)), rng.randrange(2))
                      for _ in range(16))
        if all(cell is not None for cell in board):
            board = (None,) + board[1:]
        yield f'seeded_{index}', board


class NativeAIOriginalTests(unittest.TestCase):
    def test_catalogue_tiers_match_decoded_original_asset(self):
        catalog = json.loads((ROOT / 'protocol_map/economy_catalog.json').read_text())
        self.assertEqual(PRICE_TIERS, tuple(row['price_tier'] for row in catalog['cards']))

    def test_ordinary_evaluator_matches_original_all_preferences(self):
        oracle = OriginalAI()
        comparisons = 0
        for index, (label, board) in enumerate(boards()):
            owner = index & 1
            card = Card(index, (1, 5, 255, 0, 64, 170)[index % 6])
            oracle.seed((card,), board, owner)
            oracle.w8(oracle.game + 0x17a, 0)
            for preference in (0, 1, 2, 3, 4, 5, 255):
                for field, score in placement_scores(card, board, owner, preference):
                    with self.subTest(board=label, preference=preference, field=field):
                        self.assertEqual(score, oracle.score(field, preference))
                    comparisons += 1
        self.assertGreater(comparisons, 700)

    def test_full_move_and_draw_order_match_original(self):
        oracle = OriginalAI()
        personalities = (Personality(), Personality(0, 0, 0), Personality(100, 1, 100),
                         Personality(100, 2, 100), Personality(100, 3, 100),
                         Personality(100, 4, 100), Personality(49, 255, 51))
        rng = random.Random(1611)
        for index, (label, board) in enumerate(boards()):
            hand = tuple(Card(rng.randrange(250), rng.randrange(256)) for _ in range(index % 5 + 1))
            for personality in personalities:
                values = [rng.randrange(32768) for _ in range(6)]
                stream = Draws(values)
                move = choose_move(hand, board, index & 1, stream, personality)
                oracle.seed(hand, board, index & 1, values, personality=personality)
                selected = oracle.hand(index & 1)
                field = oracle.placement()
                with self.subTest(board=label, personality=personality):
                    self.assertEqual(move, Move(selected, field))
                    self.assertEqual(stream.used, oracle.rng)

    def test_hand_and_target_polls_match_original_rate_gates_and_order(self):
        oracle = OriginalAI()
        hand = tuple(Card(i, i * 17) for i in range(5))
        for denominator in (1, 2, 60, 32768):
            for trigger in (0, 1, 2, 59, 60, 61, 32767):
                for choice in (0, 1, 7, 32767):
                    for owner in (0, 1):
                        values = [trigger, choice]
                        oracle.seed(hand, (None,) * 16, owner, values, denominator)
                        stream = Draws(values)
                        self.assertEqual(poll_hand(hand, stream, denominator), oracle.hand(owner))
                        self.assertEqual(stream.used, oracle.rng)
                        targets = (14, 1, 6)  # Candidate order is retained, not sorted.
                        oracle.seed(hand, (None,) * 16, owner, values, denominator)
                        stream = Draws(values)
                        self.assertEqual(poll_target(targets, stream, denominator), oracle.target(targets, owner))
                        self.assertEqual(stream.used, oracle.rng)

    def test_placement_chance_thresholds_and_rate_gate_match_original(self):
        oracle = OriginalAI()
        board = tuple(list(boards())[1][1])
        card = Card(99, 255)
        for trigger in (0, 1, 2, 60, 61):
            for score_draw in (0, 48, 49, 99, 32767):
                for preference_draw in (0, 50, 51, 99):
                    personality = Personality(49, 3, 51)
                    values = [trigger, score_draw, preference_draw, 7]
                    oracle.seed((card,), board, 0, values, 60, personality)
                    oracle.w8(oracle.game + 0x17a, 0)
                    stream = Draws(values)
                    self.assertEqual(poll_placement(card, board, 0, stream, personality), oracle.placement())
                    self.assertEqual(stream.used, oracle.rng)

    def test_single_battle_target_skips_original_selection_rng(self):
        oracle = OriginalAI()
        oracle.card(5, owner=0, arrows=1)
        oracle.card(1, owner=1, arrows=16)
        oracle.call(0x100d3760, [5], this=oracle.game)
        oracle.step(2)
        self.assertEqual(oracle.r8(oracle.game + 0x17c), 6)
        self.assertFalse(oracle.rng)
        self.assertEqual(choose_target((1,), Draws([])), 1)

    def test_three_player_target_indices_and_draw_order_match_original(self):
        oracle=OriginalAI()
        oracle.w8(0x20300000,1)  # Original mode record selects5x5 adjacency.
        oracle.w8(oracle.player+0xe5,3);oracle.w8(oracle.player+0xe6,1)
        cases=0
        for owner in (0,1,2):
            for targets in ((24,),(16,24),(23,18,1),(24,23,22,21,20,19,18,17)):
                for denominator in (1,60):
                    for values in ((0,0),(1,7),(2,32767),(61,15)):
                        oracle.seed((Card(),),(None,)*25,owner,values,denominator)
                        stream=Draws(values)
                        expected=oracle.target(targets,owner)
                        self.assertEqual(poll_target(targets,stream,denominator,board_size=25),expected)
                        self.assertEqual(stream.used,oracle.rng)
                        cases+=1
        self.assertEqual(cases,96)
        # The original classifier itself takes a sole reciprocal target in row5,
        # bypassing target-selection randomness even in the25-cell mode.
        oracle.seed((Card(),),(None,)*25,0)
        oracle.card(23,owner=0,arrows=4)
        oracle.card(24,owner=1,arrows=64)
        oracle.call(0x100d3760,[23],this=oracle.game)
        oracle.step(2)
        self.assertEqual(oracle.r8(oracle.game+0x17c),6)
        self.assertEqual(oracle.r8(oracle.game+0x17a),24)
        self.assertEqual(oracle.rng,[])
        self.assertEqual(choose_target((24,),Draws([]),board_size=25),24)
        self.assertEqual(choose_target((24,18),Draws([0,1]),board_size=25),18)
        for size in (15,24,26,True):
            with self.assertRaises(AIError):choose_target((16,),Draws([]),board_size=size)
        with self.assertRaises(AIError):choose_target((25,),Draws([]),board_size=25)

    def test_fully_blocked_original_candidate_scan_is_empty(self):
        oracle = OriginalAI()
        oracle.seed((Card(),), (BLOCKED,) * 16, 0)
        self.assertEqual(oracle.call(0x100d3a60, [16, 0], this=oracle.game) & 255, 0)
        self.assertEqual(oracle.r8(oracle.game + 0x17d), 0)
        # Do not execute the original downstream modulo-zero precondition bug.
        self.assertIsNone(choose_move((Card(),), (BLOCKED,) * 16, 0, Draws([])))


class NativeAIPolicyTests(unittest.TestCase):
    def test_tied_empty_placements_preserve_ascending_field_order(self):
        for choice in (0, 1, 15, 16, 32767):
            self.assertEqual(poll_placement(Card(0, 255), (None,) * 16, 0,
                Draws([0, 0, 0, choice]), denominator=1), choice % 16)

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
        for hand, board, owner in (((Card(0x8009),), (None,) * 16, 0),
                                   ((Card(),), (None,) * 25, 0),
                                   ((Card(),), (None,) * 16, 2),
                                   ((Card(),) * 6, (None,) * 16, 0)):
            with self.assertRaises(AIError):
                choose_move(hand, board, owner, Draws([]))
        for targets in ((1, 1), (-1,), (16,), tuple(range(9))):
            with self.assertRaises(AIError):
                choose_target(targets, Draws([]))


if __name__ == '__main__':
    unittest.main()
