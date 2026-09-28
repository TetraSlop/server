"""Native room-channel departure releases its table before another room entry."""

import asyncio
import unittest

from tests import ROOT
from tests.network.test_server import Client
from tests.protocol.test_admission_identity import native_identity
from tests.rooms.test_room_rules import settings
from tests.rooms import test_room_transitions as transitions
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server
from tmserver.protocol.identities import GAME_KEY, service_nick
from tmserver.rooms.config import load_ordinary_rooms


class OrdinaryRoomPartTests(unittest.IsolatedAsyncioTestCase):
    drain = transitions.RoomTransitions.drain
    send = transitions.RoomTransitions.send
    entry = transitions.RoomTransitions.entry
    enter = transitions.RoomTransitions.enter

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.server = Server(
            account_count=3,
            ordinary_rooms=load_ordinary_rooms(ROOT / "examples/auto_rooms.example.json"),
        )
        self.clients, self.inbox = [], [[], []]
        port = await self.server.start(0)
        for nick in list(self.server.accounts)[:2]:
            client = Client()
            self.clients.append(client)
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.peers = [self.server.nicks[c.nick] for c in self.clients]
        self.keys = [c.nick.decode() for c in self.clients]
        self.quick = self.server.ordinary_tables[-1]
        self.local = self.server.default_table

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()

    async def channel(self, side, verb, channel):
        await self.clients[side].send(verb + b" :" + channel)
        await self.drain(side)

    async def room(self, side, table):
        await self.enter(side, table.room)
        await self.channel(side, b"JOIN", table.room.room_channel)

    async def admit(self, side, table, *, computer=False):
        view = self.server.economy.account_view(self.keys[side])
        await self.send(
            side,
            table.room.table_id,
            packet(
                0x41,
                0x12 if computer else 1,
                body=native_identity(view, "GameENC" if computer else "GameEN"),
            ),
        )

    async def chat(self, side, target):
        view = self.server.economy.account_view(self.keys[side])
        await self.send(side, target, packet(0x42, 1, body=f"@Chat=/NN={view.nn:016X}"))

    async def computer_menu(self):
        await self.room(0, self.local)
        await self.admit(0, self.local, computer=True)
        view = self.server.economy.account_view(self.keys[0])
        await self.send(0, self.local.room.table_id, packet(1, body=native_identity(view, "Req")))
        self.assertEqual(self.local.match.phase, "computer_menu")
        source = service_nick(self.local.table_raw)
        replies = [decode(p) for s, p in self.inbox[0] if s == source and p.startswith(b"GTM0G")]
        self.assertTrue(any("ComList" in reply.sections for reply in replies))

    async def test_room_part_without_table_channel_allows_computer_menu(self):
        await self.room(0, self.quick)
        await self.admit(0, self.quick)
        await self.send(
            0, self.quick.room.table_id, packet(0x14, body=settings(self.quick.room_rules))
        )
        before = self.server.economy.snapshot()
        self.assertNotIn(self.quick.table_channel, self.peers[0].channels)
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        await self.channel(0, b"PART", self.quick.room.room_channel)
        self.assertIsNone(self.server.table_for_peer(self.peers[0]))
        self.assertNotIn(self.peers[0], self.quick.room.members)
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
        self.assertEqual(self.quick.room.table_state, 0)
        self.assertEqual(self.server.economy.snapshot(), before)
        await self.computer_menu()
        # A repeated old PART is harmless even after the new menu reserves us.
        match = self.local.match
        token = self.local.match_reservations[self.peers[0]]
        await self.channel(0, b"PART", self.quick.room.room_channel)
        self.assertIs(self.local.match, match)
        self.assertIs(self.local.match_reservations[self.peers[0]], token)

    async def test_room_part_preserves_other_lobby_player_and_their_reservation(self):
        for side in (0, 1):
            await self.room(side, self.quick)
            await self.admit(side, self.quick)
            await self.chat(side, self.quick.room.room_id)
            await self.chat(side, self.quick.room.table_id)
        await self.send(
            0, self.quick.room.table_id, packet(0x14, body=settings(self.quick.room_rules))
        )
        match = self.quick.match
        token = self.quick.match_reservations[self.peers[1]]
        self.inbox = [[], []]
        await self.channel(0, b"PART", self.quick.room.room_channel)
        self.assertIs(self.quick.match, match)
        self.assertEqual([p.peer for p in match.players], [self.peers[1]])
        self.assertIs(match.owner_peer, self.peers[1])
        self.assertIs(self.quick.match_reservations[self.peers[1]], token)
        self.assertNotIn(self.peers[0], self.quick.room.members)
        self.assertIn(self.peers[1], self.quick.room.members)
        for group in (self.quick.room.room_id, self.quick.room.table_id):
            self.assertEqual(
                set(self.server.chat.groups[service_nick(group ^ GAME_KEY)]), {self.peers[1]}
            )
        self.assertFalse(any(p.startswith(b"GTM0G41000E") for _, p in self.inbox[0]))
        await self.computer_menu()

    async def test_late_old_room_part_preserves_new_computer_admission(self):
        await self.room(0, self.quick)
        await self.computer_menu()
        await self.chat(0, self.local.room.room_id)
        await self.chat(0, self.local.room.table_id)
        groups = {group: dict(members) for group, members in self.server.chat.groups.items()}
        match = self.local.match
        token = self.local.match_reservations[self.peers[0]]
        before = self.server.economy.snapshot(), match.snapshot(), self.local.room.snapshot()
        await self.channel(0, b"PART", self.quick.room.room_channel)
        self.assertIs(self.local.match, match)
        self.assertIs(self.local.match_reservations[self.peers[0]], token)
        self.assertIn(self.peers[0], self.local.room.members)
        self.assertEqual(self.server.chat.groups, groups)
        self.assertEqual(
            (self.server.economy.snapshot(), match.snapshot(), self.local.room.snapshot()), before
        )

    async def test_leaving_computer_configuration_allows_reentry(self):
        await self.computer_menu()
        before = self.server.economy.snapshot()
        await self.channel(0, b"PART", self.local.room.room_channel)
        self.assertIsNone(self.server.table_for_peer(self.peers[0]))
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
        self.assertNotIn(self.peers[0], self.local.room.members)
        self.assertEqual(self.server.economy.snapshot(), before)
        await self.computer_menu()


if __name__ == "__main__":
    unittest.main()
