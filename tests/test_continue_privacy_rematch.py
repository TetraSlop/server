"""Continue's privacy preference preserves its accepted result deck editor."""

import unittest
from collections import Counter
from unittest.mock import patch

from tests import test_match_prize_edges as edges
from tests import test_result_rewards as rewards
from tests.test_match_transactions import TableFixture
from tmserver.economy_service import Catalog
from tmserver.match_service import packet
from tmserver.match_transactions import match_transaction
from tmserver.result_rewards import ResultRewardError


class ContinuePrivacyIntegration(unittest.IsolatedAsyncioTestCase):
    wagers = True
    asyncSetUp = edges.WagerPrizeEdges.asyncSetUp
    asyncTearDown = edges.WagerPrizeEdges.asyncTearDown
    flush = edges.WagerPrizeEdges.flush
    send = edges.WagerPrizeEdges.send
    offer = edges.WagerPrizeEdges.offer
    admit = edges.WagerPrizeEdges.admit
    play = edges.WagerPrizeEdges.play
    choices = edges.WagerPrizeEdges.choices
    expected = edges.WagerPrizeEdges.expected
    restore_copy = edges.WagerPrizeEdges.restore_copy

    async def check_privacy(self, voter):
        await self.play()
        settlement = await self.choices()
        paid = [self.server.economy.balance(key) for key in self.keys]
        owned = [Counter(sum(self.expected(settlement, seat), [])) for seat in range(2)]
        for seat in range(2):
            await self.send(
                seat, packet(0x43, 0x11, seat, f"@Continue=/Ans=1/PR={int(seat == voter)}/Conf=0")
            )
        self.assertEqual(self.server.match.phase, "selection")
        self.assertEqual(self.server.match.round_stake, 400)
        for seat, key in enumerate(self.keys):
            self.assertEqual(self.server.economy.balance(key), paid[seat] - 400)
            self.assertEqual(
                Counter(map(edges.values, self.server.economy.inventory(key))), owned[seat]
            )
        key = self.keys[voter]
        token = self.server.result_rewards.reservation(key)
        self.assertIsNotNone(token)
        # The final voter is accepted inside the rematch's joint stake commit,
        # before the adapter saves PR. Its fingerprint must also stay current.
        self.assertTrue(self.server.result_rewards.refresh_reservation(key, token))
        self.assertEqual(self.server.economy.profile_bytes(key)[0xB6], 1)
        card = self.server.economy.inventory(key)[-1]
        name = b"Private rematch"
        await self.send(
            voter,
            packet(
                0x43,
                0x22,
                voter,
                f"@Decks=/P={voter}/C=1@0=/D={edges.compact(card)}|9@DN1=/S=" + name.hex(),
            ),
        )
        self.assertEqual(self.server.economy.inventory(key)[-1].deck, 9)
        self.assertEqual(dict(self.server.economy.account_view(key).deck_names)[1], name)
        self.assertEqual(
            [self.server.economy.balance(k) for k in self.keys], [p - 400 for p in paid]
        )
        cards = [self.server.economy.inventory(k) for k in self.keys]
        await self.clients[0].close()
        self.assertEqual(await self.clients[1].read(), b"")
        self.assertEqual([self.server.economy.balance(k) for k in self.keys], paid)
        self.assertEqual([self.server.economy.inventory(k) for k in self.keys], cards)
        restored, _ = await self.restore_copy()
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
            self.assertEqual(restored.economy.profile_bytes(key)[0xB6], 1)
            self.assertEqual(dict(restored.economy.account_view(key).deck_names)[1], name)
        finally:
            await restored.stop()

    async def test_first_voters_privacy_survives_rematch_refresh_and_late_decks(self):
        await self.check_privacy(0)

    async def test_final_voters_privacy_survives_rematch_refresh_and_late_decks(self):
        await self.check_privacy(1)


class ContinuePrivacyGuard(unittest.TestCase):
    setUp = rewards.ResultRewardsTests.setUp
    stage = rewards.ResultRewardsTests.stage

    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def test_continue_privacy_does_not_adopt_an_unrelated_profile_change(self):
        self.stage()
        token = self.r.commit("one")
        self.e.save_options(
            "one",
            dict(Ar=1, Cu=0, Vi=0, Se=3, Bgm=3, Per=1, Ran=0, CMD=1, CAD=1, CL=8, CT=60, HNSS=1),
        )
        self.r.save_continue_privacy("one", 1)
        refreshed = self.e.commit_match_money([(token, 200, 0)])["one"]
        with self.assertRaises(ResultRewardError):
            self.r.refresh_reservation("one", refreshed)
        self.assertIs(self.r.reservation("one"), token)


class ContinuePrivacyTransactions(TableFixture, unittest.TestCase):
    def test_failed_save_keeps_privacy_and_pending_or_accepted_context_unchanged(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                self.prepare_table()
                self.begin()
                self.result()
                self.prepare_prize()
                self.server.commit_match_prizes(self.match)
                key = self.keys[0]
                if accepted:
                    with match_transaction(self.server, (key,)) as state:
                        state.result_rewards.commit(key)
                before = self.server.snapshot()
                rewards = self.server.result_rewards
                pending, receipt = rewards._pending.get(key), rewards._accepted.get(key)
                with (
                    patch.object(
                        self.server, "persist_changes", side_effect=OSError("save failed")
                    ),
                    self.assertRaises(OSError),
                    match_transaction(self.server, (key,)) as state,
                ):
                    state.result_rewards.commit(key)
                    state.result_rewards.save_continue_privacy(key, 1)
                self.assertEqual(self.server.snapshot(), before)
                self.assertIs(rewards._pending.get(key), pending)
                self.assertIs(rewards._accepted.get(key), receipt)


if __name__ == "__main__":
    unittest.main()
