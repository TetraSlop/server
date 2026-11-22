"""Opt-in weekly result accounting shares the profile/rating commit boundary."""

import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from tests.economy.test_match_transactions import TableFixture
from tests.rankings.test_rating_policy import KEYS, economy, prepare
from tests.support.memory_store import MemoryStore
from tmserver.economy.errors import EconomyError
from tmserver.economy.service import EconomyService
from tmserver.rankings.policy import RatingPolicy
from tmserver.rankings.periods import RankingPeriods
from tmserver.rankings.stats import PracticeStats

MONDAY = 1_704_067_200  # 2024-01-01 00:00 UTC.
WEEK = 7 * 24 * 60 * 60


def result(
    service, policy, *, scores=(7, 3), keys=KEYS[:2], match_id="week:1", rewards=None, tokens=None
):
    rewards = dict.fromkeys(keys, 0) if rewards is None else rewards
    batch = prepare(service, policy, scores, keys=keys, match_id=match_id, tokens=tokens,
                    payouts=rewards, vs_player_game=len(keys) > 1)
    return *batch, rewards


def commit(policy, batch):
    rounds, stats, changes, rewards = batch
    return policy.commit_growth(
        rounds, stats, rewards, rating_changes_by_key=changes, vs_player_game=len(rounds) > 1
    )


class WeeklyRatingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.economy = economy()
        self.now = MONDAY + 123
        self.policy = RatingPolicy(
            self.economy, mode="local-history", weekly=True, clock=lambda: self.now
        )

    def test_flags_are_explicit_and_v1_remains_exact_until_initialized(self):
        baseline = RatingPolicy(self.economy).snapshot()
        self.assertEqual(set(baseline), {"version", "history"})
        self.assertEqual(baseline["version"], 1)
        self.assertEqual(self.policy.snapshot(), baseline)
        with patch.object(self.policy, "clock", side_effect=AssertionError("read clock")):
            self.assertEqual(self.policy.weekly_history(), {})
            self.assertEqual(self.policy.snapshot(), baseline)
        imported = RatingPolicy(self.economy, mode="local-history", snapshot=baseline, weekly=True)
        self.assertEqual(imported.snapshot(), baseline)
        self.assertTrue(imported.weekly)
        for mode, weekly in (("preserve", True), ("local-history", 1), ("local-history", None)):
            with self.subTest(mode=mode, weekly=weekly), self.assertRaises(EconomyError):
                RatingPolicy(self.economy, mode=mode, weekly=weekly)
        with self.assertRaises(EconomyError):
            RatingPolicy(self.economy, clock=123)
        self.assertTrue(self.policy.advance_week())
        initialized = self.policy.snapshot()
        self.assertEqual(set(initialized), {"version", "history", "periods"})
        self.assertEqual(initialized["version"], 2)
        self.assertEqual(self.policy.weekly_history(), {})
        self.assertEqual(initialized["periods"]["week_start"], MONDAY)

    def test_strict_v1_v2_restore_authenticates_current_and_previous_accounts(self):
        self.policy.advance_week()
        snapshot = self.policy.snapshot()
        attempts = (
            dict(snapshot, version=True),
            dict(snapshot, version=3),
            dict(snapshot, extra=0),
            {"version": 2, "history": snapshot["history"]},
            dict(snapshot, periods=None),
            dict(snapshot, periods={}),
            dict(snapshot, version=1),
            {"version": 1},
        )
        for malformed in attempts:
            with self.subTest(snapshot=malformed), self.assertRaises(ValueError):
                RatingPolicy(self.economy, snapshot=malformed)
        for previous in (False, True):
            periods = RankingPeriods.restore()
            periods.record(MONDAY, {"missing": (10, 200)})
            if previous:
                periods.advance(MONDAY + WEEK)
            with self.subTest(previous=previous), self.assertRaises(EconomyError):
                RatingPolicy(self.economy, snapshot=dict(snapshot, periods=periods.snapshot()))

    def test_disabled_modes_retain_saved_periods_without_reading_clock_or_recording(self):
        commit(self.policy, result(self.economy, self.policy, rewards={"first": 123, "second": 4}))
        snapshot = json.loads(json.dumps(self.policy.snapshot()))
        for mode in ("preserve", "local-history"):
            with self.subTest(mode=mode):
                service = EconomyService.restore(self.economy.snapshot())
                clock = Mock(side_effect=AssertionError("disabled clock"))
                policy = RatingPolicy(service, mode=mode, snapshot=snapshot, clock=clock)
                self.assertFalse(policy.weekly)
                self.assertEqual(policy.snapshot(), snapshot)
                self.assertFalse(policy.advance_week())
                self.assertEqual(policy.weekly_history(), {})
                rounds, stats, changes = prepare(service, policy, (7, 3), match_id="disabled")
                policy.commit_growth(rounds, stats, rating_changes_by_key=changes)
                self.assertEqual(policy.snapshot()["periods"], snapshot["periods"])
                clock.assert_not_called()

    def test_human_and_cpu_results_count_only_authorized_rewards(self):
        first = result(self.economy, self.policy, rewards={"first": 137, "second": 42})
        tokens = commit(self.policy, first)
        self.assertEqual(
            self.policy.weekly_history(), {"first": (137, None), "second": (42, None)}
        )
        for key in KEYS[:2]:
            self.assertEqual(
                PracticeStats.from_profile(self.economy.profile_bytes(key)).vs_player_games, 10
            )
        cpu = result(
            self.economy,
            self.policy,
            keys=("first",),
            match_id="cpu:2",
            rewards={"first": 200},
            tokens={"first": tokens["first"]},
        )
        commit(self.policy, cpu)
        self.assertEqual(self.policy.weekly_history()["first"][0], 337)
        self.assertEqual(
            PracticeStats.from_profile(self.economy.profile_bytes("first")).vs_player_games, 10
        )
        self.now = MONDAY + WEEK
        self.assertTrue(self.policy.advance_week())
        self.assertEqual(self.policy.periods.snapshot()["week_start"], self.now)
        self.assertEqual(
            self.policy.weekly_history(),
            {"first": (None, cpu[1]["first"].rating), "second": (None, first[1]["second"].rating)},
        )

    def test_refund_is_zero_and_native_cumulative_wrap_is_only_actual_delta(self):
        saved = self.economy.snapshot()
        profile = bytes.fromhex(saved["accounts"]["first"]["template_hex"])
        stats = replace(PracticeStats.from_profile(profile), reward_sum=0xFFFFFFFA)
        saved["accounts"]["first"]["template_hex"] = stats.apply_to_profile(profile).hex()
        service = EconomyService.restore(saved)
        policy = RatingPolicy(service, mode="local-history", weekly=True, clock=lambda: MONDAY)
        paid = result(service, policy, rewards={"first": 20, "second": 0})
        tokens = commit(policy, paid)
        self.assertEqual(paid[1]["first"].reward_sum, 14)
        self.assertEqual(policy.weekly_history()["first"][0], 20)
        refund = result(
            service,
            policy,
            scores=(5, 5),
            match_id="refund:2",
            tokens=tokens,
            rewards={"first": 99, "second": 99},
        )
        commit(policy, refund)
        self.assertEqual(refund[1]["first"].current_reward, 99)
        self.assertEqual(
            {k: prize for k, (prize, _) in policy.weekly_history().items()},
            {"first": 20, "second": 0},
        )

    def test_invalid_final_participant_and_invalid_clock_leave_every_journal_unchanged(self):
        batch = result(self.economy, self.policy, keys=KEYS, scores=(6, 6, 3))
        baseline = self.economy.snapshot(), self.policy.snapshot()
        periods = self.policy.periods
        invalid = dict(batch[1], third=None)
        with self.assertRaises(EconomyError):
            commit(self.policy, (batch[0], invalid, *batch[2:]))
        self.assertEqual((self.economy.snapshot(), self.policy.snapshot()), baseline)
        self.assertIs(self.policy.periods, periods)
        self.now = float("nan")
        with self.assertRaises(EconomyError):
            commit(self.policy, batch)
        with self.assertRaises(EconomyError):
            self.policy.advance_week()
        self.assertEqual((self.economy.snapshot(), self.policy.snapshot()), baseline)
        self.assertIs(self.policy.periods, periods)

    def test_periods_are_replaced_only_on_change_and_read_projection_is_detached(self):
        commit(self.policy, result(self.economy, self.policy, rewards={"first": 25, "second": 0}))
        baseline, periods = self.policy.snapshot(), self.policy.periods
        self.assertFalse(self.policy.advance_week())
        self.assertIs(self.policy.periods, periods)
        self.now += WEEK
        projected = self.policy.weekly_history()
        self.assertEqual(self.policy.periods.snapshot()["week_start"], MONDAY)
        projected.clear()
        self.assertEqual(self.policy.snapshot(), baseline)
        staged = self.policy.stage_copy(economy=self.economy)
        self.assertTrue(staged.advance_week())
        self.assertIsNot(staged.periods, periods)
        self.assertIs(self.policy.periods, periods)
        self.assertEqual(self.policy.snapshot(), baseline)
        self.policy.publish_staged(staged)
        self.assertIs(self.policy.periods, staged.periods)
        self.assertEqual(self.policy.periods.snapshot()["week_start"], MONDAY + WEEK)

    def test_duplicate_result_after_rollover_and_restart_never_counts_twice(self):
        batch = result(self.economy, self.policy, rewards={"first": 45, "second": 0})
        tokens = commit(self.policy, batch)
        self.now += WEEK
        self.policy.advance_week()
        baseline = self.economy.snapshot(), self.policy.snapshot()
        refreshed = [
            (tokens[key], before, after) for key, (_, before, after) in zip(KEYS, batch[0])
        ]
        for rounds in (batch[0], refreshed):
            with self.assertRaises(EconomyError):
                commit(self.policy, (rounds, *batch[1:]))
            self.assertEqual((self.economy.snapshot(), self.policy.snapshot()), baseline)
        service = EconomyService.restore(json.loads(json.dumps(baseline[0])))
        policy = RatingPolicy(
            service,
            mode="local-history",
            weekly=True,
            clock=lambda: self.now,
            snapshot=json.loads(json.dumps(baseline[1])),
        )
        self.assertEqual(policy.snapshot(), baseline[1])
        rounds = [
            (service.reserve_match(key), before, after)
            for key, (_, before, after) in zip(KEYS, batch[0])
        ]
        with self.assertRaises(EconomyError):
            commit(policy, (rounds, *batch[1:]))
        self.assertEqual((service.snapshot(), policy.snapshot()), baseline)


class WeeklyRatingTransactionTests(TableFixture, unittest.TestCase):
    def test_result_persistence_failure_discards_period_history_and_account_changes(self):
        store = MemoryStore()
        self.prepare_table(store)
        self.server.ratings = RatingPolicy(
            self.server.economy, mode="local-history", weekly=True, clock=lambda: MONDAY
        )
        self.server.ratings.advance_week()
        self.server.persist()
        self.begin()
        self.prepare_result()
        baseline, saved = self.server.snapshot(), store.load()
        periods = self.server.ratings.periods
        proposed = []

        def fail(accounts, journals):
            self.assertEqual(self.server.snapshot(), baseline)
            self.assertIs(self.server.ratings.periods, periods)
            proposed.append(deepcopy(journals["ratings"]))
            self.assertEqual(set(proposed[0]["periods"]["current"]), set(self.keys[:2]))
            raise OSError("weekly result write")

        with (
            patch.object(self.server, "persist_changes", side_effect=fail),
            self.assertRaises(OSError),
        ):
            self.server.default_table.commit_match_results(self.match)
        self.assertEqual(self.server.snapshot(), baseline)
        self.assertEqual(store.load(), saved)
        self.assertIs(self.server.ratings.periods, periods)
        self.server.default_table.commit_match_results(self.match)
        self.assertEqual(self.server.ratings.snapshot(), proposed[0])
        self.assertEqual(store.load()["ratings"], proposed[0])
        self.assertIsNot(self.server.ratings.periods, periods)


if __name__ == "__main__":
    unittest.main()
