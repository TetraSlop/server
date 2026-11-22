"""Event table observer admission, private-hand exclusion and independent I/O."""

import asyncio
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.events import test_event_matches_integration as event_tests
from tests.protocol.test_admission_identity import native_identity
from tests.events.test_event_rooms import presence
from tests.support.irc import Client
from tmserver.protocol.identities import GAME_KEY, service_nick
from tmserver.game.errors import MatchError
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server
from tmserver.rooms.spectators import SpectatorService


class EventSpectatorsIntegration(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = event_tests.EventMatchIntegrationTests.asyncSetUp
    drain = event_tests.EventMatchIntegrationTests.drain
    send = event_tests.EventMatchIntegrationTests.send
    identity = event_tests.EventMatchIntegrationTests.identity
    enter_selection = event_tests.EventMatchIntegrationTests.enter_selection
    admit = event_tests.EventMatchIntegrationTests.admit
    engine = event_tests.EventMatchIntegrationTests.engine

    async def test_viewer_and_channel_exit_preserve_event_participants(self):
        await self.start(2)
        await self.admit()
        await self.watch()
        spectators = self.server.event_matches.spectators
        peer = self.server.nicks[self.observer.nick]
        before = self.engine.snapshot()
        for body, expected in (("@Viewer=/IN=0", True), ("@Viewer=/OUT=0", False)):
            await self.observer_send(packet(65, 17, body=body))
            await self.observer.send(b"PING :viewer-event")
            self.assertEqual(await self.observer.read(), b"PONG viewer-event")
            self.assertEqual(peer in spectators.viewers, expected)
        await self.observer_send(packet(32, body="@ExitCh="))
        await self.observer.send(b"PING :exit-event")
        self.assertEqual(await self.observer.read(), b"PONG exit-event")
        self.assertNotIn(peer, spectators.admissions)
        self.assertFalse(peer.closed)
        self.assertEqual(self.engine.snapshot(), before)
        self.assertEqual(self.server.economy.profile_bytes(self.observer_key), self.observer_before)

    play = event_tests.EventMatchIntegrationTests.play

    async def asyncTearDown(self):
        if hasattr(self, "observer"):
            await self.observer.close()
        await event_tests.EventMatchIntegrationTests.asyncTearDown(self)

    async def start(self, n=2):
        self.table = replace(self.table, player_count=n)
        self.server = Server(
            state_path=self.path,
            account_count=n + 1,
            event_configs=(self.cfg,),
            event_tables=(self.table,),
            event_clock=lambda: 200,
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        port = await self.server.start(0)
        self.clients = [Client() for _ in range(n)]
        self.inbox = [[] for _ in range(n)]
        keys = list(self.server.accounts)
        for client, nick in zip(self.clients, keys):
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]
        self.observer = Client()
        await self.observer.open(port, account=keys[n])
        self.assertIn(b" 422 ", await self.observer.read())
        self.observer_key = self.observer.nick.decode()
        self.target = service_nick(self.table.table_id ^ GAME_KEY)
        self.observer_view = self.server.economy.account_view(self.observer_key)
        self.observer_before = self.server.economy.profile_bytes(self.observer_key)

    async def observer_send(self, payload, target=None):
        await self.observer.send(b"NOTICE " + (target or self.target) + b" :" + payload)

    async def read_watch(self):
        for _ in range(200):
            line = await self.observer.read()
            self.assertTrue(line, "event observer was unexpectedly closed")
            if line.startswith(b":" + self.target + b"!p@local NOTICE "):
                return line.split(b" :", 1)[1]
        self.fail("observer response limit")

    async def admit_watch(self):
        binding = self.server.event_rooms.rooms[0]
        view = self.observer_view
        await self.observer_send(
            presence(binding.room_id, view.nn, view.player_id, view.name),
            service_nick(self.cfg.room_id ^ GAME_KEY),
        )
        await self.observer_send(packet(0x2D, body="@CheckJoinTable="))
        self.assertEqual(
            decode(await self.read_watch()).sections["CheckJoinTable_Ans"],
            {"Join": "1", "Mem": "0"},
        )
        # JOIN may be preceded by room notices; consume through an explicit barrier.
        await self.observer.send(b"JOIN " + self.table.channel)
        await self.observer.send(b"PING :observer-join")
        for _ in range(200):
            line = await self.observer.read()
            self.assertTrue(line, "observer closed during JOIN")
            if line == b"PONG observer-join":
                break
        else:
            self.fail("missing observer JOIN barrier")
        await self.observer_send(packet(1, body=native_identity(view, "Req")))
        admission = decode(await self.read_watch())
        self.assertEqual(admission.primary, 2)
        self.assertEqual(admission.sections["Tab"]["in"], "0")

    async def watch(self):
        await self.admit_watch()
        await self.observer_send(packet(0x43, 0x25, 0, "@Data=/Watch=0"))
        bootstrap = [await self.read_watch() for _ in range(3)]
        self.assertEqual([decode(p).subtype for p in bootstrap], [0x25, 0x26, 9])
        self.assertEqual(decode(bootstrap[0]).sections["WatchInfo"]["N"], str(len(self.clients)))
        self.assertEqual(decode(bootstrap[0]).sections["WatchInfo"]["R"].split("|")[-1], "0")
        binding = self.server.event_rooms.room_for_service(self.table.table_id)
        slot = next(t for t in binding.room.tables if t.table_id == self.table.table_id)
        self.assertEqual((slot.table_metadata[37] - 65) & 3, 0)
        self.assertFalse(self.server.economy.is_busy(self.observer_key))

    async def finish_and_check(self, n):
        await self.start(n)
        await self.admit()
        await self.watch()
        await self.play()
        messages = []
        for _ in range(200):
            raw = await self.read_watch()
            messages.append(raw)
            if b"@EventResult=" in raw:
                break
        else:
            self.fail("missing event observer result")
        self.assertEqual(
            [decode(p).argument for p in messages if decode(p).subtype == 9], list(range(1, 5 * n))
        )
        self.assertEqual(
            [decode(p).argument for p in messages if decode(p).subtype == 10], list(range(5 * n))
        )
        self.assertFalse(
            any(b"@EventCard=" in p or b"@TCList=" in p or b"@ResultData=" in p for p in messages)
        )
        self.assertEqual(self.server.economy.profile_bytes(self.observer_key), self.observer_before)
        for side in range(n):
            await self.send(
                side, self.table.table_id, packet(0x43, 14, 5 * n, f"@Ready=/Ans={side}")
            )
        self.assertIsNone(self.server.event_matches.coordinator.snapshot("festival"))
        self.assertFalse(
            any(self.server.economy.is_busy(k) for k in self.keys + [self.observer_key])
        )

    async def test_two_player_event_observer_full_round(self):
        await self.finish_and_check(2)

    async def test_three_player_event_observer_full_round(self):
        await self.finish_and_check(3)

    async def test_deferred_bootstrap_codec_failure_preserves_encrypted_event_round(self):
        await self.start(3)
        await self.admit()
        await self.admit_watch()
        engine = self.engine
        before = [self.server.economy.profile_bytes(key) for key in self.keys]
        spectators = self.server.event_matches.spectators
        peer = self.server.nicks[self.observer.nick]
        # Event configuration forbids CBC. Inject the shared codec failure at
        # deferred attachment instead of creating an unsupported event board.
        snapshot = engine.observer_snapshot()
        with patch.object(
            engine, "observer_snapshot", return_value=replace(snapshot, phase="battle")
        ):
            await self.observer_send(packet(0x43, 0x25, 0, "@Data=/Watch=0"))
            await self.observer.send(b"PING :deferred-event-watch")
            self.assertEqual(await self.observer.read(), b"PONG deferred-event-watch")
        service = spectators.contexts[self.table.table_id][1]
        self.assertTrue(service.active(peer))
        with patch(
            "tmserver.rooms.spectators.bootstrap",
            side_effect=MatchError("injected snapshot encoding failure"),
        ):
            await self.send(0, self.table.table_id, packet(0x43, 9, 0, "@TurnData=/Ans=0"))
            self.assertEqual(await self.observer.read(), b"")
        self.assertFalse(service.active(peer))
        self.assertNotIn(peer, spectators.admissions)
        self.assertIsNone(self.server.event_matches.failure)
        self.assertEqual(engine.phase, "turn")
        self.assertTrue(all(self.server.economy.is_busy(key) for key in self.keys))
        await self.play()
        self.assertIs(self.engine, engine)
        self.assertFalse(engine.outcome.aborted)
        self.assertIsNone(self.server.event_matches.failure)
        for side, key in enumerate(self.keys):
            turns = [decode(p).argument for _, p in self.inbox[side] if b"@TurnData=" in p]
            self.assertEqual(turns, list(range(15)))
            self.assertEqual(sum(b"@EventResult=" in p for _, p in self.inbox[side]), 1)
            self.assertFalse(self.server.nicks[self.clients[side].nick].closed)
            self.assertEqual(self.server.economy.profile_bytes(key), before[side])
        self.assertEqual(self.server.economy.profile_bytes(self.observer_key), self.observer_before)

    async def test_slow_observer_stream_does_not_hold_event_participant_lock(self):
        await self.start()
        await self.admit()
        await self.watch()
        peer = self.server.nicks[self.observer.nick]
        original = peer.send
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(message):
            if b"GTM0G43" in message:
                entered.set()
                await release.wait()
            await original(message)

        with patch.object(peer, "send", side_effect=slow):
            try:
                for side in range(2):
                    await self.send(
                        side, self.table.table_id, packet(0x43, 9, 0, f"@TurnData=/Ans={side}")
                    )
                await asyncio.wait_for(
                    self.send(0, self.table.table_id, packet(0x43, 10, 0, "@PutData=/P=0/H=0/F=0")),
                    2,
                )
                await asyncio.wait_for(entered.wait(), 2)
                async with asyncio.timeout(1):
                    async with self.server.event_matches.lock:
                        pass
                self.assertTrue(self.engine.sequence > 0 or self.engine.phase == "battle")
            finally:
                release.set()

    async def test_participant_abort_closes_only_that_events_observers(self):
        await self.start()
        await self.admit()
        await self.watch()
        await self.clients[0].close()
        for _ in range(30):
            if await self.observer.read() == b"":
                break
        else:
            self.fail("event observer outlived aborted match")
        self.assertFalse(self.server.event_matches.spectators.admissions)
        self.assertEqual(self.server.economy.profile_bytes(self.observer_key), self.observer_before)

    async def test_observer_cannot_join_without_membership(self):
        await self.start()
        await self.admit()
        before = self.engine.snapshot()
        await self.observer_send(packet(1, body=f"@Req=/NN={self.observer_view.nn:016X}/HID=3"))
        self.assertEqual(await self.observer.read(), b"")
        self.assertEqual(self.engine.snapshot(), before)
        self.assertTrue(all(not self.server.nicks[c.nick].closed for c in self.clients))

    async def test_authenticated_observer_cannot_submit_a_participant_move(self):
        await self.start()
        await self.admit()
        await self.watch()
        before = self.engine.snapshot()
        await self.observer_send(packet(0x43, 10, 0, "@PutData=/P=0/H=0/F=0"))
        self.assertEqual(await self.observer.read(), b"")
        self.assertEqual(self.engine.snapshot(), before)
        self.assertTrue(all(not self.server.nicks[c.nick].closed for c in self.clients))

    async def test_observer_writer_error_closes_observer_and_preserves_participants(self):
        await self.start()
        await self.admit()
        await self.watch()
        peer = self.server.nicks[self.observer.nick]
        with patch.object(peer, "send", side_effect=ValueError("injected writer failure")):
            for side in range(2):
                await self.send(
                    side, self.table.table_id, packet(0x43, 9, 0, f"@TurnData=/Ans={side}")
                )
            await self.send(0, self.table.table_id, packet(0x43, 10, 0, "@PutData=/P=0/H=0/F=0"))
            self.assertEqual(await self.observer.read(), b"")
        self.assertFalse(self.server.event_matches.spectators.admissions)
        self.assertTrue(all(not self.server.nicks[c.nick].closed for c in self.clients))
        async with asyncio.timeout(1):
            async with self.server.event_matches.lock:
                pass


if __name__ == "__main__":
    unittest.main()
