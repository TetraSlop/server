"""Offline administrator commands against disposable PostgreSQL databases."""

import io
import json
import unittest
from contextlib import redirect_stdout

from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture
from tmserver.cli import admin
from tmserver.economy.service import EconomyService
from tmserver.storage.postgres import PostgresStateStore


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class AdminPostgresTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.economy = EconomyService(seed=37)
        for nn, key in enumerate(("alice", "bob"), 1):
            self.economy.provision(key, nn=nn, player_id=nn, name=key.encode())
        self.store.save(self.economy.snapshot())
        self.store.close()  # Each command owns its own real writer connection.

    def argv(self, *command):
        return ["--database-url", self.url.render_as_string(hide_password=False), *command]

    def execute(self, *command):
        output = io.StringIO()
        with redirect_stdout(output):
            admin.cli(self.argv(*command))
        return json.loads(output.getvalue())

    def saved(self):
        with PostgresStateStore(self.url) as store:
            return store.load()

    def prize(self, amount=500):
        return (
            "prize-award", "--account-key", "alice", "--id", "award-1", "--top30", str(amount),
            "--request-id", "pg-request", "--operator", "fixture", "--reason", "Weekly award",
        )

    def test_award_reload_and_exact_retry_keep_accounts_and_one_receipt(self):
        before = self.saved()
        receipt = self.execute(*self.prize())
        self.assertEqual(receipt["status"], "queued")
        after = self.saved()
        self.assertEqual(after["accounts"], before["accounts"])
        self.assertEqual(after["admin_audit"]["receipts"], [receipt])
        self.assertEqual(self.execute(*self.prize()), receipt)
        self.assertEqual(self.execute("operator-receipt", "--request-id", "pg-request"), receipt)
        with self.assertRaises(SystemExit):
            self.execute(*self.prize(900))
        self.assertEqual(self.saved(), after)

    def test_active_writer_rejects_command_without_success_then_releases(self):
        with PostgresStateStore(self.url) as owner:
            before = owner.load()
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit):
                admin.cli(self.argv(*self.prize()))
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(owner.load(), before)
        self.assertEqual(self.execute(*self.prize())["status"], "queued")
