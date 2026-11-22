"""Borrowed backends reach real table factories and failed rounds release accounts."""

import asyncio
import unittest
from dataclasses import replace

from tests.events import test_event_multitable as events
from tests.game.test_rules_backend import recording_backend
from tests.support.irc import Client
from tests.rooms import test_ordinary_rooms as ordinary
from tmserver.game.models import BoardCard
from tmserver.game.original.runtime import default_rules
from tmserver.game.packets import packet
from tmserver.network.server import Server
from tmserver.protocol.identities import GAME_KEY, game_id, service_nick


def failing_battle(match, backend, *, deferred):
    """Stage reciprocal cards so either driver reaches the actual backend boundary."""
    match.phase = "battle"
    match.active = match.source = 0
    match.board[0] = BoardCard(match.players[0].hand[0], 0, 0, 0)
    match.board[1] = BoardCard(match.players[1].hand[0], 1, 1, 0)
    match.targets = [1]
    match._pending_battle = 1 if deferred else None
    match.acks = set(match._humans())
    calls = []

    def calculate(attacker, defender, draw, **effects):
        if draw is match.draw:
            calls.append(draw())
            raise RuntimeError("injected failure after native RNG callback")
        return default_rules().calculate_battle(attacker, defender, draw, **effects)

    backend.calculate_battle.side_effect = calculate
    return calls


async def disconnected(test, client):
    for _ in range(100):
        line = await client.read(timeout=3)
        if not line:
            return
        test.assertNotIn(b"@ResultData=", line)
        test.assertNotIn(b"@EventResult=", line)
    test.fail("failed round did not disconnect its participant")


class OrdinaryBackendIntegration(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ordinary.OrdinaryRoomsIntegration.asyncSetUp
    asyncTearDown = ordinary.OrdinaryRoomsIntegration.asyncTearDown
    start = ordinary.OrdinaryRoomsIntegration.start
    group = ordinary.OrdinaryRoomsIntegration.group
    flush = ordinary.OrdinaryRoomsIntegration.flush
    send = ordinary.OrdinaryRoomsIntegration.send
    admit = ordinary.OrdinaryRoomsIntegration.admit
    advance = ordinary.OrdinaryRoomsIntegration.advance
    finish = ordinary.OrdinaryRoomsIntegration.finish

    async def failed(self, *, deferred):
        backend = recording_backend()
        await self.start(rules_backend=backend, allow_wagers=True, allow_departures=True)
        original = [self.server.economy.account_view(key).balance for key in self.keys]
        await self.admit(0, stake=100)
        await self.admit(1, stake=100)
        for table in self.tables:
            self.assertIs(table.match.rules_backend, backend)
        affected, healthy = self.tables
        match = affected.match
        before = healthy.match.snapshot()
        healthy_reservations = dict(healthy.match_reservations)
        draws = failing_battle(match, backend, deferred=deferred)
        if deferred:
            affected.schedule_match_work(match)
            await asyncio.wait_for(asyncio.shield(affected.match_task), 3)
        else:
            await self.clients[0].send(
                b"NOTICE "
                + service_nick(affected.table_raw)
                + b" :"
                + packet(0x43, 11, match.sequence, "@BattleSelect=/B=1")
            )
        for side in (0, 1):
            await disconnected(self, self.clients[side])
            self.assertFalse(self.server.economy.is_reserved(self.keys[side]))
            self.assertEqual(
                self.server.economy.account_view(self.keys[side]).balance, original[side]
            )
        self.assertEqual(len(draws), 1)
        self.assertEqual(match.phase, "finished")
        self.assertFalse(affected.match_reservations)
        self.assertIsNot(affected.match, match)
        self.assertIs(affected.match.rules_backend, backend)
        self.assertEqual(healthy.match.snapshot(), before)
        self.assertEqual(healthy.match_reservations, healthy_reservations)
        for side in (2, 3):
            self.assertIn(self.clients[side].nick, self.server.nicks)
            self.assertTrue(self.server.economy.is_reserved(self.keys[side]))
        await self.finish((1,))
        self.assertEqual(len(draws), 1)
        await self.server.stop()
        await self.server.stop()
        backend.close.assert_not_called()

    async def test_message_failure_refunds_only_its_table_and_keeps_backend_borrowed(self):
        await self.failed(deferred=False)

    async def test_deferred_failure_refunds_only_its_table_and_keeps_backend_borrowed(self):
        await self.failed(deferred=True)


class EventBackendIntegration(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = events.EventMultiTable.asyncSetUp
    asyncTearDown = events.EventMultiTable.asyncTearDown
    send = events.EventMultiTable.send
    identity = events.EventMultiTable.identity
    enter_selection = events.EventMultiTable.enter_selection
    drain = events.EventMultiTable.drain
    round = events.EventMultiTable.round
    admit_all = events.EventMultiTable.admit_all
    tick = events.EventMultiTable.tick
    finish = events.EventMultiTable.finish
    release = events.EventMultiTable.release

    async def start(self):
        self.backend = recording_backend()
        self.tables = (
            self.table,
            replace(self.table, table_id=game_id(0x1004), channel=b"#EVTABLE002"),
        )
        self.server = Server(
            state_path=self.path,
            account_count=4,
            event_configs=(self.cfg,),
            event_tables=self.tables,
            event_clock=lambda: 200,
            event_progress_policy="rounds",
            rules_backend=self.backend,
        )
        port = await self.server.start(0)
        self.clients = [Client() for _ in range(4)]
        self.inbox = [[] for _ in self.clients]
        self.closed = set()
        for client, nick in zip(self.clients, self.server.accounts):
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [client.nick.decode() for client in self.clients]

    async def failed(self, *, deferred):
        await self.start()
        await self.admit_all()
        self.assertIs(self.server.event_matches.coordinator.rules_backend, self.backend)
        for index in (0, 1):
            self.assertIs(self.round(index).engine._combat.rules_backend, self.backend)
        affected, healthy = self.round(0), self.round(1)
        before = healthy.engine.snapshot()
        reservations = tuple(entry.reservation for entry in healthy.entries)
        draws = failing_battle(affected.engine._combat, self.backend, deferred=deferred)
        self.closed.update((0, 1))
        if not deferred:
            await self.clients[0].send(
                b"NOTICE "
                + service_nick(self.tables[0].table_id ^ GAME_KEY)
                + b" :"
                + packet(0x43, 11, affected.engine.sequence, "@BattleSelect=/B=1")
            )
        for side in (0, 1):
            await disconnected(self, self.clients[side])
            self.assertFalse(self.server.economy.is_reserved(self.keys[side]))
            self.assertEqual(self.server.events.member(self.keys[side], "festival").steps, 0)
        self.assertEqual(len(draws), 1)
        self.assertNotIn(self.tables[0].table_id, self.server.event_matches.coordinator._rounds)
        self.assertEqual(healthy.engine.snapshot(), before)
        self.assertEqual(tuple(entry.reservation for entry in healthy.entries), reservations)
        self.assertIsNone(self.server.event_matches.failure)
        task = self.server.event_matches.task
        self.assertFalse(task.done(), repr(task.exception()) if task.done() else "")
        for side in (2, 3):
            self.assertIn(self.clients[side].nick, self.server.nicks)
            self.assertTrue(self.server.economy.is_reserved(self.keys[side]))
        await self.finish((1,))
        await self.release(1)
        self.assertEqual(len(self.server.event_results.snapshot()["rounds"]), 1)
        self.assertEqual(len(draws), 1)
        await self.server.stop()
        self.backend.close.assert_not_called()

    async def test_event_message_failure_releases_only_its_round(self):
        await self.failed(deferred=False)

    async def test_event_deferred_failure_releases_only_its_round(self):
        await self.failed(deferred=True)


if __name__ == "__main__":
    unittest.main()
