import copy
import json
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path

from tests import FIXTURES
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.models import MAX_MONEY
from tmserver.economy.service import EconomyService
from tmserver.cards.model import Card
from tmserver.cards.catalog import Catalog
from tmserver.economy.errors import EconomyError
from tmserver.economy.match_ledger import (
    MatchEconomy,
    MatchEconomyError,
    next_stake,
    offline_payout,
)
from tmserver.game.prizes import PrizeAward, PrizeCard, PrizeLedger
from tmserver.economy.result_rewards import ResultRewardService


class MatchEconomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.e = EconomyService(seed=73, catalog=self.catalog)
        for s, key in enumerate(("a", "b", "c")):
            self.e.provision(key, nn=s + 1, player_id=s + 1, name=key.encode(), balance=1000)
        self.m = MatchEconomy(self.e)
        self.a = AuctionEconomy(self.e)
        self.r = ResultRewardService(self.e, self.a)
        self.tokens = {}
        self.hands = {}

    def begin(self, n=2, stake=100, mode="versus", seats=None, rid="r1"):
        seats = range(n) if seats is None else seats
        self.tokens = {s: self.e.reserve_match(("a", "b", "c")[s]) for s in seats}
        self.hands = {s: self.e.inventory(("a", "b", "c")[s])[:5] for s in range(n)}
        self.tokens = self.m.begin(rid, self.tokens, stake, mode=mode, player_count=n)
        return self.tokens

    def result(self, scores=(7, 3), cbc=None, rid="r1"):
        self.tokens, pay = self.m.record_result(
            rid, self.tokens, scores=scores, ratings=[300] * len(scores), cbc=cbc
        )
        return pay

    def participants(self):
        return {s: (self.tokens.get(s), hand) for s, hand in self.hands.items()}

    def award(self, winner=0, loser=1, slot=0):
        source = self.hands[loser][slot]
        return PrizeAward(winner, loser, slot, slot, source, replace(source, deck=255))

    def snapshot(self):
        return dict(
            economy=self.e.snapshot(),
            match=self.m.snapshot(),
            auction=self.a.snapshot(),
            rewards=self.r.snapshot(),
        )

    def restart(self):
        d = json.loads(json.dumps(self.snapshot()))
        self.e = EconomyService.restore(d["economy"], catalog=self.catalog)
        self.m = MatchEconomy.restore(self.e, d["match"])
        self.a = AuctionEconomy.restore(self.e, d["auction"])
        self.r = ResultRewardService.restore(self.e, self.a, d["rewards"])

    def test_begin_is_atomic_and_exactly_once_across_current_capability_retry(self):
        self.begin()
        self.assertEqual([self.e.balance(k) for k in ("a", "b")], [900, 900])
        before = self.snapshot()
        self.tokens = self.m.begin("r1", self.tokens, 100)
        self.assertEqual(self.snapshot(), before)
        for kwargs in ({"stake": 101}, {"mode": "computer"}, {"player_count": 3}):
            args = dict(stake=100)
            args.update(kwargs)
            with self.assertRaises(EconomyError):
                self.m.begin("r1", self.tokens, **args)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(EconomyError):
            self.m.begin("new", self.tokens, 100)

    def test_insufficient_third_balance_or_stale_token_rejects_entire_debit(self):
        self.e.provision("poor", nn=4, player_id=4, name=b"poor", balance=10)
        tokens = {s: self.e.reserve_match(k) for s, k in enumerate(("a", "b", "poor"))}
        before = self.snapshot()
        with self.assertRaises(EconomyError):
            self.m.begin("r1", tokens, 100, player_count=3)
        self.assertEqual(self.snapshot(), before)
        self.e.release_match(tokens[2])
        tokens[2] = self.e.reserve_match("c")
        self.e.release_match(tokens[1])
        with self.assertRaises(EconomyError):
            self.m.begin("r1", tokens, 100, player_count=3)
        self.assertEqual(self.snapshot(), before)

    def test_open_stake_refunds_once_after_restart_and_releases_all_accounts(self):
        self.begin(3, 200)
        self.restart()
        self.assertEqual(self.m.recover_all(self.a), 1)
        self.assertEqual([self.e.balance(k) for k in ("a", "b", "c")], [1000] * 3)
        self.assertFalse(any(self.e.is_busy(k) for k in ("a", "b", "c")))
        before = self.snapshot()
        self.assertEqual(self.m.recover_all(self.a), 0)
        self.assertEqual(self.snapshot(), before)
        self.restart()
        self.assertEqual(self.m.recover_all(self.a), 0)

    def test_versus_payout_is_zero_sum_with_tie_remainders_and_cap_refunds(self):
        self.begin(3, 101)
        pay = self.result((6, 6, 3))
        self.assertEqual(pay, {0: 152, 1: 151, 2: 0})
        self.assertEqual(sum(self.e.balance(k) for k in ("a", "b", "c")), 3000)
        before = self.snapshot()
        self.assertEqual(self.result((6, 6, 3)), pay)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(EconomyError):
            self.result((7, 5, 3))
        self.assertEqual(self.snapshot(), before)

    def test_pot_overflow_is_refunded_without_destroying_money(self):
        self.e.provision("rich", nn=4, player_id=4, name=b"rich", balance=MAX_MONEY)
        tokens = {0: self.e.reserve_match("rich"), 1: self.e.reserve_match("b")}
        tokens = self.m.begin("rich_round", tokens, 100)
        self.assertEqual(self.m.quote("rich_round", tokens, (10, 0), (300, 300)), {0: 100, 1: 100})
        tokens, pay = self.m.record_result("rich_round", tokens, scores=(10, 0), ratings=(300, 300))
        self.assertEqual(self.e.balance("rich"), MAX_MONEY)
        self.assertEqual(self.e.balance("b"), 1000)

    def test_draw_refunds_all_human_stakes_and_computer_uses_original_payout(self):
        self.begin()
        self.assertEqual(self.result((5, 5)), {0: 100, 1: 100})
        self.tokens = self.m.abort("r1", self.tokens, self.a)
        for t in self.tokens.values():
            self.e.release_match(t)
        self.begin(stake=100, mode="computer", seats=(0,), rid="cpu")
        self.assertEqual(self.result((10, 0), rid="cpu"), {0: 900})
        self.assertEqual(self.e.balance("a"), 1800)

    def test_paid_result_abort_keeps_money_and_recovers_only_cbc_before_choices(self):
        self.begin()
        cbc = replace(self.hands[0][0], deck=255)
        self.result(cbc={0: (cbc,)})
        inventories = {k: self.e.inventory(k) for k in ("a", "b")}
        self.restart()
        self.assertEqual(self.m.recover_all(self.a), 1)
        self.assertEqual([self.e.balance(k) for k in ("a", "b")], [1100, 900])
        for k in inventories:
            self.assertEqual(self.e.inventory(k), inventories[k])
        self.assertEqual(sum(p.card is not None for p in self.a.pending("a")), 1)
        self.assertEqual(len(self.a.pending("b")), 0)
        before = self.snapshot()
        self.m.recover_all(self.a)
        self.assertEqual(self.snapshot(), before)

    def test_settlement_validates_sources_and_stages_awards_with_cbc_in_native_order(self):
        self.begin()
        cbc = replace(self.hands[0][1], deck=255)
        self.result(cbc={0: (cbc,)})
        award = self.award()
        original = {k: self.e.inventory(k) for k in ("a", "b")}
        self.tokens, extras = self.m.settle_cards("r1", self.participants(), (award,))
        self.assertEqual(extras[0], (cbc, award.card))
        self.assertEqual(extras[1], ())
        self.assertEqual(self.e.inventory("a"), original["a"])
        self.assertEqual(len(self.e.inventory("b")), 9)
        before = self.snapshot()
        self.tokens, again = self.m.settle_cards("r1", self.participants(), (award,))
        self.assertEqual(again, extras)
        self.assertEqual(self.snapshot(), before)
        self.m.handoff("r1", self.tokens, self.r)
        for s in self.tokens:
            self.tokens[s] = self.r.commit(("a", "b")[s])
        self.assertEqual(len(self.e.inventory("a")), 12)
        self.assertEqual(len(self.e.inventory("b")), 9)
        self.assertEqual(
            Counter(self.e.inventory("a") + self.e.inventory("b")),
            Counter(original["a"] + original["b"] + (cbc,)),
        )
        self.restart()
        self.assertEqual(self.m.recover_all(self.a), 0)

    def test_forged_third_transfer_or_repeated_slot_changes_nothing(self):
        self.begin(3)
        self.result((8, 4, 3))
        before = self.snapshot()
        good = self.award()
        second = self.award(loser=2)
        for transfers in (
            (good, good),
            (good, replace(second, card=replace(second.card, arrows=second.card.arrows ^ 255))),
            (good, replace(second, source_card=self.hands[2][1])),
            (replace(good, winner=1),),
        ):
            with self.assertRaises(EconomyError):
                self.m.settle_cards("r1", self.participants(), transfers)
            self.assertEqual(self.snapshot(), before)
        for s, t in self.tokens.items():
            self.assertTrue(self.e.validate_match_selection(t, self.hands[s]))

    def test_duplicate_equal_cards_remove_exact_multiplicity_and_clear_loser_deck(self):
        card = self.e.inventory("a")[0]
        self.e.provision(
            "duplicates", nn=4, player_id=4, name=b"dup", cards=[replace(card, deck=7)] + [card] * 9
        )
        self.tokens = {0: self.e.reserve_match("a"), 1: self.e.reserve_match("duplicates")}
        self.hands = {0: self.e.inventory("a")[:5], 1: self.e.inventory("duplicates")[:5]}
        self.tokens = self.m.begin("r1", self.tokens, 0)
        self.result((10, 0))
        awards = tuple(self.award(slot=s) for s in range(5))
        self.tokens, extras = self.m.settle_cards("r1", self.participants(), awards)
        self.assertEqual(len(self.e.inventory("duplicates")), 5)
        self.assertTrue(all(c.deck == 255 for c in extras[0]))
        self.assertTrue(all(c.deck == 255 for c in self.e.inventory("duplicates")))

    def test_capacity_is_deferred_to_result_overflow_without_losing_award(self):
        card = self.e.inventory("a")[0]
        self.e.provision("full", nn=4, player_id=4, name=b"full", cards=[card] * 1000)
        self.tokens = {0: self.e.reserve_match("full"), 1: self.e.reserve_match("b")}
        self.hands = {0: self.e.inventory("full")[:5], 1: self.e.inventory("b")[:5]}
        self.tokens = self.m.begin("r1", self.tokens, 0)
        self.result()
        self.tokens, extras = self.m.settle_cards("r1", self.participants(), (self.award(),))
        self.m.handoff("r1", self.tokens, self.r)
        before = self.snapshot()
        with self.assertRaises(EconomyError):
            self.r.commit("full")
        self.assertEqual(self.snapshot(), before)
        self.restart()
        self.r.recover_all()
        self.m.recover_all(self.a)
        self.assertEqual(len(self.e.inventory("full")), 1000)
        self.assertEqual(sum(p.card is not None for p in self.a.pending("full")), 1)

    def test_crash_after_loss_before_handoff_recovers_award_exactly_once(self):
        self.begin()
        self.result()
        self.tokens, _ = self.m.settle_cards("r1", self.participants(), (self.award(),))
        self.restart()
        self.m.recover_all(self.a)
        self.assertEqual(len(self.e.inventory("b")), 9)
        self.assertEqual(sum(p.card is not None for p in self.a.pending("a")), 1)
        self.restart()
        self.m.recover_all(self.a)
        self.assertEqual(sum(p.card is not None for p in self.a.pending("a")), 1)

    def test_handoff_blocks_conflicting_context_before_staging_either_account(self):
        self.begin()
        self.result()
        self.tokens, _ = self.m.settle_cards("r1", self.participants(), (self.award(),))
        self.r.stage("b", self.tokens[1], "different", 1, ())
        before = self.snapshot()
        with self.assertRaises(EconomyError):
            self.m.handoff("r1", self.tokens, self.r)
        self.assertEqual(self.snapshot(), before)

    def test_three_player_perfect_ledger_conserves_every_owned_card(self):
        self.begin(3)
        self.result((15, 0, 0))
        ledger = PrizeLedger(
            (15, 0, 0),
            tuple(tuple(PrizeCard(i, c, 0) for i, c in enumerate(self.hands[s])) for s in range(3)),
        )
        self.assertTrue(ledger.complete)
        self.assertEqual(len(ledger.settlement().transfers), 10)
        before = Counter(c for k in ("a", "b", "c") for c in self.e.inventory(k))
        self.tokens, extras = self.m.settle_cards(
            "r1", self.participants(), ledger.settlement().transfers
        )
        self.m.handoff("r1", self.tokens, self.r)
        for s in self.tokens:
            self.tokens[s] = self.r.commit(("a", "b", "c")[s])
        self.assertEqual([len(self.e.inventory(k)) for k in ("a", "b", "c")], [20, 5, 5])
        self.assertEqual(Counter(c for k in ("a", "b", "c") for c in self.e.inventory(k)), before)

    def test_computer_loss_and_win_have_explicit_trusted_hand_provenance(self):
        self.begin(mode="computer", seats=(0,))
        self.result((3, 7))
        self.tokens, extras = self.m.settle_cards("r1", self.participants(), (self.award(1, 0),))
        self.assertEqual(extras, {0: ()})
        self.assertEqual(len(self.e.inventory("a")), 9)
        self.m.handoff("r1", self.tokens, self.r)
        self.tokens[0] = self.r.commit("a")
        self.e.release_match(self.tokens[0])
        self.r.forget("a")
        self.begin(mode="computer", seats=(0,), rid="cpu2")
        self.result((7, 3), rid="cpu2")
        self.tokens, extras = self.m.settle_cards("cpu2", self.participants(), (self.award(0, 1),))
        self.assertEqual(len(extras[0]), 1)

    def test_money_primitive_validates_every_account_and_old_tokens_cannot_replay(self):
        tokens = [self.e.reserve_match(k) for k in ("a", "b", "c")]
        before = self.snapshot()
        for amount in (-1, 1001, True):
            with self.assertRaises(EconomyError):
                self.e.commit_match_money([(tokens[0], 100, 0), (tokens[2], amount, 0)])
            self.assertEqual(self.snapshot(), before)
        fresh = self.e.commit_match_money([(tokens[0], 100, 0), (tokens[1], 0, 100)])
        self.assertEqual(self.e.balance("a"), 900)
        self.assertEqual(self.e.balance("b"), 1100)
        with self.assertRaises(EconomyError):
            self.e.commit_match_money([(tokens[0], 0, 100)])
        self.assertTrue(self.e.match_account(fresh["a"]))

    def test_second_round_uses_new_id_and_double_stake_only_on_explicit_begin(self):
        self.begin()
        self.result((5, 5))
        self.tokens, _ = self.m.settle_cards("r1", self.participants(), ())
        self.m.handoff("r1", self.tokens, self.r)
        for s in self.tokens:
            self.tokens[s] = self.r.commit(("a", "b")[s])
        before = self.snapshot()
        stake = next_stake(100, 1)
        self.assertEqual(self.snapshot(), before)  # Merely opening Continue charges nothing.
        self.tokens = self.m.begin("r2", self.tokens, stake)
        self.assertEqual([self.e.balance(k) for k in ("a", "b")], [800, 800])
        self.tokens = self.m.abort("r2", self.tokens, self.a)
        self.assertEqual([self.e.balance(k) for k in ("a", "b")], [1000, 1000])
        self.assertEqual(self.m.status("r1")["money"], "paid")
        self.assertEqual(self.m.status("r2")["money"], "refunded")

    def test_combined_reward_capacity_rejects_before_losing_any_card(self):
        self.begin()
        cbc = (replace(self.hands[0][0], deck=255),) * 30
        self.result(cbc={0: cbc})
        before = self.snapshot()
        with self.assertRaises(EconomyError):
            self.m.settle_cards("r1", self.participants(), (self.award(),))
        self.assertEqual(self.snapshot(), before)

    def test_card_like_full_serializers_do_not_change_settlement_identity(self):
        from tmserver.game.models import Card as WireCard

        self.begin()
        self.result()
        award = self.award()
        wire = lambda c: WireCard(*(getattr(c, k) for k in Card.__dataclass_fields__))
        participant = {
            s: (t, tuple(wire(c) for c in self.hands[s])) for s, t in self.tokens.items()
        }
        self.tokens, extras = self.m.settle_cards(
            "r1",
            participant,
            (replace(award, source_card=wire(award.source_card), card=wire(award.card)),),
        )
        before = self.snapshot()
        self.tokens, again = self.m.settle_cards("r1", self.participants(), (award,))
        self.assertEqual(again, extras)
        self.assertEqual(self.snapshot(), before)

    def test_restore_rejects_cross_account_or_phase_corruption(self):
        self.begin()
        self.result(cbc={0: (self.hands[0][0],)})
        snap = self.m.snapshot()
        for changes in (
            {"money": "escrow"},
            {"seats": {"0": "absent", "1": "b"}},
            {"payouts": {"0": 1}},
            {"closed": True},
            {"handed": [0]},
            {"transfer_digest": "x"},
            {"cbc": {}},
        ):
            bad = copy.deepcopy(snap)
            bad["rounds"][0].update(changes)
            with self.assertRaises(EconomyError):
                MatchEconomy.restore(self.e, bad)
        bad = copy.deepcopy(snap)
        bad["rounds"].append(copy.deepcopy(bad["rounds"][0]))
        bad["rounds"][1]["id"] = "other"
        with self.assertRaises(EconomyError):
            MatchEconomy.restore(self.e, bad)

    def test_original_offline_payout_and_double_up_fixtures(self):
        data = json.loads(
            (
                FIXTURES / "protocol/wager_transfer_vectors.json"
            ).read_text()
        )
        count = 0
        for row in data["offline_payouts"]:
            self.assertEqual(
                offline_payout(row["scores"], row["ratings"], row["wager"]) & 0xFFFFFFFF,
                row["reward_u32"],
            )
            count += 1
        self.assertEqual(count, 588)
        for row in data["stakes"]:
            if row["kind"] == "continue_wager" and row["wager"] <= 2000:
                self.assertEqual(next_stake(row["wager"], row["double"]), row["after"])

    def test_cpu_card_prizes_do_not_imply_money_capability(self):
        tokens = {0: self.e.reserve_match("a")}
        before = self.e.snapshot()
        with self.assertRaises(EconomyError):
            self.m.begin("bad", tokens, 1, mode="computer", money_enabled=False)
        self.assertEqual(self.e.snapshot(), before)
        tokens = self.m.begin("cards", tokens, 0, mode="computer", money_enabled=False)
        self.assertEqual(self.m.quote("cards", tokens, (10, 0), (300, 300)), {0: 0})
        tokens, payouts = self.m.record_result("cards", tokens, scores=(10, 0), ratings=(300, 300))
        self.assertEqual(payouts, {0: 0})
        self.assertEqual(self.e.balance("a"), 1000)
        restored = MatchEconomy.restore(self.e, json.loads(json.dumps(self.m.snapshot())))
        self.assertFalse(restored.status("cards")["money_enabled"])
        self.assertEqual(restored.quote("cards", tokens, (10, 0), (300, 300)), {0: 0})
        bad = copy.deepcopy(self.m.snapshot())
        bad["rounds"][0]["payouts"]["0"] = 1
        with self.assertRaises(EconomyError):
            MatchEconomy.restore(self.e, bad)

    def test_legacy_round_without_money_capability_retains_prior_policy(self):
        self.begin(mode="computer", seats=(0,), stake=0)
        snapshot = self.m.snapshot()
        del snapshot["rounds"][0]["money_enabled"]
        restored = MatchEconomy.restore(self.e, snapshot)
        self.assertTrue(restored.status("r1")["money_enabled"])
        self.assertEqual(restored.quote("r1", self.tokens, (10, 0), (300, 300)), {0: 450})

    def test_too_few_owned_cards_rejects_all_round_admission_before_debit(self):
        self.e.provision("short", nn=4, player_id=4, name=b"short", cards=self.e.inventory("a")[:4])
        tokens = {0: self.e.reserve_match("a"), 1: self.e.reserve_match("short")}
        before = self.e.snapshot()
        with self.assertRaises(EconomyError):
            self.m.begin("unplayable", tokens, 100)
        self.assertEqual(self.e.snapshot(), before)
        self.assertFalse(self.m.snapshot()["rounds"])
        with self.assertRaises(EconomyError):
            self.m.begin("cpu", {0: tokens[1]}, 0, mode="computer", money_enabled=False)
        self.assertEqual(self.e.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
