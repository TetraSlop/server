"""Encrypted late event-room PART cannot clear a newer room, table or observer."""

import asyncio
import unittest

from tests.test_admission_identity import native_identity
from tests import test_event_matches_integration as event_tests
from tests import test_event_spectators as observer_tests
from tests.test_event_rooms import config, presence
from tests.test_server import Client
from tmserver.event_matchmaking import EventTableConfig
from tmserver.identities import GAME_KEY, TABLE_RAW, game_id, service_nick
from tmserver.match_service import packet
from tmserver.server import Server
from tmserver.spectator_service import SpectatorService
from tmserver.tm_protocol import parse


class EventRoomPartTests(unittest.IsolatedAsyncioTestCase):
    drain = event_tests.EventMatchIntegrationTests.drain
    send = event_tests.EventMatchIntegrationTests.send
    identity = event_tests.EventMatchIntegrationTests.identity
    enter_selection = event_tests.EventMatchIntegrationTests.enter_selection
    admit = event_tests.EventMatchIntegrationTests.admit
    engine = event_tests.EventMatchIntegrationTests.engine
    watch = observer_tests.EventSpectatorsIntegration.watch
    observer_send = observer_tests.EventSpectatorsIntegration.observer_send
    read_watch = observer_tests.EventSpectatorsIntegration.read_watch

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.cfg = config(match_enabled=True)
        self.table = EventTableConfig("festival", game_id(0x1003), b"#EVTABLE001")
        self.server = Server(
            account_count=3,
            event_configs=(self.cfg, config("old", 0x1100, name=b"Old Event")),
            event_tables=(self.table,),
            event_clock=lambda: 200,
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        self.port = await self.server.start(0)
        self.clients = [Client(), Client()]
        self.inbox = [[], []]
        for client, nick in zip(self.clients, self.server.accounts):
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [client.nick.decode() for client in self.clients]
        self.peers = [self.server.nicks[client.nick] for client in self.clients]
        self.current, self.old = self.server.event_rooms.rooms

    async def asyncTearDown(self):
        if hasattr(self, "observer"):
            await self.observer.close()
        for client in self.clients:
            await client.close()
        await self.server.stop()

    async def enter(self, side, room):
        view = self.server.economy.account_view(self.keys[side])
        await self.send(
            side, room.room_id, presence(room.room_id, view.nn, view.player_id, view.name)
        )

    async def channel(self, side, verb, channel):
        await self.clients[side].send(verb + b" " + channel)
        await self.drain()

    async def leave_old(self):
        await self.channel(0, b"PART", self.old.room_channel)
        self.assertNotIn(self.old.room_channel, self.peers[0].channels)
        self.assertIn(self.current.room_channel, self.peers[0].channels)
        self.assertIn(self.peers[0], self.current.room.members)
        self.assertEqual(self.server.events._present[self.keys[0]], "festival")

    async def old_room(self):
        await self.enter(0, self.old.room)
        await self.channel(0, b"JOIN", self.old.room_channel)

    async def test_old_part_after_new_room_entry_preserves_new_admission(self):
        await self.old_room()
        await self.enter(0, self.current.room)
        await self.channel(0, b"JOIN", self.current.room_channel)
        admission = self.server.events.admission(self.keys[0])
        before = self.server.snapshot(), self.current.room.snapshot()
        await self.leave_old()
        self.assertEqual(self.server.events.admission(self.keys[0]), admission)
        self.assertEqual((self.server.snapshot(), self.current.room.snapshot()), before)

    async def test_old_part_preserves_new_selection_and_current_part_releases_it(self):
        await self.old_room()
        await self.enter_selection(0, False)
        await self.enter(1, self.current.room)
        await self.channel(0, b"JOIN", self.current.room_channel)
        coordinator = self.server.event_matches.coordinator
        number = coordinator.session(self.peers[0])
        self.assertIsNotNone(number)
        before = self.server.snapshot()
        await self.leave_old()
        self.assertEqual(coordinator.session(self.peers[0]), number)
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        self.inbox = [[], []]
        await self.channel(0, b"PART", self.current.room_channel)
        self.assertIsNone(coordinator.session(self.peers[0]))
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
        self.assertNotIn(self.keys[0], self.server.events._present)
        self.assertNotIn(self.peers[0], self.current.room.members)
        self.assertIn(self.peers[1], self.current.room.members)
        self.assertIs(self.server.nicks[self.clients[0].nick], self.peers[0])
        changes = [
            parse(payload[5:])[-1] for _, payload in self.inbox[1] if payload.startswith(b"GTM0L")
        ]
        self.assertTrue(any(command.token == b"PC" for command in changes))
        self.assertEqual(self.server.snapshot(), before)

    async def test_old_event_part_does_not_release_current_ordinary_table(self):
        await self.old_room()
        await self.enter(0, self.server.room)
        await self.channel(0, b"JOIN", self.server.room.room_channel)
        view = self.server.economy.account_view(self.keys[0])
        await self.send(
            0, game_id(TABLE_RAW), packet(0x41, 1, body=native_identity(view, "GameEN"))
        )
        engine = self.server.match
        token = self.server.match_reservations[self.peers[0]]
        before = self.server.snapshot(), engine.snapshot(), self.server.room.snapshot()
        await self.channel(0, b"PART", self.old.room_channel)
        self.assertIs(self.server.match, engine)
        self.assertIs(self.server.match_reservations[self.peers[0]], token)
        self.assertIn(self.peers[0], self.server.room.members)
        self.assertEqual(
            (self.server.snapshot(), engine.snapshot(), self.server.room.snapshot()), before
        )

    async def test_old_part_preserves_active_event_table_and_current_part_aborts_it(self):
        await self.old_room()
        await self.admit()
        await self.channel(0, b"JOIN", self.current.room_channel)
        engine = self.engine
        before = engine.snapshot(), self.server.snapshot()
        await self.leave_old()
        self.assertIs(self.engine, engine)
        self.assertEqual((engine.snapshot(), self.server.snapshot()), before)
        self.assertTrue(all(self.server.economy.is_reserved(key) for key in self.keys))
        await self.clients[0].send(b"PART " + self.current.room_channel)
        for client in self.clients:
            for _ in range(80):
                if not await client.read():
                    break
            else:
                self.fail("current event room PART did not close aborted participants")
        self.assertIsNone(self.server.event_matches.coordinator.snapshot("festival"))
        self.assertFalse(any(self.server.economy.is_reserved(key) for key in self.keys))
        self.assertEqual(self.current.room.table_count, 0)
        self.assertEqual(self.server.snapshot(), before[1])

    async def observer_barrier(self):
        await self.observer.send(b"PING :observer-part")
        for _ in range(200):
            line = await self.observer.read()
            self.assertTrue(line, "observer was disconnected")
            if line == b"PONG observer-part":
                return
        self.fail("observer barrier missing")

    async def test_old_part_preserves_current_observer_and_current_part_only_releases_observer(
        self,
    ):
        await self.admit()
        self.observer = Client()
        await self.observer.open(self.port, account=list(self.server.accounts)[2])
        self.assertIn(b" 422 ", await self.observer.read())
        self.observer_key = self.observer.nick.decode()
        self.observer_view = self.server.economy.account_view(self.observer_key)
        self.observer_before = self.server.economy.profile_bytes(self.observer_key)
        self.target = service_nick(self.table.table_id ^ GAME_KEY)
        view = self.observer_view
        await self.observer_send(
            presence(self.old.room_id, view.nn, view.player_id, view.name),
            service_nick(self.old.raw_owner),
        )
        await self.observer.send(b"JOIN " + self.old.room_channel)
        await self.observer_barrier()
        await self.watch()
        await self.observer.send(b"JOIN " + self.current.room_channel)
        await self.observer_barrier()
        peer = self.server.nicks[self.observer.nick]
        spectators = self.server.event_matches.spectators
        admission = spectators.admissions[peer]
        before = self.engine.snapshot()
        await self.observer.send(b"PART " + self.old.room_channel)
        await self.observer_barrier()
        self.assertEqual(spectators.admissions[peer], admission)
        self.assertIn(peer, self.current.room.members)
        self.assertEqual(self.server.events._present[self.observer_key], "festival")
        await self.observer.send(b"PART " + self.current.room_channel)
        await self.observer_barrier()
        self.assertNotIn(peer, spectators.admissions)
        self.assertNotIn(peer, self.current.room.members)
        self.assertNotIn(self.observer_key, self.server.events._present)
        self.assertFalse(peer.closed)
        self.assertEqual(self.engine.snapshot(), before)
        self.assertTrue(all(self.server.economy.is_reserved(key) for key in self.keys))


if __name__ == "__main__":
    unittest.main()
