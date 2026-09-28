"""Offline recovery forecasts agree with real startup without changing the save."""

import asyncio
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.economy import test_match_economy as fixture
from tmserver.cli import admin
from tmserver.cards.catalog import Catalog
from tmserver.storage.recovery import preview_recovery
from tmserver.network.server import Server
from tmserver.storage.json import JsonStateStore


class RecoveryPreviewTests(unittest.TestCase):
    setUp = fixture.MatchEconomyTests.setUp
    begin = fixture.MatchEconomyTests.begin
    result = fixture.MatchEconomyTests.result
    participants = fixture.MatchEconomyTests.participants
    award = fixture.MatchEconomyTests.award

    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def saved(self):
        return dict(
            self.e.snapshot(),
            auction=self.a.snapshot(),
            match_economy=self.m.snapshot(),
            result_rewards=self.r.snapshot(),
        )

    def row(self, key, *, refund=0, cards=0, returned_before=0):
        balance = self.e.balance(key)
        return dict(
            account_key=key,
            balance_before=balance,
            balance_after=balance + refund,
            refunded_money=refund,
            returned_cards_added=cards,
            returned_cards_after_recovery=returned_before + cards,
        )

    def check_startup(self, *, rounds=0, results=0, accounts=()):
        saved = self.saved()
        untouched = copy.deepcopy(saved)
        expected = dict(
            interrupted_rounds=rounds, unaccepted_results=results, accounts=list(accounts)
        )
        self.assertEqual(preview_recovery(saved), expected)
        self.assertEqual(preview_recovery(saved), expected)
        self.assertEqual(saved, untouched)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "save.json"
            JsonStateStore(path).save(saved)
            original_bytes = path.read_bytes()
            output = io.StringIO()
            with (
                redirect_stdout(output),
                patch.object(JsonStateStore, "save", side_effect=AssertionError("preview wrote")),
                patch.object(
                    JsonStateStore, "save_changes", side_effect=AssertionError("preview wrote")
                ),
            ):
                admin.cli(["--state", str(path), "recovery"])
            self.assertEqual(json.loads(output.getvalue()), expected)
            self.assertEqual(path.read_bytes(), original_bytes)
            game = Server(state_path=path)
            try:
                self.assertEqual(
                    game.auction_ledger.listings(active_only=False),
                    self.a.listings(active_only=False),
                )
                for key in saved["accounts"]:
                    change = next((row for row in accounts if row["account_key"] == key), None)
                    self.assertEqual(
                        game.economy.balance(key),
                        saved["accounts"][key]["balance"]
                        if change is None
                        else change["balance_after"],
                    )
                    self.assertEqual(game.economy.inventory(key), self.e.inventory(key))
                    old_returns = sum(p.kind == "returned" for p in self.a.pending(key))
                    self.assertEqual(
                        sum(p.kind == "returned" for p in game.auction_ledger.pending(key)),
                        old_returns if change is None else change["returned_cards_after_recovery"],
                    )
                self.assertEqual(
                    preview_recovery(game.snapshot()),
                    dict(interrupted_rounds=0, unaccepted_results=0, accounts=[]),
                )
            finally:
                asyncio.run(game.stop())

    def test_three_player_escrow_refund_preserves_existing_returns(self):
        self.a.enqueue_returned("c", "older", self.e.inventory("c")[:1])
        self.begin(n=3, stake=200)
        self.check_startup(
            rounds=1,
            accounts=[
                self.row("a", refund=200),
                self.row("b", refund=200),
                self.row("c", refund=200, returned_before=1),
            ],
        )

    def test_paid_unsettled_result_returns_earned_cards_without_refunding_again(self):
        self.a.enqueue_returned("c", "unrelated", self.e.inventory("c")[:1])
        self.begin()
        self.result(cbc={0: self.hands[0][:1], 1: self.hands[1][:2]})
        self.check_startup(rounds=1, accounts=[self.row("a", cards=1), self.row("b", cards=2)])

    def test_handed_result_is_counted_only_in_its_acceptance_journal(self):
        self.begin()
        self.result(cbc={0: self.hands[0][:1]})
        self.tokens, _ = self.m.settle_cards("r1", self.participants(), (self.award(),))
        self.m.handoff("r1", self.tokens, self.r)
        self.check_startup(results=2, accounts=[self.row("a", cards=2)])

    def test_accepted_cards_are_not_recovered_twice(self):
        self.begin()
        self.result(cbc={0: self.hands[0][:1]})
        self.tokens, _ = self.m.settle_cards("r1", self.participants(), ())
        self.m.handoff("r1", self.tokens, self.r)
        for key in ("a", "b"):
            self.r.commit(key)
        self.check_startup()

    def test_empty_older_save_needs_no_recovery(self):
        snapshot = self.e.snapshot()
        self.assertEqual(
            preview_recovery(snapshot),
            dict(interrupted_rounds=0, unaccepted_results=0, accounts=[]),
        )
        self.assertEqual(set(snapshot), {"version", "accounts"})

    def test_expired_auction_and_queued_bid_refund_are_not_startup_recovery(self):
        item = self.a.exhibit("a", self.e.inventory("a")[0], 100, 20, 1, 0, now=100)
        item = self.a.bid("b", item.id, 100, item.revision, now=101)
        self.a.bid("c", item.id, 120, item.revision, now=102)
        self.assertEqual(self.a.pending("b")[0].amount, 100)
        self.check_startup()
