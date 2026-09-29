"""Durable identity allocation uses the same transaction and writer as player saves."""

from pathlib import Path
import unittest
from unittest.mock import patch

from tests.accounts.test_provisioning import ISSUER, USER
from tests.storage import test_postgres_store as database_tests
from tmserver.accounts.provisioning import import_bindings, provision_identity
from tmserver.network.server import Server


@unittest.skipUnless(database_tests.TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class IdentityPostgresTests(database_tests.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.host = Server(store=self.store)

    def create(self):
        return provision_identity(self.host, ISSUER, USER, name="Discord Player")

    def restart(self):
        self.host = Server(store=self.reopen())

    def downgrade(self):
        from alembic import command
        from alembic.config import Config
        from tmserver.storage import database
        from tmserver.storage.schema import WRITER_LOCK

        engine = database.database_engine(self.url)
        try:
            with engine.begin() as connection:
                self.assertTrue(connection.scalar(database_tests.sa.text(
                    "SELECT pg_try_advisory_xact_lock(:key)"), {"key": WRITER_LOCK}))
                config = Config()
                config.set_main_option("script_location", str(Path(database.__file__).with_name("migrations")))
                config.attributes["connection"] = connection
                command.downgrade(config, "0002_admin_audit")
        finally:
            engine.dispose()

    def test_restart_returns_same_account_without_reissuing_starter_cards(self):
        key = self.create()
        saved, revision = self.store.load(), self.store.revision
        self.restart()
        with patch.object(self.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(self.create(), key)
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(len(saved["accounts"][key]["cards"]), 10)
        self.assertEqual(saved["accounts"][key]["balance"], 10000)

    def test_identity_journal_failure_rolls_back_account_and_starter_cards(self):
        from tmserver.storage.schema import accounts, cards

        saved, live = self.store.load(), self.host.snapshot()

        def fail(snapshot, previous, *, names):
            self.assertEqual(set(names), {"identities"})
            # The account and cards were written inside the transaction already.
            self.assertEqual(self.store.connection.scalar(
                database_tests.sa.select(database_tests.sa.func.count()).select_from(accounts)), 1)
            self.assertEqual(self.store.connection.scalar(
                database_tests.sa.select(database_tests.sa.func.count()).select_from(cards)), 10)
            raise OSError("injected identity journal failure")

        with patch.object(self.store, "_save_services", fail), self.assertRaises(OSError):
            self.create()
        self.assertEqual(self.host.snapshot(), live)
        self.restart()
        self.assertEqual(self.store.load(), saved)
        self.assertIsNone(self.host.identities.account(ISSUER, USER))
        self.assertIsNotNone(self.create())

    def test_lost_commit_ack_reloads_saved_owner_before_retry(self):
        import psycopg

        commit = psycopg.Connection.commit
        live, revision = self.host.snapshot(), self.store.revision

        def lost_ack(connection):
            commit(connection)
            raise psycopg.OperationalError("injected lost acknowledgement")

        with patch.object(psycopg.Connection, "commit", lost_ack), self.assertRaises(OSError):
            self.create()
        self.assertEqual(self.host.snapshot(), live)
        self.restart()
        saved = self.store.load()
        with patch.object(self.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(self.create(), self.host.identities.account(ISSUER, USER))
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.store.revision, revision + 1)

    def test_import_retains_existing_save_and_protects_account_from_reassignment(self):
        key = self.host.ensure_account(next(iter(self.host.accounts)), name=b"Existing")
        saved = self.store.load()["accounts"]
        import_bindings(self.host, ISSUER, {USER: key})
        self.restart()
        self.assertEqual(self.create(), key)
        self.assertEqual(self.store.load()["accounts"], saved)
        with self.assertRaises(ValueError):
            import_bindings(self.host, ISSUER, {"22222222-2222-4222-8222-222222222222": key})

    def test_empty_registry_can_downgrade_and_upgrade_without_changing_accounts(self):
        from tmserver.storage.database import upgrade

        self.host.ensure_account(next(iter(self.host.accounts)))
        saved = self.store.load()
        self.store.close()
        self.downgrade()
        upgrade(self.url)
        self.restart()
        self.assertEqual(self.store.load(), {name: value for name, value in saved.items() if name != "identities"})

    def test_downgrade_refuses_to_discard_ownership(self):
        from tmserver.storage.schema import SCHEMA_REVISION

        self.create()
        saved = self.store.load()
        self.store.close()
        with self.assertRaisesRegex(RuntimeError, "identity ownership"):
            self.downgrade()
        self.restart()
        self.assertEqual(self.store.load(), saved)
        with self.store.connection.begin():
            self.assertEqual(self.store.connection.scalar(database_tests.sa.text(
                "SELECT version_num FROM alembic_version")), SCHEMA_REVISION)
