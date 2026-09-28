"""Encrypted simultaneous ordinary rooms, persistence and failure isolation."""

import asyncio
import struct
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.events.test_event_rooms import config as event_config
from tests.events.test_event_rooms import presence
from tests.profiles.test_profile_accounts import file_request, transact
from tests.network.test_server import Client
from tmserver.protocol.identities import (
    GAME_KEY,
    ROOM_RAW,
    TABLE_RAW,
    game_id,
    player_id,
    service_nick,
)
from tmserver.game.match import decode, packet
from tmserver.rooms.config import OrdinaryRoomConfig, directory_files, load_ordinary_rooms
from tmserver.profiles.location import FileOwner
from tmserver.profiles.service import ProfileServer, checked
from tmserver.network.server import Server
from tmserver.protocol.commands import parse

EXTRA = OrdinaryRoomConfig(b"Second Room", 0x101, 0x201, b"#ROOM2", b"#TABLE2")


class OrdinaryRoomConfiguration(unittest.IsolatedAsyncioTestCase):
    async def test_route_channel_account_and_event_collisions_rejected(self):
        for row in (
            replace(EXTRA, room_raw=ROOM_RAW),
            replace(EXTRA, table_raw=TABLE_RAW),
            replace(EXTRA, table_raw=1),
            replace(EXTRA, room_channel=b"#localroom01"),
            replace(EXTRA, name=b"Local Room"),
            replace(EXTRA, room_raw=0x1000),
        ):
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    Server(ordinary_rooms=(row,), event_configs=(event_config(),))
        with self.assertRaises(ValueError):
            Server(ordinary_rooms=(replace(EXTRA, players=3),))
        with self.assertRaises(ValueError):
            Server(ordinary_rooms=(EXTRA, EXTRA))

    async def test_example_load_and_nondefault_three_player_cpu_constraints(self):
        rows = load_ordinary_rooms(ROOT / "examples/rooms.example.json")
        host = Server(account_count=4, ordinary_rooms=rows)
        try:
            self.assertEqual(len(host.ordinary_tables), 3)
            self.assertIsNot(host.ordinary_tables[1].match_lock, host.default_table.match_lock)
            self.assertIs(host.ordinary_tables[1].host.economy, host.economy)
        finally:
            await host.stop()
        with self.assertRaises(ValueError):
            replace(EXTRA, mode="computer", players=3)


class OrdinaryRoomsIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.clients = []
        self.server = None

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        if self.server is not None:
            await self.server.stop()
        self.temp.cleanup()

    async def start(self, cpu=False, **kwargs):
        mode = "computer" if cpu else "versus"
        self.cfg = replace(EXTRA, mode=mode)
        self.groups = ((0,), (1,)) if cpu else ((0, 1), (2, 3))
        self.server = Server(
            state_path=self.path,
            account_count=2 if cpu else 4,
            match_mode=mode,
            ordinary_rooms=(self.cfg,),
            **kwargs,
        )
        self.tables = self.server.ordinary_tables
        self.port = await self.server.start(0)
        self.received = [[] for _ in range(2 if cpu else 4)]
        for nick in self.server.accounts:
            c = Client()
            self.clients.append(c)
            await c.open(self.port, account=nick)
            self.assertIn(b" 422 ", await c.read())
        self.keys = [c.nick.decode() for c in self.clients]

    def group(self, side):
        return next((i, g) for i, g in enumerate(self.groups) if side in g)

    async def flush(self, side):
        client = self.clients[side]
        index, _ = self.group(side)
        table = self.tables[index]
        messages = []
        for _ in range(2):
            await client.send(b"PING :room-barrier")
            for _ in range(200):
                line = await client.read()
                self.assertTrue(line, "healthy room client closed")
                if line == b"PONG room-barrier":
                    break
                if b" NOTICE " in line:
                    payload = line.split(b" :", 1)[1]
                    expected = service_nick(
                        table.table_raw
                        if payload.startswith(b"GTM0G")
                        else table.room.room_id ^ GAME_KEY
                    )
                    self.assertTrue(line.startswith(b":" + expected + b"!p@local NOTICE "), line)
                    self.received[side].append(payload)
                    messages.append(payload)
            else:
                self.fail("room response limit")
            if table.match_task is not None:
                await asyncio.wait_for(asyncio.shield(table.match_task), 5)
        return messages

    async def send(self, side, payload, room=False):
        index, group = self.group(side)
        table = self.tables[index]
        target = service_nick(table.room.room_id ^ GAME_KEY if room else table.table_raw)
        await self.clients[side].send(b"NOTICE " + target + b" :" + payload)
        return {p: await self.flush(p) for p in group}

    async def admit(self, number, stake=0):
        table = self.tables[number]
        group = self.groups[number]
        for side in group:
            view = self.server.economy.account_view(self.keys[side])
            await self.send(
                side, presence(table.room.room_id, view.nn, view.player_id, view.name), room=True
            )
            await self.clients[side].join(table.table_channel)
            await self.send(side, packet(0x41, 1, body=f"@GameEN=/NN={view.nn:016X}"))
        if stake:
            await self.send(
                group[0],
                packet(
                    0x14,
                    body=f"@Tet=/bm={stake}/du=0/st=0/cb=0/ca=0/gs=0/tl=0@Tab=/in=0/lu=100/ll=0/au=300/al=100/co=0/pa=0",
                ),
            )
        for side in group:
            await self.send(
                side, packet(1, body=f"@Req=/NN={player_id(self.clients[side].nick):016X}")
            )
            await self.send(side, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(table.match.phase, "selection")
        for seat, side in enumerate(group):
            cards = self.server.economy.inventory(self.keys[side])[:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
            )
            await self.send(side, packet(0x43, 7, seat, body))
        self.assertEqual(table.match.phase, "turn")

    async def advance(self, number):
        table = self.tables[number]
        match = table.match
        group = self.groups[number]
        if match.phase == "result":
            return
        seq = match.sequence
        for seat, side in enumerate(group):
            await self.send(side, packet(0x43, 9, seq, f"@TurnData=/Ans={seat}"))
        if match.sequence != seq or match.phase == "result":
            return
        if match.phase == "turn":
            side = group[match.active]
            await self.send(
                side,
                packet(
                    0x43, 10, seq, f"@PutData=/P={match.active}/H=0/F={match.legal_fields()[0]}"
                ),
            )
        while match.phase == "battle":
            side = group[match.active]
            await self.send(side, packet(0x43, 11, seq, f"@BattleSelect=/B={match.targets[0]}"))

    async def finish(self, numbers=(0, 1)):
        for _ in range(30):
            if all(self.tables[n].match.phase == "result" for n in numbers):
                return
            for number in numbers:
                await self.advance(number)
        self.fail("independent rooms did not finish")

    async def leave_result(self, number):
        match = self.tables[number].match
        group = self.groups[number]
        for seat, side in enumerate(group):
            await self.send(side, packet(0x43, 14, match.sequence, f"@Ready=/Ans={seat}"))
        await self.send(group[0], packet(0x43, 17, 0, "@Continue=/Ans=2/PR=0/Conf=0"))

    async def assert_closed(self, side):
        for _ in range(30):
            line = await self.clients[side].read()
            if line == b"":
                return
            self.assertTrue(
                b" :GTM0L" in line or b" PART " in line
            )  # Ordered room/channel departure precedes EOF.
        self.fail("participant connection did not close")

    async def test_two_simultaneous_human_rounds_commit_four_profiles_once(self):
        await self.start()
        await self.admit(0)
        first = self.tables[0].match.snapshot()
        await self.admit(1)
        self.assertEqual(self.tables[0].match.snapshot(), first)
        await self.finish()
        for side, key in enumerate(self.keys):
            self.assertEqual(len(self.server.economy.inventory(key)), 10)
            results = [
                decode(p)
                for p in self.received[side]
                if p.startswith(b"GTM0G") and decode(p).subtype == 13
            ]
            self.assertEqual(len(results), 1)
        await self.leave_result(0)
        self.assertTrue(self.tables[1].match_reservations)
        await self.leave_result(1)
        restored = Server(state_path=self.path, account_count=4, ordinary_rooms=(self.cfg,))
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
        finally:
            await restored.stop()

    async def test_abort_and_refund_are_confined_to_one_room(self):
        await self.start(allow_wagers=True)
        await self.admit(0, stake=1)
        await self.admit(1, stake=2)
        other = self.tables[1].match
        before = other.snapshot()
        await self.clients[0].close()
        await self.assert_closed(1)
        self.assertEqual(other.snapshot(), before)
        self.assertIs(self.tables[1].match, other)
        self.assertTrue(
            all(self.server.economy.balance(self.keys[s]) == 10000 for s in self.groups[0])
        )
        await self.finish((1,))
        await self.leave_result(1)
        self.assertEqual(
            sum(self.server.economy.balance(self.keys[s]) for s in self.groups[1]), 20000
        )
        self.assertFalse(any(t.match_reservations for t in self.tables))

    async def test_two_cpu_tables_progress_without_sharing_turns_or_npc_rosters(self):
        await self.start(cpu=True)
        await self.admit(0)
        await self.admit(1)
        await self.finish()
        await self.leave_result(0)
        await self.leave_result(1)
        self.assertEqual([t.match.completed_matches for t in self.tables], [1, 1])
        self.assertEqual([len(t.room.members) for t in self.tables], [1, 1])
        self.assertTrue(all(not t.match_reservations for t in self.tables))

    async def test_explicit_quit_aborts_only_its_table_and_other_round_can_finish(self):
        await self.start(allow_wagers=True)
        await self.admit(0, stake=1)
        await self.admit(1, stake=2)
        other = self.tables[1].match
        before = other.snapshot()
        await self.clients[0].send(
            b"NOTICE "
            + service_nick(self.tables[0].table_raw)
            + b" :"
            + packet(0x43, 5, 10, "@Quit=/No=0")
        )
        for _ in range(30):
            if not await self.clients[0].read():
                break
        else:
            self.fail("quitter did not disconnect")
        await self.assert_closed(1)
        self.assertFalse(self.tables[0].match_reservations)
        self.assertEqual(other.snapshot(), before)
        self.assertTrue(all(self.server.economy.balance(self.keys[s]) == 10000 for s in (0, 1)))
        await self.finish((1,))
        await self.leave_result(1)

    async def test_owner_scoped_encrypted_directory_ptl_and_room_replay(self):
        await self.start()
        await self.admit(0)
        await self.admit(1)
        p = ProfileServer(Path(self.temp.name) / "profile.jsonl", self.server)
        port = await p.start(0)
        try:
            for index, table in enumerate(self.tables):
                c = self.clients[index * 2]
                key = c.nick.decode()
                for name, expected, raw in (
                    ("b/g/PTL", table.room.snapshot(), table.room.room_id ^ GAME_KEY),
                    ("b/g/RL000", directory_files(self.server)["b/g/RL000"], 0),
                ):
                    request = bytearray(file_request(name, size=0x10000)[:-4])
                    struct.pack_into("<Q", request, 8, FileOwner(raw, 1, 1).encode())
                    error, data = await transact(
                        port,
                        key,
                        3,
                        0,
                        checked(request),
                        context=(c.cipher, c.iv),
                        account_id=self.server.account_specs[key][0],
                    )
                    self.assertEqual(error, 0)
                    self.assertEqual(data[:-4], expected)
            for side in range(len(self.clients)):
                await self.flush(side)
            for index, table in enumerate(self.tables):
                from tmserver.protocol.commands import Command, encode

                seq = table.room.sequence - 1
                answer = await self.send(
                    self.groups[index][0],
                    b"GTM0L" + encode([Command(b"DR", str(seq).encode())]),
                    room=True,
                )
                deltas = [
                    parse(p[5:]) for p in answer[self.groups[index][0]] if p.startswith(b"GTM0L")
                ]
                self.assertEqual(len(deltas), 1)
                self.assertEqual(deltas[0][2].payload, str(seq + 1).encode())
        finally:
            await p.stop()

    async def test_cross_table_admission_rejected_without_disturbing_other_table(self):
        await self.start()
        await self.admit(0)
        await self.admit(1)
        before = self.tables[1].match.snapshot()
        await self.clients[0].send(
            b"NOTICE "
            + service_nick(self.tables[1].table_raw)
            + b" :"
            + packet(1, body=f"@Req=/NN={player_id(self.clients[0].nick):016X}")
        )
        await self.assert_closed(0)
        await self.assert_closed(1)
        self.assertEqual(self.tables[1].match.snapshot(), before)
        self.assertTrue(all(not self.server.nicks[self.clients[i].nick].closed for i in (2, 3)))

    async def test_secondary_part_releases_only_its_own_table(self):
        await self.start()
        await self.admit(0)
        await self.admit(1)
        before = self.tables[0].match.snapshot()
        await self.clients[2].send(b"PART " + self.tables[1].table_channel)
        await self.flush(2)
        await self.assert_closed(3)
        self.assertEqual(self.tables[0].match.snapshot(), before)
        self.assertFalse(self.tables[1].match_reservations)
        await self.finish((0,))
        await self.leave_result(0)

    async def test_secondary_departure_keeps_primary_match_and_accounts_reserved(self):
        await self.start(allow_departures=True)
        await self.admit(0)
        await self.admit(1)
        primary, secondary = self.tables
        before = primary.match.snapshot()
        reservations = dict(primary.match_reservations)
        departed_match = secondary.match
        await self.clients[2].send(b"PART " + secondary.table_channel)
        await self.flush(2)
        if secondary.departures.task is not None:
            await asyncio.wait_for(asyncio.shield(secondary.departures.task), 5)
        await self.flush(3)
        self.assertEqual(departed_match.departure_seats, (0,))
        self.assertEqual(departed_match.phase, "finished")
        self.assertFalse(secondary.match_reservations)
        self.assertEqual(primary.match.snapshot(), before)
        self.assertEqual(primary.match_reservations, reservations)
        self.assertTrue(all(self.server.economy.is_reserved(self.keys[side]) for side in (0, 1)))
        self.assertFalse(any(self.server.economy.is_reserved(self.keys[side]) for side in (2, 3)))
        await self.finish((0,))
        await self.leave_result(0)

    async def test_secondary_timeout_preserves_primary_match(self):
        from tmserver.rooms.watchdog import MatchWatchdog

        await self.start()
        await self.admit(0)
        await self.admit(1)
        before = self.tables[0].match.snapshot()
        now = [0.0]
        table = self.tables[1]
        table.watchdog = MatchWatchdog(clock=lambda: now[0], ack_timeout=1)
        table.watchdog.observe(table.match, table.match)
        now[0] = 2
        expired = await table.check_match_timeout()
        self.assertIsNotNone(expired)
        await self.assert_closed(2)
        await self.assert_closed(3)
        self.assertEqual(self.tables[0].match.snapshot(), before)
        self.assertTrue(all(not self.server.nicks[self.clients[i].nick].closed for i in (0, 1)))

    async def test_failed_secondary_send_under_lock_does_not_abort_primary(self):
        await self.start()
        await self.admit(0)
        await self.admit(1)
        before = self.tables[0].match.snapshot()
        peer = self.server.nicks[self.clients[2].nick]
        with patch.object(peer, "send", side_effect=OSError("secondary writer failed")):
            async with self.tables[1].match_lock:
                await self.tables[1].publish_match(
                    self.tables[1].match,
                    self.tables[1].match._emit(packet(0x43, 0x28, body="@DataError=")),
                )
        await self.assert_closed(2)
        await self.assert_closed(3)
        self.assertFalse(self.tables[1].match_reservations)
        self.assertEqual(self.tables[0].match.snapshot(), before)

    async def test_new_room_presence_removes_old_idle_roster_and_updates_counts(self):
        await self.start()
        client = self.clients[0]
        view = self.server.economy.account_view(self.keys[0])
        first, second = self.tables
        await self.send(
            0, presence(first.room.room_id, view.nn, view.player_id, view.name), room=True
        )
        source = service_nick(second.room.room_id ^ GAME_KEY)
        await client.send(
            b"NOTICE "
            + source
            + b" :"
            + presence(second.room.room_id, view.nn, view.player_id, view.name)
        )
        await client.send(b"PING :moved-room")
        for _ in range(20):
            line = await client.read()
            self.assertTrue(line)
            if line == b"PONG moved-room":
                break
            self.assertTrue(line.startswith(b":" + source + b"!p@local NOTICE "))
        else:
            self.fail("room move did not finish")
        peer = self.server.nicks[client.nick]
        self.assertNotIn(peer, first.room.members)
        self.assertIn(peer, second.room.members)
        raw = directory_files(self.server)["b/g/RL000"]
        self.assertEqual(
            [struct.unpack_from("<I", raw, 0x48 + i * 0xC8 + 0x10)[0] for i in range(2)], [0, 1]
        )


if __name__ == "__main__":
    unittest.main()
