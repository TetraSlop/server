"""Visitor dispatch does not consume seats or bypass another table's ownership."""

import unittest

from tests.rooms import test_table_roster as roster
from tmserver.game.packets import decode, packet
from tmserver.protocol.identities import service_nick


class TableDispatchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = roster.RosterIntegration.asyncSetUp
    send = roster.RosterIntegration.send
    drain = roster.RosterIntegration.drain
    table = roster.RosterIntegration.table
    enter = roster.RosterIntegration.enter
    admit = roster.RosterIntegration.admit

    async def test_roster_query_can_inspect_another_table_without_transferring_ownership(self):
        home = self.server.default_table
        other = self.server.ordinary_tables[1]
        await self.admit(0)
        await self.enter(1, other)
        await self.admit(1, other)
        before = (
            home.match.snapshot(),
            other.match.snapshot(),
            dict(home.match_reservations),
            dict(other.match_reservations),
            self.server.economy.snapshot(),
        )
        identity = self.server.economy.account_view(self.clients[0].nick.decode()).nn
        replies = await self.table(0, packet(0x41, 3, body=f"@GameM=/ID={identity:016X}"), other)
        self.assertEqual([len(rows) for rows in replies], [1, 0, 0, 0])
        source, reply = replies[0][0]
        self.assertEqual(source, service_nick(other.table_raw))
        message = decode(reply)
        self.assertEqual((message.family, message.subtype), (0x41, 4))
        self.assertEqual(message.sections["GameML"]["ID"], f"{identity:016X}")
        self.assertEqual(message.sections["GameML"]["Num"], "1")
        self.assertEqual(
            message.sections["GameML"]["MLID"], f"{other.match.players[0].nickname_id:016X}"
        )
        for request in (
            packet(0x41, 3, body="@GameM=/ID=0000000000000000"),
            packet(0x12, body="@Rule="),
            packet(0x41, 1, body="@GameEN="),
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                await other.dispatch_table(self.peers[0], request)
        self.assertIs(self.server.table_for_peer(self.peers[0]), home)
        self.assertEqual(
            (
                home.match.snapshot(),
                other.match.snapshot(),
                home.match_reservations,
                other.match_reservations,
                self.server.economy.snapshot(),
            ),
            before,
        )

    async def test_unhandled_visitor_and_handled_notice_do_not_fall_through_to_admission(self):
        table = self.server.default_table
        before = self.server.economy.snapshot()
        for request in (
            b"another service",
            packet(0xFE),
            packet(0x41, 0xFE),
            packet(0x43, 0xFE),
        ):
            with self.subTest(request=request):
                self.assertIs(await table.dispatch_table(self.peers[0], request), False)
        for request in (
            packet(0x41, 0x11, body="@Viewer=/IN=0"),
            packet(0x20, body="@ExitCh="),
        ):
            with self.subTest(request=request):
                self.assertIs(await table.dispatch_table(self.peers[0], request), True)
        self.assertEqual(await self.drain(), [[], [], [], []])
        self.assertFalse(table.match.players)
        self.assertFalse(table.match_reservations)
        self.assertFalse(table.match.viewer_peers)
        self.assertEqual(self.server.economy.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
