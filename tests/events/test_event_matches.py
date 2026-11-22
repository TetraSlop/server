"""Isolated event combat + unmodified original receiver/exit-controller checks."""

import sys
import unittest
from pathlib import Path

from tests import FIXTURES
from tmserver.events.matches import (
    EventMatchRound,
    EventParticipant,
    event_session_init,
    event_session_quit,
)
from tmserver.game.models import Card
from tmserver.game.errors import MatchError
from tmserver.game.packets import decode, packet

P = FIXTURES / "protocol"

CARDS = tuple(Card(i, 50, 0, 40, 30, 1, 0) for i in range(5))


def make(n=2, cpu=False, arrows=0):
    cards = tuple(Card(i, 50, 0, 40, 30, 1, arrows) for i in range(5))
    return EventMatchRound(
        tuple(
            EventParticipant(str(i), 100 + i, ("Player" + str(i)).encode(), 10 + i, cards)
            for i in range(1 if cpu else n)
        ),
        cpu_cards=cards if cpu else None,
        seed=17,
    )


def finish(s):
    output = []
    for _ in range(300):
        if s.phase == "result":
            return output
        if s.pending_work:
            output += s.advance(1)
            continue
        if s.phase == "turn":
            sequence = s.sequence
            for seat, p in enumerate(s.players):
                if p.peer is not None:
                    output += s.handle(p.peer, packet(0x43, 9, sequence, f"@TurnData=/Ans={seat}"))
            if s.phase == "turn" and s.players[s.active].peer is not None:
                p = s.players[s.active]
                field = next(i for i, c in enumerate(s.snapshot()["board"]) if c is None)
                output += s.handle(
                    p.peer, packet(0x43, 10, s.sequence, f"@PutData=/P={s.active}/H=0/F={field}")
                )
        elif s.phase == "battle":
            p = s.players[s.active]
            output += s.handle(
                p.peer,
                packet(
                    0x43,
                    11,
                    s.sequence,
                    "@BattleSelect=/B=" + str(s.snapshot()["battle_targets"][0]),
                ),
            )
        else:
            raise AssertionError(s.phase)
    raise AssertionError("event round did not finish")


class EventRoundTests(unittest.TestCase):
    def test_two_three_and_cpu_complete_without_inventory_or_stats_commit(self):
        for n, cpu in [(2, False), (3, False), (2, True)]:
            with self.subTest(n=n, cpu=cpu):
                s = make(n, cpu)
                initial_stats = tuple(p.result_stats for p in s.players)
                start = s.begin()
                self.assertEqual(s.begin(), [])
                result = finish(s)
                self.assertEqual(s.sequence, 5 * n)
                self.assertEqual(sum(s.outcome.scores), 5 * n)
                self.assertFalse(s.outcome.aborted)
                self.assertEqual(
                    tuple(p.result_stats for p in s.players if p.peer is not None), initial_stats
                )
                kinds = [next(iter(decode(d.payload).sections)) for d in start + result]
                self.assertIn("EventResult", kinds)
                self.assertFalse(
                    set(kinds) & {"ResultData", "VsGameInit", "CardSelect", "TCList", "TradeCard"}
                )
                for seat, p in enumerate(s.players):
                    if p.peer is None:
                        continue
                    out = s.handle(p.peer, packet(0x43, 14, 5 * n, f"@Ready=/Ans={seat}"))
                    self.assertEqual(
                        out[0].payload,
                        packet(
                            0x43,
                            17,
                            2,
                            "@Continue=/Ans=" + "|".join(["3"] * n) + "/B=" + "|".join(["0"] * n),
                        ),
                    )
                    self.assertEqual(
                        s.handle(p.peer, packet(0x43, 14, 5 * n, f"@Ready=/Ans={seat}")), []
                    )
                self.assertEqual(s.phase, "finished")

    def test_shared_combat_runs_with_reciprocal_arrows(self):
        s = make(arrows=255)
        s.begin()
        out = finish(s)
        self.assertTrue(any(b"@BattleData=" in d.payload for d in out))
        self.assertEqual(s.phase, "result")

    def test_untrusted_routes_and_wrong_result_release_rejected_atomically(self):
        s = make()
        s.begin()
        bad = [
            packet(0x43, 7, body="@CardSelect=/C=5"),
            packet(0x43, 17, body="@Continue=/Ans=1/Conf=0"),
            packet(0x43, 14, 10, "@Ready=/Ans=0"),
            packet(0x43, 15, body="@GetSelect=/E=0/H=0"),
        ]
        for payload in bad:
            before = s.snapshot()
            with self.assertRaises(MatchError):
                s.handle("0", payload)
            self.assertEqual(s.snapshot(), before)
        finish(s)
        for payload in [
            packet(0x43, 14, 9, "@Ready=/Ans=0"),
            packet(0x43, 14, 10, "@Ready=/Ans=1"),
        ]:
            before = s.snapshot()
            with self.assertRaises(MatchError):
                s.handle("0", payload)
            self.assertEqual(s.snapshot(), before)
        with self.assertRaises(MatchError):
            s.handle("stranger", packet(0x43, 14, 10, "@Ready=/Ans=0"))

    def test_cancel_never_exposes_inventory_replacement(self):
        s = make()
        s.begin()
        s.cancel()
        self.assertEqual(s.phase, "finished")
        self.assertFalse(s.pending_work)
        self.assertTrue(s.outcome.aborted)
        self.assertEqual(s.advance(), [])
        unstarted = make()
        unstarted.cancel()
        self.assertEqual(unstarted.begin(), [])
        self.assertIsNone(unstarted.outcome)

    def test_invalid_configuration_rejected(self):
        a = EventParticipant("0", 1, b"Alice", 1, CARDS)
        for ps, kw in [
            ((a,), {}),
            ((a, a), {}),
            ((a,), {"cpu_cards": CARDS, "turn_limit": 6}),
            ((a, a), {"cpu_cards": CARDS}),
            ((EventParticipant("0", 0, b"Alice", 1, CARDS), a), {}),
        ]:
            with self.assertRaises((ValueError, MatchError)):
                EventMatchRound(ps, **kw)
        for args in [dict(session=256), dict(session=1, status=4)]:
            with self.assertRaises(MatchError):
                event_session_quit(**args)


if __name__ == "__main__":
    unittest.main()
