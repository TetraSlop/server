"""Corrections share a real PostgreSQL transaction with their audit receipt."""

import unittest

from tests.admin.test_actions_postgres import PostgresActionCases
from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class EventMemberPostgresTests(PostgresActionCases, DatabaseFixture, unittest.TestCase):
    journals = {"events"}

    def setUp(self):
        super().setUp()
        from tests.events.test_event_rooms import config
        from tmserver.admin.actions import apply_event_member
        from tmserver.network.server import Server

        self.apply = apply_event_member
        self.options = dict(event_configs=(config(),), event_clock=lambda: 200)
        self.host = Server(store=self.store, **self.options)
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))
        self.host.events.admit_room(self.key, "festival")
        self.host.persist()

    def restart(self):
        from tmserver.network.server import Server

        self.host = Server(store=self.reopen(), **self.options)

    def request(self, **changes):
        from tests.admin.test_member_actions import request

        return request(**({"account_key": self.key} | changes))
