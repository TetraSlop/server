"""Authenticated event receipts, explicit progression, and durable replay."""

import copy
import unittest
from dataclasses import replace

from tests.test_event_rooms import config
from tmserver.economy_service import EconomyService
from tmserver.event_matches import EventRoundOutcome
from tmserver.event_results import EventResults
from tmserver.event_service import EventHost
from tmserver.identities import game_id


class EventResultTests(unittest.TestCase):
    def setUp(self):
        self.economy = EconomyService(seed=1)
        for i in range(3):
            self.economy.provision(
                str(i), nn=game_id(i + 1), player_id=i + 1, name=f"Player{i}".encode()
            )
        self.cfg = config(match_enabled=True)
        self.host = EventHost(self.economy, (self.cfg,), clock=lambda: 200)
        for i in range(3):
            self.host.admit_room(str(i), "festival")
        self.journal = EventResults()

    def test_local_policies_rank_ties_cpu_and_restart_are_exactly_once(self):
        before = self.economy.snapshot()
        for serial, (policy, scores, classes, keys, expected) in enumerate(
            (
                ("none", (7, 3), (1, 3), ("0", "1"), (0, 0, 0)),
                ("wins", (5, 5), (2, 2), ("0", "1"), (0, 0, 0)),
                ("wins", (6, 6, 3), (1, 1, 4), ("0", "1", "2"), (1, 1, 0)),
                ("rounds", (6, 4), (1, 3), ("0", None), (2, 1, 0)),
            )
        ):
            outcome = EventRoundOutcome(tuple(range(1, len(scores) + 1)), scores, classes, False)
            identity = f"local:{serial}"
            self.assertTrue(
                self.journal.record(self.host, "festival", identity, outcome, keys, policy)
            )
            self.assertFalse(
                self.journal.record(self.host, "festival", identity, outcome, keys, policy)
            )
            self.assertEqual(
                tuple(self.host.member(str(i), "festival").steps for i in range(3)), expected
            )
            self.journal = EventResults.restore(self.journal.snapshot())
            self.host = EventHost.restore(
                self.economy, (self.cfg,), self.host.snapshot(), clock=lambda: 200
            )
            self.assertFalse(
                self.journal.record(self.host, "festival", identity, outcome, keys, policy)
            )
        self.assertEqual(self.economy.snapshot(), before)

    def test_aborted_wrong_accounts_and_excluded_members_do_not_partly_progress(self):
        outcome = EventRoundOutcome((1, 2), (7, 3), (1, 3), False)
        before = self.host.snapshot()
        for item, keys in (
            (replace(outcome, aborted=True), ("0", "1")),
            (outcome, ("1", "0")),
            (replace(outcome, scores=(7, 4)), ("0", "1")),
        ):
            with self.assertRaises(ValueError):
                self.journal.record(self.host, "festival", "bad", item, keys, "rounds")
            self.assertEqual(self.host.snapshot(), before)
        self.host.set_eligibility("1", "festival", -1)
        before = self.host.snapshot()
        with self.assertRaises(ValueError):
            self.journal.record(self.host, "festival", "excluded", outcome, ("0", "1"), "rounds")
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.journal.snapshot()["rounds"], [])

    def test_mismatched_replay_and_corrupt_snapshot_fail(self):
        outcome = EventRoundOutcome((1, 2), (7, 3), (1, 3), False)
        self.journal.record(self.host, "festival", "one", outcome, ("0", "1"))
        with self.assertRaises(ValueError):
            self.journal.record(self.host, "festival", "one", outcome, ("0", "1"), "wins")
        snapshot = self.journal.snapshot()
        for key, value in (
            ("scores", None),
            ("scores", [7, 4]),
            ("keys", [[], None]),
            ("classes", [True, 3]),
            ("characters", [1, 1]),
        ):
            bad = copy.deepcopy(snapshot)
            bad["rounds"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                EventResults.restore(bad)
        snapshot["rounds"] *= 2
        with self.assertRaises(ValueError):
            EventResults.restore(snapshot)


if __name__ == "__main__":
    unittest.main()
