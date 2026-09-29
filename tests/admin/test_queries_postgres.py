"""Operator reads agree across real PostgreSQL and JSON without acquiring a writer."""

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.events.test_event_rooms import config, presence
from tests.rooms.test_ordinary_rooms import EXTRA
from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture, sa
from tmserver.admin.queries import (
    account_detail,
    account_rows,
    account_summary,
    claim_rows,
    room_rows,
    server_overview,
)
from tmserver.cli.admin import AdminState
from tmserver.network.server import Server
from tmserver.storage.json import JsonStateStore
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
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.json = JsonStateStore(Path(folder) / "state.json")
        self.source, self.keys = saved_fixture()
        snapshot = self.source.snapshot()
        self.store.save(snapshot)
        self.json.save(snapshot)

    def revision(self):
        with self.store.connection.begin():
            return self.store.connection.scalar(sa.select(state_meta.c.revision))

    def prohibit_storage(self, stack):
        for store in (self.store, self.json):
            for method in ("load", "save", "save_accounts", "save_changes"):
                stack.enter_context(patch.object(store, method, side_effect=AssertionError(method)))

        def no_sql(*args, **kwargs):
            raise AssertionError("operator projection attempted database I/O")

        sa.event.listen(self.store.engine, "before_cursor_execute", no_sql)
        stack.callback(sa.event.remove, self.store.engine, "before_cursor_execute", no_sql)

    def test_live_and_offline_claims_match_json_without_database_io_or_writes(self):
        postgres = Server(store=self.store, **host_options())
        local = Server(store=self.json, **host_options())
        configs = host_options()["event_configs"]
        saved = self.store.load()
        offline = AdminState(self.store, deepcopy(saved), configs)
        json_offline = AdminState(self.json, self.json.load(), configs)
        revision, raw = self.revision(), self.json.path.read_bytes()
        before = deepcopy(postgres.snapshot())
        with ExitStack() as stack:
            self.prohibit_storage(stack)
            self.assertEqual(account_rows(postgres), account_rows(local))
            self.assertEqual(claim_rows(postgres), claim_rows(local))
            for key in postgres.account_keys:
                self.assertEqual(account_detail(postgres, key), account_detail(local, key))
                self.assertEqual(account_summary(postgres, key), offline.account_summary(key))
                self.assertEqual(offline.account_summary(key), json_offline.account_summary(key))
            claims = {row["kind"]: row for row in claim_rows(postgres)}
            self.assertEqual(
                [(claims[k]["prize_points"], claims[k]["money"], claims[k]["cards"])
                 for k in ("Prize point award", "Event grant", "Returned card")],
                [(15, 0, 0), (0, 75, 2), (0, 0, 1)],
            )
            detached = account_detail(postgres, self.keys[0])
            detached["inventory"][0]["attack"] = -1
            detached["pending_claims"][0]["money"] = -1
            with self.assertRaises(KeyError):
                account_detail(postgres, "unknown")
        self.assertEqual(postgres.snapshot(), before)
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.revision(), revision)
        self.assertEqual(self.json.path.read_bytes(), raw)

    def test_live_connection_and_room_presence_use_the_running_owner(self):
        postgres = Server(store=self.store, **host_options())
        local = Server(store=self.json, **host_options())
        saved, revision = self.store.load(), self.revision()
        for host in (postgres, local):
            host.nicks[self.keys[0].encode()] = SimpleNamespace(closed=False)
            host.nicks[self.keys[1].encode()] = SimpleNamespace(closed=True)
            host.nicks["joueur-é_日本".encode()] = SimpleNamespace(closed=False)
            room = host.ordinary_room_bindings[0][1].room
            owner = host.economy.account_view(self.keys[0])
            room.handle("peer", presence(room.room_id, owner.nn, owner.player_id, owner.name),
                        authenticated_identity=(owner.nn, owner.player_id))
            room.tables[0].set_table(1, 1, owner.nn)
        with ExitStack() as stack:
            self.prohibit_storage(stack)
            self.assertEqual(account_rows(postgres), account_rows(local))
            self.assertEqual(room_rows(postgres), room_rows(local))
            online = {row["id"] for row in account_rows(postgres) if row["connected"]}
            self.assertEqual(online, {self.keys[0], "joueur-é_日本"})
            occupied = [row for row in room_rows(postgres) if row["occupied_tables"]]
            self.assertEqual(len(occupied), 1)
            self.assertEqual((occupied[0]["members"], occupied[0]["occupied_tables"]), (1, 1))
            with patch("tmserver.admin.queries.time.monotonic", return_value=20):
                overview = server_overview(postgres, started_at=10)
            self.assertEqual(
                (overview["connected_accounts"], overview["occupied_tables"],
                 overview["storage"], overview["uptime_seconds"]),
                (2, 1, "Ready", 10),
            )
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.revision(), revision)

    def test_offline_escrow_inspection_does_not_run_startup_recovery(self):
        tokens = {seat: self.source.economy.reserve_match(key)
                  for seat, key in enumerate(self.keys[:2])}
        self.source.match_economy.begin("open-round", tokens, 50)
        snapshot = self.source.snapshot()
        self.store.save(snapshot)
        self.json.save(snapshot)
        saved, revision, raw = self.store.load(), self.revision(), self.json.path.read_bytes()
        configs = host_options()["event_configs"]
        with ExitStack() as stack:
            self.prohibit_storage(stack)
            offline = AdminState(self.store, deepcopy(saved), configs)
            local = AdminState(self.json, deepcopy(snapshot), configs)
            summary = offline.account_summary(self.keys[0])
            self.assertEqual(summary, local.account_summary(self.keys[0]))
            self.assertEqual(summary["match_rounds"], [dict(
                id="open-round", seat=0, money="escrow", escrowed_money=50,
                has_result=False, cards_settled=False, pending_cards=0,
            )])
            self.assertEqual(summary["balance"], snapshot["accounts"][self.keys[0]]["balance"])
            self.assertEqual(summary["auction_claims"]["returned_cards"], 1)
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.revision(), revision)
        self.assertEqual(self.json.path.read_bytes(), raw)
