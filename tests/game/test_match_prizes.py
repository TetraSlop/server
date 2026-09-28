"""Prize ledger invariants and original-x86 result/compaction comparisons."""

import sys
import unittest
from pathlib import Path

from tmserver.game.prizes import (
    PrizeCard,
    PrizeChoice,
    PrizeError,
    PrizeLedger,
    PrizeOption,
    choose_computer_prize,
    poll_computer_prize,
)
from tmserver.game.models import Card


def hands(owners, slots=(0, 1, 2, 3, 4)):
    return [
        [
            PrizeCard(slots[j], Card(i * 5 + j, 10, 0, 10, 10, 1, 0, j), owner)
            for j, owner in enumerate(hand)
        ]
        for i, hand in enumerate(owners)
    ]


class PrizeTests(unittest.TestCase):

    def test_choice_authority_eligibility_and_generation_are_atomic(self):
        ledger = PrizeLedger((6, 3, 6), hands([[0] * 5, [0, 0, 2, 2, 1], [2] * 5]))
        p = ledger.pending
        self.assertIsNotNone(p)
        before = (ledger.pending, ledger.transfers)
        for winner, index, generation in (
            (1, 0, p.choice_id),
            (p.winner, 4, p.choice_id),
            (p.winner, 0, p.choice_id + 1),
            (p.winner, True, p.choice_id),
        ):
            with self.assertRaises(PrizeError):
                ledger.select(winner, index, choice_id=generation)
            self.assertEqual((ledger.pending, ledger.transfers), before)
        a = ledger.select(p.winner, 1, choice_id=p.choice_id)
        self.assertEqual((a.loser, a.original_slot), (1, 1))
        self.assertIsNotNone(ledger.pending)  # tied top seat2 can also win from1
        next_before = (ledger.pending, ledger.transfers)
        with self.assertRaises(PrizeError):
            ledger.select(ledger.pending.winner, 2, choice_id=p.choice_id)
        self.assertEqual((ledger.pending, ledger.transfers), next_before)
        p = ledger.pending
        ledger.select(p.winner, p.options[0].hand_index, choice_id=p.choice_id)
        result = ledger.settlement()
        self.assertEqual(len(result.transfers), 2)
        with self.assertRaises(PrizeError):
            ledger.select(0, 0, choice_id=1)
        self.assertIs(ledger.settlement(), result)

    def test_perfect_is_automatic_and_all_original_identities_survive(self):
        ledger = PrizeLedger((0, 15, 0), hands([[1] * 5, [1] * 5, [1] * 5]))
        self.assertTrue(ledger.complete)
        self.assertIsNone(ledger.pending)
        result = ledger.settlement()
        self.assertEqual([len(x) for x in result.surviving], [0, 5, 0])
        self.assertEqual([len(x) for x in result.awards], [0, 10, 0])
        self.assertEqual(
            [(a.loser, a.hand_index) for a in result.transfers], [(0, 0)] * 5 + [(2, 0)] * 5
        )
        self.assertEqual(
            sorted(c.card.model for h in result.surviving for c in h)
            + sorted(a.card.model for a in result.transfers),
            list(range(5, 10)) + list(range(5)) + list(range(10, 15)),
        )

    def test_draw_and_absent_captures_never_transfer(self):
        for scores, owners in [
            ((5, 5), [[1] * 5, [0] * 5]),
            ((6, 4), [[0] * 5, [1] * 5]),
            ((5, 5, 5), [[2] * 5, [0] * 5, [1] * 5]),
        ]:
            ledger = PrizeLedger(scores, hands(owners))
            self.assertTrue(ledger.complete)
            self.assertFalse(ledger.settlement().transfers)
            self.assertEqual([len(x) for x in ledger.settlement().surviving], [5] * len(scores))

    def test_invalid_input_and_no_partial_settlement(self):
        valid = hands([[0] * 5, [1] * 5])
        cases = [
            ((5,), valid),
            ((0, 0), valid),
            ((True, 9), valid),
            ((5, 5), [valid[0]]),
            ((5, 5), [valid[0][:-1], valid[1]]),
            ((5, 5), [[valid[0][0]] * 5, valid[1]]),
            ((5, 5), [[PrizeCard(c.original_slot, c.card, 2) for c in valid[0]], valid[1]]),
        ]
        for scores, hand in cases:
            with self.assertRaises(PrizeError):
                PrizeLedger(scores, hand)
        ledger = PrizeLedger((7, 3), hands([[0] * 5, [0, 0, 1, 1, 1]]))
        with self.assertRaises(PrizeError):
            ledger.settlement()


if __name__ == "__main__":
    unittest.main()
