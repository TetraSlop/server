"""Pin the saved document byte for byte: every journal plus the accounts.

A failure here means the save format changed. If that is intended, regenerate
the fixtures with `python -m tests.support.golden_save` and review the diff.
"""

import difflib
import json
import unittest

from tests.support.golden_save import (
    EVENT,
    SNAPSHOT,
    build,
    canonical,
    document,
    restore_document,
)
from tests.support.memory_store import MemoryStore
from tmserver.storage.validation import SERVICE_NAMES, validate_snapshot


class GoldenDocument:
    def assert_document(self, actual, expected):
        if actual != expected:
            diff = difflib.unified_diff(
                expected.splitlines(), actual.splitlines(), "golden", "actual", lineterm="", n=2
            )
            self.fail(
                "saved document differs from the golden fixture; regenerate it with "
                "`python -m tests.support.golden_save` only if the change is intended\n"
                + "\n".join(list(diff)[:60])
            )


class GoldenSaveTests(GoldenDocument, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = SNAPSHOT.read_bytes().decode("utf8")
        cls.store = MemoryStore()
        cls.server = build(cls.store)

    def test_golden_covers_every_journal_with_entries(self):
        snapshot = json.loads(self.golden)
        self.assertEqual(list(snapshot)[:2], ["version", "accounts"])
        self.assertEqual(set(snapshot) - {"version", "accounts"}, SERVICE_NAMES)
        self.assertEqual(len(snapshot["accounts"]), 4)
        for name in SERVICE_NAMES:
            with self.subTest(journal=name):
                journal = snapshot[name]
                self.assertTrue(any(value not in ({}, [], None) for key, value in journal.items()
                                    if key != "version"))
        validate_snapshot(snapshot, event_configs=(EVENT,))

    def test_scripted_run_writes_the_golden_document(self):
        self.assert_document(document(self.server.snapshot()), self.golden)

    def test_scoped_commits_saved_the_same_document(self):
        # Each transaction persisted only what it changed; together they are the whole save.
        self.assertEqual(canonical(self.store.load()), canonical(json.loads(self.golden)))

    def test_restore_then_snapshot_reproduces_the_golden_bytes(self):
        self.assert_document(document(restore_document(json.loads(self.golden))), self.golden)


if __name__ == "__main__":
    unittest.main()
