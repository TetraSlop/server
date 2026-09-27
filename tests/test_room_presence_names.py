"""Room snapshots and deltas retain the authenticated character name."""

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from tests.test_display_name_identity import NAMES, provision_names
from tests.test_event_rooms import config, presence
from tests.test_server import Client
from tmserver.identities import GAME_KEY, service_nick
from tmserver.server import Server
from tmserver.tm_protocol import parse


class RoomPresenceNameTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def case(self, event):
        asyncio.get_running_loop().slow_callback_duration = 1
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            self.server = Server(
                state_path=Path(directory) / "state.json",
                event_configs=(config(),),
                event_clock=lambda: 200,
            )
            self.clients = []
            try:
                provision_names(self.server)
                port = await self.server.start(0)
                for nick in self.server.accounts:
                    client = Client()
                    self.clients.append(client)
                    await client.open(port, account=nick)
                    self.assertIn(b" 422 ", await client.read())
                self.peers = [self.server.nicks[c.nick] for c in self.clients]
                self.keys = [c.nick.decode() for c in self.clients]
                self.room = self.server.event_rooms.rooms[0].room if event else self.server.room
                self.target = service_nick(self.room.room_id ^ GAME_KEY)
                yield
            finally:
                for client in self.clients:
                    await client.close()
                await self.server.stop()

    async def barrier(self, side):
        client = self.clients[side]
        await client.send(b"PING :presence-name")
        replies = []
        for _ in range(40):
            line = await client.read()
            self.assertTrue(line, "canonical presence disconnected the client")
            if line == b"PONG presence-name":
                return replies
            replies.append(line)
        self.fail("missing presence barrier")

    async def send_presence(self, side, token=b"DE", name=None):
        view = self.server.economy.account_view(self.keys[side])
        await self.clients[side].send(
            b"NOTICE "
            + self.target
            + b" :"
            + presence(
                self.room.room_id,
                view.nn,
                view.player_id,
                view.name if name is None else name,
                token,
            )
        )

    @staticmethod
    def names(lines):
        return [
            command.payload.split(b"\x06")[4]
            for line in lines
            if b" :GTM0L" in line
            for command in parse(line.split(b" :", 1)[1][5:])
            if command.token == b"PD"
        ]

    async def test_forged_de_and_pd_never_reach_ordinary_or_event_rosters(self):
        for event in (False, True):
            for token in (b"DE", b"PD"):
                with self.subTest(event=event, token=token):
                    async with self.case(event):
                        await self.send_presence(1)
                        await self.barrier(1)
                        if token == b"PD":
                            await self.send_presence(0)
                            await self.barrier(0)
                            await self.barrier(1)
                        before = self.server.snapshot()
                        saved = self.server.store.path.read_bytes()
                        # Both accounts share their first 15 bytes. The final
                        # byte still belongs to authenticated identity.
                        await self.send_presence(0, token, NAMES[1])
                        await self.clients[0].send(b"PING :forged-name")
                        for _ in range(12):
                            line = await self.clients[0].read()
                            if not line:
                                break
                            self.assertNotEqual(line, b"PONG forged-name", "forged name accepted")
                        else:
                            self.fail("forged presence did not close its connection")
                        self.assertEqual(self.names(await self.barrier(1)), [])
                        self.assertNotIn(self.peers[0], self.room.members)
                        self.assertEqual(set(self.room.members), {self.peers[1]})
                        self.assertEqual(self.server.snapshot(), before)
                        self.assertEqual(self.server.store.path.read_bytes(), saved)

    async def test_full_canonical_names_survive_de_pd_and_room_snapshots(self):
        for event in (False, True):
            with self.subTest(event=event):
                async with self.case(event):
                    for side in (1, 0):
                        await self.send_presence(side)
                        await self.barrier(side)
                    self.assertEqual(self.names(await self.barrier(1)), [NAMES[0]])
                    before = self.server.snapshot()
                    for side in (0, 1):
                        await self.send_presence(side, b"PD")
                        self.assertEqual(self.names(await self.barrier(side)), [NAMES[side]])
                        self.assertEqual(self.names(await self.barrier(1 - side)), [NAMES[side]])
                    snapshot = self.room.snapshot()
                    for index, name in enumerate(reversed(NAMES)):
                        at = 0x50 + index * 0x58 + 0x28
                        self.assertEqual(snapshot[at : at + 16], name)
                    self.assertEqual(self.server.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
