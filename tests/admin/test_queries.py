"""Saved claims remain visible without collection or startup recovery."""

from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from tests.economy import test_match_economy as fixture
from tmserver.cards.catalog import Catalog
from tmserver.admin.queries import account_summary
from tmserver.cli.admin import AdminState
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.economy.models import MAX_MONEY
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.economy.service import EconomyService


class AccountQueryTests(unittest.TestCase):
    setUp = fixture.MatchEconomyTests.setUp
    begin = fixture.MatchEconomyTests.begin
    result = fixture.MatchEconomyTests.result
    participants = fixture.MatchEconomyTests.participants
    award = fixture.MatchEconomyTests.award

    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def summary(self, key="a"):
        saved = dict(
            self.e.snapshot(),
            auction=self.a.snapshot(),
            match_economy=self.m.snapshot(),
            result_rewards=self.r.snapshot(),
        )
        before = deepcopy(saved)
        store = Mock()
        with (
            patch.object(AuctionEconomy, "advance", side_effect=AssertionError("expiry")),
            patch.object(MatchEconomy, "recover_all", side_effect=AssertionError("recovery")),
            patch.object(ResultRewardService, "recover_all", side_effect=AssertionError("recovery")),
        ):
            state = AdminState(store, saved, ())
            summary = account_summary(state, key)
        self.assertEqual(store.method_calls, [])
        self.assertEqual(saved, before)
        return summary

    def test_escrow_and_paid_unsettled_cards_are_separate_from_owned_assets(self):
        self.begin(n=3, stake=200)
        summary = self.summary()
        self.assertEqual(summary["balance"], 800)
        self.assertEqual(summary["match_rounds"], [
            dict(id="r1", seat=0, money="escrow", escrowed_money=200,
                 has_result=False, cards_settled=False, pending_cards=0)
        ])
        self.assertIsNone(summary["pending_result"])
        self.result(scores=(7, 6, 2), cbc={0: self.hands[0][:2]})
        summary = self.summary()
        self.assertEqual(summary["match_rounds"], [
            dict(id="r1", seat=0, money="paid", escrowed_money=0,
                 has_result=True, cards_settled=False, pending_cards=2)
        ])
        self.assertEqual(summary["auction_claims"]["returned_cards"], 0)
        self.assertIsNone(summary["pending_result"])

    def test_handoff_acceptance_and_recovery_never_double_count_cards(self):
        self.begin()
        self.result(cbc={0: self.hands[0][:1]})
        self.tokens, _ = self.m.settle_cards("r1", self.participants(), (self.award(),))
        self.assertEqual(self.summary()["match_rounds"][0]["pending_cards"], 2)
        self.m.handoff("r1", self.tokens, self.r)
        summary = self.summary()
        self.assertEqual(summary["match_rounds"], [])
        self.assertEqual(summary["pending_result"]["reward_id"], self.r.status("a")["reward_id"])
        self.assertEqual(summary["pending_result"]["extra_count"], 2)
        self.assertEqual(summary["auction_claims"]["returned_cards"], 0)
        self.r.recover("a")
        summary = self.summary()
        self.assertIsNone(summary["pending_result"])
        self.assertEqual(summary["auction_claims"]["returned_cards"], 2)
        self.r.commit("b")
        self.assertIsNone(self.summary("b")["pending_result"])

    def test_account_claims_are_detached_and_do_not_expose_other_owners(self):
        self.a.enqueue_returned("a", "a-return", self.e.inventory("a")[:1])
        self.a.enqueue_returned("b", "b-return", self.e.inventory("b")[:2])
        summary = self.summary()
        self.assertEqual(summary["auction_claims"]["returned_cards"], 1)
        self.assertEqual(len(summary["auction_claims"]["receipts"]), 1)
        summary["auction_claims"]["receipts"][0]["card"][0] = 999
        self.assertNotEqual(self.summary()["auction_claims"]["receipts"][0]["card"][0], 999)

    def test_all_auction_claim_kinds_remain_accurate_after_partial_collection(self):
        saved = self.e.snapshot()
        saved["accounts"]["a"]["balance"] = MAX_MONEY - 20
        self.e = EconomyService.restore(saved)
        self.a = AuctionEconomy(self.e)
        self.m = MatchEconomy(self.e)
        self.r = ResultRewardService(self.e, self.a)
        sold = self.a.exhibit("a", self.e.inventory("a")[0], 100, 20, 1, 0, now=100)
        sold = self.a.bid("b", sold.id, 100, sold.revision, now=101)
        sold = self.a.bid("c", sold.id, 120, sold.revision, now=102)
        unsold = self.a.exhibit("a", self.e.inventory("a")[0], 100, 20, 1, 0, now=100)
        self.a.enqueue_returned("a", "earlier-batch", self.e.inventory("a")[:1] * 30)
        self.a.advance(sold.expires)
        self.assertEqual(self.a.listing(unsold.id).status, "returned")
        before = self.summary()["auction_claims"]
        self.assertEqual((before["payments"], before["returned_cards"]), (120, 31))
        self.assertEqual((before["refunds"], before["won_cards"]), (0, 0))
        self.assertEqual(self.summary("b")["auction_claims"]["refunds"], 100)
        self.assertEqual(self.summary("c")["auction_claims"]["won_cards"], 1)
        payment = next(row for row in before["receipts"] if row["kind"] == "payment")

        collection = self.a.reserve_collection("a")
        self.assertEqual(self.a.commit_payment(collection), 20)
        self.a.commit_returned(collection)
        self.a.commit_purchased(collection)
        self.a.commit_refund(collection)
        summary = self.summary()
        pending = summary["auction_claims"]
        self.assertEqual((summary["balance"], summary["card_count"]), (MAX_MONEY, 38))
        self.assertEqual((pending["payments"], pending["returned_cards"]), (100, 1))
        self.assertEqual(
            next(row for row in pending["receipts"] if row["kind"] == "payment"),
            dict(payment, amount=100),
        )
        for key in ("b", "c"):
            collection = self.a.reserve_collection(key)
            self.a.commit_payment(collection)
            self.a.commit_returned(collection)
            self.a.commit_purchased(collection)
            self.a.commit_refund(collection)
            self.assertEqual(self.summary(key)["auction_claims"]["receipts"], [])
        self.assertEqual(self.summary("b")["balance"], 1000)
        self.assertEqual(self.summary("c")["card_count"], 11)

    def test_inspection_keeps_expired_listings_active_until_explicit_expiry(self):
        item = self.a.exhibit("a", self.e.inventory("a")[0], 100, 20, 1, 0, now=0)
        summary = self.summary()
        self.assertEqual(summary["auction_claims"]["receipts"], [])
        self.assertEqual(self.a.listing(item.id).status, "active")

    def test_older_save_without_claim_journals_has_empty_claims(self):
        saved = self.e.snapshot()
        before = deepcopy(saved)
        store = Mock()
        state = AdminState(store, saved, ())
        summary = account_summary(state, "a")
        self.assertEqual(summary["auction_claims"], dict(
            payments=0, refunds=0, returned_cards=0, won_cards=0, receipts=[]
        ))
        self.assertEqual(summary["event_claims"], [])
        self.assertIsNone(summary["pending_result"])
        self.assertEqual(summary["match_rounds"], [])
        self.assertEqual(store.method_calls, [])
        self.assertEqual(saved, before)
