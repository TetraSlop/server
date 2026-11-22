"""Existing service dialects and physical-card identity survive shared edit parsing."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from tests.economy.auctions import test_auction_service as auctions
from tests.economy import test_economy_service as shops
from tests.events import test_event_cards as events
from tests.economy import test_prize_service as prizes
from tests.economy import test_result_rewards as rewards
from tests.economy import test_trade_service as trades
from tmserver.cards.catalog import Catalog
from tmserver.economy.errors import EconomyError
from tmserver.economy.service import EconomyService
from tmserver.economy.packets import named


ROUTES = ("shop", "prize", "event", "auction", "trade", "result")


class InventoryEditCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def start_fixture(self, cls, key, *, duplicates=False):
        f = cls()
        f.catalog = self.catalog
        if duplicates:
            provision = EconomyService.provision

            def with_duplicates(economy, account, **options):
                if account == key:
                    cards = options.get("cards")
                    if cards is None:
                        cards = [economy.catalog.generate(i, lambda: 20) for i in range(1, 11)]
                    options["cards"] = [
                        replace(cards[0], deck=7),
                        replace(cards[0], deck=8),
                        *cards[2:],
                    ]
                return provision(economy, account, **options)

            with patch.object(EconomyService, "provision", with_duplicates):
                f.setUp()
        else:
            f.setUp()
        return f

    def fixture(self, kind, *, duplicates=False):
        cls, key = {
            "shop": (shops.EconomyTests, "alice"),
            "prize": (prizes.PrizeTests, "one"),
            "event": (events.EventCardTests, "a"),
            "auction": (auctions.AuctionServiceTests, "seller"),
            "trade": (trades.TradeTests, "left"),
            "result": (rewards.ResultRewardsTests, "one"),
        }[kind]
        f = self.start_fixture(cls, key, duplicates=duplicates)
        economy = f.shop if kind == "shop" else f.economy if kind == "trade" else f.e

        def committed():
            view = economy.account_view(key)
            return tuple(view.cards), dict(view.deck_names)

        state, finish, error = committed, lambda: None, EconomyError
        if kind == "shop":
            send = f.send
        elif kind == "prize":
            f.entry()
            send, error = f.send, prizes.PrizeError
        elif kind == "event":
            f.entry()
            f.get()
            send, error = f.send, EconomyError
        elif kind == "auction":
            session, _ = f.enter(key)
            f.get(key, session)
            current = f.s._collections[key]
            state = lambda: (tuple(current.cards), dict(current.names))
            send = lambda subtype, body: f.s.handle(key, named(0xB2, subtype, session, body))
            finish = lambda: f.get(key, session)
        elif kind == "trade":
            f.settle()
            current = f.trade._active["peer0"]
            state = lambda: (tuple(current.final[0]), dict(current.names[0]))
            send = lambda subtype, body: f.send(0, subtype, body)
            finish = lambda: [f.send(side, 0x1D, f"@Get=/P={side}") for side in (0, 1)]
            error = trades.TradeError
        else:
            f.stage(extras=())
            current = f.r._pending[key]
            state = lambda: (tuple(current.cards), dict(current.names))
            send = lambda subtype, body: f.r.handle(key, named(0x43, subtype, 0, body))
            finish = lambda: f.r.commit(key)
            error = rewards.ResultRewardError
        return SimpleNamespace(
            economy=economy, state=state, committed=committed, send=send, finish=finish, error=error
        )

    @staticmethod
    def head(kind, token, fields):
        return f"@{token}=" + ("/P=0" if kind in ("trade", "result") else "") + fields

    def check(self, case, subtype, body, accepted, expected):
        before = case.state(), case.economy.snapshot()
        if accepted:
            self.assertEqual(case.send(subtype, body), [])
            self.assertEqual(case.state(), expected)
            case.finish()
            self.assertEqual(case.committed(), expected)
        else:
            with self.assertRaises(case.error):
                case.send(subtype, body)
            self.assertEqual((case.state(), case.economy.snapshot()), before)

    def test_mode_and_count_spelling_remain_service_specific(self):
        for kind in ROUTES:
            for subtype, token, fields, accepted in (
                (0x10, "Erase", "/Mode=01/C=0", kind not in ("event", "auction")),
                (0x10, "Erase", "/Mode=02", kind in ("trade", "result")),
                (0x22, "Decks", "/C=-0", kind in ("shop", "auction")),
                (0x22, "Decks", "/C=00", True),
            ):
                with self.subTest(service=kind, fields=fields):
                    case = self.fixture(kind)
                    self.check(
                        case, subtype, self.head(kind, token, fields), accepted, case.state()
                    )

    def test_minus_zero_card_fields_and_body_side_authentication(self):
        for kind in ROUTES:
            with self.subTest(service=kind, field="deck"):
                case = self.fixture(kind)
                cards, names = case.state()
                body = self.head(kind, "Decks", "/C=1")
                body += "@0=/D=" + "|".join(map(str, cards[0].reduced())) + "|-0"
                expected = (replace(cards[0], deck=0), *cards[1:]), names
                self.check(case, 0x22, body, kind in ("shop", "auction"), expected)
            with self.subTest(service=kind, field="side"):
                case = self.fixture(kind)
                # Trade/result require the authenticated side; the other
                # services prohibit the field altogether.
                self.check(case, 0x22, "@Decks=/P=1/C=0", False, case.state())

    def test_deck_name_controls_and_del_keep_auction_printability_rule(self):
        for kind in ROUTES:
            for name in (b"\x01", b"\x7f"):
                with self.subTest(service=kind, name=name):
                    case = self.fixture(kind)
                    cards, names = case.state()
                    body = self.head(kind, "Decks", "/C=0") + "@DN1=/S=" + name.hex()
                    self.check(case, 0x22, body, kind != "auction", (cards, names | {1: name}))

    def test_duplicate_wire_cards_edit_the_first_owned_copy(self):
        for kind in ROUTES:
            with self.subTest(service=kind):
                case = self.fixture(kind, duplicates=True)
                cards, names = case.state()
                self.assertEqual(cards[0].reduced(), cards[1].reduced())
                wire = "|".join(map(str, cards[0].reduced()))
                case.send(0x22, self.head(kind, "Decks", "/C=1") + "@0=/D=" + wire + "|9")
                self.assertEqual(case.state(), ((replace(cards[0], deck=9), *cards[1:]), names))
                case.send(0x10, self.head(kind, "Erase", "/Mode=1/C=1") + "@0=/D=" + wire)
                self.assertEqual(case.state(), (cards[1:], names))
                case.finish()
                self.assertEqual(case.committed(), (cards[1:], names))

    def test_trade_offer_exclusion_does_not_exclude_global_slot_clearing(self):
        f = self.start_fixture(trades.TradeTests, "left", duplicates=True)
        cards = list(f.economy.inventory("left"))
        f.begin()
        f.send(0, 0x1A, trades.offer(0, cards[:1]))
        current = f.trade._active["peer0"]
        before = tuple(current.base[0]), dict(current.names[0]), f.economy.snapshot()
        with self.assertRaises(trades.TradeError):
            f.send(0, 0x10, trades.edits(0, "Erase", cards[:2], mode=1))
        self.assertEqual((tuple(current.base[0]), current.names[0], f.economy.snapshot()), before)
        f.send(0, 0x22, trades.edits(0, "Decks", [replace(cards[0], deck=7)]))
        self.assertEqual(
            current.base[0], [replace(cards[0], deck=255), replace(cards[1], deck=7), *cards[2:]]
        )
        self.assertEqual(f.economy.snapshot(), before[2])


if __name__ == "__main__":
    unittest.main()
