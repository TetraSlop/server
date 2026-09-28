"""Commit ordering and isolation for staged shop and trade operations."""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from tests.economy import test_trade_service as trade_tests
from tests.economy.test_trade_service import edits
from tmserver.economy.commerce import Commerce
from tmserver.cards.catalog import Catalog
from tmserver.economy.errors import EconomyError
from tmserver.economy.service import EconomyService
from tmserver.economy.packets import named, parse_named
from tmserver.storage.json import JsonStateStore
from tmserver.economy.trade import TradeService


class CommerceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.economy = EconomyService(seed=1, catalog=self.catalog)
        for index, key in enumerate(("alice", "bob", "carol"), 1):
            self.economy.provision(key, nn=index, name=key.encode())
        self.save = Mock()
        self.commerce = Commerce(self.economy, self.save)

    def enter(self):
        view = self.economy.account_view("alice")
        replies = self.commerce.shop(
            "alice",
            named(0xB0, 0, 0, f"@ShReq=/NN={view.nn:016X}/HID=0/Dm=0/Vol=0/CN={view.name.hex()}"),
        )
        self.session = int(parse_named(replies[1])[3]["Init"]["N"])
        return replies

    def shop(self, subtype, body):
        return self.commerce.shop("alice", named(0xB2, subtype, self.session, body))

    def test_purchase_commits_only_one_account_before_publishing_memory(self):
        self.enter()
        reply = self.shop(0x14, "@Buy=/No=1")
        self.assertEqual(self.shop(0x14, "@Buy=/No=1"), reply)
        before = self.economy.snapshot()
        untouched = self.economy._accounts["bob"]

        def commit(accounts):
            self.assertEqual(set(accounts), {"alice"})
            self.assertEqual(accounts["alice"]["balance"], 9000)
            self.assertEqual(len(accounts["alice"]["cards"]), 20)
            self.assertEqual(self.economy.snapshot(), before)

        self.save.side_effect = commit
        self.shop(0x15, f"@Get=/No={self.session}")
        self.assertEqual(self.economy.balance("alice"), 9000)
        self.assertEqual(len(self.economy.inventory("alice")), 20)
        self.assertIs(self.economy._accounts["bob"], untouched)
        self.assertEqual(self.economy.snapshot()["accounts"]["bob"], before["accounts"]["bob"])

    def test_failed_offer_does_not_publish_random_state_or_pending_purchase(self):
        self.enter()
        before = deepcopy(self.economy._accounts["alice"])
        rng = self.economy.rng.getstate()
        self.save.side_effect = OSError("injected failed write")
        with self.assertRaises(OSError):
            self.shop(0x14, "@Buy=/No=1")
        self.assertEqual(self.economy._accounts["alice"], before)
        self.assertEqual(self.economy.rng.getstate(), rng)

    def test_failed_get_retains_the_original_inventory_and_pending_offer(self):
        self.enter()
        self.shop(0x14, "@Buy=/No=1")
        before = deepcopy(self.economy._accounts["alice"])
        self.save.side_effect = OSError("injected failed write")
        with self.assertRaises(OSError):
            self.shop(0x15, f"@Get=/No={self.session}")
        self.assertEqual(self.economy._accounts["alice"], before)

    def test_rejected_shop_message_never_reaches_storage(self):
        self.enter()
        self.save.reset_mock()
        before = deepcopy(self.economy._accounts["alice"])
        with self.assertRaises(EconomyError):
            self.shop(0x15, "@Get=/No=255")
        self.save.assert_not_called()
        self.assertEqual(self.economy._accounts["alice"], before)

    def test_staging_cannot_copy_an_unrelated_account_or_reservation(self):
        class Uncopyable:
            def __deepcopy__(self, memo):
                raise AssertionError("unrelated account was copied")

        self.economy._accounts["carol"] = Uncopyable()
        reservation = self.economy.reserve_match("bob")
        self.enter()
        self.shop(0x14, "@Buy=/No=1")
        self.shop(0x15, f"@Get=/No={self.session}")
        self.assertIs(self.economy._reservations["bob"], reservation)

    def test_two_account_commit_failure_preserves_inventory_and_capabilities(self):
        token = self.economy.reserve_exchange(("alice", "bob"))
        before = self.economy.snapshot()
        cards = {view.key: list(view.cards) for view in token.accounts}
        cards["alice"][0], cards["bob"][0] = cards["bob"][0], cards["alice"][0]
        self.save.side_effect = OSError("injected failed trade")
        with self.assertRaises(OSError):
            self.commerce.commit_exchange(token, cards)
        self.assertEqual(self.economy.snapshot(), before)
        self.assertIs(self.economy._reservations["alice"], token)
        self.assertIs(self.economy._reservations["bob"], token)

    def test_json_account_commit_preserves_unrelated_accounts_and_journals(self):
        snapshot = self.economy.snapshot()
        snapshot["auction"] = {"receipt": ["keep", 123]}
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStateStore(Path(folder) / "state.json")
            store.save(snapshot)
            self.commerce = Commerce(self.economy, store.save_accounts)
            self.enter()
            self.shop(0x14, "@Buy=/No=1")
            self.shop(0x15, f"@Get=/No={self.session}")
            restored = store.load()
            self.assertEqual(restored["auction"], snapshot["auction"])
            self.assertEqual(restored["accounts"]["bob"], snapshot["accounts"]["bob"])
            self.assertEqual(restored["accounts"]["alice"]["balance"], 9000)

    def test_json_write_failure_keeps_memory_and_the_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = JsonStateStore(Path(folder) / "state.json")
            store.save(self.economy.snapshot())
            self.commerce = Commerce(self.economy, store.save_accounts)
            self.enter()
            self.shop(0x14, "@Buy=/No=1")
            account = deepcopy(self.economy._accounts["alice"])
            original = store.path.read_bytes()
            with (
                patch.object(Path, "replace", side_effect=OSError("failed replace")),
                self.assertRaises(OSError),
            ):
                self.shop(0x15, f"@Get=/No={self.session}")
            self.assertEqual(self.economy._accounts["alice"], account)
            self.assertEqual(store.path.read_bytes(), original)


class TradeCommitTests(unittest.TestCase):
    send = trade_tests.TradeTests.send
    begin = trade_tests.TradeTests.begin
    offers = trade_tests.TradeTests.offers
    confirm = trade_tests.TradeTests.confirm
    complete = trade_tests.TradeTests.complete
    settle = trade_tests.TradeTests.settle

    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        trade_tests.TradeTests.setUp(self)
        self.save = Mock()
        self.trade = TradeService(
            self.economy,
            first_session=17,
            transactions=Commerce(self.economy, self.save),
        )
        self.trade.bind("peer0", "left")
        self.trade.bind("peer1", "right")

    def test_final_get_is_the_only_trade_commit_and_failure_is_not_closed(self):
        self.settle()
        self.save.assert_not_called()
        self.send(0, 0x1D, "@Get=/P=0")
        self.save.assert_not_called()
        before = self.economy.snapshot()
        self.save.side_effect = OSError("injected failed final Get")
        with self.assertRaises(OSError):
            self.send(1, 0x1D, "@Get=/P=1")
        self.assertEqual(self.economy.snapshot(), before)
        self.assertIsNotNone(self.trade.status("peer0"))
        self.assertFalse(self.trade._closed)

    def test_delayed_deck_edit_commits_before_memory_and_get_retry_never_writes(self):
        self.settle()
        self.send(0, 0x1D, "@Get=/P=0")
        self.send(1, 0x1D, "@Get=/P=1")
        self.assertEqual(self.save.call_count, 1)
        self.assertEqual(set(self.save.call_args.args[0]), {"left", "right"})
        self.send(0, 0x1D, "@Get=/P=0")
        self.assertEqual(self.save.call_count, 1)
        before = self.economy.snapshot()
        self.save.side_effect = OSError("injected failed delayed rename")
        with self.assertRaises(OSError):
            self.send(0, 0x22, edits(0, "Decks", name=(2, b"Delayed")))
        self.assertEqual(self.economy.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
