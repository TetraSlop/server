"""Saved personal rules remain separate while an encrypted client changes rooms."""

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests import FIXTURES, ROOT
from tests.rooms import test_room_transitions as transitions
from tests.protocol.test_admission_identity import native_identity
from tests.rooms.test_room_rules import settings
from tests.network.test_server import Client
from tmserver.protocol.identities import GAME_KEY, service_nick
from tmserver.game.models import DEFAULT_GAME_RULES
from tmserver.game.packets import decode, packet
from tmserver.rooms.config import directory_files, load_ordinary_rooms
from tmserver.rooms.rules import RoomRules
from tmserver.network.server import Server
from tmserver.profiles.preferences import read_preferences


class RoomRuleTransitions(unittest.IsolatedAsyncioTestCase):
    drain = transitions.RoomTransitions.drain
    send = transitions.RoomTransitions.send
    entry = transitions.RoomTransitions.entry
    enter = transitions.RoomTransitions.enter

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.json"
        self.configs = load_ordinary_rooms(
            ROOT / "examples/auto_rooms.example.json"
        )
        self.server = Server(state_path=self.path, account_count=3, ordinary_rooms=self.configs)
        self.clients, self.inbox = [], [[], [], []]
        port = await self.server.start(0)
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.peers = [self.server.nicks[c.nick] for c in self.clients]
        self.keys = [c.nick.decode() for c in self.clients]
        self.rooms = [t.room for t in self.server.ordinary_tables]

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def query(self, table):
        await self.send(0, table.room.table_id, packet(0x12, body="@Rule="))
        source = service_nick(table.room.table_id ^ GAME_KEY)
        return next(
            decode(payload).sections["Tet"]
            for sender, payload in reversed(self.inbox[0])
            if sender == source and payload.startswith(b"GTM0G13")
        )

    async def test_original_editor_apply_packets_work_in_their_advertised_rooms(self):
        evidence = json.loads(
            (FIXTURES / "fixed_room_apply.json").read_text()
        )
        self.assertEqual(
            hashlib.sha256(directory_files(self.server)["b/g/RL000"]).hexdigest(),
            evidence["directory_sha256"],
        )
        bindings = {c.name.decode(): t for c, t in self.server.ordinary_room_bindings}
        for case in evidence["cases"]:
            with self.subTest(room=case["room"]):
                table = bindings[case["room"]]
                self.assertEqual(
                    hashlib.sha256(table.room.snapshot()).hexdigest(), case["ptl_sha256"]
                )
                await self.enter(0, table.room)
                await self.clients[0].join(table.table_channel)
                view = self.server.economy.account_view(self.keys[0])
                await self.send(
                    0, table.room.table_id, packet(0x41, 1, body=native_identity(view, "GameEN"))
                )
                await self.send(
                    0,
                    table.room.table_id,
                    packet(
                        0x24,
                        body="@Save=/Tbl=0/Game=1" + settings(RoomRules(**case["saved_game"])),
                    ),
                )
                original = case["apply"].encode("ascii")
                await self.send(0, table.room.table_id, original)
                expected = decode(original).sections
                self.assertEqual(await self.query(table), expected["Tet"])
                self.assertEqual(
                    table.match.table_rules, {k: int(v) for k, v in expected["Tab"].items()}
                )
                self.assertEqual(
                    read_preferences(self.server.economy.profile_bytes(self.keys[0]))["Tet"],
                    case["saved_game"],
                )
                self.assertTrue(table.match.settings_applied)
                self.assertFalse(self.peers[0].closed)
                await self.send(0, table.room.table_id, packet(0x41, 5, body="@GameQT="))
                await self.clients[0].send(b"PART " + table.table_channel)
                await self.drain()

    async def test_saved_defaults_survive_fixed_rooms_and_remain_usable_in_open_tables(self):
        personal = RoomRules(st=1, ca=1, tl=5, gs=2)
        open_table, *fixed = self.server.ordinary_tables[1:]
        await self.enter(1, open_table.room)  # An independent observer of room departures.
        await self.enter(2, fixed[1].room)
        for table in (open_table, *fixed, open_table):
            with self.subTest(room=table.room.room_channel):
                await self.enter(0, table.room)
                self.assertEqual(
                    [self.peers[0] in room.members for room in self.rooms],
                    [room is table.room for room in self.rooms],
                )
                await self.clients[0].join(table.table_channel)
                view = self.server.economy.account_view(self.keys[0])
                await self.send(
                    0, table.room.table_id, packet(0x41, 1, body=native_identity(view, "GameEN"))
                )
                expected = table.room_rules or RoomRules(**DEFAULT_GAME_RULES)
                self.assertEqual(
                    {k: int(v) for k, v in (await self.query(table)).items()}, expected.as_dict()
                )
                if table is open_table:
                    await self.send(
                        0,
                        table.room.table_id,
                        packet(0x24, body="@Save=/Tbl=0/Game=1" + settings(personal)),
                    )
                    expected = personal
                await self.send(0, table.room.table_id, packet(0x14, body=settings(expected)))
                self.assertEqual(
                    {k: int(v) for k, v in (await self.query(table)).items()}, expected.as_dict()
                )
                self.assertEqual(table.room.table_metadata[30:37], expected.packed())
                self.assertEqual(
                    read_preferences(self.server.economy.profile_bytes(self.keys[0]))["Tet"],
                    personal.as_dict(),
                )
                await self.send(0, table.room.table_id, packet(0x41, 5, body="@GameQT="))
                await self.clients[0].send(b"PART " + table.table_channel)
                await self.drain()
                self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
                reset = table.room_rules or RoomRules()
                self.assertEqual(table.room.table_metadata[30:37], reset.packed())

        self.assertIn(self.peers[1], open_table.room.members)
        self.assertIn(self.peers[2], fixed[1].room.members)
        for client in self.clients:
            await client.close()
        await self.server.stop()
        restored = Server(state_path=self.path, account_count=3, ordinary_rooms=self.configs)
        try:
            self.assertEqual(
                read_preferences(restored.economy.profile_bytes(self.keys[0]))["Tet"],
                personal.as_dict(),
            )
            for table in restored.ordinary_tables:
                rules = table.room_rules or RoomRules()
                self.assertEqual(table.match.game_rules, rules.as_dict())
                self.assertEqual(table.room.table_metadata[30:37], rules.packed())
        finally:
            await restored.stop()


if __name__ == "__main__":
    unittest.main()
