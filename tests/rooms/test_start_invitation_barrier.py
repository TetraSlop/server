"""A table newcomer must finish GameEA cleanup before its start invite."""

import unittest

from tests.rooms import test_table_roster as roster_tests
from tests.game.test_flexible_seats import seated
from tests.game.test_match_service import NATIVE_SETTINGS
from tests.rooms.harness import serve
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, packet
from tmserver.rooms.roster import TableRoster


class StartInvitationBarrierUnits(unittest.TestCase):

    def test_direct_request_or_finished_table_drops_pending_invite(self):
        for done in ("requested", "finished"):
            m = seated(2)
            r = TableRoster()
            r.ready.add(0)
            r.publish(m, [])
            r.publish(m, m.handle(0, packet(65, 10, body="@GameReady=")))
            if done == "requested":
                m.handle(1, packet(1, body="@Req="))
            else:
                m.phase = "finished"
            r.publish(m, [])
            r.ready.add(1)
            self.assertFalse(any(decode(d.payload).subtype == 7 for d in r.publish(m, [])))

    def test_fixed_three_seat_table_has_the_same_cleanup_race(self):
        m = MatchService(mode="versus", player_count=3)
        for i in range(3):
            m.register(i, character_id=i + 1, nickname_id=i + 1)
        m.handle(0, packet(20, body=NATIVE_SETTINGS))
        r = TableRoster()
        r.ready.update((0, 1))
        r.publish(m, [])
        offered = r.publish(m, m.handle(0, packet(65, 10, body="@GameReady=")))
        self.assertEqual([d.peer for d in offered], [1])
        r.ready.add(2)
        self.assertEqual(
            [(d.peer, decode(d.payload).subtype) for d in r.publish(m, [])], [(2, 4), (2, 7)]
        )


class StartInvitationBarrierNetwork(unittest.IsolatedAsyncioTestCase):
    flexible_seats = True
    send = roster_tests.RosterIntegration.send
    drain = roster_tests.RosterIntegration.drain
    table = roster_tests.RosterIntegration.table
    enter = roster_tests.RosterIntegration.enter
    admit = roster_tests.RosterIntegration.admit

    async def asyncSetUp(self):
        await serve(
            self,
            match_mode="versus",
            match_player_count=3,
            account_count=4,
            flexible_seats=self.flexible_seats,
        )

    async def test_third_admission_race_defers_only_newcomer_invite_then_starts(self):
        await self.enter(0)
        await self.admit(0)
        await self.table(0, packet(20, body=NATIVE_SETTINGS))
        await self.enter(1)
        await self.admit(1)
        await self.enter(1, token=b"PD", ready=True)
        await self.enter(2)
        await self.admit(2)
        offered = await self.table(0, packet(65, 10, body="@GameReady="))
        self.assertTrue(any(p.startswith(b"GTM0G410007") for s, p in offered[1]))
        self.assertFalse(any(p.startswith(b"GTM0G410007") for s, p in offered[2]))
        for side in (0, 1):
            await self.table(side, packet(1, body="@Req="))
            await self.table(side, packet(65, 8, body="@GameOK="))
        self.assertEqual(self.server.default_table.match.phase, "lobby")
        late = await self.enter(2, token=b"PD", ready=True)
        self.assertEqual(
            [(decode(p).family, decode(p).subtype) for s, p in late[2] if p.startswith(b"GTM0G")],
            [(65, 4), (65, 7)],
        )
        duplicate = await self.enter(2, token=b"PD", ready=True)
        self.assertFalse(any(p.startswith(b"GTM0G410007") for rows in duplicate for s, p in rows))
        await self.table(2, packet(1, body="@Req="))
        await self.table(2, packet(65, 8, body="@GameOK="))
        self.assertEqual(
            (self.server.default_table.match.phase, self.server.default_table.match.player_count),
            ("selection", 3),
        )


class FixedSeatStartInvitationNetwork(StartInvitationBarrierNetwork):
    flexible_seats = False


if __name__ == "__main__":
    unittest.main()
