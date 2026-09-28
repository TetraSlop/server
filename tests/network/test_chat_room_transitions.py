"""Native room departures refresh chat even when no IRC PART is sent."""

import asyncio
import unittest
from contextlib import asynccontextmanager

from tests.network import test_chat_service as chats
from tests.events.test_event_rooms import config, presence
from tests.rooms.test_ordinary_rooms import EXTRA
from tests.network.test_server import Client
from tmserver.protocol.identities import GAME_KEY, service_nick
from tmserver.game.match import decode, packet
from tmserver.network.server import Server
from tmserver.protocol.commands import Command, encode


class ChatRoomTransitionTests(unittest.IsolatedAsyncioTestCase):
    flush = chats.ChatIntegrationTests.flush

    @asynccontextmanager
    async def case(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.server = Server(
            ordinary_rooms=(EXTRA,),
            event_configs=(config(), config("other", 0x1100, name=b"Other")),
            event_clock=lambda: 200,
        )
        self.clients = [Client(), Client()]
        try:
            port = await self.server.start(0)
            for client, nick in zip(self.clients, self.server.accounts):
                await client.open(port, account=nick)
                self.assertIn(b" 422 ", await client.read())
            self.peers = [self.server.nicks[client.nick] for client in self.clients]
            self.rooms = {
                "ordinary1": self.server.ordinary_tables[0].room,
                "ordinary2": self.server.ordinary_tables[1].room,
                "event1": self.server.event_rooms.rooms[0].room,
                "event2": self.server.event_rooms.rooms[1].room,
            }
            yield
        finally:
            for client in self.clients:
                await client.close()
            await self.server.stop()

    @staticmethod
    def source(room):
        return service_nick(room.room_id ^ GAME_KEY)

    async def send(self, side, room, payload):
        await self.clients[side].send(b"NOTICE " + self.source(room) + b" :" + payload)
        return await self.flush(side)

    async def enter(self, side, room):
        view = self.server.economy.account_view(self.clients[side].nick.decode())
        await self.send(side, room, presence(room.room_id, view.nn, view.player_id, view.name))

    async def chat(self, side, room):
        view = self.server.economy.account_view(self.clients[side].nick.decode())
        return await self.send(side, room, packet(0x42, 1, body=f"@Chat=/NN={view.nn:016X}"))

    async def leave(self, side, room):
        view = self.server.economy.account_view(self.clients[side].nick.decode())
        return await self.send(
            side, room, b"GTM0L" + encode([Command(b"PC", f"0x{view.nn:016X}".encode())])
        )

    def rosters(self, lines, room):
        prefix = b":" + self.source(room) + b"!p@local NOTICE "
        return [
            decode(line.split(b" :", 1)[1]).sections["Chat"]
            for line in lines
            if line.startswith(prefix) and b" :GTM0G420004" in line
        ]

    async def old_room(self, room):
        for side in range(2):
            await self.enter(side, room)
            await self.chat(side, room)
        await self.flush(0)
        self.assertEqual(set(self.server.chat.groups[self.source(room)]), set(self.peers))

    async def assert_departed(self, room):
        self.assertNotIn(self.peers[0], room.members)
        self.assertEqual(set(self.server.chat.groups[self.source(room)]), {self.peers[1]})
        witness = self.server.economy.account_view(self.clients[1].nick.decode())
        self.assertEqual(
            self.rosters(await self.flush(1), room),
            [
                {
                    "C": "3",
                    "Num": "0|1",
                    "NN": f"{witness.nn:016X}",
                    "CN": witness.name[:15].hex().upper(),
                }
            ],
        )
        self.assertIs(self.server.nicks[self.clients[0].nick], self.peers[0])
        self.assertEqual(await self.flush(0), [])

    async def test_new_room_entry_removes_old_chat_without_part(self):
        for old_name, new_name in (
            ("ordinary1", "ordinary2"),
            ("ordinary1", "event1"),
            ("event1", "ordinary1"),
            ("event1", "event2"),
        ):
            with self.subTest(old=old_name, new=new_name):
                async with self.case():
                    old, new = self.rooms[old_name], self.rooms[new_name]
                    await self.old_room(old)
                    await self.enter(0, new)
                    self.assertIn(self.peers[0], new.members)
                    await self.assert_departed(old)
                    await self.chat(0, new)
                    before = self.server.snapshot(), dict(self.server.chat.counters)
                    # A delayed old-room exit cannot remove the destination chat.
                    await self.leave(0, old)
                    self.assertEqual(
                        set(self.server.chat.groups[self.source(new)]), {self.peers[0]}
                    )
                    self.assertIn(self.peers[0], new.members)
                    self.assertEqual((self.server.snapshot(), self.server.chat.counters), before)
                    self.assertEqual(self.rosters(await self.flush(1), old), [])

    async def test_room_exit_removes_chat_once_without_part(self):
        for name in ("ordinary1", "event1"):
            with self.subTest(room=name):
                async with self.case():
                    room = self.rooms[name]
                    await self.old_room(room)
                    await self.leave(0, room)
                    await self.assert_departed(room)
                    before = self.server.snapshot(), dict(self.server.chat.counters)
                    await self.leave(0, room)
                    self.assertEqual((self.server.snapshot(), self.server.chat.counters), before)
                    self.assertEqual(self.rosters(await self.flush(1), room), [])


if __name__ == "__main__":
    unittest.main()
