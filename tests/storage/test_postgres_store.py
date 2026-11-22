"""Real PostgreSQL tests; set TM_TEST_DATABASE_URL to a disposable test host.

Each test creates and drops its own randomly named database. The configured
database is only the administrative connection; its tables are never modified.
"""

import hashlib
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

try:
    import sqlalchemy as sa
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
except ImportError as exc:
    raise unittest.SkipTest("install tmserver[postgres] for PostgreSQL tests") from exc

from tmserver.storage.database import upgrade
from tmserver.storage.validation import validate_snapshot
from tmserver.storage.schema import metadata, state_meta
from tmserver.economy.service import EconomyService
from tmserver.storage.postgres import PostgresStateStore, database_engine
from tmserver.network.server import Server

TEST_URL = os.environ.get("TM_TEST_DATABASE_URL")


class DatabaseFixture:
    def setUp(self):
        name = "tm_test_" + uuid4().hex
        self.admin = database_engine(TEST_URL).execution_options(isolation_level="AUTOCOMMIT")
        with self.admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        self.addCleanup(self.drop_database, name)
        self.url = self.admin.url.set(database=name)
        upgrade(self.url)
        self.store = self.enterContext(PostgresStateStore(self.url))

    def drop_database(self, name):
        with self.admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        self.admin.dispose()

    def reopen(self):
        self.store.close()
        self.store = self.enterContext(PostgresStateStore(self.url))
        return self.store


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class PostgresStoreTests(DatabaseFixture, unittest.TestCase):
    def sample(self):
        game = Server()
        for nick in game.accounts:
            game.ensure_account(nick)
        return game.snapshot()

    def test_roundtrip_preserves_duplicate_cards_unsigned_identity_and_all_sections(
        self,
    ):
        snapshot = self.sample()
        key = next(iter(snapshot["accounts"]))
        account = snapshot["accounts"][key]
        account["nn"] = (1 << 64) - 1
        account["cards"] += [account["cards"][0]] * 2
        template = bytearray.fromhex(account["template_hex"])
        template[0x2FFF] = 0xA7
        account["template_hex"] = template.hex()
        before = validate_snapshot(snapshot)
        self.store.save(snapshot)
        restored = self.reopen().load()
        self.assertEqual(restored, snapshot)
        after = validate_snapshot(restored)
        for key in snapshot["accounts"]:
            self.assertEqual(after.profile_bytes(key), before.profile_bytes(key))

    def test_diff_does_not_rewrite_unchanged_accounts_or_cards(self):
        snapshot = self.sample()
        self.store.save(snapshot)

        def versions():
            engine = database_engine(self.url)
            try:
                with engine.connect() as connection:
                    return {
                        table: connection.exec_driver_sql(
                            f"SELECT xmin::text, ctid::text FROM {table} ORDER BY ctid"
                        ).all()
                        for table in ("accounts", "cards", "service_state")
                    }
            finally:
                engine.dispose()

        before = versions()
        revision = self.store.revision
        self.store.save(snapshot)
        self.assertEqual(versions(), before)
        self.assertEqual(self.store.revision, revision)
        key = min(snapshot["accounts"])
        snapshot["accounts"][key]["balance"] -= 10
        self.store.save(snapshot)
        after = versions()
        self.assertEqual(after["cards"], before["cards"])
        self.assertEqual(after["service_state"], before["service_state"])
        self.assertEqual(len(set(before["accounts"]) & set(after["accounts"])), 1)

    def test_constraint_failure_rolls_back_both_accounts_and_journal(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        changed = deepcopy(snapshot)
        one, two = sorted(changed["accounts"])
        changed["accounts"][one]["balance"] -= 100
        changed["accounts"][two]["balance"] = -1
        changed["match_economy"]["rounds"] = [{"id": "must-not-commit"}]
        with self.assertRaises(OSError):
            self.store.save(changed)
        with self.assertRaises(OSError):
            self.store.save(snapshot)
        self.assertEqual(self.reopen().load(), snapshot)

    def test_journal_failure_rolls_back_prior_inventory_updates(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        changed = deepcopy(snapshot)
        key = next(iter(changed["accounts"]))
        changed["accounts"][key]["cards"].pop()
        changed["event_results"] = {"unserializable": object()}
        with self.assertRaises(OSError):
            self.store.save(changed)
        self.assertEqual(self.reopen().load(), snapshot)

    def test_writer_lock_blocks_second_server_and_migrations_then_releases(self):
        with self.assertRaisesRegex(OSError, "another Tetra Master writer"):
            PostgresStateStore(self.url)
        with self.assertRaisesRegex(OSError, "stop the game server"):
            upgrade(self.url)
        self.store.close()
        upgrade(self.url)
        self.reopen().save(self.sample())

    def test_schema_matches_models_and_upgrade_is_repeatable(self):
        self.store.close()
        upgrade(self.url)
        upgrade(self.url)
        engine = database_engine(self.url)
        try:
            with engine.connect() as connection:
                differences = compare_metadata(MigrationContext.configure(connection), metadata)
                self.assertEqual(differences, [])
        finally:
            engine.dispose()

    def test_outside_revision_change_rejects_even_noop_save(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        engine = database_engine(self.url)
        try:
            with engine.begin() as connection:
                connection.execute(state_meta.update().values(revision=state_meta.c.revision + 1))
        finally:
            engine.dispose()
        with self.assertRaises(OSError):
            self.store.save(snapshot)
        self.assertEqual(self.reopen().load(), snapshot)

    def test_connection_loss_never_reconnects_writer(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        with self.store.connection.begin():
            pid = self.store.connection.scalar(sa.text("SELECT pg_backend_pid()"))
        with self.admin.connect() as connection:
            connection.execute(sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with self.assertRaises(OSError):
            self.store.save(snapshot)
        with self.assertRaises(OSError):
            self.store.load()
        self.assertEqual(self.reopen().load(), snapshot)

    def test_lost_commit_acknowledgement_reloads_committed_state_without_retry(self):
        import psycopg

        snapshot = self.sample()
        self.store.save(snapshot)
        key = next(iter(snapshot["accounts"]))
        snapshot["accounts"][key]["balance"] += 75
        original = psycopg.Connection.commit

        def commit_then_lose_ack(connection):
            original(connection)
            raise psycopg.OperationalError("injected lost COMMIT acknowledgement")

        with (
            patch.object(psycopg.Connection, "commit", commit_then_lose_ack),
            self.assertRaises(OSError),
        ):
            self.store.save(snapshot)
        with self.assertRaises(OSError):
            self.store.save(snapshot)
        self.assertEqual(self.reopen().load(), snapshot)

    def test_older_economy_only_save_retains_missing_optional_sections(self):
        economy = EconomyService(seed=1)
        economy.provision("old", nn=(1 << 64) - 2)
        self.store.save(economy.snapshot())
        self.assertEqual(self.reopen().load(), economy.snapshot())

    def test_loaded_copy_cannot_change_writer_baseline(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        self.store.load()["accounts"].clear()
        snapshot["accounts"].clear()
        self.assertEqual(len(self.store.load()["accounts"]), 2)

    def test_interrupted_stakes_refund_exactly_once_on_server_restart(self):
        game = Server(store=self.store)
        keys = [game.ensure_account(nick) for nick in game.accounts]
        tokens = {seat: game.economy.reserve_match(key) for seat, key in enumerate(keys)}
        game.match_economy.begin("interrupted", tokens, 200, mode="versus", player_count=2)
        game.persist()
        self.assertEqual([game.economy.balance(k) for k in keys], [9800, 9800])
        # Closing the storage connection simulates process loss without orderly
        # table cleanup; the next Server must do and persist economic recovery.
        restored = Server(store=self.reopen())
        self.assertEqual([restored.economy.balance(k) for k in keys], [10000, 10000])
        recovered = restored.snapshot()
        self.assertEqual(Server(store=self.reopen()).snapshot(), recovered)


if __name__ == "__main__":
    unittest.main()
