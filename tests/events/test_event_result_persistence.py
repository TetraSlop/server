"""Saved event outcomes retain strict shapes, receipt order, and owner isolation."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.events import test_event_results as results_tests
from tmserver.events.matches import EventRoundOutcome
from tmserver.events.results import EventResults


# Captured from the pre-model writer, including receipt and seat ordering.
SAVED = (
    '{"version":1,"rounds":['
    '{"id":"local:0","event":"festival","characters":[1,2],"keys":["0","1"],'
    '"scores":[7,3],"classes":[1,3],"policy":"none"},'
    '{"id":"local:1","event":"festival","characters":[1,2],"keys":["0","1"],'
    '"scores":[5,5],"classes":[2,2],"policy":"wins"},'
    '{"id":"local:2","event":"festival","characters":[1,2,3],'
    '"keys":["0","1","2"],"scores":[6,6,3],"classes":[1,1,4],"policy":"wins"},'
    '{"id":"local:3","event":"festival","characters":[1,2],"keys":["0",null],'
    '"scores":[6,4],"classes":[1,3],"policy":"rounds"}]}'
)


class EventResultPersistenceTests(unittest.TestCase):
    setUp = results_tests.EventResultTests.setUp

    def test_recorded_json_and_restored_receipt_order_match_old_writer(self):
        expected = json.loads(SAVED)
        for row in expected["rounds"]:
            outcome = EventRoundOutcome(
                tuple(row["characters"]), tuple(row["scores"]), tuple(row["classes"]), False
            )
            self.assertTrue(
                self.journal.record(
                    self.host, row["event"], row["id"], outcome, iter(row["keys"]), row["policy"]
                )
            )
        self.assertEqual(json.dumps(self.journal.snapshot(), separators=(",", ":")), SAVED)
        restored = EventResults.restore(json.loads(SAVED))
        self.assertEqual(json.dumps(restored.snapshot(), separators=(",", ":")), SAVED)

    def test_legacy_version_equivalence_and_identifier_key_boundaries(self):
        for version in (1, True, 1.0):
            saved = json.loads(SAVED)
            saved["version"] = version
            self.assertEqual(EventResults.restore(saved).snapshot(), json.loads(SAVED))
        saved = json.loads(SAVED)
        saved["rounds"] = [saved["rounds"][0]]
        row = saved["rounds"][0]
        row.update(id="a" * 128, event="_-.:AZaz09", characters=[1, 0x7FFFFFFF])
        row["keys"] = ["\u00e9" * 128, "\n\u0000"]
        self.assertEqual(EventResults.restore(saved).snapshot(), saved)

    def test_missing_unknown_fields_and_non_arrays_are_rejected(self):
        saved = json.loads(SAVED)
        for field in saved["rounds"][0]:
            bad = copy.deepcopy(saved)
            del bad["rounds"][0][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                EventResults.restore(bad)
        for field in ("version", "rounds"):
            bad = copy.deepcopy(saved)
            del bad[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                EventResults.restore(bad)
        bad = copy.deepcopy(saved)
        bad["rounds"][0]["extra"] = 0
        malformed = [[], {**saved, "extra": 0}, bad, {**saved, "rounds": ()}]
        for version in (None, "1", 0, 2):
            malformed.append({**saved, "version": version})
        for bad in malformed:
            with self.subTest(snapshot=bad), self.assertRaises(ValueError):
                EventResults.restore(bad)
        for field in ("characters", "keys", "scores", "classes"):
            for value in (None, {}, "1,2", tuple(saved["rounds"][0][field])):
                bad = copy.deepcopy(saved)
                bad["rounds"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    EventResults.restore(bad)

    def test_numeric_seat_fields_remain_strict_and_bounded(self):
        for field in ("characters", "scores", "classes"):
            valid = json.loads(SAVED)["rounds"][0][field][0]
            for value in (True, False, float(valid), str(valid), None):
                bad = json.loads(SAVED)
                bad["rounds"][0][field][0] = value
                if field == "scores" and type(value) is bool:
                    # If coerced, these scores would otherwise be a valid result.
                    bad["rounds"][0]["scores"] = [value, 10 - int(value)]
                    bad["rounds"][0]["classes"] = [3, 1]
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    EventResults.restore(bad)
        for field, values in (
            ("characters", [0, 2]),
            ("characters", [-1, 2]),
            ("characters", [1, 0x80000000]),
            ("scores", [-1, 11]),
            ("scores", [26, -16]),
        ):
            bad = json.loads(SAVED)
            bad["rounds"][0][field] = values
            with self.subTest(field=field, values=values), self.assertRaises(ValueError):
                EventResults.restore(bad)

    def test_completed_outcome_invariants_are_still_checked_on_restore(self):
        for field, value in (
            ("id", "a\n"),
            ("id", ""),
            ("id", "a" * 129),
            ("event", "\u00e9"),
            ("characters", [1, 1]),
            ("characters", [1]),
            ("keys", [None, None]),
            ("keys", ["0", "0"]),
            ("keys", ["0"]),
            ("keys", ["", None]),
            ("keys", ["a" * 129, None]),
            ("keys", [1, None]),
            ("scores", [7, 4]),
            ("scores", [5]),
            ("scores", [5, 5, 5, 5]),
            ("classes", [2, 2]),
            ("classes", [1]),
            ("policy", "automatic"),
        ):
            bad = json.loads(SAVED)
            bad["rounds"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                EventResults.restore(bad)
        bad = json.loads(SAVED)
        bad["rounds"].append(copy.deepcopy(bad["rounds"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate event result receipt"):
            EventResults.restore(bad)

    def test_restore_snapshot_and_staged_publish_keep_owned_receipts_isolated(self):
        source = json.loads(SAVED)
        journal = EventResults.restore(source)
        source["rounds"][0]["scores"][0] = 0
        detached = journal.snapshot()
        for field in ("characters", "keys", "scores", "classes"):
            detached["rounds"][0][field].clear()
        detached["rounds"].pop()
        self.assertEqual(journal.snapshot(), json.loads(SAVED))
        staged = journal.stage_copy()
        self.assertIs(staged._rows["local:0"], journal._rows["local:0"])
        outcome = EventRoundOutcome((1, 2), (7, 3), (1, 3), False)
        staged.record(self.host, "festival", "new", outcome, ("0", "1"))
        self.assertEqual(journal.snapshot(), json.loads(SAVED))
        journal.publish_staged(staged)
        self.assertEqual(journal.snapshot()["rounds"][-1]["id"], "new")


if __name__ == "__main__":
    unittest.main()
