"""Overlapping abort/departure cleanup with real room state and fake transport."""

import asyncio
import struct
import unittest
from unittest.mock import patch

from tests.test_event_rooms import presence
from tests.test_match_watchdog_integration import SETTINGS
from tests.test_session_recovery import Writer
from tmserver.match_service import Card, packet
from tmserver.match_watchdog import MatchWatchdog
from tmserver.ordinary_rooms import OrdinaryRoomConfig, OrdinaryTableConfig
from tmserver.server import Server, Session
from tmserver.tm_protocol import parse


class TableAbortCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        room = OrdinaryRoomConfig(
            b"Shared Room", 0x101, 0x201, b"#ROOM2", b"#TABLE2", "computer",
            tables=(OrdinaryTableConfig(0x202, b"#TABLE3", "computer"),),
        )
        self.server = Server(account_count=3, allow_departures=True, ordinary_rooms=(room,))
        self.tables = self.server.ordinary_tables[1:]
        self.room = self.tables[0].room
        self.peers = []
        for index, nick in enumerate(self.server.accounts):
            peer = Session(self.server, None, Writer(), index + 1)
            peer.nick = nick
            self.server.ensure_account(nick)
            self.server.trade.bind(peer, nick.decode("ascii"))
            self.server.nicks[nick] = peer
            self.server.sessions.add(peer)
            self.peers.append(peer)
            view = self.server.economy.account_view(nick.decode("ascii"))
            await self.tables[0].publish_room(self.room.handle(
                peer, presence(self.room.room_id, view.nn, view.player_id, view.name),
                authenticated_identity=(view.nn, view.player_id),
            ))
        for table, peer in zip(self.tables, self.peers):
            await self.start_match(table, peer)
        self.assertEqual(set(self.room.npcs), {512, 513})

    async def asyncTearDown(self):
        await self.server.stop()

    async def start_match(self, table, peer):
        for payload in (
            packet(65, 1, body="@GameEN="), packet(20, body=SETTINGS),
            packet(1, body="@Req="), packet(65, 8, body="@GameOK="),
        ):
            await table.dispatch_table(peer, payload)
        source = self.server.economy.inventory(peer.nick.decode("ascii"))[:5]
        cards = [Card(*(getattr(c, name) for name in Card.__dataclass_fields__)) for c in source]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D={card.full()}" for i, card in enumerate(cards)
        )
        await table.dispatch_table(peer, packet(67, 7, 0, body))
        self.assertEqual(table.match.phase, "turn")

    def assert_empty_table(self, table, old):
        self.assertIsNot(table.match, old)
        self.assertEqual((table.match.phase, table.match.players), ("lobby", []))
        self.assertFalse(table.match_reservations)
        self.assertEqual(
            (table.room.table_state, table.room.table_count, table.room.table_owner), (0, 0, 0)
        )
        self.assertFalse(old.pending_work)

    async def test_expiry_clears_observer_ptl_and_npc_without_touching_sibling(self):
        sibling, table = self.tables
        old = table.match
        before = sibling.match.snapshot(), dict(sibling.match_reservations)
        durable = self.server.economy.snapshot()
        now = [0.0]
        table.watchdog = MatchWatchdog(clock=lambda: now[0], ack_timeout=1)
        table.watchdog.observe(old, old)
        now[0] = 1
        observer = self.peers[2]
        observer.writer.writes.clear()
        sequence = self.room.sequence
        async with table.match_lock:
            expiry = asyncio.create_task(table.check_match_timeout())
            await asyncio.sleep(0)
            await self.peers[1].close()
            departure = table.departures.task
            self.assertIsNotNone(departure)
        self.assertEqual((await expiry).reason, "turn_ack")
        await departure
        self.assert_empty_table(table, old)
        self.assertEqual((sibling.match.snapshot(), sibling.match_reservations), before)
        self.assertFalse(self.peers[0].closed)
        self.assertFalse(observer.closed)
        self.assertEqual(self.server.economy.snapshot(), durable)
        self.assertEqual(set(self.room.npcs), {512})
        self.assertFalse(self.server.economy.is_reserved(self.peers[1].nick.decode("ascii")))
        self.assertTrue(self.server.economy.is_reserved(self.peers[0].nick.decode("ascii")))

        updates = [parse(data[:-6].split(b" :GTM0L", 1)[1])
                   for _, data in observer.writer.writes if b" :GTM0L" in data]
        self.assertEqual([commands[-1].token for commands in updates], [b"PC", b"TD", b"PC"])
        self.assertEqual([int(commands[2].payload) for commands in updates],
                         list(range(sequence + 1, sequence + 4)))
        self.assertEqual(updates[1][-1].payload.split(b"\x06")[0], table.table_channel)
        self.assertEqual(updates[1][-1].payload.split(b"\x06")[3], b"0")
        self.assertEqual(updates[2][-1].payload, b"0x0000000000000201")
        snapshot = self.room.snapshot()
        self.assertEqual(struct.unpack_from("<I", snapshot, 0x44)[0], 3)
        self.assertEqual(snapshot[0x5850 + 0x14], 2)
        self.assertEqual(snapshot[0x5850 + 0x68 + 0x14], 0)

        # A late cleanup from the expired generation cannot erase a new table.
        replacement = Session(self.server, None, Writer(), 4)
        replacement.nick = self.peers[1].nick
        self.server.nicks[replacement.nick] = replacement
        self.server.sessions.add(replacement)
        self.server.trade.bind(replacement, replacement.nick.decode("ascii"))
        await self.start_match(table, replacement)
        current = table.match
        before = current.snapshot(), dict(table.match_reservations), self.room.snapshot()
        async with table.match_lock:
            await table.publish_room(table.reset_table_updates(old))
        self.assertIs(table.match, current)
        self.assertEqual((current.snapshot(), table.match_reservations, self.room.snapshot()), before)

    async def test_processing_error_after_final_disconnect_releases_aborted_generation(self):
        sibling, table = self.tables
        old = table.match
        before = sibling.match.snapshot(), dict(sibling.match_reservations)
        # Fault injection starts one pending continuation; no battle outcome is fabricated.
        old.phase, old._pending_battle = "battle", 0
        with patch.object(old, "advance", side_effect=ValueError("failed continuation")):
            async with table.match_lock:
                work = asyncio.create_task(table.run_match_work(old))
                # The worker yields once before acquiring the held lock.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await self.peers[1].close()
                departure = table.departures.task
                self.assertIsNotNone(departure)
            await work
            await departure
        self.assert_empty_table(table, old)
        self.assertEqual((sibling.match.snapshot(), sibling.match_reservations), before)
        self.assertEqual(set(self.room.npcs), {512})
        self.assertFalse(self.server.economy.is_reserved(self.peers[1].nick.decode("ascii")))


if __name__ == "__main__":
    unittest.main()
