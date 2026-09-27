"""Pending offer retries preserve progress through encrypted trade settlement."""

import asyncio
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tests import test_trade_integration as trades
from tests import test_trade_service as trade_domain
from tests.test_server import Client
from tests.test_trade_service import card, data, edits, offer
from tmserver.economy_service import Catalog, EconomyService
from tmserver.identities import player_id
from tmserver.server import ACCOUNTS, Server
from tmserver.state_store import JsonStateStore
from tmserver.trade_service import TradeError


class TradePageBoundaryTests(unittest.TestCase):
    setUp = trade_domain.TradeTests.setUp
    send = trade_domain.TradeTests.send
    begin = trade_domain.TradeTests.begin

    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def test_replacement_page_validates_ownership_before_replacing_buffered_suffix(self):
        self.begin()
        cards = self.economy.inventory("left")
        self.send(0, 0x1A, offer(0, cards[:2], total=4))
        self.send(0, 0x1A, offer(0, cards[2:3], total=4, start=2))
        trade = self.trade._active["peer0"]
        before = trade.uploads[0], self.trade.status("peer0"), self.economy.snapshot()
        with self.assertRaisesRegex(TradeError, "multiplicity"):
            self.send(0, 0x1A, offer(0, cards[:1], total=4, start=1))
        self.assertEqual(
            (trade.uploads[0], self.trade.status("peer0"), self.economy.snapshot()), before
        )
        self.send(0, 0x1A, offer(0, cards[3:4], total=4, start=1))
        self.assertEqual(trade.uploads[0], (4, (cards[0].reduced(), cards[3].reduced())))
        self.assertFalse(trade.offers[0])
        self.send(0, 0x1A, offer(0, cards[1:3], total=4, start=2))
        self.assertEqual(trade.offers[0], (cards[0], cards[3], cards[1], cards[2]))
        self.assertEqual(self.economy.snapshot(), self.initial)


class TradeLifecycleBoundaryTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = trades.TradeIntegrationTests.asyncTearDown
    flush = trades.TradeIntegrationTests.flush
    send = trades.TradeIntegrationTests.send
    control = trades.TradeIntegrationTests.control
    metadata = trades.TradeIntegrationTests.metadata
    enter = trades.TradeIntegrationTests.enter
    operation = trades.TradeIntegrationTests.operation

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.temp.name) / "state.json"
        economy = EconomyService(seed=1)
        for side, nick in enumerate(ACCOUNTS):
            economy.provision(
                nick.decode(),
                nn=player_id(nick),
                player_id=side + 1,
                name=(b"Left", b"Right")[side],
                cards=[card(economy.catalog, model) for model in range(side * 30, side * 30 + 30)],
            )
        JsonStateStore(self.path).save(economy.snapshot())
        self.server = Server(state_path=self.path)
        self.port = await self.server.start(0)
        self.clients = [Client(), Client()]
        for client, nick in zip(self.clients, ACCOUNTS):
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [client.nick.decode() for client in self.clients]

    async def test_first_page_retry_preserves_progress_and_changed_page_restarts_upload(self):
        original = [self.server.economy.inventory(key) for key in self.keys]
        before, saved = self.server.snapshot(), self.path.read_bytes()
        await self.enter()
        peer = self.server.nicks[self.clients[0].nick]
        trade = self.server.trade._active[peer]
        reservation = trade.reservation
        offered = []
        for side, cards in enumerate(original):
            with self.subTest(side=side):
                first = offer(side, cards[:14], total=30)
                second = offer(side, cards[14:28], total=30, start=14)
                self.assertEqual(await self.operation(side, 0x1A, first), [])
                self.assertEqual(await self.operation(side, 0x1A, second), [])
                assembled = trade.uploads[side]
                for repeated in (first, second):
                    self.assertEqual(await self.operation(side, 0x1A, repeated), [])
                    self.assertEqual(trade.uploads[side], assembled)
                # A replacement may share its first page but change the next.
                changed = [*cards[:14], cards[15], cards[14], *cards[16:]]
                self.assertEqual(await self.operation(side, 0x1A, first), [])
                second = offer(side, changed[14:28], total=30, start=14)
                self.assertEqual(await self.operation(side, 0x1A, second), [])
                self.assertEqual(
                    trade.uploads[side], (30, tuple(card.reduced() for card in changed[:28]))
                )
                # A different first page still replaces the pending upload.
                changed = [cards[1], cards[0], *changed[2:]]
                self.assertEqual(
                    await self.operation(side, 0x1A, offer(side, changed[:14], total=30)), []
                )
                self.assertEqual(len(trade.uploads[side][1]), 14)
                self.assertEqual(await self.operation(side, 0x1A, second), [])
                replies = await self.operation(
                    side, 0x1A, offer(side, changed[28:], total=30, start=28)
                )
                self.assertTrue(any(row[1] == 0x1A for row in replies))
                self.assertIsNone(trade.uploads[side])
                self.assertEqual(trade.offers[side], tuple(changed))
                offered.append(changed)
        self.assertIs(trade.reservation, reservation)
        self.assertEqual(self.server.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), saved)

        for mode in (1, 2):
            for side in (0, 1):
                await self.operation(side, 0x1B, data(side, mode, (0, 29)))
        await self.operation(0, 0x1C, "@Ans=/P=0/M=2")
        replies = await self.operation(1, 0x1C, "@Ans=/P=1/M=2")
        self.assertEqual([row[3] for row in replies], [{"Data": {"M": "4"}}] * 2)
        self.assertEqual(await self.operation(0, 0x1D, "@Get=/P=0"), [])
        self.assertEqual(await self.operation(0, 0x1D, "@Get=/P=0"), [])
        # Native detached deck edits remain valid while the other Get is pending.
        self.assertEqual(
            await self.operation(0, 0x22, edits(0, "Decks", name=(1, b"After Get"))), []
        )
        self.assertEqual(self.server.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), saved)
        replies = await self.operation(1, 0x1D, "@Get=/P=1")
        self.assertEqual(len(replies), 2)
        for side, key in enumerate(self.keys):
            expected = offered[side][1:29] + [offered[1 - side][0], offered[1 - side][29]]
            self.assertEqual(Counter(self.server.economy.inventory(key)), Counter(expected))
            self.assertFalse(self.server.economy.is_reserved(key))
            self.assertIsNone(self.server.trade.status(self.server.nicks[self.clients[side].nick]))
        self.assertEqual(
            dict(self.server.economy.account_view(self.keys[0]).deck_names)[1], b"After Get"
        )
        committed = self.server.snapshot(), self.path.read_bytes()
        self.assertEqual(len(await self.operation(0, 0x1D, "@Get=/P=0")), 1)
        self.assertEqual((self.server.snapshot(), self.path.read_bytes()), committed)
        restored = Server(state_path=self.path)
        try:
            self.assertEqual(restored.snapshot(), committed[0])
        finally:
            await restored.stop()


if __name__ == "__main__":
    unittest.main()
