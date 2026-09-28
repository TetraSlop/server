"""Encrypted room migration and rejection across ordinary/event services."""

import asyncio
import struct
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests import ROOT
from tests.events.test_event_rooms import config, presence
from tests.rooms.test_ordinary_rooms import EXTRA
from tests.network.test_server import Client
from tmserver.network import dispatch as services
from tmserver.events.matchmaking import EventTableConfig
from tmserver.protocol.identities import GAME_KEY, game_id, service_nick
from tmserver.game.match import decode, packet
from tmserver.rooms.config import directory_files
from tmserver.network.server import Server
from tmserver.protocol.commands import Command, encode, parse


class RoomTransitions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.cfg = config(match_enabled=True)
        self.second = config("second", 0x1100, name=b"Second Event")
        self.server = Server(
            state_path=Path(self.temp.name) / "state.json",
            account_count=3,
            ordinary_rooms=(EXTRA,),
            event_configs=(self.cfg, self.second),
            event_tables=(EventTableConfig("festival", game_id(0x1003), b"#EVTABLE001"),),
            event_clock=lambda: 200,
        )
        port = await self.server.start(0)
        self.clients = []
        self.inbox = [[], [], []]
        for nickname in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(port, account=nickname)
            self.assertIn(b" 422 ", await client.read())
        self.peers = [self.server.nicks[c.nick] for c in self.clients]
        self.keys = [c.nick.decode() for c in self.clients]
        self.rooms = [
            self.server.room,
            *[b.room for b in self.server.event_rooms.rooms],
            self.server.ordinary_tables[1].room,
        ]

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def drain(self, first=0):
        for side in (first, *(i for i in range(len(self.clients)) if i != first)):
            client = self.clients[side]
            await client.send(b"PING :room-transition")
            for _ in range(200):
                line = await client.read()
                self.assertTrue(line, "room transition disconnected an account")
                if line == b"PONG room-transition":
                    break
                if b" NOTICE " in line:
                    self.inbox[side].append((line.split(b"!", 1)[0][1:], line.split(b" :", 1)[1]))
            else:
                self.fail("room delivery limit")

    async def send(self, side, target, payload):
        await self.clients[side].send(
            b"NOTICE " + service_nick(target ^ GAME_KEY) + b" :" + payload
        )
        await self.drain(side)

    def entry(self, side, room):
        view = self.server.economy.account_view(self.keys[side])
        return presence(room.room_id, view.nn, view.player_id, view.name)

    async def enter(self, side, room):
        await self.send(side, room.room_id, self.entry(side, room))

    def snapshot(self):
        return tuple(r.snapshot() for r in self.rooms), self.server.events.snapshot()

    async def test_ordinary_event_and_event_event_moves_leave_one_roster(self):
        await self.enter(1, self.rooms[0])
        await self.enter(2, self.rooms[1])
        await self.enter(0, self.rooms[0])
        for destination in (*self.rooms[1:], self.rooms[0]):
            old = next(r for r in self.rooms if self.peers[0] in r.members)
            await self.enter(0, destination)
            self.assertEqual(
                [self.peers[0] in r.members for r in self.rooms],
                [r is destination for r in self.rooms],
            )
            self.assertEqual(sum(len(r.members) for r in self.rooms), 3)
            for side in (1, 2):
                if self.peers[side] in old.members:
                    commands = [
                        parse(p[5:])[-1]
                        for s, p in self.inbox[side]
                        if s == service_nick(old.room_id ^ GAME_KEY) and p.startswith(b"GTM0L")
                    ]
                    self.assertTrue(
                        any(
                            c.token == b"PC"
                            and c.payload
                            == f"0x{self.server.economy.account_view(self.keys[0]).nn:016X}".encode()
                            for c in commands
                        )
                    )
            directory = directory_files(self.server)["b/g/RL000"]
            self.assertEqual(
                sum(
                    struct.unpack_from("<I", directory, 0x48 + i * 0xC8 + 0x10)[0]
                    for i in range(struct.unpack_from("<I", directory, 0x40)[0])
                ),
                3,
            )
        self.assertNotIn(self.keys[0], self.server.events._present)
        self.assertIsNotNone(self.server.events.member(self.keys[0], "festival"))
        self.assertIsNotNone(self.server.events.member(self.keys[0], "second"))

    async def test_forged_destination_presence_keeps_original_membership(self):
        await self.enter(0, self.rooms[0])
        before = self.snapshot()
        with self.assertRaises(ValueError):
            await services.dispatch(
                self.peers[0], service_nick(0x1000), self.entry(0, self.rooms[0])
            )
        self.assertEqual(self.snapshot(), before)
        await self.enter(0, self.rooms[1])
        before = self.snapshot()
        with self.assertRaises(ValueError):
            await services.dispatch(
                self.peers[0], service_nick(EXTRA.room_raw), self.entry(0, self.rooms[1])
            )
        self.assertEqual(self.snapshot(), before)

    async def test_event_selection_blocks_room_move_until_clean_quit(self):
        await self.enter(0, self.rooms[1])
        view = self.server.economy.account_view(self.keys[0])
        await self.send(
            0,
            self.cfg.init_id,
            packet(
                0x98,
                body=f"@EInitReq=/NN={view.nn:016X}/HID={view.player_id}/CN={view.name.hex()}/Dm=1/Vol=1/GID={view.player_id:016X}",
            ),
        )
        number = self.server.event_matches.coordinator.session(self.peers[0])
        self.assertIsNotNone(number)
        before = self.snapshot()
        for destination in (self.rooms[0], self.rooms[2], self.rooms[3]):
            with self.assertRaises(ValueError):
                await services.dispatch(
                    self.peers[0],
                    service_nick(destination.room_id ^ GAME_KEY),
                    self.entry(0, destination),
                )
            self.assertEqual(self.snapshot(), before)
            self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        await self.send(0, self.cfg.init_id, packet(0xB2, 0x17, number, f"@Quit=/No={number}"))
        await self.enter(0, self.rooms[2])
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
        self.assertEqual(self.server.events._present[self.keys[0]], "second")
        self.assertNotIn(self.peers[0], self.rooms[1].members)

    async def test_full_destination_keeps_old_room_and_event_presence(self):
        await self.enter(0, self.rooms[1])
        destination = self.rooms[3]
        fake_peers = [object() for _ in range(16)]
        try:
            for i, peer in enumerate(fake_peers):
                destination.handle(
                    peer, presence(destination.room_id, game_id(20 + i), 20 + i, b"Capacity")
                )
            before = self.snapshot()
            await self.enter(0, destination)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.server.events._present[self.keys[0]], "festival")
            self.assertEqual(parse(self.inbox[0][-1][1][5:])[0].token, b"DF")
        finally:
            for peer in fake_peers:
                destination.members.pop(peer, None)

    async def test_explicit_pc_leaves_room_without_ack_and_replays_once(self):
        identity = self.server.economy.account_view(self.keys[0]).nn
        request = b"GTM0L" + encode([Command(b"PC", f"0x{identity:016X}".encode())])
        for room in self.rooms[:2]:
            await self.enter(0, room)
            await self.enter(1, room)
            before = room.sequence
            self.inbox = [[], [], []]
            await self.send(0, room.room_id, request)
            self.assertNotIn(self.peers[0], room.members)
            self.assertIn(self.peers[1], room.members)
            self.assertEqual(self.inbox[0], [])
            self.assertEqual(room.sequence, before + 1)
            departure = self.inbox[1][-1][1]
            self.assertEqual(
                parse(departure[5:])[-1], Command(b"PC", f"0x{identity:016X}".encode())
            )
            await self.send(0, room.room_id, request)
            self.assertEqual(room.sequence, before + 1)
            self.inbox = [[], [], []]
            await self.send(
                1, room.room_id, b"GTM0L" + encode([Command(b"DR", str(before).encode())])
            )
            self.assertEqual(self.inbox[1][-1][1], departure)
        self.assertNotIn(self.keys[0], self.server.events._present)
        self.assertIsNotNone(self.server.events.member(self.keys[0], "festival"))

    async def test_pc_authentication_and_late_old_room_leave_preserve_new_room(self):
        await self.enter(0, self.rooms[1])
        await self.enter(1, self.rooms[1])
        other = self.server.economy.account_view(self.keys[1]).nn
        before = self.snapshot()
        for identity in (f"0x{other:016X}".encode(), b"1", b"0x10000000000000000"):
            with self.assertRaises(ValueError):
                await services.dispatch(
                    self.peers[0],
                    service_nick(0x1000),
                    b"GTM0L" + encode([Command(b"PC", identity)]),
                )
            self.assertEqual(self.snapshot(), before)
        await self.enter(0, self.rooms[2])
        before = self.snapshot()
        own = self.server.economy.account_view(self.keys[0]).nn
        await self.send(
            0, self.rooms[1].room_id, b"GTM0L" + encode([Command(b"PC", f"0x{own:016X}".encode())])
        )
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.server.events._present[self.keys[0]], "second")


if __name__ == "__main__":
    unittest.main()
