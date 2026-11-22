"""Concurrent tables share only a room roster and ordered table announcements."""

import struct
import unittest
from dataclasses import replace
from types import SimpleNamespace

from tests.rooms import test_ordinary_rooms as base
from tests.events.test_event_rooms import presence as room_presence
from tests.rooms.test_room_service import presence
from tmserver.protocol.identities import GAME_KEY, game_id, service_nick
from tests.rooms.harness import serve
from tmserver.game.packets import decode, packet
from tmserver.rooms.config import OrdinaryTableConfig, directory_files
from tmserver.rooms.service import RoomService
from tmserver.network.server import Server
from tmserver.rooms.spectators import SpectatorService


class SharedRoomUnits(unittest.TestCase):

    def test_cpu_rosters_and_cleanup_are_scoped_to_their_table(self):
        room = RoomService()
        second = room.add_table(game_id(0x301), b"#SECOND")
        room.handle("one", presence())
        a = SimpleNamespace(name=b"CPU One", nickname_id=512, character_id=2)
        b = SimpleNamespace(name=b"CPU Two", nickname_id=513, character_id=3)
        room.practice_opponents("one", (a,))
        second.practice_opponents("one", (b,))
        self.assertEqual(set(room.npcs), {512, 513})
        second.clear_npcs()
        self.assertEqual(set(room.npcs), {512})
        self.assertEqual(struct.unpack_from("<III", second.snapshot(), 0x40), (104, 2, 3))

    def test_long_cpu_asset_name_fits_room_fields(self):
        room = RoomService()
        room.handle("one", presence())
        cpu = SimpleNamespace(name=b"Flower Girl Natasha", nickname_id=512, character_id=2)
        room.practice_opponents("one", (cpu,))
        record = room.npcs[512][1]
        self.assertEqual(record[0x28:0x38], b"Flower Girl Nat\0")
        self.assertEqual(len(record), 0x58)

    def test_duplicate_and_oversized_table_configuration_is_rejected(self):
        extra = OrdinaryTableConfig(base.EXTRA.table_raw, b"#THIRD")
        with self.assertRaises(ValueError):
            Server(ordinary_rooms=(replace(base.EXTRA, tables=(extra,)),))
        with self.assertRaises(ValueError):
            replace(base.EXTRA, tables=(extra,) * 127)
        room = RoomService()
        with self.assertRaises(ValueError):
            room.add_table(room.table_id, b"#DIFFERENT")


class SharedRoomIntegration(unittest.IsolatedAsyncioTestCase):
    group = base.OrdinaryRoomsIntegration.group
    flush = base.OrdinaryRoomsIntegration.flush
    send = base.OrdinaryRoomsIntegration.send
    admit = base.OrdinaryRoomsIntegration.admit
    advance = base.OrdinaryRoomsIntegration.advance
    finish = base.OrdinaryRoomsIntegration.finish
    leave_result = base.OrdinaryRoomsIntegration.leave_result
    assert_closed = base.OrdinaryRoomsIntegration.assert_closed
    test_simultaneous_human_rounds = (
        base.OrdinaryRoomsIntegration.test_two_simultaneous_human_rounds_commit_four_profiles_once
    )
    test_abort_and_refund_isolation = (
        base.OrdinaryRoomsIntegration.test_abort_and_refund_are_confined_to_one_room
    )
    test_second_table_timeout = (
        base.OrdinaryRoomsIntegration.test_secondary_timeout_preserves_primary_match
    )
    test_second_table_part = (
        base.OrdinaryRoomsIntegration.test_secondary_part_releases_only_its_own_table
    )
    test_encrypted_ptl_and_replay = (
        base.OrdinaryRoomsIntegration.test_owner_scoped_encrypted_directory_ptl_and_room_replay
    )

    async def start(self, cpu=False, **kwargs):
        mode = "computer" if cpu else "versus"
        extra = OrdinaryTableConfig(0x202, b"#TABLE3", mode)
        self.cfg = replace(base.EXTRA, mode=mode, tables=(extra,))
        self.groups = ((0,), (1,)) if cpu else ((0, 1), (2, 3))
        count = kwargs.pop("accounts", 2 if cpu else 4)
        await serve(self, account_count=count, ordinary_rooms=(self.cfg,), **kwargs)
        self.tables = self.server.ordinary_tables[1:]
        self.received = [[] for _ in range(count)]
        self.keys = [c.nick.decode() for c in self.clients]

    async def test_cpu_tables_keep_distinct_rosters_and_live_directory_counts(self):
        await self.start(cpu=True)
        await self.admit(0)
        await self.admit(1)
        room = self.tables[0].room
        self.assertIs(self.tables[1].room.room_root, room)
        self.assertEqual(set(room.npcs), {512, 513})
        self.assertEqual(len(room.members), 2)
        files = directory_files(self.server)
        offset = 0x48 + 0xC8
        self.assertEqual(struct.unpack_from("<I", files["b/g/RL000"], offset + 0x10)[0], 2)
        self.assertEqual(struct.unpack_from("<I", files["b/g/RL000"], offset + 0x18)[0], 2)
        self.assertEqual(files["b/g/RL000"][offset + 0x6A : offset + 0x6D], b"AAC")
        self.assertEqual(struct.unpack_from("<I", files["b/g/ZL"], 0x48)[0], 2)
        await self.finish()
        await self.leave_result(1)
        self.assertEqual(set(room.npcs), {512})
        await self.leave_result(0)
        self.assertFalse(room.npcs)
        self.assertEqual([t.match.completed_matches for t in self.tables], [1, 1])

    async def test_second_table_observer_survives_first_table_abort(self):
        await self.start(
            cpu=True,
            accounts=3,
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        await self.admit(0)
        await self.admit(1)
        table = self.tables[1]
        observer = self.clients[2]
        view = self.server.economy.account_view(self.keys[2])
        before = self.server.economy.profile_bytes(self.keys[2])
        await observer.send(
            b"NOTICE "
            + service_nick(table.room.room_id ^ GAME_KEY)
            + b" :"
            + room_presence(table.room.room_id, view.nn, view.player_id, view.name)
        )
        await observer.send(b"JOIN " + table.table_channel)
        target = service_nick(table.table_raw)

        async def read_watch():
            for _ in range(100):
                line = await observer.read()
                self.assertTrue(line, "secondary observer disconnected")
                if b" :GTM0G" in line:
                    self.assertTrue(line.startswith(b":" + target + b"!p@local NOTICE "), line)
                    return decode(line.split(b" :", 1)[1])
            self.fail("observer response limit")

        await observer.send(
            b"NOTICE "
            + target
            + b" :"
            + packet(1, body=f"@Req=/NN={view.nn:016X}/HID={view.player_id}")
        )
        self.assertEqual((await read_watch()).primary, 2)
        await observer.send(b"NOTICE " + target + b" :" + packet(0x43, 0x25, 0, "@Data=/Watch=0"))
        self.assertEqual([(await read_watch()).subtype for _ in range(3)], [0x25, 0x26, 9])
        state = table.match.snapshot()
        await self.clients[0].close()
        await self.flush(1)
        self.assertEqual(table.match.snapshot(), state)
        await self.finish((1,))
        seen = []
        for _ in range(150):
            msg = await read_watch()
            seen.append(msg)
            if msg.subtype == 13:
                break
        else:
            self.fail("secondary observer never received result")
        self.assertEqual([m.argument for m in seen if m.subtype == 9], list(range(1, 10)))
        self.assertEqual([m.argument for m in seen if m.subtype == 10], list(range(10)))
        self.assertFalse(any(m.subtype == 0x1A for m in seen))
        self.assertEqual(self.server.economy.profile_bytes(self.keys[2]), before)
        await self.leave_result(1)


if __name__ == "__main__":
    unittest.main()
