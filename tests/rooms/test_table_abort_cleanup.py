"""Overlapping abort/departure cleanup with real room state and fake transport."""

import asyncio
import struct
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from tests.events.test_event_rooms import presence
from tests.rooms.test_match_watchdog_integration import SETTINGS
from tests.network.test_session_recovery import Writer
from tmserver.economy.service import named
from tmserver.game.match import Card, packet
from tmserver.rooms.watchdog import MatchWatchdog
from tmserver.rooms.config import OrdinaryRoomConfig, OrdinaryTableConfig
from tmserver.network.server import Server, Session
from tmserver.protocol.commands import parse


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


class BlockingWriter(Writer):
    def __init__(self):
        super().__init__()
        self.block = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def drain(self):
        if self.block:
            self.entered.set()
            await self.release.wait()
        await super().drain()


class WorkerFailureReentryTests(unittest.IsolatedAsyncioTestCase):
    async def check_cleanup(self, worker, destination):
        room = OrdinaryRoomConfig(b"Other Room", 0x101, 0x201, b"#ROOM2", b"#TABLE2")
        server = Server(
            account_count=3, match_mode="versus", allow_departures=True, ordinary_rooms=(room,)
        )
        peers, blocked, task, entering = [], BlockingWriter(), None, None
        table = server.default_table
        try:
            for index, nick in enumerate(server.accounts):
                peer = Session(server, None, blocked if index == 2 else Writer(), index + 1)
                peer.nick = nick
                key = server.ensure_account(nick)
                server.trade.bind(peer, key)
                server.nicks[nick] = peer
                server.sessions.add(peer)
                peers.append(peer)
                view = server.economy.account_view(key)
                await table.publish_room(server.room.handle(
                    peer, presence(server.room.room_id, view.nn, view.player_id, view.name),
                    authenticated_identity=(view.nn, view.player_id),
                ))
            for peer in peers[:2]:
                await table.dispatch_table(peer, packet(65, 1, body="@GameEN="))
            await table.dispatch_table(peers[0], packet(20, body=SETTINGS))
            for peer in peers[:2]:
                await table.dispatch_table(peer, packet(1, body="@Req="))
                await table.dispatch_table(peer, packet(65, 8, body="@GameOK="))
            for seat, peer in enumerate(peers[:2]):
                cards = server.economy.inventory(peer.nick.decode())[:5]
                body = "@CardSelect=/C=5" + "".join(
                    f"@N{i}=/D=" + "|".join(map(str, card.full()))
                    for i, card in enumerate(cards)
                )
                await table.dispatch_table(peer, packet(67, 7, seat, body))
            old, before = table.match, server.snapshot()
            self.assertEqual(old.phase, "turn")
            survivor, key = peers[1], peers[1].nick.decode()
            blocked.block = True
            fault = (
                nullcontext()
                if worker == "watchdog"
                else patch.object(
                    old,
                    "advance" if worker == "continuation" else "depart",
                    side_effect=ValueError("injected worker failure"),
                )
            )
            with fault:
                if worker == "continuation":
                    # Replace only the continuation's outcome with a fault.
                    old.phase, old._pending_battle = "battle", 0
                    task = asyncio.create_task(table.run_match_work(old))
                elif worker == "watchdog":
                    now = [0.0]
                    table.watchdog = MatchWatchdog(clock=lambda: now[0], ack_timeout=1)
                    table.watchdog.observe(old, old)
                    now[0] = 1
                    task = asyncio.create_task(table.check_match_timeout())
                else:
                    self.assertTrue(table.departures.request(peers[0]))
                    task = table.departures.task
                await asyncio.wait_for(blocked.entered.wait(), 2)
                self.assertTrue(peers[0].closed)
                self.assertFalse(survivor.closed)
                self.assertIsNot(table.match, old)
                self.assertFalse(server.economy.is_reserved(key))
                self.assertEqual(server.snapshot(), before)

                owner, token, current = None, None, None
                if destination in ("same-table", "other-table"):
                    owner = table if destination == "same-table" else server.ordinary_tables[1]
                    # Admission changes state before waiting on room delivery.
                    entering = asyncio.create_task(owner.dispatch_table(
                        survivor, packet(65, 1, body="@GameEN=")
                    ))
                    await asyncio.sleep(0)
                    self.assertIs(server.table_for_peer(survivor), owner)
                    token, current = owner.match_reservations[survivor], owner.match
                elif destination == "shop":
                    view = server.economy.account_view(key)
                    request = named(
                        0xB0, 0, 0,
                        f"@ShReq=/NN={view.nn:016X}/HID=0/Dm=0/Vol=0/CN={view.name.hex()}",
                    )
                    replies = server.commerce.shop(key, request)
                    self.assertTrue(server.economy.is_busy(key))
                after_entry = server.snapshot()
                blocked.release.set()
                result = await asyncio.wait_for(asyncio.shield(task), 2)
                if worker == "watchdog":
                    self.assertEqual(result.reason, "turn_ack")
                if owner is not None:
                    await asyncio.wait_for(entering, 2)
            self.assertEqual(survivor.closed, destination == "none")
            self.assertEqual(server.snapshot(), after_entry)
            self.assertFalse(peers[2].closed)
            self.assertFalse(server.economy.is_reserved(peers[0].nick.decode()))
            if owner is not None:
                self.assertIs(server.table_for_peer(survivor), owner)
                self.assertIs(owner.match, current)
                self.assertIs(owner.match_reservations[survivor], token)
            elif destination == "shop":
                self.assertEqual(server.commerce.shop(key, request), replies)
            else:
                self.assertFalse(server.economy.is_busy(key))
        finally:
            blocked.release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            if entering is not None:
                await asyncio.gather(entering, return_exceptions=True)
            await server.stop()

    async def test_continuation_failure_preserves_new_service_and_closes_unclaimed_peers(self):
        for destination in ("none", "same-table", "other-table", "shop"):
            with self.subTest(destination=destination):
                await self.check_cleanup("continuation", destination)

    async def test_departure_failure_preserves_new_service_and_closes_unclaimed_peers(self):
        for destination in ("none", "same-table", "other-table", "shop"):
            with self.subTest(destination=destination):
                await self.check_cleanup("departure", destination)

    async def test_timeout_preserves_new_service_and_closes_unclaimed_peers(self):
        for destination in ("none", "same-table", "other-table", "shop"):
            with self.subTest(destination=destination):
                await self.check_cleanup("watchdog", destination)


if __name__ == "__main__":
    unittest.main()
