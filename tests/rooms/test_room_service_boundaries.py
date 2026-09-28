"""Cross-service observer ownership and explicit table exit over encrypted peers."""

import unittest
from contextlib import asynccontextmanager

from tests.network import test_chat_service as chats
from tests.events import test_event_spectators as events
from tests.rooms import test_spectator_integration as ordinary
from tests.protocol.test_admission_identity import native_identity
from tmserver.protocol.identities import ROOM_RAW, TABLE_RAW, service_nick
from tmserver.game.packets import decode, packet


class RoomServiceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def event_observer(self):
        fixture = events.EventSpectatorsIntegration()
        await fixture.asyncSetUp()
        try:
            await fixture.start(2)
            await fixture.admit()
            await fixture.watch()
            yield fixture
        finally:
            await fixture.asyncTearDown()

    @asynccontextmanager
    async def ordinary_observer(self):
        fixture = ordinary.SpectatorIntegrationTests()
        await fixture.asyncSetUp()
        try:
            await fixture.admit_watch()
            await fixture.request_watch()
            for _ in range(3):
                await fixture.read_watch()
            yield fixture
        finally:
            await fixture.asyncTearDown()

    async def barrier(self, client):
        await client.send(b"PING :room-boundary")
        lines = []
        for _ in range(100):
            line = await client.read()
            self.assertTrue(line, "healthy observer disconnected")
            if line == b"PONG room-boundary":
                return lines
            lines.append(line)
        self.fail("missing room boundary barrier")

    async def test_event_observer_must_exit_before_ordinary_admission(self):
        for section, request_type in (("GameEN", (0x41, 1)), ("Req", (1,))):
            with self.subTest(section=section):
                async with self.event_observer() as fixture:
                    server = fixture.server
                    peer = server.nicks[fixture.observer.nick]
                    table = server.ordinary_tables[0]
                    spectators = server.event_matches.spectators
                    admission = spectators.admissions[peer]
                    before = server.snapshot(), table.match.snapshot(), fixture.engine.snapshot()
                    tokens = dict(server.economy._reservations)
                    request = packet(
                        *request_type, body=native_identity(fixture.observer_view, section)
                    )
                    # Inspect the rejected dispatch before socket-error cleanup can
                    # hide a partial ordinary admission or release the event view.
                    with self.assertRaisesRegex(ValueError, "current event"):
                        await table.dispatch_table(peer, request)
                    self.assertEqual(
                        (server.snapshot(), table.match.snapshot(), fixture.engine.snapshot()), before
                    )
                    self.assertEqual(server.economy._reservations, tokens)
                    self.assertEqual(spectators.admissions[peer], admission)
                    self.assertIsNone(server.table_for_peer(peer))
                    await self.barrier(fixture.observer)
                    await fixture.drain()

                    # The native explicit exit permits a new service immediately,
                    # without PART or a reconnect, and leaves event players intact.
                    await fixture.observer_send(packet(32, body="@ExitCh="))
                    await self.barrier(fixture.observer)
                    self.assertNotIn(peer, spectators.admissions)
                    target = service_nick(TABLE_RAW)
                    await fixture.observer.send(b"NOTICE " + target + b" :" + request)
                    replies = await self.barrier(fixture.observer)
                    self.assertTrue(any(b"/EN=1" in line for line in replies))
                    self.assertIs(server.table_for_peer(peer), table)
                    self.assertTrue(server.economy.is_reserved(fixture.observer_key))
                    self.assertEqual(fixture.engine.snapshot(), before[2])
                    self.assertEqual(server.snapshot(), before[0])
                    await fixture.drain()

    @staticmethod
    def rosters(lines, target):
        return [
            decode(line.split(b" :", 1)[1]).sections["Chat"]
            for line in lines
            if line.startswith(b":" + target + b"!p@local NOTICE ")
            and b" :GTM0G420004" in line
        ]

    async def test_ordinary_observer_exit_clears_only_its_table_chat(self):
        async with self.ordinary_observer() as fixture:
            server = fixture.server
            table, room = service_nick(TABLE_RAW), service_nick(ROOM_RAW)
            peers = [server.nicks[client.nick] for client in fixture.clients]

            async def send(side, target, payload):
                await fixture.clients[side].send(b"NOTICE " + target + b" :" + payload)
                replies = [None, None]
                for index in (side, 1 - side):
                    replies[index] = await chats.ChatIntegrationTests.flush(fixture, index)
                return replies

            async def chat(side, target):
                view = server.economy.account_view(fixture.clients[side].nick.decode())
                return await send(side, target, packet(0x42, 1, body=f"@Chat=/NN={view.nn:016X}"))

            for side in range(2):
                for target in (table, room):
                    await chat(side, target)
            before = (
                server.snapshot(),
                server.default_table.match.snapshot(),
                dict(server.default_table.match_reservations),
            )
            room_counter = server.chat.counters[room]
            observer = peers[1]
            replies = await send(1, table, packet(32, body="@ExitCh="))
            self.assertNotIn(observer, server.default_table.spectator_admissions)
            self.assertNotIn(observer, server.default_table.spectator_senders)
            self.assertEqual(set(server.chat.groups[table]), {peers[0]})
            view = server.economy.account_view(fixture.clients[0].nick.decode())
            self.assertEqual(
                self.rosters(replies[0], table),
                [{"C": "3", "Num": "0|1", "NN": f"{view.nn:016X}",
                  "CN": view.name[:15].hex().upper()}],
            )
            self.assertEqual(self.rosters(replies[1], table), [])
            self.assertEqual(set(server.chat.groups[room]), set(peers))
            self.assertEqual(server.chat.counters[room], room_counter)
            self.assertEqual(
                (
                    server.snapshot(),
                    server.default_table.match.snapshot(),
                    server.default_table.match_reservations,
                ),
                before,
            )
            counters = dict(server.chat.counters)
            repeated = await send(1, table, packet(32, body="@ExitCh="))
            self.assertTrue(all(not self.rosters(lines, table) for lines in repeated))
            self.assertEqual(server.chat.counters, counters)

            # Re-enter on the still-joined channel; the retired observer writer
            # and removed chat membership must not own this new admission.
            view = server.economy.account_view(fixture.clients[1].nick.decode())
            await fixture.clients[1].send(
                b"NOTICE " + table + b" :" + packet(1, body=native_identity(view, "Req"))
            )
            self.assertEqual(decode(await fixture.read_watch()).sections["PLAYACK"]["EN"], "1")
            await chat(1, table)
            await fixture.request_watch()
            self.assertEqual(
                [decode(await fixture.read_watch()).subtype for _ in range(3)], [37, 38, 9]
            )
            self.assertEqual(set(server.chat.groups[table]), set(peers))
            self.assertEqual(set(server.chat.groups[room]), set(peers))
            self.assertEqual(
                (
                    server.snapshot(),
                    server.default_table.match.snapshot(),
                    server.default_table.match_reservations,
                ),
                before,
            )
            for side, peer in enumerate(peers):
                self.assertIs(server.nicks[fixture.clients[side].nick], peer)
                self.assertEqual(await chats.ChatIntegrationTests.flush(fixture, side), [])


if __name__ == "__main__":
    unittest.main()
