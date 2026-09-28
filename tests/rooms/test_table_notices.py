"""Actual control notices do not create spectator seats or reset deadlines."""

import sys
import unittest
from pathlib import Path

from tests.events import test_event_matchmaking as event
from tests.rooms import test_table_kick as lobby
from tests.rooms.test_table_kick import make_table
from tmserver.game.match import packet


class TableNoticeUnits(unittest.TestCase):
    def test_viewer_tracks_advisory_presence_without_changing_match(self):
        m = make_table()
        before = m.snapshot()
        for mode, expected in (("IN", {1}), ("IN", {1}), ("OUT", set()), ("OUT", set())):
            self.assertEqual(m.handle(1, packet(65, 17, body=f"@Viewer=/{mode}=0")), [])
            self.assertEqual(m.viewer_peers, expected)
            self.assertEqual(m.snapshot(), before)
        for body in ("@Viewer=", "@Viewer=/IN=1", "@Viewer=/IN=0/OUT=0", "@Other=/IN=0"):
            with self.assertRaises(ValueError):
                m.handle(1, packet(65, 17, body=body))
        m.handle(1, packet(65, 17, body="@Viewer=/IN=0"))
        m.handle(0, packet(65, 5, body="@GameQT=/ID=0000000000000002"))
        self.assertFalse(m.viewer_peers)

    def test_late_channel_exit_cannot_abort_active_match(self):
        m = make_table()
        m.begin()
        before = m.snapshot()
        self.assertEqual(m.handle(0, packet(32, body="@ExitCh=")), [])
        self.assertEqual(m.snapshot(), before)
        for p in (
            packet(32, 1, body="@ExitCh="),
            packet(32, body="@ExitCh=/NN=2"),
            packet(32, body="@GameExit="),
        ):
            with self.assertRaises(ValueError):
                m.handle(0, p)


class TableNoticeIntegration(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = lobby.KickIntegration.asyncSetUp
    asyncTearDown = lobby.KickIntegration.asyncTearDown
    flush = lobby.KickIntegration.flush
    send = lobby.KickIntegration.send
    admit = lobby.KickIntegration.admit

    async def test_failed_start_channel_cancel_releases_without_closing_other_accounts(self):
        from tests.game.test_match_service import NATIVE_SETTINGS

        for i in range(3):
            await self.admit(i)
        await self.send(0, packet(20, body=NATIVE_SETTINGS))
        await self.send(0, packet(65, 10, body="@GameReady="))
        await self.send(0, packet(1, body="@Req="))
        m = self.server.default_table.match
        before = self.server.economy.snapshot()
        replies = await self.send(0, packet(32, body="@ExitCh="))
        fail = packet(65, 16, body="@GameWa=/Error=-1")
        self.assertEqual(replies, [[], [fail], [fail], []])
        self.assertEqual(m.phase, "finished")
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.server.economy.snapshot(), before)
        self.assertEqual(await self.send(0, packet(32, body="@ExitCh=")), [[], [], [], []])
        self.assertTrue(all(not p.closed for p in self.server.sessions))

    async def test_viewer_notices_and_nonmember_exit_have_no_success_reply_or_seat(self):
        await self.admit(0)
        m = self.server.default_table.match
        peer = self.server.nicks[self.clients[0].nick]
        before = m.snapshot()
        tokens = dict(self.server.default_table.match_reservations)
        self.assertEqual(await self.send(0, packet(65, 17, body="@Viewer=/IN=0")), [[], [], [], []])
        self.assertEqual(m.viewer_peers, {peer})
        self.assertEqual(await self.send(3, packet(65, 17, body="@Viewer=/IN=0")), [[], [], [], []])
        self.assertEqual(await self.send(3, packet(32, body="@ExitCh=")), [[], [], [], []])
        self.assertEqual(m.viewer_peers, {peer})
        self.assertEqual(m.snapshot(), before)
        self.assertEqual(self.server.default_table.match_reservations, tokens)
        self.assertEqual(
            await self.send(0, packet(65, 17, body="@Viewer=/OUT=0")), [[], [], [], []]
        )
        self.assertFalse(m.viewer_peers)


class EventTableNotices(unittest.TestCase):
    def setUp(self):
        self.case = event.CoordinatorTests()
        event.CoordinatorTests.setUpClass()
        self.case.setUp()

    def test_viewer_keeps_event_turn_barriers_and_reservations(self):
        c = self.case
        c.start()
        r = c.c._rounds[c.table.table_id]
        before = c.c.snapshot("festival")
        saved = c.economy.snapshot()
        for mode in ("IN", "IN", "OUT"):
            self.assertEqual(c.send("1", packet(65, 17, body=f"@Viewer=/{mode}=0")), [])
            self.assertEqual(r.viewers, {"1"} if mode == "IN" else set())
            self.assertEqual(c.c.snapshot("festival"), before)
        self.assertEqual(c.send("1", packet(32, body="@ExitCh=")), [])
        self.assertEqual(c.economy.snapshot(), saved)
        self.assertFalse(c.c.take_closures())

    def test_event_admission_channel_exit_uses_existing_group_cancellation(self):
        c = self.case
        for key in ("1", "2"):
            c.admit(key)
        c.c.poll()
        for key in ("1", "2"):
            c.consent(key)
        c.send("1", packet(65, 1, body=c.identity("1", "GameEN")))
        c.send("1", packet(65, 17, body="@Viewer=/IN=0"))
        c.send("1", packet(32, body="@ExitCh="))
        self.assertIsNone(c.c.snapshot("festival"))
        self.assertEqual(set(c.c.take_closures()), {"1", "2"})
        for key in ("1", "2"):
            self.assertFalse(c.economy.is_reserved(key))


if __name__ == "__main__":
    unittest.main()
