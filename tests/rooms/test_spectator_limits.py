"""Oversized supported CBC snapshots must never interrupt participant play."""

import unittest
from unittest.mock import patch

from tests.game import test_three_player_integration as versus
from tests.game.test_match_service import ack, put, select
from tests.protocol.test_admission_identity import native_identity
from tests.rooms.harness import serve, state_path
from tmserver.cards.catalog import Catalog
from tmserver.cards.model import Card as InventoryCard
from tmserver.economy.service import EconomyService
from tmserver.game.match import MatchService
from tmserver.game.models import Card
from tmserver.game.packets import decode, packet
from tmserver.network.local_accounts import account_specs
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.rooms.packets import TABLE_CHANNEL
from tmserver.rooms.spectators import SpectatorError, SpectatorService, bootstrap
from tmserver.storage.json import JsonStateStore


FIELDS = tuple(18 if index % 5 in (1, 3) else 0 for index in range(25))
WATCH = packet(0x43, 0x25, 0, "@Data=/Watch=0")


def generated_card():
    draws = iter((109, 5, 104, 104, 99, 0, 0, 0, 0, 0, 0, 0, 0))
    return Card.from_card(Catalog().generate(214, lambda: next(draws)))


def advance_turn(match):
    output = []
    for seat, player in enumerate(match.players):
        output += match.handle(player.peer, ack(seat, match.sequence))
    output += match.handle(
        match.players[match.active].peer,
        put(match.active, match.sequence, 0, match.legal_fields()[0]),
    )
    for _ in range(200):
        if match.phase != "battle":
            return output
        if match.pending_work:
            output += match.advance()
        else:
            output += match.handle(
                match.players[match.active].peer,
                packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}"),
            )
    raise AssertionError("battle did not finish")


class SpectatorSnapshotLimits(unittest.TestCase):
    def setUp(self):
        self.match = MatchService(
            mode="versus", player_count=3, seed=1, allow_cbc=True, start_fields=FIELDS
        )
        for seat in range(3):
            self.match.register(f"p{seat}", seat + 1, name=f"P{seat}".encode())
        self.match.begin()
        for seat in range(3):
            self.match.handle(f"p{seat}", select((generated_card(),) * 5, seat))
        for _ in range(8):
            advance_turn(self.match)

    def test_supported_dense_boundary_rejects_immediate_join_without_mutation(self):
        before = self.match.observer_snapshot()
        self.assertEqual(sum(cell.card is not None for cell in before.board), 16)
        self.assertEqual(len(bootstrap(before)[1]) - 13, 441)
        advance_turn(self.match)
        snapshot = self.match.observer_snapshot()
        self.assertEqual(sum(cell.card is not None for cell in snapshot.board), 17)
        service = SpectatorService(enabled=True)
        state, rng = self.match.snapshot(), self.match.rng.state
        with self.assertRaisesRegex(SpectatorError, "single-body contract"):
            service.handle("late", WATCH, snapshot, epoch=self.match)
        self.assertEqual(service.count, 0)
        self.assertEqual((self.match.snapshot(), self.match.rng.state), (state, rng))

    def test_deferred_dense_bootstrap_evicts_only_waiting_observer(self):
        service = SpectatorService(enabled=True)
        for delivery in service.handle(
            "watching", WATCH, self.match.observer_snapshot(), epoch=self.match
        ):
            service.record_sent(delivery.peer, delivery.payload, epoch=self.match)
        for seat in range(3):
            self.match.handle(f"p{seat}", ack(seat, self.match.sequence))
        output = self.match.handle(
            f"p{self.match.active}",
            put(self.match.active, self.match.sequence, 0, self.match.legal_fields()[0]),
        )
        self.assertEqual(self.match.phase, "battle")
        self.assertEqual(
            service.handle("late", WATCH, self.match.observer_snapshot(), epoch=self.match), []
        )
        while self.match.phase == "battle":
            if self.match.pending_work:
                output += self.match.advance()
            else:
                output += self.match.handle(
                    f"p{self.match.active}",
                    packet(
                        0x43, 11, self.match.sequence, f"@BattleSelect=/B={self.match.targets[0]}"
                    ),
                )
        state, rng = self.match.snapshot(), self.match.rng.state
        service.publish(output, self.match.observer_snapshot(), epoch=self.match)
        self.assertEqual(service.take_evicted(), (("late", "unsupported_snapshot"),))
        self.assertTrue(service.active("watching"))
        self.assertEqual((self.match.snapshot(), self.match.rng.state), (state, rng))


class SpectatorLimitsIntegration(unittest.IsolatedAsyncioTestCase):
    flush = versus.ThreePlayerIntegrationTests.flush
    send = versus.ThreePlayerIntegrationTests.send
    admit = versus.ThreePlayerIntegrationTests.admit

    async def asyncSetUp(self):
        state_path(self)
        economy = EconomyService(seed=1)
        for key, (_, _, character) in account_specs(4).items():
            economy.provision(
                key,
                nn=player_id(key.encode()),
                player_id=character,
                name=f"P{character}".encode(),
                cards=[InventoryCard.from_card(generated_card())] * 5,
            )
        JsonStateStore(self.state_path).save(economy.snapshot())
        await serve(
            self,
            account_count=4,
            match_mode="versus",
            match_player_count=3,
            match_start_fields=FIELDS,
            allow_cbc=True,
            spectator=SpectatorService(enabled=True),
        )
        self.target = service_nick(TABLE_RAW)
        self.keys = [client.nick.decode() for client in self.clients[:3]]
        await self.admit()
        for side in range(3):
            await self.send(side, select((generated_card(),) * 5, side))

    async def move(self):
        match = self.server.default_table.match
        for side in range(3):
            await self.send(side, ack(side, match.sequence))
        return await self.send(
            match.active, put(match.active, match.sequence, 0, match.legal_fields()[0])
        )

    async def finish_battles(self):
        match = self.server.default_table.match
        for _ in range(200):
            if match.phase != "battle":
                return
            await self.send(
                match.active,
                packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}"),
            )
        self.fail("battle did not finish")

    async def test_deferred_dense_snapshot_preserves_encrypted_participants_and_round(self):
        table = self.server.default_table
        match = table.match
        while match.sequence < 8:
            await self.move()
            await self.finish_battles()
        self.assertEqual(len(bootstrap(match.observer_snapshot())[1]) - 13, 441)
        observer = self.clients[3]
        observer_before = self.server.economy.profile_bytes(observer.nick.decode())
        await observer.join(TABLE_CHANNEL)
        view = self.server.economy.account_view(observer.nick.decode())
        await observer.send(
            b"NOTICE " + self.target + b" :" + packet(1, body=native_identity(view, "Req"))
        )
        self.assertIn(b"@PLAYACK=", await observer.read())
        # Hold only background continuation scheduling, so an actual encrypted
        # watch request can deterministically arrive during the real battle.
        with patch.object(table, "schedule_match_work"):
            await self.move()
            self.assertEqual(match.phase, "battle")
            await observer.send(b"NOTICE " + self.target + b" :" + WATCH)
            await observer.send(b"PING :deferred-watch")
            self.assertEqual(await observer.read(), b"PONG deferred-watch")
        observer_peer = self.server.nicks[observer.nick]
        self.assertTrue(table.spectators.active(observer_peer))
        tokens = dict(table.match_reservations)
        table.schedule_match_work(match)
        replies = [await self.flush(side) for side in range(3)]
        while match.phase == "battle":
            batch = await self.send(
                match.active,
                packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}"),
            )
            replies = [old + new for old, new in zip(replies, batch)]
        self.assertEqual(match.sequence, 9)
        self.assertEqual(sum(cell.card is not None for cell in match.observer_snapshot().board), 17)
        self.assertEqual(await observer.read(), b"")
        self.assertFalse(table.spectators.active(observer_peer))
        self.assertNotIn(observer_peer, table.spectator_admissions)
        self.assertEqual(table.match_reservations, tokens)
        for packets in replies:
            self.assertTrue(
                any(decode(p).subtype == 9 and decode(p).argument == 9 for p in packets)
            )
        while match.phase != "result":
            await self.move()
            await self.finish_battles()
        self.assertIs(table.match, match)
        self.assertTrue(all(not self.server.nicks[c.nick].closed for c in self.clients[:3]))
        self.assertFalse(self.server.economy.is_busy(observer.nick.decode()))
        self.assertEqual(self.server.economy.profile_bytes(observer.nick.decode()), observer_before)
