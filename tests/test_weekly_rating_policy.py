"""Opt-in weekly result accounting shares the profile/rating commit boundary."""

import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_match_transactions import TableFixture
from tests.test_rating_policy import KEYS, economy, prepare
from tmserver.economy_service import EconomyError, EconomyService
from tmserver.rating_policy import RatingPolicy, RatingPolicyError
from tmserver.ranking_periods import RankingPeriods
from tmserver.result_stats import PracticeStats
from tmserver.state_store import JsonStateStore

MONDAY = 1_704_067_200  # 2024-01-01 00:00 UTC.
WEEK = 7 * 24 * 60 * 60


def result(
    service, policy, *, scores=(7, 3), keys=KEYS[:2], match_id="week:1", rewards=None, tokens=None
):
    rewards = dict.fromkeys(keys, 0) if rewards is None else rewards
    tokens = {key: service.reserve_match(key) for key in keys} if tokens is None else tokens
    quoted = policy.quote(match_id, dict(enumerate(keys)), scores)
    changes = {key: quoted[seat] for seat, key in enumerate(keys)}
    rounds, stats = [], {}
    for key in keys:
        change = changes[key]
        previous = PracticeStats.from_profile(service.profile_bytes(key))
        stats[key] = previous.next_result(
            (change.result_class < 2) - (change.result_class > 2),
            rewards[key],
            fully_tied=len(set(scores)) == 1,
            rating_change=change,
            vs_player_game=len(keys) > 1,
        )
        cards = service.inventory(key)[:5]
        rounds.append((tokens[key], cards, cards))
    return rounds, stats, changes, rewards


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
            self.assertEqual(self.policy.weekly_history(), ({}, 0))
            self.assertEqual(self.policy.snapshot(), baseline)
        imported = RatingPolicy(self.economy, mode="local-history", snapshot=baseline, weekly=True)
        self.assertEqual(imported.snapshot(), baseline)
        self.assertTrue(imported.weekly)
        for mode, weekly in (("preserve", True), ("local-history", 1), ("local-history", None)):
            with self.subTest(mode=mode, weekly=weekly), self.assertRaises(RatingPolicyError):
                RatingPolicy(self.economy, mode=mode, weekly=weekly)
        with self.assertRaises(RatingPolicyError):
            RatingPolicy(self.economy, clock=123)
        self.assertTrue(self.policy.advance_week())
        initialized = self.policy.snapshot()
        self.assertEqual(set(initialized), {"version", "history", "periods"})
        self.assertEqual(initialized["version"], 2)
        self.assertEqual(self.policy.weekly_history(), ({}, MONDAY))

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
            with self.subTest(snapshot=malformed), self.assertRaises(RatingPolicyError):
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
                self.assertEqual(policy.weekly_history(), ({}, 0))
                rounds, stats, changes = prepare(service, policy, (7, 3), match_id="disabled")
                policy.commit_growth(rounds, stats, rating_changes_by_key=changes)
                self.assertEqual(policy.snapshot()["periods"], snapshot["periods"])
                clock.assert_not_called()

    def test_human_and_cpu_results_count_only_authorized_rewards(self):
        first = result(self.economy, self.policy, rewards={"first": 137, "second": 42})
        tokens = commit(self.policy, first)
        history, updated = self.policy.weekly_history()
        self.assertEqual(updated, MONDAY)
        self.assertEqual(
            {k: h.weekly_prize for k, h in history.items()}, {"first": 137, "second": 42}
        )
        self.assertTrue(all(h.last_week_rating is None for h in history.values()))
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
        self.assertEqual(self.policy.weekly_history()[0]["first"].weekly_prize, 337)
        self.assertEqual(
            PracticeStats.from_profile(self.economy.profile_bytes("first")).vs_player_games, 10
        )
        self.now = MONDAY + WEEK
        self.assertTrue(self.policy.advance_week())
        history, updated = self.policy.weekly_history()
        self.assertEqual(updated, self.now)
        self.assertEqual(history["first"].last_week_rating, cpu[1]["first"].rating)
        self.assertEqual(history["second"].last_week_rating, first[1]["second"].rating)

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
        self.assertEqual(policy.weekly_history()[0]["first"].weekly_prize, 20)
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
            {k: h.weekly_prize for k, h in policy.weekly_history()[0].items()},
            {"first": 20, "second": 0},
        )

    def test_invalid_final_participant_and_invalid_clock_leave_every_journal_unchanged(self):
        batch = result(self.economy, self.policy, keys=KEYS, scores=(6, 6, 3))
        baseline = self.economy.snapshot(), self.policy.snapshot()
        periods = self.policy.periods
        invalid = dict(batch[1], third=replace(batch[1]["third"], rn=b"Not authorized"))
        with self.assertRaises(EconomyError):
            commit(self.policy, (batch[0], invalid, *batch[2:]))
        self.assertEqual((self.economy.snapshot(), self.policy.snapshot()), baseline)
        self.assertIs(self.policy.periods, periods)
        self.now = float("nan")
        with self.assertRaises(RatingPolicyError):
            commit(self.policy, batch)
        with self.assertRaises(RatingPolicyError):
            self.policy.advance_week()
        self.assertEqual((self.economy.snapshot(), self.policy.snapshot()), baseline)
        self.assertIs(self.policy.periods, periods)

    def test_periods_are_replaced_only_on_change_and_read_projection_is_detached(self):
        commit(self.policy, result(self.economy, self.policy, rewards={"first": 25, "second": 0}))
        baseline, periods = self.policy.snapshot(), self.policy.periods
        self.assertFalse(self.policy.advance_week())
        self.assertIs(self.policy.periods, periods)
        self.now += WEEK
        projected, updated = self.policy.weekly_history()
        self.assertEqual(updated, MONDAY)
        projected.clear()
        self.assertEqual(self.policy.snapshot(), baseline)
        staged = self.policy.stage_state(self.economy)
        self.assertTrue(staged.advance_week())
        self.assertIsNot(staged.periods, periods)
        self.assertIs(self.policy.periods, periods)
        self.assertEqual(self.policy.snapshot(), baseline)
        self.policy.publish_state(staged)
        self.assertIs(self.policy.periods, staged.periods)
        self.assertEqual(self.policy.weekly_history()[1], MONDAY + WEEK)

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
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStateStore(Path(folder) / "state.json")
            self.prepare_table(store)
            self.server.ratings = RatingPolicy(
                self.server.economy, mode="local-history", weekly=True, clock=lambda: MONDAY
            )
            self.server.ratings.advance_week()
            self.server.persist()
            self.begin()
            self.prepare_result()
            baseline, saved = self.server.snapshot(), store.path.read_bytes()
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
            self.assertEqual(store.path.read_bytes(), saved)
            self.assertIs(self.server.ratings.periods, periods)
            self.server.default_table.commit_match_results(self.match)
            self.assertEqual(self.server.ratings.snapshot(), proposed[0])
            self.assertEqual(JsonStateStore(store.path).load()["ratings"], proposed[0])
            self.assertIsNot(self.server.ratings.periods, periods)


if __name__ == "__main__":
    unittest.main()
