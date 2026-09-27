"""Authenticated event receipts, explicit progression, and durable replay."""

import copy
import json
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

    def test_account_iterator_progress_and_restart_replay_are_exactly_once(self):
        outcome = EventRoundOutcome((1, 2, 3), (6, 5, 4), (0, 2, 4), False)
        keys = ("0", "1", "2")
        before = self.economy.snapshot()
        self.assertTrue(
            self.journal.record(
                self.host, "festival", "iterator", outcome, (key for key in keys), "rounds"
            )
        )
        self.assertEqual(tuple(self.host.member(key, "festival").steps for key in keys), (1, 1, 1))
        progress = self.host.snapshot()
        receipts = self.journal.snapshot()
        self.assertEqual(receipts["rounds"][0]["keys"], list(keys))
        self.assertFalse(
            self.journal.record(self.host, "festival", "iterator", outcome, iter(keys), "rounds")
        )
        self.host = EventHost.restore(
            self.economy, (self.cfg,), json.loads(json.dumps(progress)), clock=lambda: 200
        )
        self.journal = EventResults.restore(json.loads(json.dumps(receipts)))
        self.assertFalse(
            self.journal.record(self.host, "festival", "iterator", outcome, iter(keys), "rounds")
        )
        self.assertEqual(self.host.snapshot(), progress)
        self.assertEqual(self.journal.snapshot(), receipts)
        self.assertEqual(self.economy.snapshot(), before)

    def test_conflicting_last_progress_receipt_does_not_partly_apply_result(self):
        outcome = EventRoundOutcome((1, 2, 3), (6, 5, 4), (0, 2, 4), False)
        # A trusted progress update already used this ID with different facts.
        self.host.update_progress("2", "festival", "conflict", steps_delta=2)
        before = self.host.snapshot(), self.journal.snapshot(), self.economy.snapshot()
        with self.assertRaisesRegex(ValueError, "progress ID replay has different facts"):
            self.journal.record(
                self.host, "festival", "conflict", outcome, ("0", "1", "2"), "rounds"
            )
        self.assertEqual(
            (self.host.snapshot(), self.journal.snapshot(), self.economy.snapshot()), before
        )
        # The failed ID does not poison a valid later result or its replay.
        self.assertTrue(
            self.journal.record(
                self.host, "festival", "fresh", outcome, ("0", "1", "2"), "rounds"
            )
        )
        self.assertEqual(
            tuple(self.host.member(str(i), "festival").steps for i in range(3)), (1, 1, 3)
        )
        saved = self.host.snapshot(), self.journal.snapshot()
        host = EventHost.restore(self.economy, (self.cfg,), saved[0], clock=lambda: 200)
        journal = EventResults.restore(saved[1])
        self.assertFalse(
            journal.record(host, "festival", "fresh", outcome, ("0", "1", "2"), "rounds")
        )
        self.assertEqual((host.snapshot(), journal.snapshot()), saved)

    def test_account_iterator_validates_every_participant_before_progress(self):
        self.economy.provision("3", nn=game_id(4), player_id=4, name=b"Nonmember")
        outcome = EventRoundOutcome((1, 2), (7, 3), (1, 3), False)
        before = self.host.snapshot()
        accounts = self.economy.snapshot()
        for item, keys in (
            (outcome, ("missing", "1")),
            (outcome, ("0", "missing")),
            (outcome, ("0", "2")),
            (replace(outcome, character_ids=(1, 4)), ("0", "3")),
        ):
            with self.subTest(keys=keys):
                with self.assertRaises(ValueError):
                    self.journal.record(
                        self.host, "festival", "invalid-iterator", item, iter(keys), "rounds"
                    )
                self.assertEqual(self.host.snapshot(), before)
                self.assertEqual(self.journal.snapshot()["rounds"], [])
                self.assertEqual(self.economy.snapshot(), accounts)

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
