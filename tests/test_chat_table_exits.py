"""A lobby exit releases only that table's chat membership."""

import asyncio
import unittest
from contextlib import asynccontextmanager

from tests import test_chat_service as chats
from tests.test_match_service import NATIVE_SETTINGS
from tests.test_ordinary_rooms import EXTRA
from tests.test_server import Client
from tmserver.identities import ROOM_RAW, TABLE_RAW, player_id, service_nick
from tmserver.match_service import decode, packet
from tmserver.server import Server


class ChatTableExitTests(unittest.IsolatedAsyncioTestCase):
    flush = chats.ChatIntegrationTests.flush

    @asynccontextmanager
    async def case(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.server = Server(match_mode="versus", ordinary_rooms=(EXTRA,))
        self.clients = [Client(), Client()]
        try:
            port = await self.server.start(0)
            for client, nick in zip(self.clients, self.server.accounts):
                await client.open(port, account=nick)
                self.assertIn(b" 422 ", await client.read())
            self.peers = [self.server.nicks[c.nick] for c in self.clients]
            yield
        finally:
            for client in self.clients:
                await client.close()
            await self.server.stop()

    async def send(self, side, target, payload):
        await self.clients[side].send(b"NOTICE " + target + b" :" + payload)
        out = [None, None]
        for index in (side, 1 - side):
            out[index] = await self.flush(index)
        return out

    async def chat(self, side, target):
        identity = player_id(self.clients[side].nick)
        return await self.send(side, target, packet(0x42, 1, body=f"@Chat=/NN={identity:016X}"))

    @staticmethod
    def rosters(lines, target):
        return [
            decode(line.split(b" :", 1)[1]).sections["Chat"]
            for line in lines
            if line.startswith(b":" + target + b"!p@local NOTICE ") and b" :GTM0G420004" in line
        ]

    async def test_quit_exit_and_kick_update_only_the_removed_table_chat(self):
        target, room = service_nick(TABLE_RAW), service_nick(ROOM_RAW)
        for kind in ("GameQT", "GameExit", "kick"):
            with self.subTest(exit=kind):
                async with self.case():
                    for side, client in enumerate(self.clients):
                        await self.send(
                            side,
                            target,
                            packet(0x41, 1, body=f"@GameEN=/NN={player_id(client.nick):016X}"),
                        )
                        await self.chat(side, target)
                        await self.chat(side, room)
                    await self.send(0, target, packet(0x14, body=NATIVE_SETTINGS))
                    owner, guest = self.peers
                    before = self.server.economy.snapshot()
                    match = self.server.match
                    owner_token = self.server.match_reservations[owner]
                    room_counter = self.server.chat.counters[room]
                    request = (
                        packet(0x41, 5, body=f"@GameQT=/ID={player_id(guest.nick):016X}")
                        if kind == "kick"
                        else packet(0x41, 5 if kind == "GameQT" else 13, body=f"@{kind}=")
                    )
                    sender = 0 if kind == "kick" else 1
                    replies = await self.send(sender, target, request)
                    self.assertEqual(set(self.server.chat.groups[target]), {owner})
                    view = self.server.economy.account_view(owner.nick.decode())
                    self.assertEqual(
                        self.rosters(replies[0], target),
                        [
                            {
                                "C": "3",
                                "Num": "0|1",
                                "NN": f"{view.nn:016X}",
                                "CN": view.name[:15].hex().upper(),
                            }
                        ],
                    )
                    self.assertEqual(self.rosters(replies[1], target), [])
                    self.assertEqual(set(self.server.chat.groups[room]), {owner, guest})
                    self.assertEqual(self.server.chat.counters[room], room_counter)
                    self.assertIs(self.server.match, match)
                    self.assertEqual([p.peer for p in match.players], [owner])
                    self.assertIs(self.server.match_reservations[owner], owner_token)
                    self.assertNotIn(guest, self.server.match_reservations)
                    self.assertEqual(self.server.economy.snapshot(), before)

                    destination = service_nick(EXTRA.table_raw)
                    await self.chat(1, destination)
                    counters = dict(self.server.chat.counters)
                    repeated = await self.send(sender, target, request)
                    self.assertTrue(all(not self.rosters(lines, target) for lines in repeated))
                    self.assertEqual(set(self.server.chat.groups[destination]), {guest})
                    self.assertEqual(set(self.server.chat.groups[room]), {owner, guest})
                    self.assertEqual(self.server.chat.counters, counters)
                    self.assertEqual(self.server.economy.snapshot(), before)
                    for side, peer in enumerate(self.peers):
                        self.assertIs(self.server.nicks[self.clients[side].nick], peer)
                        self.assertEqual(await self.flush(side), [])


if __name__ == "__main__":
    unittest.main()
