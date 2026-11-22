"""Claim failures cannot publish money, cards, receipts or working editors."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.economy.auctions.test_auction_service import bid_request, exhibit_request
from tests.economy.entries import collection_request, prize_entry
from tests.events.test_event_rooms import config
from tmserver.economy.claim_transactions import (
    auction_transaction,
    event_transaction,
    prize_transaction,
)
from tmserver.economy.packets import named, parse_named
from tmserver.events.matches import EventRoundOutcome
from tmserver.network.server import Server


class ClaimFixture:
    def prepare(self, store=None):
        self.now = 200
        self.config = config(card_service_enabled=True)
        self.server = Server(
            store=store,
            account_count=3,
            event_configs=(self.config,),
            event_clock=lambda: self.now,
            auction_clock=lambda: self.now,
        )
        self.keys = [self.server.ensure_account(nick) for nick in self.server.accounts]
        self.key, self.buyer, self.other = self.keys

    def auction(self, key, payload):
        with auction_transaction(self.server, (key,)) as staged:
            return staged.handle(key, payload)

    def sell(self):
        self.auction(self.key, exhibit_request(self.server.economy, self.key))
        self.auction(self.buyer, bid_request(self.server.economy, self.buyer, 1, 100, 0))
        self.now = self.server.auction_ledger.listing(1).expires
        self.server.advance_auctions()

    def collect(self, key):
        replies = self.auction(key, collection_request(self.server.economy, key))
        return int(parse_named(replies[0])[3]["Init"]["N"])

    def auction_get(self, key, number):
        return self.auction(key, named(0xB2, 0x15, number, f"@Get=/No={number}"))

    def prize(self, payload):
        with prize_transaction(self.server, self.key) as staged:
            return staged.handle(self.key, payload)

    def prize_award(self):
        with prize_transaction(self.server, self.key) as staged:
            staged.grant_award(self.key, "award", top30=5000)

    def enter_prize(self):
        replies = self.prize(prize_entry(self.server.economy, self.key))
        return int(parse_named(replies[1])[3]["ExcInit"]["N"])

    def event_grant(self):
        card = replace(self.server.economy.inventory(self.key)[0], deck=255)
        with event_transaction(self.server, self.keys[:2]) as staged:
            for key in self.keys[:2]:
                staged.events.admit_room(key, "festival")
            staged.cards.grant(self.key, "festival", "earned", cards=(card,) * 12, money=250)

    def event_entry(self):
        v = self.server.economy.account_view(self.key)
        return named(0x90, 0, 0, f"@CardReq=/NN={v.nn:016X}/HID=0/Dm=1/Vol=1/CN={v.name.hex()}")

    def event(self, payload):
        with event_transaction(self.server, (self.key,)) as staged:
            return staged.cards.handle(self.key, payload, event_id="festival")

    def enter_event(self):
        return int(parse_named(self.event(self.event_entry())[1])[3]["EcmInit"]["N"])

    def record_result(self):
        ids = tuple(self.server.economy.account_view(key).player_id for key in self.keys[:2])
        with event_transaction(self.server, self.keys[:2]) as staged:
            staged.results.record(
                staged.events,
                "festival",
                "round:1",
                EventRoundOutcome(ids, (7, 3), (1, 3), False),
                self.keys[:2],
                "rounds",
            )


class ClaimTransactionTests(ClaimFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()

    def fails_unchanged(self, operation):
        before = self.server.snapshot()
        reservations = dict(self.server.economy._reservations)

        def failed_save(accounts, journals):
            self.assertEqual(self.server.snapshot(), before)
            raise OSError("injected claim write failure")

        with (
            patch.object(self.server, "persist_changes", side_effect=failed_save),
            self.assertRaises(OSError),
        ):
            operation()
        self.assertEqual(self.server.snapshot(), before)
        self.assertEqual(self.server.economy._reservations, reservations)

    def test_failed_exhibit_keeps_owned_card_and_next_listing_id(self):
        payload = exhibit_request(self.server.economy, self.key)
        self.fails_unchanged(lambda: self.auction(self.key, payload))
        self.assertEqual(self.server.auction_ledger._next_id, 1)
        self.assertFalse(self.server.auction_ledger.listings())

    def test_failed_outbid_keeps_escrow_history_and_previous_bidder_refund(self):
        self.auction(self.key, exhibit_request(self.server.economy, self.key))
        self.auction(self.buyer, bid_request(self.server.economy, self.buyer, 1, 100, 0))
        self.fails_unchanged(
            lambda: self.auction(
                self.other, bid_request(self.server.economy, self.other, 1, 150, 1)
            )
        )
        self.assertEqual(self.server.economy.balance(self.buyer), 9900)
        self.assertFalse(self.server.auction_ledger.pending(self.buyer))

    def test_expiry_publishes_listing_and_claims_only_after_commit(self):
        self.auction(self.key, exhibit_request(self.server.economy, self.key))
        with patch.object(self.server, "persist_changes", side_effect=AssertionError("idle write")):
            self.server.advance_auctions()
        self.now = self.server.auction_ledger.listing(1).expires
        self.fails_unchanged(self.server.advance_auctions)
        self.server.advance_auctions()
        self.assertEqual(len(self.server.auction_ledger.pending(self.key)), 1)

    def test_failed_payment_entry_retains_money_receipt_without_new_session(self):
        self.sell()
        self.fails_unchanged(lambda: self.collect(self.key))
        self.assertFalse(self.server.auction.collection_active(self.key))
        self.assertNotIn(self.key, self.server.auction._sessions)
        self.collect(self.key)
        self.assertEqual(self.server.economy.balance(self.key), 10100)

    def test_both_collection_get_failures_retain_original_phase_and_capabilities(self):
        self.sell()
        number = self.collect(self.buyer)
        for stage in (0, 1):
            row = self.server.auction._collections[self.buyer]
            ledger_row = self.server.auction_ledger._collections[self.buyer]
            self.fails_unchanged(lambda: self.auction_get(self.buyer, number))
            self.assertIs(self.server.auction._collections[self.buyer], row)
            self.assertIs(self.server.auction_ledger._collections[self.buyer], ledger_row)
            self.assertEqual(row.stage, stage)
            self.auction_get(self.buyer, number)
        self.assertEqual(len(self.server.economy.inventory(self.buyer)), 11)
        self.assertFalse(self.server.auction_ledger.pending(self.buyer))

    def test_other_live_collection_and_match_reservation_keep_identity(self):
        self.collect(self.other)
        row = self.server.auction._collections[self.other]
        ledger_row = self.server.auction_ledger._collections[self.other]
        token = self.server.economy.reserve_match(self.buyer)
        self.auction(self.key, exhibit_request(self.server.economy, self.key))
        self.assertIs(self.server.auction._collections[self.other], row)
        self.assertIs(self.server.auction_ledger._collections[self.other], ledger_row)
        self.assertIs(self.server.economy._reservations[self.buyer], token)

    def test_failed_prize_award_redemption_retains_pending_award(self):
        self.prize_award()
        self.fails_unchanged(self.enter_prize)
        self.assertFalse(self.server.prize.is_active(self.key))
        self.enter_prize()
        self.assertEqual(self.server.economy.prize_points(self.key), 5000)

    def test_failed_prize_buy_does_not_advance_rng_or_publish_offer(self):
        self.prize_award()
        number = self.enter_prize()
        rng = self.server.prize._rng.getstate()
        payload = named(0xB2, 0x14, number, "@Buy=/No=45")
        self.fails_unchanged(lambda: self.prize(payload))
        self.assertEqual(self.server.prize._rng.getstate(), rng)
        self.assertIsNone(self.server.prize._active[self.key].pending)
        self.prize(payload)
        self.assertNotEqual(self.server.prize._rng.getstate(), rng)

    def test_failed_prize_get_retains_pending_purchase_and_points(self):
        self.prize_award()
        number = self.enter_prize()
        self.prize(named(0xB2, 0x14, number, "@Buy=/No=45"))
        row = self.server.prize._active[self.key]
        payload = named(0xB2, 0x15, number, f"@Get=/No={number}")
        self.fails_unchanged(lambda: self.prize(payload))
        self.assertIs(self.server.prize._active[self.key], row)
        self.prize(payload)
        self.prize(payload)
        self.assertEqual(len(self.server.economy.inventory(self.key)), 11)
        self.assertEqual(self.server.economy.prize_points(self.key), 5000 - row.pending.cost)

    def test_failed_event_membership_does_not_publish_member_or_presence(self):
        def enter():
            with event_transaction(self.server, (self.key,)) as staged:
                staged.events.admit_room(self.key, "festival")

        self.fails_unchanged(enter)
        self.assertNotIn(self.key, self.server.events._present)

    def test_event_money_and_partial_card_claim_failures_leave_offsets_unchanged(self):
        self.event_grant()
        self.fails_unchanged(self.enter_event)
        self.assertFalse(self.server.event_cards.is_active(self.key))
        number = self.enter_event()
        row = self.server.event_cards._active[self.key]
        payload = named(0xB2, 0x15, number, f"@Get=/No={number}")
        self.fails_unchanged(lambda: self.event(payload))
        self.assertIs(self.server.event_cards._active[self.key], row)
        self.event(payload)
        self.event(payload)
        self.assertEqual(len(self.server.economy.inventory(self.key)), 19)
        self.assertEqual(
            self.server.event_cards.pending(self.key, "festival"), {"cards": 3, "money": 0}
        )

    def test_failed_event_edit_leaves_working_cards_and_names_unchanged(self):
        self.event_grant()
        self.enter_event()
        row = self.server.event_cards._active[self.key]
        before = list(row.cards), dict(row.names)

        def edit():
            with event_transaction(self.server, (self.key,)) as staged:
                staged.cards._active[self.key].cards.pop()
                staged.cards._active[self.key].names[1] = b"new"

        self.fails_unchanged(edit)
        self.assertEqual((row.cards, row.names), before)

    def test_failed_event_result_keeps_progress_and_receipt_unpublished(self):
        self.event_grant()
        self.fails_unchanged(self.record_result)
        self.record_result()
        self.record_result()
        self.assertEqual(
            [self.server.events.member(key, "festival").steps for key in self.keys[:2]], [1, 1]
        )
        self.assertEqual(len(self.server.event_results.snapshot()["rounds"]), 1)

    def test_failed_event_match_deck_write_keeps_the_original_account_capability(self):
        token = self.server.economy.reserve_match(self.key)
        before = self.server.economy.snapshot()
        with (
            patch.object(self.server.commerce, "save_accounts", side_effect=OSError("deck write")),
            self.assertRaises(OSError),
        ):
            self.server.commerce.apply_match_deck_changes(token, (), {1: b"new"})
        self.assertEqual(self.server.economy.snapshot(), before)
        self.assertIs(self.server.economy._reservations[self.key], token)
