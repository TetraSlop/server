"""Deadline recovery through Server/Session, with only clock/transport replaced."""

import asyncio
import unittest

from tests.test_session_recovery import Writer
from tmserver.match_service import Card, packet
from tmserver.match_watchdog import MatchWatchdog
from tmserver.server import ACCOUNTS, Server, Session

SETTINGS = "@Tet=/bm=0/du=0/st=0/cb=0/ca=0/gs=0/tl=0@Tab=/in=0/lu=100/ll=0/au=300/al=100/co=0/pa=0"


class WatchdogIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 0.0
        self.watch = MatchWatchdog(clock=lambda: self.now)
        self.server = Server(watchdog=self.watch)
        self.peer = self.new_peer()

    async def asyncTearDown(self):
        await self.server.stop()

    def new_peer(self):
        peer = Session(self.server, None, Writer(), len(self.server.sessions) + 1)
        peer.nick = next(iter(ACCOUNTS))
        self.server.ensure_account(peer.nick)
        self.server.trade.bind(peer, peer.nick.decode("ascii"))
        self.server.nicks[peer.nick] = peer
        self.server.sessions.add(peer)
        return peer

    async def selection(self):
        await self.server.default_table.dispatch_table(self.peer, packet(0x41, 1, body="@GameEN="))
        await self.server.default_table.dispatch_table(self.peer, packet(0x14, body=SETTINGS))
        await self.server.default_table.dispatch_table(self.peer, packet(1, body="@Req="))
        await self.server.default_table.dispatch_table(self.peer, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(self.server.default_table.match.phase, "selection")

    async def turn(self):
        await self.selection()
        source = self.server.economy.inventory(self.peer.nick.decode("ascii"))[:5]
        cards = [Card(*(getattr(c, name) for name in Card.__dataclass_fields__)) for c in source]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D={card.full()}" for i, card in enumerate(cards)
        )
        await self.server.default_table.dispatch_table(self.peer, packet(0x43, 7, 0, body))
        self.assertEqual(self.server.default_table.match.phase, "turn")

    def assert_released(self, old):
        self.assertTrue(self.peer.closed)
        self.assertTrue(self.peer.writer.closed)
        self.assertNotIn(self.peer.nick, self.server.nicks)
        self.assertNotIn(self.peer, self.server.default_table.match_reservations)
        self.assertFalse(self.server.economy.is_reserved(self.peer.nick.decode("ascii")))
        self.assertIsNot(self.server.default_table.match, old)
        self.assertEqual(self.server.default_table.match.players, [])
        self.assertEqual(self.server.default_table.match.phase, "lobby")
        self.assertEqual(
            (
                self.server.room.table_state,
                self.server.room.table_count,
                self.server.room.table_owner,
            ),
            (0, 0, 0),
        )
        self.assertFalse(old.pending_work)

    async def test_selection_expiry_closes_and_releases_then_same_account_can_reenter(self):
        await self.selection()
        old = self.server.default_table.match
        self.now = 74.999
        self.assertIsNone(await self.server.default_table.check_match_timeout())
        self.now = 75
        failure = await self.server.default_table.check_match_timeout()
        self.assertEqual(failure.reason, "card_selection")
        self.assert_released(old)
        self.peer = self.new_peer()
        await self.server.default_table.dispatch_table(self.peer, packet(0x41, 1, body="@GameEN="))
        self.assertIs(self.server.nicks[self.peer.nick], self.peer)
        self.assertTrue(any(p.peer is self.peer for p in self.server.default_table.match.players))
        self.assertIsNone(await self.server.default_table.check_match_timeout())

    async def test_ack_expiry_closes_and_releases(self):
        await self.turn()
        old = self.server.default_table.match
        self.now = 120
        failure = await self.server.default_table.check_match_timeout()
        self.assertEqual((failure.reason, failure.sequence), ("turn_ack", 0))
        self.assert_released(old)

    async def test_expiry_before_queued_final_departure_clears_room_state(self):
        self.server.default_table.departures.enabled = True
        self.server.allow_departures = True
        self.server.default_table.reset_match()
        await self.turn()
        old = self.server.default_table.match
        self.now = 120
        async with self.server.default_table.match_lock:
            expiry = asyncio.create_task(self.server.default_table.check_match_timeout())
            await asyncio.sleep(0)
            await self.peer.close()
            departure = self.server.default_table.departures.task
            self.assertIsNotNone(departure)
            self.assertIs(self.server.default_table.match, old)
        self.assertEqual((await expiry).reason, "turn_ack")
        await departure
        self.assert_released(old)

    async def test_start_handshake_expiry_releases_without_debit_or_profile_change(self):
        await self.server.default_table.dispatch_table(self.peer, packet(65, 1, body="@GameEN="))
        await self.server.default_table.dispatch_table(self.peer, packet(20, body=SETTINGS))
        old = self.server.default_table.match
        before = self.server.economy.snapshot()
        self.now = 1000
        self.assertIsNone(await self.server.default_table.check_match_timeout())
        await self.server.default_table.dispatch_table(self.peer, packet(1, body="@Req="))
        self.now = 1119.999
        self.assertIsNone(await self.server.default_table.check_match_timeout())
        await self.server.default_table.dispatch_table(self.peer, packet(1, body="@Req="))
        self.now = 1120
        failure = await self.server.default_table.check_match_timeout()
        self.assertEqual((failure.reason, failure.missing_peers), ("start_handshake", (self.peer,)))
        self.assert_released(old)
        self.assertEqual(self.server.economy.snapshot(), before)

    async def test_decision_expiry_closes_and_releases(self):
        await self.turn()
        old = self.server.default_table.match
        self.now = 100
        await self.server.default_table.dispatch_table(
            self.peer, packet(0x43, 9, 0, "@TurnData=/Ans=0")
        )
        self.now = 174.999
        self.assertIsNone(await self.server.default_table.check_match_timeout())
        self.now = 175
        failure = await self.server.default_table.check_match_timeout()
        self.assertEqual(failure.reason, "placement")
        self.assert_released(old)

    async def test_live_ping_and_duplicate_ack_do_not_extend_decision(self):
        await self.turn()
        await self.server.default_table.dispatch_table(
            self.peer, packet(0x43, 9, 0, "@TurnData=/Ans=0")
        )
        for now in (30, 60, 74):
            self.now = now
            await self.peer.command(b"PING", [b"alive"])
            await self.server.default_table.dispatch_table(
                self.peer, packet(0x43, 9, 0, "@TurnData=/Ans=0")
            )
            self.assertIsNone(await self.server.default_table.check_match_timeout())
        self.assertTrue(any(b"PONG alive" in data for _, data in self.peer.writer.writes))
        self.now = 75
        self.assertEqual(
            (await self.server.default_table.check_match_timeout()).reason, "placement"
        )
        self.assertTrue(self.peer.closed)

    async def test_cancelled_old_continuation_cannot_mutate_new_table(self):
        await self.turn()
        old = self.server.default_table.match
        # Seed only the scheduler-facing pending marker: cancellation must run
        # before any old combat reads this intentionally incomplete scene.
        old.phase, old._pending_battle = "battle", 3
        self.server.default_table.schedule_match_work(old)
        old_task = self.server.default_table.match_task
        self.assertIsNotNone(old_task)
        self.server.default_table.reset_match()
        await self.peer.close()
        self.peer = self.new_peer()
        await self.server.default_table.dispatch_table(self.peer, packet(0x41, 1, body="@GameEN="))
        current = self.server.default_table.match
        before = current.snapshot()
        room_before = self.server.room.snapshot()
        await asyncio.gather(old_task, return_exceptions=True)
        # Even an already-queued old worker entry sees the epoch mismatch.
        await self.server.default_table.run_match_work(old)
        self.assertIs(self.server.default_table.match, current)
        self.assertEqual(current.snapshot(), before)
        self.assertEqual(self.server.room.snapshot(), room_before)
        self.assertFalse(old.pending_work)
        self.now = 100000
        self.assertIsNone(await self.server.default_table.check_match_timeout())
        self.assertFalse(self.peer.closed)


if __name__ == "__main__":
    unittest.main()
