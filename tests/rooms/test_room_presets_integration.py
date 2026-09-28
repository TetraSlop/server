"""Published room rules and Computer permission match the playable services."""

import asyncio
import hashlib
import json
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests import FIXTURES, ROOT
from tests.game import test_computer_menu_integration as computer
from tests.profiles.test_profile_accounts import file_request, transact
from tmserver.protocol.identities import GAME_KEY, ROOM_RAW, TABLE_RAW
from tmserver.game.match import DEFAULT_GAME_RULES, decode, packet
from tmserver.rooms.config import (
    OrdinaryRoomConfig,
    OrdinaryTableConfig,
    directory_files,
    load_ordinary_rooms,
)
from tmserver.profiles.location import FileOwner
from tmserver.profiles.service import ProfileServer, checked
from tmserver.rooms.rules import RoomRules
from tmserver.network.server import Server
from tests.network.test_server import Client


PRESETS = ROOT / "examples/auto_rooms.example.json"


class RoomPresetConfiguration(unittest.IsolatedAsyncioTestCase):
    async def test_advertisements_match_original_client_execution_fixture(self):
        evidence = json.loads((FIXTURES / "room_computer_gate.json").read_text())
        server = Server(account_count=3, ordinary_rooms=load_ordinary_rooms(PRESETS))
        try:
            rooms = directory_files(server)["b/g/RL000"]
            self.assertEqual(
                hashlib.sha256(rooms).hexdigest(), evidence["production_directory_sha256"]
            )
            for table, original in zip(
                server.ordinary_tables, evidence["production_rooms"], strict=True
            ):
                self.assertEqual(
                    hashlib.sha256(table.room.snapshot()).hexdigest(), original["ptl_sha256"]
                )
                self.assertEqual(original["result"]["disabled"], not table.room.computer_enabled)
                self.assertFalse(original["result"]["join_disabled"])
                if table.room_rules is not None:
                    self.assertEqual(original["projected_rules"], table.room_rules.as_dict())
        finally:
            await server.stop()

    async def test_launcher_directory_rules_match_every_table_and_survive_reset(self):
        configs = load_ordinary_rooms(PRESETS)
        server = Server(account_count=3, ordinary_rooms=configs)
        try:
            rows = directory_files(server)["b/g/RL000"]
            self.assertEqual(struct.unpack_from("<I", rows, 0x40)[0], 5)
            expected = [
                (b"Local Room", None, True),
                (b"Open Tables", None, False),
                (b"Classic Rules", RoomRules(), False),
                (b"Special Rules", RoomRules(st=1, cb=1, ca=1), False),
                (b"Quick Play", RoomRules(tl=1), False),
            ]
            for index, (name, rules, cpu) in enumerate(expected):
                with self.subTest(room=name):
                    record = rows[0x48 + index * 0xC8 : 0x48 + (index + 1) * 0xC8]
                    table = server.ordinary_tables[index]
                    game = DEFAULT_GAME_RULES if rules is None else rules.as_dict()
                    self.assertEqual(record[0x2A:].split(b"*", 1)[0], name)
                    self.assertEqual(record[0x28], ord("B" if rules is None else "A"))
                    self.assertEqual(record[0x29], ord("A") if cpu else 0)
                    self.assertEqual(record[0x49:0x50], RoomRules(**game).packed())
                    self.assertEqual(table.match.game_rules, game)
                    self.assertEqual(table.room.table_metadata[0], ord("A" if cpu else "B"))
                    self.assertEqual(table.room.table_metadata[30:37], RoomRules(**game).packed())
                    table.match.game_rules["tl"] = 5
                    table.reset_table_updates(table.match)
                    self.assertEqual(table.match.game_rules, game)
                    self.assertEqual(table.room.table_metadata[30:37], RoomRules(**game).packed())
        finally:
            await server.stop()

    async def test_mixed_room_limits_computer_permission_to_its_cpu_table(self):
        config = OrdinaryRoomConfig(
            b"Mixed",
            257,
            513,
            b"#MIXED",
            b"#HUMAN",
            rules=RoomRules(gs=0, tl=3),
            tables=(OrdinaryTableConfig(514, b"#CPU", mode="computer"),),
        )
        server = Server(ordinary_rooms=(config,))
        try:
            record = directory_files(server)["b/g/RL000"][0x110:0x1D8]
            self.assertEqual(record[0x28:0x2A], b"AA")
            human, cpu = server.ordinary_tables[1:]
            for table, prefix in ((human, b"B"), (cpu, b"A")):
                self.assertEqual(table.match.game_rules, config.rules.as_dict())
                table.room.set_table(1, 1, 123, config.rules.as_dict())
                self.assertEqual(table.room.table_record()[0x28:0x29], prefix)
                table.reset_table_updates(table.match)
                self.assertEqual(table.room.table_metadata[:1], prefix)
            self.assertEqual(len(human.room.tables), 2)
        finally:
            await server.stop()

    def test_invalid_room_rule_configuration_is_rejected(self):
        document = json.loads(PRESETS.read_text())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rooms.json"
            for invalid in (None, [], {"wager": 10}, {"tl": 6}, {"st": True}):
                with self.subTest(rules=invalid):
                    document["rooms"][0]["rules"] = invalid
                    path.write_text(json.dumps(document))
                    with self.assertRaises(ValueError):
                        load_ordinary_rooms(path)
        config = load_ordinary_rooms(PRESETS)[0]
        with self.assertRaises(ValueError):
            replace(config, rules={"tl": 1})


class ComputerDiscoveryIntegration(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = computer.ComputerMenuIntegrationTests.asyncTearDown
    request = computer.ComputerMenuIntegrationTests.request
    menu = computer.ComputerMenuIntegrationTests.menu

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp.name) / "state.json"
        self.server = Server(
            state_path=self.state_path,
            account_count=3,
            ordinary_rooms=load_ordinary_rooms(PRESETS),
        )
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()

    async def test_human_table_rejects_computer_entry_without_reserving_or_closing_peer(self):
        from tests.protocol.test_admission_identity import native_identity

        table = self.server.ordinary_tables[1]
        view = self.server.economy.account_view(self.key)
        before = self.state_path.read_bytes()
        replies = await self.request(
            table.table_raw, packet(0x41, 0x12, body=native_identity(view, "GameENC"))
        )
        self.assertEqual(decode(replies[0]).sections, {"GameECA": {"EN": "-32871"}})
        self.assertFalse(table.match.players)
        self.assertFalse(table.match_reservations)
        self.assertEqual(self.state_path.read_bytes(), before)
        await self.menu()  # The same connection can enter its supported CPU table.

    async def read_file(self, port, name, owner=0):
        request = bytearray(file_request(name, size=0x10000)[:-4])
        struct.pack_into("<Q", request, 8, FileOwner(owner, 1, 1).encode())
        error, data = await transact(
            port,
            self.key,
            3,
            0,
            checked(request),
            context=(self.client.cipher, self.client.iv),
            account_id=self.server.account_specs[self.key][0],
        )
        self.assertEqual(error, 0)
        return data[:-4]

    async def test_encrypted_directory_exposes_computer_and_admits_two_opponents(self):
        profile = ProfileServer(Path(self.temp.name) / "profile.jsonl", self.server)
        port = await profile.start(0)
        try:
            rooms = await self.read_file(port, "b/g/RL000")
            tables = await self.read_file(port, "b/g/PTL", ROOM_RAW)
            self.assertEqual(rooms[0x48 + 0x29], ord("A"))
            self.assertEqual(tables[0x5850 + 0x28], ord("A"))
            self.assertEqual(tables[0x5850 + 0x14], 0)
            raw = struct.unpack_from("<Q", tables, 0x5850)[0] ^ GAME_KEY
            self.assertEqual(raw, TABLE_RAW)
            await self.menu()
            await self.request(raw, packet(0x43, 6, 3, "@ComGame=/Rule=0|0|0|0|2|0|0|1/Com=1|3"))
            cards = self.server.economy.inventory(self.key)[:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
            )
            replies = await self.request(raw, packet(0x43, 7, 0, body))
            start = next(decode(p).sections["StartData"] for p in replies if b"@StartData=" in p)
            self.assertEqual(len(start["F"].split("|")), 25)
            self.assertEqual(self.server.default_table.match.player_count, 3)
        finally:
            await profile.stop()


if __name__ == "__main__":
    unittest.main()
