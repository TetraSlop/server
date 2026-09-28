"""Event-driver failure cleanup cannot close a player's later ordinary activity."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from tests.rooms import test_table_release_rebind as release
from tests.events.test_event_rooms import config
from tests.network.test_session_recovery import Writer
from tmserver.network import dispatch as services
from tmserver.economy.packets import named
from tmserver.events.models import EventTableConfig
from tmserver.protocol.identities import SHOP_RAW, TABLE_RAW, game_id, service_nick
from tmserver.game.packets import packet
from tmserver.network.server import Server, Session


class ClosingWriter(Writer):
    def __init__(self):
        super().__init__()
        self.hold = False
        self.waiting = asyncio.Event()
        self.resume = asyncio.Event()

    async def wait_closed(self):
        if self.hold:
            self.waiting.set()
            await self.resume.wait()


class EventDriverReentryTests(unittest.IsolatedAsyncioTestCase):
    dispatch = release.TableReleaseRebindTests.dispatch
    enter_event = release.TableReleaseRebindTests.enter_event

    async def asyncSetUp(self):
        self.event = config(match_enabled=True)
        table = EventTableConfig(self.event.id, game_id(0x1003), b"#EVTABLE001")
        self.server = Server(
            event_configs=(self.event,), event_tables=(table,), event_clock=lambda: 200
        )
        self.adapter = self.server.event_matches
        self.binding = self.server.event_rooms.rooms[0]
        for index, nick in enumerate(self.server.accounts):
            peer = Session(self.server, None, ClosingWriter(), index + 1)
            peer.nick = nick
            key = self.server.ensure_account(nick)
            self.server.trade.bind(peer, key)
            self.server.nicks[nick] = peer
            self.server.sessions.add(peer)
            await self.enter_event(peer)
            await peer.command(b"JOIN", [self.binding.room_channel])

    async def asyncTearDown(self):
        await self.server.stop()

    @asynccontextmanager
    async def paused_failure(self):
        # Follow the same set order as the failure handler, without changing it.
        first, survivor = [
            p for p in self.server.sessions if self.adapter.coordinator.participant(p)
        ]
        first.writer.hold = True
        with patch.object(
            self.adapter.coordinator, "poll", side_effect=ValueError("event driver failure")
        ):
            task = asyncio.create_task(self.adapter._run())
            try:
                await asyncio.wait_for(first.writer.waiting.wait(), 2)
                self.assertTrue(first.closed)
                self.assertFalse(survivor.closed)
                self.assertTrue(self.adapter.coordinator.participant(survivor))
                yield survivor
            finally:
                first.writer.resume.set()
                await asyncio.wait_for(task, 2)
        self.assertIsInstance(self.adapter.failure, ValueError)

    async def leave_selection(self, peer):
        # PART remains available after the event driver has stopped. Neither
        # unpaired selection has a round whose queued closures could interfere.
        await peer.part(self.binding.room_channel)
        self.assertFalse(self.adapter.coordinator.participant(peer))
        self.assertFalse(self.adapter.outbox)

    def assert_live(self, peer):
        self.assertFalse(peer.closed)
        self.assertFalse(peer.writer.closed)
        self.assertIs(self.server.nicks.get(peer.nick), peer)

    async def test_new_ordinary_admission_survives_delayed_failure_cleanup(self):
        async with self.paused_failure() as peer:
            await self.leave_selection(peer)
            self.assertTrue(
                await services.dispatch(
                    peer, service_nick(TABLE_RAW), packet(65, 1, body="@GameEN=")
                )
            )
            table = self.server.default_table
            self.assertIs(self.server.table_for_peer(peer), table)
            match = table.match
            reservation = table.match_reservations[peer]
            before = match.snapshot()
        self.assert_live(peer)
        self.assertIs(self.server.table_for_peer(peer), table)
        self.assertIs(table.match, match)
        self.assertEqual(table.match.snapshot(), before)
        self.assertIs(table.match_reservations[peer], reservation)
        self.assertTrue(self.server.economy.is_busy(peer.nick.decode("ascii")))

    async def test_new_shop_session_survives_delayed_failure_cleanup(self):
        async with self.paused_failure() as peer:
            await self.leave_selection(peer)
            key = peer.nick.decode("ascii")
            view = self.server.economy.account_view(key)
            request = named(
                0xB0,
                0,
                0,
                f"@ShReq=/NN={view.nn:016X}/HID=0/Dm=0/Vol=0/CN={view.name.hex()}",
            )
            self.assertTrue(await services.dispatch(peer, service_nick(SHOP_RAW), request))
            self.assertTrue(self.server.economy.is_busy(key))
            before = self.server.economy.snapshot()
        self.assert_live(peer)
        self.assertTrue(self.server.economy.is_busy(key))
        self.assertEqual(self.server.economy.snapshot(), before)

    async def test_current_event_participant_still_closes(self):
        async with self.paused_failure() as peer:
            pass
        self.assertTrue(peer.closed)
        self.assertNotIn(peer.nick, self.server.nicks)
        self.assertFalse(self.adapter.coordinator.participant(peer))
        self.assertFalse(self.server.economy.is_busy(peer.nick.decode("ascii")))

    async def test_released_idle_participant_still_closes(self):
        async with self.paused_failure() as peer:
            await self.leave_selection(peer)
        self.assertTrue(peer.closed)
        self.assertNotIn(peer.nick, self.server.nicks)
        self.assertFalse(self.server.economy.is_busy(peer.nick.decode("ascii")))


if __name__ == "__main__":
    unittest.main()
