"""Pure weekly state: rollover, frozen ratings, validation and isolated restore."""

import copy
import json
import unittest
from datetime import UTC, datetime

from tmserver.rankings.periods import RankingPeriodError, RankingPeriods

MONDAY = int(datetime(2026, 9, 21, tzinfo=UTC).timestamp())
WEEK = 604800
MAX = 0xFFFFFFFF


class RankingPeriodsTests(unittest.TestCase):
    def test_uninitialized_state_and_fractional_clock(self):
        periods = RankingPeriods.restore()
        self.assertIsNone(periods.snapshot())
        self.assertEqual(periods.historical_stats(), {})
        self.assertEqual(periods.account_keys, frozenset())
        self.assertTrue(periods.advance(MONDAY + 12.75))
        self.assertEqual(periods.snapshot()["week_start"], MONDAY)
        self.assertFalse(periods.advance(MONDAY))
        self.assertFalse(periods.advance(MONDAY + WEEK - 0.25))

    def test_current_prizes_include_zero_and_wrap_dword(self):
        periods = RankingPeriods()
        periods.record(MONDAY, {"a": (MAX, 201), "b": (0, 199)})
        periods.record(MONDAY + 1, {"a": (3, 198)})
        self.assertEqual(
            periods.historical_stats(),
            {"a": (2, None), "b": (0, None)},
        )
        self.assertEqual(periods.snapshot()["current"]["a"], {"prize": 2, "rating": 198})
        self.assertEqual(periods.account_keys, frozenset(("a", "b")))

    def test_boundary_rollover_freezes_last_rating_and_preserves_issued_values(self):
        periods = RankingPeriods()
        periods.record(MONDAY, {"a": (20, 201), "b": (0, 203)})
        periods.record(MONDAY + WEEK - 0.25, {"a": (10, 198)})
        issued = periods.historical_stats()
        periods.record(MONDAY + WEEK, {"a": (7, 195), "c": (0, 205)})
        self.assertEqual(periods.snapshot()["week_start"], MONDAY + WEEK)
        self.assertEqual(
            periods.historical_stats(),
            {
                "a": (7, 198),
                "b": (None, 203),
                "c": (0, None),
            },
        )
        periods.record(MONDAY + WEEK + 1, {"a": (1, 192)})
        self.assertEqual(periods.historical_stats()["a"][1], 198)
        self.assertEqual(issued["a"], (30, None))

    def test_skipped_weeks_and_an_empty_week_clear_previous(self):
        for jump in (2, 5):
            with self.subTest(jump=jump):
                periods = RankingPeriods()
                periods.record(MONDAY, {"a": (10, 198)})
                periods.advance(MONDAY + jump * WEEK)
                self.assertEqual(periods.historical_stats(), {})
                self.assertEqual(periods.account_keys, frozenset())
        periods = RankingPeriods()
        periods.record(MONDAY, {"a": (10, 198)})
        periods.advance(MONDAY + WEEK)
        self.assertEqual(periods.historical_stats()["a"][1], 198)
        periods.advance(MONDAY + 2 * WEEK)
        self.assertEqual(periods.historical_stats(), {})

    def test_json_restart_on_each_side_of_rollover(self):
        periods = RankingPeriods()
        periods.record(MONDAY + WEEK - 1, {"a": (10, 198), "b": (0, 201)})
        restored = RankingPeriods.restore(json.loads(json.dumps(periods.snapshot())))
        self.assertFalse(restored.advance(MONDAY + WEEK - 0.5))
        self.assertTrue(restored.advance(MONDAY + WEEK))
        after_rollover = RankingPeriods.restore(json.loads(json.dumps(restored.snapshot())))
        self.assertFalse(after_rollover.advance(MONDAY + WEEK))
        self.assertEqual(after_rollover.snapshot(), restored.snapshot())
        after_rollover.record(MONDAY + WEEK + 1, {"a": (4, 195)})
        self.assertEqual(
            after_rollover.historical_stats()["a"],
            (4, 198),
        )
        self.assertEqual(periods.historical_stats()["a"], (10, None))

    def test_bad_final_contribution_does_not_roll_over_or_partly_apply(self):
        periods = RankingPeriods()
        periods.record(MONDAY, {"a": (10, 198)})
        before = periods.snapshot()
        for key, value in (
            ("b", (-1, 200)),
            ("b", (MAX + 1, 200)),
            ("b", (True, 200)),
            ("b", (1, False)),
            ("b", (1, MAX + 1)),
            ("b", (1,)),
            ("b", None),
            ("", (1, 200)),
            ("x" * 129, (1, 200)),
            (7, (1, 200)),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaises(RankingPeriodError):
                    periods.record(MONDAY + WEEK, {"a": (3, 195), key: value})
                self.assertEqual(periods.snapshot(), before)
        with self.assertRaises(RankingPeriodError):
            periods.record(MONDAY + WEEK, [("a", (3, 195))])
        self.assertEqual(periods.snapshot(), before)

    def test_bad_clock_and_backwards_week_leave_state_unchanged(self):
        periods = RankingPeriods()
        periods.record(MONDAY, {"a": (10, 198)})
        before = periods.snapshot()
        for now in (-1, MAX + 1, True, "123", None, float("nan"), float("inf"), MONDAY - 1):
            with self.subTest(now=now):
                with self.assertRaises(RankingPeriodError):
                    periods.advance(now)
                with self.assertRaises(RankingPeriodError):
                    periods.record(now, {"a": (1, 195)})
                self.assertEqual(periods.snapshot(), before)

    def test_epoch_partial_week_and_last_native_timestamp(self):
        periods = RankingPeriods()
        periods.record(0.0, {"a": (1, 198)})
        self.assertEqual(periods.snapshot()["week_start"], -259200)
        restored = RankingPeriods.restore(periods.snapshot())
        self.assertFalse(restored.advance(345599.9))
        self.assertTrue(restored.advance(345600))  # Monday, January 5, 1970.
        self.assertEqual(restored.historical_stats()["a"][1], 198)
        self.assertTrue(restored.advance(float(MAX)))
        self.assertEqual(
            RankingPeriods.restore(restored.snapshot()).snapshot(), restored.snapshot()
        )

    def test_snapshot_and_restore_do_not_alias_mutable_data(self):
        periods = RankingPeriods()
        periods.record(MONDAY, {"a": (1, 198)})
        periods.record(MONDAY + WEEK, {"a": (2, 195)})
        snapshot = periods.snapshot()
        restored = RankingPeriods.restore(snapshot)
        snapshot["current"]["a"]["prize"] = 999
        snapshot["previous"]["a"] = 999
        self.assertEqual(periods.snapshot(), restored.snapshot())
        restored.record(MONDAY + WEEK, {"a": (3, 192)})
        self.assertEqual(periods.historical_stats()["a"][0], 2)
        self.assertEqual(restored.historical_stats()["a"][0], 5)

    def test_restore_rejects_malformed_schema_and_rows(self):
        periods = RankingPeriods()
        periods.record(MONDAY, {"a": (1, 198)})
        snapshot = periods.snapshot()
        bad_snapshots = [[], {}, dict(snapshot, extra=1)]
        for field, value in (
            ("version", True),
            ("version", 2),
            ("policy", "retail"),
            ("week_start", MONDAY + 1),
            ("week_start", True),
            ("week_start", -259201),
            ("week_start", MAX + WEEK),
            ("current", []),
            ("current", {"a": {"prize": 1}}),
            ("current", {"a": {"prize": 1, "rating": -1}}),
            ("current", {"a": {"prize": True, "rating": 198}}),
            ("current", {"": {"prize": 1, "rating": 198}}),
            ("previous", []),
            ("previous", {"b": MAX + 1}),
            ("previous", {"b": False}),
            ("previous", {9: 198}),
        ):
            bad_snapshots.append(dict(snapshot, **{field: value}))
        for bad in bad_snapshots:
            original = copy.deepcopy(bad)
            with self.subTest(snapshot=bad), self.assertRaises(RankingPeriodError):
                RankingPeriods.restore(bad)
            self.assertEqual(bad, original)
        self.assertEqual(periods.snapshot(), snapshot)


if __name__ == "__main__":
    unittest.main()
