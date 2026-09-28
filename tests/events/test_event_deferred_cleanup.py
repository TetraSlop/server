"""Retired observer closures must not consume a buffered new service admission."""

import asyncio
import unittest
from unittest.mock import patch

from tests.rooms import test_table_release_rebind as event_helpers
from tests.protocol.test_admission_identity import native_identity
from tests.events.test_event_rooms import config, presence
from tests.network.test_session_recovery import ReadingReader, Writer
from tmserver.protocol import wire
from tmserver.economy.packets import named
from tmserver.events.matchmaking import EventTableConfig
from tmserver.protocol.identities import GAME_KEY, SHOP_RAW, game_id, service_nick
from tmserver.game.packets import packet
from tmserver.network.server import Server, Session
from tmserver.rooms.spectators import SpectatorService


class EventDeferredCleanupTests(unittest.IsolatedAsyncioTestCase):
    dispatch = event_helpers.TableReleaseRebindTests.dispatch
    enter_event = event_helpers.TableReleaseRebindTests.enter_event
    start_watched_event = event_helpers.TableReleaseRebindTests.start_watched_event

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.event = config(match_enabled=True, card_service_enabled=True)
        self.event_table = EventTableConfig(self.event.id, game_id(0x1003), b"#EVTABLE001")
        self.server = Server(
            account_count=5,
            event_configs=(self.event,),
            event_tables=(self.event_table,),
            event_clock=lambda: 200,
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        self.peers = []
        for index, nick in enumerate(self.server.accounts):
            peer = Session(self.server, None, Writer(), index + 1)
            peer.nick = nick
            key = self.server.ensure_account(nick)
            self.server.trade.bind(peer, key)
            self.server.nicks[nick] = peer
            self.server.sessions.add(peer)
            self.peers.append(peer)
        await self.start_watched_event()
        self.observer = self.peers[0]
        self.key = self.observer.nick.decode("ascii")
        self.view = self.server.economy.account_view(self.key)
        self.profile = self.server.economy.profile_bytes(self.key)
        self.spectators = self.server.event_matches.spectators
        await self.dispatch(
            self.observer,
            self.event.room_id,
            presence(self.event.room_id, self.view.nn, self.view.player_id, self.view.name),
        )
        await self.observer.command(b"JOIN", [self.event_table.channel])
        await self.dispatch(
            self.observer,
            self.event_table.table_id,
            packet(1, body=native_identity(self.view, "Req")),
        )
        await self.dispatch(
            self.observer, self.event_table.table_id, packet(67, 37, body="@Data=/Watch=0")
        )
        await asyncio.gather(*tuple(self.spectators.senders))

    async def asyncTearDown(self):
        await self.server.stop()

    def request(self, destination):
        if destination == "shop":
            payload = named(
                0xB0,
                0,
                0,
                f"@ShReq=/NN={self.view.nn:016X}/HID=0/Dm=0/Vol=0/CN={self.view.name.hex()}",
            )
            return service_nick(SHOP_RAW), payload
        if destination == "table":
            return service_nick(self.server.default_table.table_raw), packet(
                65, 1, body=native_identity(self.view, "GameEN")
            )
        return service_nick(self.event_table.table_id ^ GAME_KEY), packet(
            1, body=native_identity(self.view, "Req")
        )

    async def check_deferred_close(self, trigger, destination=None):
        peer = self.observer
        runner = retired = None
        processed = asyncio.Event()
        release = asyncio.Event()
        observations = []
        original_command, original_drain = peer.command, peer.writer.drain
        entered = asyncio.Event()

        async def observe_command(verb, args):
            observations.append(
                (peer in self.spectators.admissions, peer.closed, len(self.spectators.closers))
            )
            try:
                return await original_command(verb, args)
            finally:
                processed.set()

        async def fail_first_drain():
            if not entered.is_set():
                entered.set()
                await release.wait()
                raise OSError("retired observer drain failed")
            await original_drain()

        try:
            if destination is not None:
                peer.reader = ReadingReader()
                peer.cipher = await asyncio.to_thread(wire.SqBlowfish, bytes(range(8)))
                peer.iv = 42
                runner = asyncio.create_task(peer.run())
                await asyncio.wait_for(peer.reader.reading.wait(), 2)
                target, payload = self.request(destination)
                frame = wire.frame_line(
                    b"NOTICE " + target + b" :" + payload, peer.cipher, peer.iv
                )

            with patch.object(peer, "command", side_effect=observe_command):
                if trigger == "abort":
                    retired = asyncio.create_task(
                        self.server.event_matches.disconnect(self.peers[3])
                    )
                    if destination is not None:
                        peer.reader.feed_data(frame)
                    await asyncio.wait_for(retired, 3)
                else:
                    with patch.object(peer.writer, "drain", side_effect=fail_first_drain):
                        await self.dispatch(
                            peer, self.event_table.table_id, packet(0x2D, body="@CheckJoinTable=")
                        )
                        await asyncio.wait_for(entered.wait(), 2)
                        retired = self.spectators.tasks[peer]
                        # The sender failure is ready first, then the buffered
                        # frame, then the closer that the failed sender schedules.
                        release.set()
                        if destination is not None:
                            peer.reader.feed_data(frame)
                        await asyncio.wait_for(retired, 3)
                        if self.spectators.closers:
                            await asyncio.gather(*tuple(self.spectators.closers))
                if destination is not None:
                    await asyncio.wait_for(processed.wait(), 3)
                if self.spectators.closers:
                    await asyncio.gather(*tuple(self.spectators.closers))

            self.assertEqual(peer.closed, destination is None)
            self.assertFalse(self.peers[1].closed)
            self.assertEqual(self.server.economy.profile_bytes(self.key), self.profile)
            if destination is not None:
                self.assertEqual(observations, [(False, False, 1)])
                self.assertIs(self.server.nicks.get(peer.nick), peer)
                self.assertFalse(peer.writer.closed)
            if destination == "shop":
                self.assertTrue(self.server.economy.is_busy(self.key))
                self.assertIsNotNone(self.server.economy._account(self.key).session)
            elif destination == "table":
                self.assertIs(self.server.table_for_peer(peer), self.server.default_table)
                self.assertIn(peer, self.server.default_table.match_reservations)
            elif destination == "watch":
                self.assertIn(peer, self.spectators.admissions)
                self.assertFalse(self.server.economy.is_busy(self.key))
            else:
                self.assertNotIn(peer, self.spectators.admissions)
                self.assertFalse(self.server.economy.is_busy(self.key))
        finally:
            release.set()
            for task in (runner, retired):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(t for t in (runner, retired) if t is not None), return_exceptions=True
            )

    async def test_abort_closer_preserves_buffered_shop_entry(self):
        await self.check_deferred_close("abort", "shop")

    async def test_failed_sender_closer_preserves_buffered_shop_entry(self):
        await self.check_deferred_close("sender", "shop")

    async def test_failed_sender_closer_preserves_buffered_table_entry(self):
        await self.check_deferred_close("sender", "table")

    async def test_failed_sender_closer_preserves_buffered_watch_reentry(self):
        await self.check_deferred_close("sender", "watch")

    async def test_abort_closes_observer_without_new_ownership(self):
        await self.check_deferred_close("abort")

    async def test_failed_sender_closes_observer_without_new_ownership(self):
        await self.check_deferred_close("sender")


if __name__ == "__main__":
    unittest.main()
