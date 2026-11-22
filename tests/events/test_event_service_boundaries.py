"""Event admission cannot reuse room presence while an ordinary table owns the peer."""

import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from tests.events import test_event_spectators as events
from tests.rooms import test_spectator_integration as ordinary_tests
from tests.protocol.test_admission_identity import native_identity
from tests.events.test_event_rooms import presence
from tests.game.test_match_service import NATIVE_SETTINGS
from tests.support.irc import Client
from tmserver.protocol.identities import GAME_KEY, TABLE_RAW, service_nick
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server


class EventServiceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def barrier(self, client):
        await client.send(b"PING :event-boundary")
        output = []
        for _ in range(100):
            line = await client.read()
            self.assertTrue(line, "healthy peer disconnected")
            if line == b"PONG event-boundary":
                return output
            output.append(line)
        self.fail("missing event boundary barrier")

    @asynccontextmanager
    async def ordinary_watcher_in_event_room(self):
        event = events.EventSpectatorsIntegration()
        await event.asyncSetUp()
        cpu_client = Client()
        try:
            # Reuse the event fixture's two players and third-account observer,
            # adding one independent account to host the ordinary CPU game.
            def configured(**options):
                return Server(**(options | {"account_count": 4}))

            with patch.object(events, "Server", side_effect=configured):
                await event.start(2)
            await event.admit()
            server = event.server
            await cpu_client.open(
                server.listener.sockets[0].getsockname()[1], account=list(server.accounts)[3]
            )
            self.assertIn(b" 422 ", await cpu_client.read())
            ordinary = ordinary_tests.SpectatorIntegrationTests()
            ordinary.server = server
            ordinary.clients = [cpu_client, event.observer]
            ordinary.target = service_nick(TABLE_RAW)
            view = server.economy.account_view(cpu_client.nick.decode())
            for payload in (
                packet(0x41, 1, body=native_identity(view, "GameEN")),
                packet(20, body=NATIVE_SETTINGS),
                packet(0x41, 10, body="@GameReady="),
                packet(1, body=native_identity(view, "Req")),
                packet(0x41, 8, body="@GameOK="),
            ):
                await ordinary.player(payload)
            cards = server.economy.inventory(cpu_client.nick.decode())[:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, card.full()))
                for i, card in enumerate(cards)
            )
            await ordinary.player(packet(0x43, 7, 0, body))

            room = server.event_rooms.rooms[0]
            view = event.observer_view
            await event.observer_send(
                presence(room.room_id, view.nn, view.player_id, view.name),
                service_nick(room.raw_owner),
            )
            await self.barrier(event.observer)
            await ordinary.admit_watch()
            await event.observer.send(b"JOIN " + event.table.channel)
            await self.barrier(event.observer)
            yield event, ordinary
        finally:
            await cpu_client.close()
            await event.asyncTearDown()

    async def test_ordinary_observer_must_exit_before_event_watch_or_selection(self):
        for route in ("watch", "selection"):
            with self.subTest(route=route):
                async with self.ordinary_watcher_in_event_room() as (event, ordinary):
                    server = event.server
                    peer = server.nicks[event.observer.nick]
                    view = event.observer_view
                    coordinator = server.event_matches.coordinator
                    spectators = server.event_matches.spectators
                    if route == "watch":
                        target = event.table.table_id
                        request = packet(1, body=native_identity(view, "Req"))
                        reply_count = 1
                    else:
                        target = event.cfg.init_id
                        request = packet(
                            0x98,
                            body=f"@EInitReq=/NN={view.nn:016X}/HID={view.player_id}"
                            f"/CN={view.name.hex()}/Dm=1/Vol=1/GID={view.player_id:016X}",
                        )
                        reply_count = 2
                    before = (
                        server.snapshot(),
                        server.default_table.match.snapshot(),
                        event.engine.snapshot(),
                    )
                    tokens = dict(server.economy._reservations)
                    # Check before transport-error cleanup could hide a second
                    # admission or release the original observer's ownership.
                    with self.assertRaisesRegex(ValueError, "current table"):
                        await server.event_matches.dispatch(peer, target, request)
                    self.assertIn(peer, server.default_table.spectator_admissions)
                    self.assertNotIn(peer, spectators.admissions)
                    self.assertIsNone(coordinator.session(peer))
                    self.assertEqual(server.economy._reservations, tokens)
                    self.assertEqual(
                        (
                            server.snapshot(),
                            server.default_table.match.snapshot(),
                            event.engine.snapshot(),
                        ),
                        before,
                    )
                    await self.barrier(event.observer)
                    await event.drain()

                    await event.observer.send(
                        b"NOTICE " + ordinary.target + b" :" + packet(32, body="@ExitCh=")
                    )
                    await self.barrier(event.observer)
                    self.assertNotIn(peer, server.default_table.spectator_admissions)
                    source = service_nick(target ^ GAME_KEY)

                    async def enter():
                        await event.observer.send(b"NOTICE " + source + b" :" + request)
                        replies = []
                        for _ in range(reply_count):
                            line = await event.observer.read()
                            self.assertTrue(line.startswith(b":" + source + b"!p@local NOTICE "))
                            replies.append(line.split(b" :", 1)[1])
                        return replies

                    replies = await enter()
                    if route == "watch":
                        self.assertEqual(decode(replies[0]).sections["PLAYACK"]["EN"], "1")
                        admission = spectators.admissions[peer]
                    else:
                        admission = coordinator.session(peer)
                        self.assertIsNotNone(admission)
                        self.assertEqual(int(decode(replies[1]).sections["EInit"]["N"]), admission)
                    reserved = dict(server.economy._reservations)
                    self.assertEqual(await enter(), replies)
                    self.assertEqual(
                        spectators.admissions[peer] if route == "watch" else coordinator.session(peer),
                        admission,
                    )
                    self.assertEqual(server.economy._reservations, reserved)
                    self.assertEqual(
                        (
                            server.snapshot(),
                            server.default_table.match.snapshot(),
                            event.engine.snapshot(),
                        ),
                        before,
                    )
                    await self.barrier(event.observer)
                    await event.drain()
                    await self.barrier(ordinary.clients[0])


if __name__ == "__main__":
    unittest.main()
