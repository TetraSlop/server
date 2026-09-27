"""Encrypted fixed-room startup; no native scene or live save is used."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests import test_ordinary_rooms as ordinary
from tests.test_admission_identity import native_identity
from tests.test_event_rooms import presence
from tests.test_room_rules import settings
from tests.test_server import Client
from tmserver.identities import service_nick
from tmserver.match_service import decode, packet
from tmserver.native_board_generation import generate_board
from tmserver.native_rules import CRTLCG
from tmserver.ordinary_rooms import OrdinaryRoomConfig, directory_files
from tmserver.room_rules import RoomRules
from tmserver.server import Server
from tmserver.table_preferences import read_preferences
from tmserver.tm_protocol import Command, encode, parse


class FixedRoomIntegration(unittest.IsolatedAsyncioTestCase):
    group = ordinary.OrdinaryRoomsIntegration.group
    flush = ordinary.OrdinaryRoomsIntegration.flush
    send = ordinary.OrdinaryRoomsIntegration.send

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory()
        self.rules = RoomRules(st=1, cb=1, ca=1, tl=4)
        self.saved = RoomRules(gs=2, tl=5)
        config = OrdinaryRoomConfig(
            b"Fixed Special", 0x101, 0x201, b"#FIXEDROOM", b"#FIXEDTABLE", rules=self.rules
        )
        self.server = Server(
            state_path=Path(self.temp.name) / "state.json", ordinary_rooms=(config,)
        )
        self.table = self.server.ordinary_tables[1]
        self.tables, self.groups = (self.table,), ((0, 1),)
        self.clients, self.received = [], [[], []]
        port = await self.server.start(0)
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [client.nick.decode() for client in self.clients]
        for side, client in enumerate(self.clients):
            await client.join(self.table.room.room_channel)
            await self.send(side, self.room_presence(side), room=True)
            await client.join(self.table.table_channel)
            await self.flush(side)

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    def view(self, side):
        return self.server.economy.account_view(self.keys[side])

    def room_presence(self, side, *, ready=False):
        view = self.view(side)
        token = b"PD" if ready else b"DE"
        payload = presence(self.table.room.room_id, view.nn, view.player_id, view.name, token)
        if ready:
            fields = parse(payload[5:])[0].payload.split(b"\x06")
            info = bytearray(fields[-1])
            info[23:25] = b"DB"  # Native table Start requires status3/substatus1.
            fields[-1] = bytes(info)
            payload = b"GTM0L" + encode([Command(token, b"\x06".join(fields))])
        return payload

    def game_messages(self, replies, side):
        return [decode(p) for p in replies[side] if p.startswith(b"GTM0G")]

    async def admit(self, side):
        replies = await self.send(
            side, packet(0x41, 1, body=native_identity(self.view(side), "GameEN"))
        )
        entry = next(p for p in self.game_messages(replies, side) if "GameEA" in p.sections)
        self.assertEqual(entry.sections["GameEA"]["EN"], "1" if side == 0 else "2")

    async def save_other_defaults(self):
        await self.send(0, packet(0x24, body="@Save=/Tbl=0/Game=1" + settings(self.saved)))
        profile = self.server.economy.profile_bytes(self.keys[0])
        self.assertEqual(read_preferences(profile)["Tet"], self.saved.as_dict())
        self.assertEqual(self.table.match.game_rules, self.rules.as_dict())

    async def test_advertised_rules_survive_saved_defaults_and_reach_native_start_packets(self):
        record = directory_files(self.server)["b/g/RL000"][0x110:0x1D8]
        self.assertEqual(record[0x28], ord("A"))
        self.assertEqual(record[0x49:0x50], self.rules.packed())
        self.assertEqual(self.table.room.table_metadata[30:37], self.rules.packed())
        await self.admit(0)
        await self.save_other_defaults()
        replies = await self.send(0, packet(0x12, body="@Rule="))
        query = next(p for p in self.game_messages(replies, 0) if p.primary == 0x13)
        self.assertEqual(
            {k: int(v) for k, v in query.sections["Tet"].items()}, self.rules.as_dict()
        )
        await self.send(0, packet(0x14, body=settings(self.rules)))
        await self.admit(1)
        for side in (0, 1):
            await self.send(side, self.room_presence(side, ready=True), room=True)
        offered = await self.send(0, packet(0x41, 10, body="@GameReady="))
        self.assertTrue(
            any((p.primary, p.subtype) == (0x41, 7) for p in self.game_messages(offered, 1))
        )
        for side in (0, 1):
            replies = await self.send(side, packet(1, body=native_identity(self.view(side), "Req")))
            ack = next(p for p in self.game_messages(replies, side) if p.primary == 2)
            self.assertEqual(
                {k: int(v) for k, v in ack.sections["Tet"].items()}, self.rules.as_dict()
            )
        await self.send(0, packet(0x41, 8, body="@GameOK="))
        replies = await self.send(1, packet(0x41, 8, body="@GameOK="))
        for side in (0, 1):
            init = next(
                p.sections["VsGameInit"]
                for p in self.game_messages(replies, side)
                if "VsGameInit" in p.sections
            )
            self.assertEqual((init["N"], init["R"]), ("2", "0|1|1|1|4|0|1"))
        match = self.table.match
        expected = generate_board(2, CRTLCG(match.rng.state), st=1, cb=1, ca=1)
        for side in (0, 1):
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full()))
                for i, c in enumerate(self.view(side).cards[:5])
            )
            replies = await self.send(side, packet(0x43, 7, side, body))
        for side in (0, 1):
            start = next(
                p.sections["StartData"]
                for p in self.game_messages(replies, side)
                if "StartData" in p.sections
            )
            self.assertEqual(tuple(map(int, start["F"].split("|"))), expected.fields)
            self.assertEqual(
                (int(start["B"]), int(start["E"])), (expected.blocks, expected.effects)
            )
        self.assertEqual(match.phase, "turn")
        self.assertEqual(match.special_flags, dict(st=1, cb=1, ca=1))
        self.assertEqual(self.table.room.table_metadata[30:37], self.rules.packed())
        self.assertEqual(
            read_preferences(self.server.economy.profile_bytes(self.keys[0]))["Tet"],
            self.saved.as_dict(),
        )

    async def test_applying_saved_defaults_cannot_replace_fixed_room_rules(self):
        await self.admit(0)
        await self.save_other_defaults()
        before = self.server.economy.snapshot()
        await self.clients[0].send(
            b"NOTICE "
            + service_nick(self.table.table_raw)
            + b" :"
            + packet(0x14, body=settings(self.saved))
        )
        for _ in range(20):
            if await self.clients[0].read() == b"":
                break
        else:
            self.fail("conflicting Apply did not terminate its sender")
        await self.flush(1)  # Healthy sibling account and room cleanup still complete.
        self.assertEqual(self.server.economy.snapshot(), before)
        self.assertEqual(self.table.match.game_rules, self.rules.as_dict())
        self.assertEqual(self.table.room.table_metadata[30:37], self.rules.packed())
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
        self.assertFalse(self.server.nicks[self.clients[1].nick].closed)


if __name__ == "__main__":
    unittest.main()
