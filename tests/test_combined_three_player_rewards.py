"""Combined three-player wagers, owned prizes and generated CBC rewards."""

import unittest
from collections import Counter

from tests import test_match_prizes_integration as prizes
from tests import test_match_wagers_integration as wager_tests
from tests import test_three_player_cbc as cbc
from tmserver.match_service import packet
from tmserver.server import Server


class CombinedThreePlayerRewards(unittest.IsolatedAsyncioTestCase):
    player_count = 3
    wagers = True
    asyncSetUp = prizes.PrizeIntegrationTests.asyncSetUp
    asyncTearDown = prizes.PrizeIntegrationTests.asyncTearDown
    flush = prizes.PrizeIntegrationTests.flush
    send = prizes.PrizeIntegrationTests.send
    admit = wager_tests.WagerIntegrationTests.admit
    play_cbc = cbc.ThreePlayerCBC.play_cbc
    choose = prizes.PrizeIntegrationTests.choose
    expected = prizes.PrizeIntegrationTests.expected

    async def completed(self):
        self.before = [self.server.economy.inventory(k) for k in self.keys]
        await self.play_cbc()
        self.extras = [
            self.server.match.final_cards(self.server.nicks[c.nick]).extras for c in self.clients
        ]
        _, settlement = await self.choose()
        self.assertTrue(settlement.transfers, "fixture must transfer ordinary owned cards too")
        self.assertEqual(sum(len(x) for x in self.extras), 1)
        self.assertEqual(sum(self.server.economy.balance(k) for k in self.keys), 30000)
        self.want = []
        for seat, key in enumerate(self.keys):
            kept, awarded = self.expected(settlement, seat)
            awarded += [prizes.full(c) for c in self.extras[seat]]
            self.want.append((kept, awarded))
            self.assertEqual(
                Counter(map(prizes.full, self.server.economy.inventory(key))), Counter(kept)
            )
            self.assertEqual(
                self.server.result_rewards.status(key)["working_count"], len(kept) + len(awarded)
            )
        return settlement

    async def test_all_awards_accept_once_and_shared_money_pot_is_conserved(self):
        await self.completed()
        for seat in range(3):
            await self.send(
                seat, packet(0x43, 17, seat, f"@Continue=/Ans={2 if seat==2 else 1}/PR=0/Conf=0")
            )
        self.assertEqual(self.server.match.phase, "finished")
        for key, (kept, awarded) in zip(self.keys, self.want):
            self.assertEqual(
                Counter(map(prizes.full, self.server.economy.inventory(key))),
                Counter(kept + awarded),
            )
            self.assertFalse(self.server.auction_ledger.pending(key))
        self.assertEqual(sum(len(self.server.economy.inventory(k)) for k in self.keys), 31)
        restored = Server(state_path=self.path, account_count=3)
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
        finally:
            await restored.stop()

    async def test_restart_recovers_ordinary_and_generated_prizes_without_repeating_losses(self):
        await self.completed()
        crash = self.path.with_name("crash.json")
        crash.write_bytes(self.path.read_bytes())
        restored = Server(state_path=crash, account_count=3)
        try:
            for key, (kept, awarded) in zip(self.keys, self.want):
                self.assertEqual(
                    Counter(map(prizes.full, restored.economy.inventory(key))), Counter(kept)
                )
                self.assertEqual(
                    Counter(prizes.full(p.card) for p in restored.auction_ledger.pending(key)),
                    Counter(awarded),
                )
            total = sum(
                len(restored.economy.inventory(k)) + len(restored.auction_ledger.pending(k))
                for k in self.keys
            )
            self.assertEqual(total, 31)
            again = Server(state_path=crash, account_count=3)
            try:
                self.assertEqual(again.economy.snapshot(), restored.economy.snapshot())
                self.assertEqual(
                    again.auction_ledger.snapshot(), restored.auction_ledger.snapshot()
                )
            finally:
                await again.stop()
        finally:
            await restored.stop()


if __name__ == "__main__":
    unittest.main()
