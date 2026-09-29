"""Corrections share a real PostgreSQL transaction with their audit receipt."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture, sa


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class EventMemberPostgresTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from tests.events.test_event_rooms import config
        from tmserver.network.server import Server

        self.options = dict(event_configs=(config(),), event_clock=lambda: 200)
        self.host = Server(store=self.store, **self.options)
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))
        self.host.events.admit_room(self.key, "festival")
        self.host.persist()

    def restart(self):
        from tmserver.network.server import Server

        self.host = Server(store=self.reopen(), **self.options)

    def command(self, **changes):
        from tests.admin.test_member_actions import request

        return request(account_key=self.key, **changes)

    def test_both_actions_reload_and_retry_without_incrementing_revision(self):
        from tmserver.admin.member_actions import apply_event_member, preview_event_member

        before = self.store.load()
        commands = (self.command(), self.command(kind="eligibility", request_id="exclude"))
        receipts = []
        for command in commands:
            receipts.append(apply_event_member(self.host, command, preview_event_member(self.host, command).digest))
        revision = self.store.revision
        self.restart()
        for command, receipt in zip(commands, receipts):
            self.assertEqual(apply_event_member(self.host, command, "old digest"), receipt)
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(self.store.load()["accounts"], before["accounts"])
        member = self.host.events.member(self.key, "festival")
        self.assertEqual((member.steps, member.eligibility), (3, -1))

    def test_failure_between_journals_rolls_back_both(self):
        from tmserver.admin.member_actions import apply_event_member, preview_event_member
        from tmserver.storage.schema import service_state

        command = self.command()
        preview = preview_event_member(self.host, command)
        before, saved, revision = deepcopy(self.host.snapshot()), self.store.load(), self.store.revision
        save = self.store._save_services

        def fail_between(snapshot, previous, *, names=None):
            save(snapshot, previous, names=("events",))
            inside = self.store.connection.scalar(sa.select(service_state.c.payload).where(
                service_state.c.name == "events"
            ))
            self.assertNotEqual(inside, previous["events"])
            raise OSError("injected failure between membership and audit")

        with patch.object(self.store, "_save_services", fail_between), self.assertRaises(OSError):
            apply_event_member(self.host, command, preview.digest)
        self.assertEqual(self.host.snapshot(), before)
        self.restart()
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.store.revision, revision)
        self.assertIsNone(self.host.admin_audit.get(command.request_id))

    def test_lost_commit_acknowledgement_reloads_joint_receipt(self):
        import psycopg
        from tmserver.admin.member_actions import apply_event_member, preview_event_member

        command = self.command()
        preview = preview_event_member(self.host, command)
        before, revision = self.host.snapshot(), self.store.revision
        commit = psycopg.Connection.commit

        def lost_ack(connection):
            commit(connection)
            raise psycopg.OperationalError("lost event correction COMMIT acknowledgement")

        with patch.object(psycopg.Connection, "commit", lost_ack), self.assertRaises(OSError):
            apply_event_member(self.host, command, preview.digest)
        self.assertEqual(self.host.snapshot(), before)
        with self.assertRaises(OSError):
            apply_event_member(self.host, command, preview.digest)
        self.restart()
        receipt = self.host.admin_audit.get(command.request_id)
        self.assertEqual(receipt.request, command)
        self.assertEqual(apply_event_member(self.host, command, preview.digest), receipt)
        self.assertEqual(self.store.revision, revision + 1)
