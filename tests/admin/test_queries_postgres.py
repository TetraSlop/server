"""Offline operator reads over real PostgreSQL never write or run startup recovery."""

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.events.test_event_rooms import config
from tests.rooms.test_ordinary_rooms import EXTRA
from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture, sa
from tests.support.memory_store import MemoryStore
from tmserver.admin.queries import account_summary
from tmserver.cli.admin import AdminState
from tmserver.network.server import Server
from tmserver.storage.schema import state_meta


def host_options():
    return dict(
        account_count=3,
        event_configs=(config(card_service_enabled=True),),
        event_clock=lambda: 200,
        ordinary_rooms=(EXTRA,),
    )


def saved_fixture():
    host = Server(**host_options())
    keys = [host.ensure_account(nick) for nick in host.accounts]
    key = keys[0]
    host.economy.provision("joueur-é_日本", nn=987654, player_id=77, name=b"Guest")
    host.account_keys.add("joueur-é_日本")
    host.prize.grant_award(key, "weekly", top30=10, lucky=3, rookies=2)
    host.events.admit_room(key, "festival")
    cards = tuple(replace(card, deck=255) for card in host.economy.inventory(key)[:2])
    host.event_cards.grant(key, "festival", "event-award", cards=cards, money=75)
    host.auction_ledger.enqueue_returned(key, "interrupted", cards[:1])
    return host, keys


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class PostgresQueryTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.memory = MemoryStore()
        self.source, self.keys = saved_fixture()
        snapshot = self.source.snapshot()
        self.store.save(snapshot)
        self.memory.save(snapshot)

    def revision(self):
        with self.store.connection.begin():
            return self.store.connection.scalar(sa.select(state_meta.c.revision))

    def prohibit_storage(self, stack):
        for store in (self.store, self.memory):
            for method in ("load", "save", "save_changes"):
                stack.enter_context(patch.object(store, method, side_effect=AssertionError(method)))

        def no_sql(*args, **kwargs):
            raise AssertionError("operator projection attempted database I/O")

        sa.event.listen(self.store.engine, "before_cursor_execute", no_sql)
        stack.callback(sa.event.remove, self.store.engine, "before_cursor_execute", no_sql)

    def test_offline_escrow_inspection_does_not_run_startup_recovery(self):
        tokens = {seat: self.source.economy.reserve_match(key)
                  for seat, key in enumerate(self.keys[:2])}
        self.source.match_economy.begin("open-round", tokens, 50)
        snapshot = self.source.snapshot()
        self.store.save(snapshot)
        self.memory.save(snapshot)
        saved, revision, raw = self.store.load(), self.revision(), self.memory.load()
        configs = host_options()["event_configs"]
        with ExitStack() as stack:
            self.prohibit_storage(stack)
            offline = AdminState(self.store, deepcopy(saved), configs)
            local = AdminState(self.memory, deepcopy(snapshot), configs)
            summary = account_summary(offline, self.keys[0])
            self.assertEqual(summary, account_summary(local, self.keys[0]))
            self.assertEqual(summary["match_rounds"], [dict(
                id="open-round", seat=0, money="escrow", escrowed_money=50,
                has_result=False, cards_settled=False, pending_cards=0,
            )])
            self.assertEqual(summary["balance"], snapshot["accounts"][self.keys[0]]["balance"])
            self.assertEqual(summary["auction_claims"]["returned_cards"], 1)
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.revision(), revision)
        self.assertEqual(self.memory.load(), raw)
