"""Pin the PostgreSQL rows, journals and export against the golden document."""

import json
import unittest

from tests.storage.test_golden_save import GoldenDocument
from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture
from tests.support.golden_save import (
    EXPORT,
    SNAPSHOT,
    build,
    canonical,
    document,
    postgres_export,
    restore_document,
)


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class GoldenPostgresTests(GoldenDocument, DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.golden = SNAPSHOT.read_bytes().decode("utf8")

    def test_import_then_export_is_the_golden_export(self):
        exported = postgres_export(self.store, json.loads(self.golden))
        self.assert_document(exported, EXPORT.read_bytes().decode("utf8"))
        # The export restores to the same saved document. PostgreSQL returns
        # accounts by key, so only their order differs from the golden.
        golden = json.loads(self.golden)
        golden["accounts"] = dict(sorted(golden["accounts"].items()))
        self.assert_document(document(restore_document(json.loads(exported))), document(golden))

    def test_scripted_run_stores_the_golden_rows_and_journals(self):
        build(self.store)
        # Read back from SQL in a fresh writer, not from the writer's cache.
        saved = self.reopen().load()
        expected = json.loads(self.golden)
        self.assertEqual(list(saved["accounts"]), sorted(expected["accounts"]))
        for name, value in expected.items():
            with self.subTest(section=name):
                self.assertEqual(canonical(saved[name]), canonical(value))
        self.assertEqual(set(saved), set(expected))


if __name__ == "__main__":
    unittest.main()
