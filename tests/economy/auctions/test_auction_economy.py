"""Escrow conservation, collection commit barriers and original-x86 comparisons."""

import json
import struct
import sys
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path

from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.auctions.models import AuctionError
from tmserver.economy.models import MAX_MONEY
from tmserver.economy.service import EconomyService
from tmserver.cards.model import Card
from tmserver.cards.catalog import Catalog
from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import named


class AuctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.e = EconomyService(seed=32, catalog=self.catalog)
        for i, key in enumerate(("seller", "buyer", "third"), 1):
            self.e.provision(key, nn=i, player_id=i, name=key.encode())
        self.a = AuctionEconomy(self.e)

    def exhibit(self, key="seller", **kwargs):
        args = dict(opening=100, increment=20, hours=1, relists=0, now=100)
        args.update(kwargs)
        return self.a.exhibit(key, self.e.inventory(key)[0], **args)

    def sold(self, **kwargs):
        item = self.exhibit(**kwargs)
        item = self.a.bid("buyer", item.id, 100, item.revision, now=101)
        self.a.advance(item.expires)
        return self.a.listing(item.id)

    def collect(self, key):
        c = self.a.reserve_collection(key)
        paid = self.a.commit_payment(c)
        self.a.commit_returned(c)
        self.a.commit_purchased(c)
        refund = self.a.commit_refund(c)
        return paid, refund

    def state(self):
        return {"economy": self.e.snapshot(), "auction": self.a.snapshot()}

    def restart(self):
        state = json.loads(json.dumps(self.state()))
        self.e = EconomyService.restore(state["economy"], catalog=self.catalog)
        self.a = AuctionEconomy.restore(self.e, state["auction"])

    def assets(self):
        cards = Counter(
            c.reduced() for k in ("seller", "buyer", "third") for c in self.e.inventory(k)
        )
        money = sum(self.e.balance(k) for k in ("seller", "buyer", "third"))
        for v in self.a.listings():
            cards[v.card.reduced()] += 1
            money += v.amount
        for key in ("seller", "buyer", "third"):
            for p in self.a.pending(key):
                money += p.amount
                if p.card is not None:
                    cards[p.card.reduced()] += 1
        return cards, money

    def test_full_lifecycle_conserves_escrow_and_persists(self):
        assets = self.assets()
        original = self.e.inventory("seller")[0]
        item = self.exhibit()
        self.assertEqual(len(self.e.inventory("seller")), 9)
        self.assertEqual(item.card, replace(original, deck=255))
        first = self.a.bid("buyer", item.id, 100, 0, now=101)
        self.assertEqual(self.e.balance("buyer"), 9900)
        self.assertEqual(self.assets(), assets)
        second = self.a.bid("third", item.id, 140, first.revision, now=102)
        self.assertEqual(self.e.balance("third"), 9860)
        self.assertEqual(self.a.pending("buyer")[0].amount, 100)
        self.assertEqual(self.assets(), assets)
        self.restart()
        self.a.advance(second.expires)
        self.assertEqual(self.a.listing(item.id).status, "sold")
        self.assertEqual(self.a.advance(second.expires + 1), ())
        self.assertEqual(self.assets(), assets)
        self.assertEqual(self.collect("seller"), (140, 0))
        self.assertEqual(self.collect("buyer"), (0, 100))
        self.assertEqual(self.collect("third"), (0, 0))
        self.assertEqual(len(self.e.inventory("third")), 11)
        self.assertEqual(self.assets(), assets)
        self.restart()
        self.assertEqual(self.a.pending("seller"), ())
        self.assertEqual(self.collect("seller"), (0, 0))
        self.assertEqual(self.e.balance("seller"), 10140)
        self.assertEqual(self.e.balance("buyer"), 10000)

    def test_full_self_raise_is_escrowed_and_prior_bid_refunded_separately(self):
        assets = self.assets()
        item = self.exhibit()
        self.a.bid("buyer", item.id, 100, 0, now=101)
        self.a.bid("buyer", item.id, 120, 1, now=102)
        self.assertEqual(self.e.balance("buyer"), 9780)
        self.assertEqual(self.a.pending("buyer")[0].amount, 100)
        self.assertEqual(self.assets(), assets)
        self.assertEqual(self.collect("buyer"), (0, 100))
        self.assertEqual(self.e.balance("buyer"), 9880)

    def test_bad_listing_input_or_forged_card_is_atomic(self):
        card = self.e.inventory("seller")[0]
        original = self.state()
        for changes in (
            {"opening": 0},
            {"increment": 30},
            {"hours": 0},
            {"hours": 169},
            {"relists": 4},
            {"now": -1},
        ):
            args = dict(opening=100, increment=20, hours=1, relists=0, now=100)
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(EconomyError):
                self.a.exhibit("seller", card, **args)
            self.assertEqual(self.state(), original)
        with self.assertRaises(AuctionError):
            self.a.exhibit("seller", replace(card, arrows=card.arrows ^ 1), 100, 20, 1, 0, now=100)
        self.assertEqual(self.state(), original)

    def test_original_descriptor_byte_order_sale_value_and_ownership(self):
        card = self.e.inventory("seller")[0]
        ii = (
            card.model,
            self.catalog.sale_value(card),
            77,
            0,
            0,
            1,
            99,
            0,
            card.attack,
            card.physical,
            card.kind,
            card.magical,
            card.points,
            card.arrows,
            0,
            0,
        )
        for index in (1, 8, 9, 10, 11, 12, 13):
            bad = list(ii)
            bad[index] += 1
            before = self.state()
            with self.subTest(index=index), self.assertRaises(EconomyError):
                self.exhibit(item_metadata=bad)
            self.assertEqual(self.state(), before)
        item = self.exhibit(item_metadata=ii)
        self.assertEqual(item.item_metadata, ii)

    def test_limit_thirty_active_exhibits_and_five_retained_cards(self):
        card = self.e.inventory("seller")[0]
        self.e.provision("many", nn=4, player_id=4, name=b"Many", cards=[card] * 40)
        for _ in range(30):
            self.exhibit("many")
        with self.assertRaises(AuctionError):
            self.exhibit("many")
        self.assertEqual(len(self.e.inventory("many")), 10)
        for _ in range(5):
            self.exhibit()
        with self.assertRaises(AuctionError):
            self.exhibit()
        self.assertEqual(len(self.e.inventory("seller")), 5)

    def test_bid_validation_and_stale_revision_never_debits(self):
        item = self.exhibit()
        self.a.bid("buyer", item.id, 100, 0, now=101)
        state = self.state()
        for key, amount, revision, now in [
            ("seller", 200, 1, 102),
            ("third", 119, 1, 102),
            ("third", 10001, 1, 102),
            ("third", 120, 0, 102),
            ("third", 120, 1, 3700),
            ("third", 120, 1, 99),
        ]:
            with (
                self.subTest(key=key, amount=amount, revision=revision, now=now),
                self.assertRaises(EconomyError),
            ):
                self.a.bid(key, item.id, amount, revision, now=now)
            self.assertEqual(self.state(), state)
        self.assertEqual(self.a.history(item.id)[0].amount, 100)

    def test_unsold_reoffers_expire_once_even_after_large_clock_jump(self):
        assets = self.assets()
        item = self.exhibit(relists=3)
        self.assertEqual(self.a.advance(3699), ())
        first = self.a.advance(3700)[0]
        self.assertEqual(
            (first.status, first.reoffers, first.expires, first.revision), ("active", 1, 7300, 0)
        )
        final = self.a.advance(20000)[0]
        self.assertEqual((final.status, final.reoffers, final.closed_at), ("returned", 3, 14500))
        self.assertEqual(self.a.advance(30000), ())
        self.assertEqual(len(self.a.pending("seller")), 1)
        self.assertEqual(self.assets(), assets)
        self.collect("seller")
        self.assertEqual(self.assets(), assets)

    def test_reservations_exclude_shop_trade_match_and_auction_mutations(self):
        item = self.exhibit()
        c = self.a.reserve_collection("buyer")
        before = self.state()
        for call in (
            lambda: self.e.reserve_match("buyer"),
            lambda: self.e.reserve_exchange(("buyer", "third")),
            lambda: self.exhibit("buyer"),
            lambda: self.a.bid("buyer", item.id, 100, 0, now=101),
            lambda: self.e.handle("buyer", named(0xB0, 0, 0, "@ShReq=")),
        ):
            with self.assertRaises(EconomyError):
                call()
            self.assertEqual(self.state(), before)
        self.assertTrue(self.e.is_reserved("buyer"))
        self.assertTrue(self.a.release_collection(c))
        self.assertFalse(self.a.release_collection(c))
        self.assertFalse(self.e.is_reserved("buyer"))

    def test_empty_stages_and_out_of_order_or_replayed_get_are_rejected(self):
        c = self.a.reserve_collection("seller")
        with self.assertRaises(AuctionError):
            self.a.commit_returned(c)
        self.assertEqual(self.a.commit_payment(c), 0)
        with self.assertRaises(AuctionError):
            self.a.commit_payment(c)
        with self.assertRaises(AuctionError):
            self.a.commit_purchased(c)
        self.a.commit_returned(c)
        with self.assertRaises(AuctionError):
            self.a.commit_returned(c)
        self.a.commit_purchased(c)
        self.assertEqual(self.a.commit_refund(c), 0)
        with self.assertRaises(AuctionError):
            self.a.commit_refund(c)

    def test_disconnect_before_and_after_get_keeps_exact_pending(self):
        item = self.sold()
        c = self.a.reserve_collection("buyer")
        self.a.commit_payment(c)
        self.a.commit_returned(c)
        self.assertEqual(self.a.collection_inventory(c)[-1], item.card)
        self.a.release_collection(c)
        self.assertEqual(len(self.e.inventory("buyer")), 10)
        self.assertEqual(self.a.pending("buyer")[0].kind, "won")
        self.restart()
        c = self.a.reserve_collection("buyer")
        self.a.commit_payment(c)
        self.a.commit_returned(c)
        self.a.commit_purchased(c)
        self.a.release_collection(c)
        self.restart()
        self.collect("buyer")
        self.assertEqual(len(self.e.inventory("buyer")), 11)
        self.assertEqual(self.a.pending("buyer"), ())

    def test_payment_is_durable_before_send_and_does_not_replay_on_disconnect(self):
        self.sold()
        c = self.a.reserve_collection("seller")
        self.assertEqual(self.a.commit_payment(c), 100)
        self.a.release_collection(c)
        self.restart()
        self.assertEqual(self.e.balance("seller"), 10100)
        self.assertEqual(self.collect("seller"), (0, 0))

    def test_balance_cap_retains_unpaid_residual_instead_of_destroying_escrow(self):
        card = self.e.inventory("seller")[0]
        self.e.provision(
            "rich", nn=4, player_id=4, name=b"Rich", balance=MAX_MONEY - 25, cards=[card] * 6
        )
        item = self.exhibit("rich")
        self.a.bid("buyer", item.id, 100, 0, now=101)
        self.a.advance(3700)
        self.assertEqual(self.collect("rich"), (25, 0))
        self.assertEqual(self.e.balance("rich"), MAX_MONEY)
        self.assertEqual(self.a.pending("rich")[0].amount, 75)
        self.assertEqual(self.collect("rich"), (0, 0))
        self.assertEqual(self.a.pending("rich")[0].amount, 75)

    def test_overflow_requires_explicit_discard_and_cannot_forge_accepted_cards(self):
        card = self.e.inventory("buyer")[0]
        self.e.provision("full", nn=4, player_id=4, name=b"Full", cards=[card] * 1000)
        item = self.exhibit()
        self.a.bid("full", item.id, 100, 0, now=101)
        self.a.advance(3700)
        c = self.a.reserve_collection("full")
        self.a.commit_payment(c)
        self.a.commit_returned(c)
        before = self.state()
        with self.assertRaises(EconomyError):
            self.a.commit_purchased(c)
        with self.assertRaises(EconomyError):
            self.a.commit_purchased(c, [replace(card, arrows=card.arrows ^ 1)])
        self.assertEqual(self.state(), before)
        final = self.a.collection_inventory(c)[1:]
        self.a.commit_purchased(c, final)
        self.a.commit_refund(c)
        self.assertEqual(len(self.e.inventory("full")), 1000)
        self.assertEqual(self.e.inventory("full")[-1], item.card)
        self.assertEqual(self.a.pending("full"), ())

    def test_card_stage_deck_edits_and_names_preserve_stats_and_options(self):
        item = self.sold()
        c = self.a.reserve_collection("buyer")
        self.a.commit_payment(c)
        self.a.commit_returned(c)
        self.e.save_options(
            "buyer",
            dict(Ar=1, Cu=0, Vi=0, Se=3, Bgm=3, Per=1, Ran=0, CMD=1, CAD=1, CL=8, CT=60, HNSS=1),
        )
        cards = list(self.a.collection_inventory(c))
        cards[-1] = replace(cards[-1], deck=9)
        self.a.commit_purchased(c, cards, {1: b"Auction", 2: b"Second"})
        self.a.commit_refund(c)
        self.assertEqual(self.e.inventory("buyer")[-1], replace(item.card, deck=9))
        self.assertEqual(self.e.account_view("buyer").deck_names, ((1, b"Auction"), (2, b"Second")))
        self.assertEqual(self.e.profile_bytes("buyer")[0x126], 1)

    def test_collection_freezes_pending_ids_and_delivers_at_most_thirty_cards(self):
        card = self.e.inventory("seller")[0]
        self.e.provision("many", nn=4, player_id=4, name=b"Many", cards=[card] * 50)
        for _ in range(35):
            item = self.exhibit("many")
            self.a.advance(item.expires)
        c = self.a.reserve_collection("many")
        self.assertEqual(len(c.returned), 30)
        self.a.commit_payment(c)
        # Another seller may settle while this collection reserves its account.
        item = self.exhibit()
        self.a.bid("buyer", item.id, 100, 0, now=101)
        self.a.advance(3700)
        self.a.commit_returned(c)
        self.a.commit_purchased(c)
        self.a.commit_refund(c)
        self.assertEqual(len(self.e.inventory("many")), 45)
        self.assertEqual(len(self.a.pending("many")), 5)
        self.collect("many")
        self.assertEqual(len(self.e.inventory("many")), 50)
        self.assertTrue(self.a.pending("buyer"))

    def test_new_refund_during_collection_is_left_for_next_session(self):
        item = self.exhibit()
        self.a.bid("buyer", item.id, 100, 0, now=101)
        c = self.a.reserve_collection("buyer")
        self.a.commit_payment(c)
        self.a.bid("third", item.id, 120, 1, now=102)
        self.a.commit_returned(c)
        self.a.commit_purchased(c)
        self.assertEqual(self.a.commit_refund(c), 0)
        self.assertEqual(self.a.pending("buyer")[0].amount, 100)
        self.assertEqual(self.collect("buyer"), (0, 100))

    def test_epoch_zero_unsold_reoffer_restore(self):
        item = self.exhibit(now=0, relists=1)
        self.a.advance(3600)
        self.restart()
        self.assertEqual(
            (self.a.listing(item.id).revision, self.a.listing(item.id).reoffers), (0, 1)
        )
        self.a.bid("buyer", item.id, 100, 0, now=3601)
        self.restart()
        self.assertEqual(self.a.listing(item.id).revision, 1)

    def test_collection_snapshot_does_not_consume_staged_cards_on_restart(self):
        self.sold()
        c = self.a.reserve_collection("buyer")
        self.a.commit_payment(c)
        self.a.commit_returned(c)
        self.restart()
        self.assertFalse(self.e.is_reserved("buyer"))
        self.assertEqual(len(self.e.inventory("buyer")), 10)
        self.collect("buyer")
        self.assertEqual(len(self.e.inventory("buyer")), 11)

    def test_restore_rejects_corrupted_ledger_and_accepts_legacy_absence(self):
        self.sold()
        good = self.a.snapshot()
        for mutate in (
            lambda s: s["listings"][0].update(amount=99),
            lambda s: s["listings"][0].update(seller={**s["listings"][0]["seller"], "nn": 999}),
            lambda s: s["pending"].append(s["pending"][0]),
            lambda s: s["history"][0].update(amount=120),
            lambda s: s.update(next_id=1),
        ):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            with self.assertRaises(EconomyError):
                AuctionEconomy.restore(self.e, bad)
        self.assertEqual(AuctionEconomy.restore(self.e).snapshot()["listings"], [])

    def test_public_economy_boundary_rejects_forgery_money_overflow_and_stale_tokens(self):
        c = self.e.inventory("seller")[0]
        t = self.e.reserve_auction("seller")
        before = self.e.snapshot()
        for kw in (
            {"remove": (c, c)},
            {"debit": 10001},
            {"credit": MAX_MONEY},
            {"cards": self.e.inventory("seller") + (c,)},
            {"cards": self.e.inventory("seller")[1:]},
        ):
            with self.subTest(kw=kw), self.assertRaises(EconomyError):
                self.e.commit_auction(t, **kw)
            self.assertEqual(self.e.snapshot(), before)
        t2 = self.e.commit_auction(t, debit=10, keep_reserved=True)
        with self.assertRaises(EconomyError):
            self.e.commit_auction(t, debit=10)
        self.assertFalse(self.e.release_auction(t))
        self.assertTrue(self.e.release_auction(t2))


if __name__ == "__main__":
    unittest.main()
