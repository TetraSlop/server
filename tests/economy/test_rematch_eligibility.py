"""Rejected rematches preserve completed rewards and release every participant."""

import unittest
from collections import Counter
from contextlib import asynccontextmanager

from tests.economy import test_match_prize_edges as prizes
from tmserver.protocol.identities import TABLE_RAW, service_nick
from tmserver.game.packets import packet
from tmserver.network.server import Server


class RematchEligibilityTests(unittest.IsolatedAsyncioTestCase):
    flush = prizes.PrizeEdgeTests.flush
    send = prizes.PrizeEdgeTests.send
    play = prizes.PrizeEdgeTests.play
    choices = prizes.PrizeEdgeTests.choices
    expected = prizes.PrizeEdgeTests.expected
    offer = prizes.WagerPrizeEdges.offer

    @asynccontextmanager
    async def scenario(self, *, wagers):
        self.wagers = wagers
        await prizes.PrizeEdgeTests.asyncSetUp(self)
        try:
            for key in self.keys:
                if wagers:
                    token = self.server.economy.reserve_auction(key)
                    self.server.economy.commit_auction(token, debit=9800)
                else:
                    token = self.server.economy.reserve_auction(key)
                    self.server.economy.commit_auction(
                        token, cards=token.account.cards[:5], allow_discard=True
                    )
            self.server.persist()
            yield
        finally:
            await prizes.PrizeEdgeTests.asyncTearDown(self)

    async def admit(self):
        await self.offer(stake=200 if self.wagers else 0, double=int(self.wagers))
        for side in range(2):
            await self.send(side, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(self.server.default_table.match.phase, "selection")

    def result_state(self, match):
        return {
            "saved": self.store.load(),
            "host": self.server.snapshot(),
            "reservations": dict(self.server.default_table.match_reservations),
            "votes": dict(match.continues),
            "match": match.snapshot(),
            "rng": match.rng.state,
            "balances": [player.balance for player in match.players],
            "round": self.server.default_table.match_economy_round,
            "room": (self.server.room.snapshot(), self.server.room.sequence),
        }

    async def assert_rejected_rematch(self, match, settlement, first, reason):
        def vote(seat):
            return packet(0x43, 17, seat, "@Continue=/Ans=1/PR=0/Conf=0")

        await self.send(first, vote(first))
        last = 1 - first
        peer = self.server.nicks[self.clients[last].nick]
        before = self.result_state(match)

        # Inspect the real dispatch failure before transport cleanup recovers
        # any unaccepted rewards. The previous player's accepted vote remains.
        with self.assertRaisesRegex(ValueError, reason):
            await self.server.default_table.dispatch_table(peer, vote(last))
        self.assertEqual(self.result_state(match), before)
        self.assertEqual(len(match.continues), 1)

        # Replay through encrypted TCP to exercise the actual close/resync path.
        await self.clients[last].send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + vote(last))
        for client in self.clients:
            self.assertEqual(await client.read(), b"")
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.server.default_table.match.players, [])
        self.assertTrue(all(not self.server.economy.is_busy(key) for key in self.keys))
        self.assertEqual(
            [self.server.economy.balance(key) for key in self.keys], before["balances"]
        )
        for seat, key in enumerate(self.keys):
            kept, earned = self.expected(settlement, seat)
            inventory = [prizes.values(card) for card in self.server.economy.inventory(key)]
            recovery = [
                prizes.values(item.card) for item in self.server.auction_ledger.pending(key)
            ]
            self.assertEqual(Counter(inventory + recovery), Counter(kept + earned))

        after = self.server.snapshot()
        saved = self.store.load()
        restored = Server(store=self.store)
        try:
            self.assertEqual(restored.snapshot(), after)
            self.assertEqual(self.store.load(), saved)
        finally:
            await restored.stop()

    async def test_prize_loser_below_five_cards_cannot_rematch_in_either_voting_order(self):
        for first_winner in (False, True):
            with self.subTest(first="winner" if first_winner else "loser"):
                async with self.scenario(wagers=False):
                    match = await self.play()
                    settlement = await self.choices()
                    self.assertTrue(settlement.transfers)
                    winner = settlement.transfers[0].winner
                    loser = 1 - winner
                    self.assertLess(len(self.server.economy.inventory(self.keys[loser])), 5)
                    await self.assert_rejected_rematch(
                        match,
                        settlement,
                        winner if first_winner else loser,
                        "at least five owned cards",
                    )

    async def test_unaffordable_double_stake_cannot_rematch_in_either_voting_order(self):
        for first_winner in (False, True):
            with self.subTest(first="winner" if first_winner else "loser"):
                async with self.scenario(wagers=True):
                    match = await self.play()
                    settlement = await self.choices()
                    self.assertTrue(settlement.transfers)
                    winner = settlement.transfers[0].winner
                    loser = 1 - winner
                    self.assertEqual(self.server.economy.balance(self.keys[loser]), 0)
                    self.assertEqual((match.game_rules["bm"], match.game_rules["du"]), (200, 1))
                    await self.assert_rejected_rematch(
                        match,
                        settlement,
                        winner if first_winner else loser,
                        "cannot afford the next wager",
                    )


if __name__ == "__main__":
    unittest.main()
