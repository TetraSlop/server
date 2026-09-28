"""CBC acceptance/recovery, inventory conservation and original owner5 exports."""

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tmserver.economy.auction_ledger import AuctionEconomy
from tmserver.cards.model import Card
from tmserver.cards.catalog import Catalog
from tmserver.economy.errors import EconomyError
from tmserver.economy.service import EconomyService
from tmserver.economy.packets import named
from tmserver.economy.result_rewards import ResultRewardError, ResultRewardService
from tmserver.rankings.stats import PracticeStats


class ResultRewardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.e = EconomyService(seed=10, catalog=self.catalog)
        self.e.provision("one", nn=1, name=b"One")
        self.e.provision("two", nn=2, name=b"Two")
        self.a = AuctionEconomy(self.e)
        self.r = ResultRewardService(self.e, self.a)
        self.extra = self.catalog.generate(5, lambda: 20)

    def stage(self, key="one", extras=None, rid="table1.round1", seat=0):
        token = self.e.reserve_match(key)
        five = self.e.inventory(key)[:5]
        stats = PracticeStats.from_profile(self.e.profile_bytes(key)).next_result(1)
        token = self.e.commit_match_growth([(token, five, five)], {key: stats})[key]
        self.assertTrue(
            self.r.stage(key, token, rid, seat, (self.extra,) if extras is None else extras)
        )
        return token

    def snapshot(self):
        return dict(
            economy=self.e.snapshot(), auction=self.a.snapshot(), result_rewards=self.r.snapshot()
        )

    def restart(self):
        data = json.loads(json.dumps(self.snapshot()))
        self.e = EconomyService.restore(data["economy"], catalog=self.catalog)
        self.a = AuctionEconomy.restore(self.e, data["auction"])
        self.r = ResultRewardService.restore(self.e, self.a, data["result_rewards"])

    @staticmethod
    def edit(token, cards=(), seat=0, mode=1, name=None):
        body = f"@{token}=/P={seat}" + (f"/Mode={mode}" if token == "Erase" else "")
        if token == "Erase" and mode == 2:
            return named(0x43, 0x10, seat, body)
        body += f"/C={len(cards)}" + "".join(
            f"@{i}=/D=" + "|".join(map(str, c.reduced() + ((c.deck,) if token == "Decks" else ())))
            for i, c in enumerate(cards)
        )
        if name is not None:
            body += "@DN1=/S=" + name.hex()
        return named(0x43, 0x10 if token == "Erase" else 0x22, seat, body)

    def collect(self, key):
        c = self.a.reserve_collection(key)
        self.a.commit_payment(c)
        self.a.commit_returned(c)
        self.a.commit_purchased(c)
        self.a.commit_refund(c)

    def test_fitting_reward_commits_once_at_continue_and_keeps_all_other_fields(self):
        before = self.e.inventory("one")
        token = self.stage()
        profile = self.e.profile_bytes("one")
        self.assertEqual(self.e.inventory("one"), before)
        refreshed = self.r.commit("one")
        self.assertEqual(self.e.inventory("one"), before + (self.extra,))
        self.assertEqual(self.e.profile_bytes("one")[0x40:0x148], profile[0x40:0x148])
        self.assertEqual(self.e.balance("one"), 10000)
        self.assertIs(self.r.commit("one"), refreshed)
        with self.assertRaises(EconomyError):
            self.e.match_account(token)
        self.assertFalse(self.r.recover("one"))
        self.e.release_match(refreshed)
        self.restart()
        self.assertEqual(len(self.e.inventory("one")), 11)

    def test_third_seat_authenticated_edits_and_durable_recovery(self):
        token = self.stage(seat=2)
        with self.assertRaises(ResultRewardError):
            self.r.handle("one", self.edit("Erase", mode=2, seat=1))
        self.r.handle("one", self.edit("Erase", mode=2, seat=2))
        self.restart()
        self.assertEqual(self.r.snapshot()["pending"][0]["seat"], 2)
        self.r.recover("one")
        self.collect("one")
        self.assertEqual(len(self.e.inventory("one")), 11)
        token = self.e.reserve_match("one")
        with self.assertRaises(ResultRewardError):
            self.r.stage("one", token, "badseat", 3, (self.extra,))

    def test_stage_identity_deduplication_and_forged_provenance_rejected(self):
        token = self.stage()
        self.assertFalse(self.r.stage("one", token, "table1.round1", 0, (self.extra,)))
        with self.assertRaises(ResultRewardError):
            self.r.stage(
                "one",
                token,
                "table1.round1",
                0,
                (replace(self.extra, arrows=self.extra.arrows ^ 1),),
            )
        with self.assertRaises(ResultRewardError):
            self.r.stage("two", token, "other", 0, (self.extra,))
        with self.assertRaises(ResultRewardError):
            self.r.stage("one", token, "new", 0, (self.extra,))
        with self.assertRaises(ResultRewardError):
            self.r.stage("one", token, "x", 0, (self.extra,) * 31)
        with self.assertRaises(ResultRewardError):
            self.r.stage("one", token, "x", 0, (replace(self.extra, deck=0),))
        self.assertEqual(len(self.e.inventory("one")), 10)

    def test_overflow_requires_actual_erase_and_mode2_never_erases_all(self):
        card = self.e.inventory("one")[0]
        self.e.provision("full", nn=3, name=b"Full", cards=[card] * 1000)
        self.stage("full")
        with self.assertRaises(EconomyError):
            self.r.commit("full")
        self.r.handle("full", self.edit("Erase", mode=2))
        with self.assertRaises(EconomyError):
            self.r.commit("full")
        self.r.handle("full", self.edit("Erase", (card,)))
        self.assertEqual(len(self.e.inventory("full")), 1000)
        self.r.commit("full")
        self.assertEqual(len(self.e.inventory("full")), 1000)
        self.assertEqual(self.e.inventory("full")[-1], self.extra)

    def test_discarding_earned_card_is_explicit_and_only_accepted_at_continue(self):
        token = self.stage()
        self.r.handle("one", self.edit("Erase", (self.extra,)))
        self.assertEqual(len(self.e.inventory("one")), 10)
        self.r.commit("one")
        self.assertEqual(len(self.e.inventory("one")), 10)
        self.assertEqual(self.a.pending("one"), ())
        self.assertFalse(self.r.recover("one"))

    def test_bad_side_truncated_name_forged_card_and_multiplicity_are_atomic(self):
        self.stage()
        status = self.r.status("one")
        c = self.e.inventory("one")[0]
        bad = [
            self.edit("Erase", (c,), seat=1),
            self.edit("Erase", (replace(c, arrows=c.arrows ^ 1),)),
            self.edit("Erase", (self.extra, self.extra)),
            named(0x43, 0x22, 0, "@Decks=/P=0/C=0@DN1=/S=123"),
            named(0x43, 0x10, 0, "@Erase=/P=1/Mode=1/C=0"),
        ]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(EconomyError):
                self.r.handle("one", payload)
            self.assertEqual(self.r.status("one"), status)
        self.assertIsNone(
            self.r.handle("one", named(0x43, 0x11, 0, "@Continue=/Ans=1/PR=0/Conf=0"))
        )

    def test_decks_and_options_preserved_and_late_decks_refresh_capability(self):
        self.stage()
        chosen = replace(self.extra, deck=4)
        self.r.handle("one", self.edit("Decks", (chosen,), name=b"Reward"))
        self.e.save_options(
            "one",
            dict(Ar=1, Cu=0, Vi=0, Se=3, Bgm=3, Per=1, Ran=0, CMD=1, CAD=1, CL=8, CT=60, HNSS=1),
        )
        first = self.r.commit("one")
        self.assertEqual(self.e.inventory("one")[-1], chosen)
        self.assertEqual(dict(self.e.account_view("one").deck_names)[1], b"Reward")
        self.r.handle("one", self.edit("Decks", (replace(chosen, deck=5),)))
        self.assertEqual(self.e.inventory("one")[-1].deck, 5)
        self.assertIsNot(first, self.r.reservation("one"))
        self.assertEqual(self.e.profile_bytes("one")[0x126], 1)
        with self.assertRaises(ResultRewardError):
            self.r.handle("one", self.edit("Erase", (self.extra,)))
        self.r.forget("one")
        with self.assertRaises(ResultRewardError):
            self.r.handle("one", self.edit("Decks"))

    def test_disconnect_moves_unaccepted_rewards_to_real_returned_card_collection(self):
        token = self.stage()
        self.assertTrue(self.r.recover("one"))
        self.assertFalse(self.r.recover("one"))
        self.assertEqual(self.a.pending("one")[0].card, self.extra)
        self.assertTrue(self.e.is_reserved("one"))  # Table cleanup still owns its token.
        self.e.release_match(token)
        self.restart()
        self.collect("one")
        self.assertEqual(self.e.inventory("one")[-1], self.extra)
        self.assertEqual(self.a.pending("one"), ())
        token = self.e.reserve_match("one")
        self.assertFalse(self.r.stage("one", token, "table1.round1", 0, (self.extra,)))
        self.assertEqual(len(self.e.inventory("one")), 11)

    def test_restart_recovery_rolls_back_unaccepted_edits_but_preserves_earned_cards(self):
        original = self.e.inventory("one")
        self.stage()
        self.r.handle("one", self.edit("Erase", (original[0], self.extra)))
        self.restart()
        self.assertTrue(self.r.status("one")["recovery_only"])
        with self.assertRaises(ResultRewardError):
            self.r.commit("one")
        self.assertEqual(self.r.recover_all(), 1)
        self.assertEqual(self.r.recover_all(), 0)
        self.assertEqual(self.e.inventory("one"), original)
        self.collect("one")
        self.assertEqual(self.e.inventory("one"), original + (self.extra,))

    def test_recovery_does_not_invalidate_existing_frozen_collection(self):
        self.stage()
        self.restart()
        c = self.a.reserve_collection("one")
        self.a.commit_payment(c)
        self.r.recover_all()
        self.a.commit_returned(c)
        self.a.commit_purchased(c)
        self.a.commit_refund(c)
        self.assertEqual(len(self.e.inventory("one")), 10)
        self.assertEqual(len(self.a.pending("one")), 1)
        self.collect("one")
        self.assertEqual(len(self.e.inventory("one")), 11)

    def test_recovery_of_full_inventory_uses_existing_auction_discard_get_barrier(self):
        card = self.e.inventory("one")[0]
        self.e.provision("full", nn=3, name=b"Full", cards=[card] * 1000)
        self.stage("full")
        self.restart()
        self.r.recover_all()
        c = self.a.reserve_collection("full")
        self.a.commit_payment(c)
        with self.assertRaises(EconomyError):
            self.a.commit_returned(c)
        cards = self.a.collection_inventory(c)[1:]
        self.a.commit_returned(c, cards)
        self.a.commit_purchased(c)
        self.a.commit_refund(c)
        self.assertEqual(len(self.e.inventory("full")), 1000)
        self.assertEqual(self.e.inventory("full")[-1], self.extra)

    def test_auction_external_source_dedupes_after_claim_and_restore(self):
        self.assertTrue(self.a.enqueue_returned("one", "source1", (self.extra,)))
        self.assertFalse(self.a.enqueue_returned("one", "source1", (self.extra,)))
        with self.assertRaises(EconomyError):
            self.a.enqueue_returned("two", "source1", (self.extra,))
        with self.assertRaises(EconomyError):
            self.a.enqueue_returned("one", "source1", ())
        self.collect("one")
        self.restart()
        self.assertFalse(self.a.enqueue_returned("one", "source1", (self.extra,)))
        self.assertEqual(self.a.pending("one"), ())
        self.a.enqueue_returned("one", "source2", (self.extra,) * 30)
        self.a.enqueue_returned("one", "source3", (self.extra,) * 2)
        self.collect("one")
        self.assertEqual(len(self.a.pending("one")), 2)
        self.collect("one")
        self.assertEqual(len(self.e.inventory("one")), 43)

    def test_joint_snapshot_after_atomic_recovery_cannot_double_issue(self):
        self.stage()
        self.restart()
        self.r.recover_all()
        self.restart()
        self.assertEqual(self.r.recover_all(), 0)
        self.assertEqual(len(self.a.pending("one")), 1)
        self.collect("one")
        self.restart()
        self.assertEqual(self.r.recover_all(), 0)
        self.assertEqual(len(self.e.inventory("one")), 11)

    def test_two_accounts_stage_accept_and_recover_independently(self):
        one = self.stage("one", seat=0)
        two = self.stage("two", seat=1, rid="table1.round1")
        self.r.commit("one")
        self.r.recover("two")
        self.e.release_match(two)
        self.assertEqual(len(self.e.inventory("one")), 11)
        self.assertEqual(len(self.e.inventory("two")), 10)
        self.collect("two")
        self.assertEqual(len(self.e.inventory("two")), 11)

    def test_empty_extras_recovery_creates_no_empty_auction_marker(self):
        token = self.stage(extras=())
        self.assertTrue(self.r.recover("one"))
        self.assertEqual(self.a.pending("one"), ())
        self.e.release_match(token)
        self.restart()
        self.assertEqual(self.a.pending("one"), ())

    def test_released_result_accepts_only_late_decks_on_unchanged_idle_account(self):
        self.stage()
        token = self.r.commit("one")
        with self.assertRaises(ResultRewardError):
            self.r.release_context("one")
        self.e.release_match(token)
        self.assertTrue(self.r.release_context("one"))
        chosen = replace(self.extra, deck=9)
        self.r.handle("one", self.edit("Decks", (chosen,), name=b"Late"))
        self.assertEqual(self.e.inventory("one")[-1], chosen)
        self.assertEqual(dict(self.e.account_view("one").deck_names)[1], b"Late")
        self.assertIsNone(self.r.reservation("one"))
        self.assertIsNone(self.r.commit("one"))
        with self.assertRaises(ResultRewardError):
            self.r.handle("one", self.edit("Erase", (self.extra,)))
        new = self.e.reserve_match("one")
        with self.assertRaises(ResultRewardError):
            self.r.handle("one", self.edit("Decks"))
        self.e.release_match(new)
        with self.assertRaises(ResultRewardError):
            self.r.handle("one", self.edit("Decks"))

    def test_released_context_invalidates_on_an_intervening_inventory_mutation(self):
        self.stage()
        token = self.r.commit("one")
        self.e.release_match(token)
        self.r.release_context("one")
        t = self.e.reserve_auction("one")
        self.e.commit_auction(t, debit=1)
        with self.assertRaises(ResultRewardError):
            self.r.handle("one", self.edit("Decks", name=b"Too late"))

    def test_money_only_capability_refresh_keeps_late_decks_and_refund_release(self):
        self.stage()
        first = self.r.commit("one")
        second = self.e.commit_match_money([(first, 200, 0)])["one"]
        self.assertTrue(self.r.refresh_reservation("one", second))
        self.assertIs(self.r.reservation("one"), second)
        chosen = replace(self.extra, deck=9)
        self.r.handle("one", self.edit("Decks", (chosen,), name=b"After stake"))
        self.assertEqual(self.e.inventory("one")[-1], chosen)
        second = self.r.reservation("one")
        third = self.e.commit_match_money([(second, 0, 200)])["one"]
        self.assertTrue(self.r.refresh_reservation("one", third))
        self.e.release_match(third)
        self.assertTrue(self.r.release_context("one"))
        self.r.handle("one", self.edit("Decks", name=b"After refund"))
        self.assertEqual(dict(self.e.account_view("one").deck_names)[1], b"After refund")

    def test_refresh_cannot_adopt_inventory_profile_or_account_mutation(self):
        self.stage()
        first = self.r.commit("one")
        second = self.e.commit_match_rewards(first, (), cards=self.e.inventory("one")[:-1])
        before = self.r.reservation("one")
        with self.assertRaises(ResultRewardError):
            self.r.refresh_reservation("one", second)
        self.assertIs(self.r.reservation("one"), before)
        other = self.e.reserve_match("two")
        with self.assertRaises(ResultRewardError):
            self.r.refresh_reservation("one", other)
        self.e.release_match(other)
        self.r.forget("one")
        self.e.release_match(second)
        self.stage(rid="next")
        first = self.r.commit("one")
        options = dict(
            Ar=1, Cu=0, Vi=0, Se=3, Bgm=3, Per=1, Ran=0, CMD=1, CAD=1, CL=8, CT=60, HNSS=1
        )
        self.e.save_options("one", options)
        second = self.e.commit_match_money([(first, 200, 0)])["one"]
        with self.assertRaises(ResultRewardError):
            self.r.refresh_reservation("one", second)
        self.assertIs(self.r.reservation("one"), first)

    def test_refresh_never_revives_released_context_or_changes_pending_grant(self):
        token = self.stage()
        with self.assertRaises(ResultRewardError):
            self.r.refresh_reservation("one", token)
        token = self.r.commit("one")
        self.e.release_match(token)
        self.r.release_context("one")
        token = self.e.reserve_match("one")
        with self.assertRaises(ResultRewardError):
            self.r.refresh_reservation("one", token)
        self.r.forget("one")
        self.assertFalse(self.r.refresh_reservation("one", token))

    def test_stale_match_capability_and_generated_inventory_forgery_rejected(self):
        token = self.stage()
        before = self.e.snapshot()
        with self.assertRaises(EconomyError):
            self.e.commit_match_rewards(token, (), cards=list(token.account.cards) + [self.extra])
        with self.assertRaises(EconomyError):
            self.e.commit_match_rewards(token, (replace(self.extra, deck=2),))
        self.assertEqual(self.e.snapshot(), before)
        self.r.commit("one")
        with self.assertRaises(EconomyError):
            self.e.commit_match_rewards(token, (self.extra,))

    def test_corrupt_snapshots_rejected_and_legacy_absence_supported(self):
        self.stage()
        good = self.r.snapshot()
        for mutate in (
            lambda d: d["pending"][0].update(seat=3),
            lambda d: d["known"].clear(),
            lambda d: d["pending"][0]["extras"][0].__setitem__(6, 0),
            lambda d: d["pending"].append(d["pending"][0]),
        ):
            data = json.loads(json.dumps(good))
            mutate(data)
            with self.assertRaises(EconomyError):
                ResultRewardService.restore(self.e, self.a, data)
        self.assertEqual(ResultRewardService.restore(self.e, self.a).recover_all(), 0)
        with self.assertRaises(ResultRewardError):
            self.r.forget("one")


if __name__ == "__main__":
    unittest.main()
