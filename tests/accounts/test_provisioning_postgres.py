"""Registration commits the account and its password hash in the player-save transaction."""

import asyncio
import unittest
from unittest.mock import patch

from tests.accounts.test_provisioning import granted
from tests.storage import test_postgres_store as database_tests
from tmserver.network.server import Server


@unittest.skipUnless(database_tests.TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class RegistrationPostgresTests(database_tests.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.host = Server(store=self.store)

    def login(self):
        return asyncio.run(self.host.login("192.0.2.1", b"Alice", b"password"))

    def restart(self):
        self.host = Server(store=self.reopen())

    def test_restart_signs_in_without_reissuing_starter_cards(self):
        _, key, _, outcome = granted(self.login())
        self.assertEqual(outcome, b"created")
        saved, revision = self.store.load(), self.store.revision
        self.restart()
        with patch.object(self.store, "save_changes", side_effect=AssertionError("sign-in wrote")):
            self.assertEqual(granted(self.login())[1:4:2], (key, b"signed_in"))
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(len(saved["accounts"][key]["cards"]), 10)
        self.assertEqual(saved["accounts"][key]["balance"], 10000)

    def test_credential_journal_failure_rolls_back_account_and_starter_cards(self):
        from tmserver.storage.schema import accounts, cards

        saved, live = self.store.load(), self.host.snapshot()

        def fail(journals):
            self.assertEqual(set(journals), {"identities"})
            # The account and cards were written inside the transaction already.
            self.assertEqual(self.store.connection.scalar(
                database_tests.sa.select(database_tests.sa.func.count()).select_from(accounts)), 1)
            self.assertEqual(self.store.connection.scalar(
                database_tests.sa.select(database_tests.sa.func.count()).select_from(cards)), 10)
            raise OSError("injected credential journal failure")

        with patch.object(self.store, "_save_services", fail):
            self.assertEqual(self.login(), b":srv 901 unavailable :login refused")
        self.assertEqual(self.host.snapshot(), live)
        self.restart()
        self.assertEqual(self.store.load(), saved)
        self.assertIsNone(self.host.credentials.account("alice"))
        self.assertEqual(granted(self.login())[3], b"created")

    def test_lost_commit_ack_keeps_the_account_for_the_next_sign_in(self):
        import psycopg

        commit = psycopg.Connection.commit
        live, revision = self.host.snapshot(), self.store.revision

        def lost_ack(connection):
            commit(connection)
            raise psycopg.OperationalError("injected lost acknowledgement")

        with patch.object(psycopg.Connection, "commit", lost_ack):
            self.assertEqual(self.login(), b":srv 901 unavailable :login refused")
        self.assertEqual(self.host.snapshot(), live)
        self.restart()
        saved = self.store.load()
        with patch.object(self.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(granted(self.login())[3], b"signed_in")
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.store.revision, revision + 1)
