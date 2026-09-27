"""Original encrypted protocol flows using the real PostgreSQL save boundary."""

import asyncio
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import test_auction_prize_integration as auctions
import test_event_integration as events
import test_match_prizes_integration as prizes
import test_match_wagers_integration as wagers
import test_services_integration as services
import test_trade_integration as trades
from economy_service import named, parse_named
from identities import SHOP_RAW, player_id, service_nick
from match_service import packet
from profile_service import ProfileServer, checked
from server import Server
from test_options_service import request as options_request
from test_postgres_store import TEST_URL, DatabaseFixture, sa
from test_profile_accounts import file_request, transact
from test_server import Client


class ProtocolFixture(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    options: ClassVar[dict] = {}

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.server = Server(store=self.store, **self.options)
        self.port = await self.server.start(0)
        self.clients = []
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]
        self.client = self.clients[0]
        self.key = self.keys[0]

    async def asyncTearDown(self):
        await self.disconnect()

    async def disconnect(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()

    async def assert_restart(self):
        await self.disconnect()
        expected = self.server.snapshot()
        self.server = Server(store=self.reopen(), **self.options)
        self.assertEqual(self.server.snapshot(), expected)
        return expected


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class PostgresShopTests(ProtocolFixture):
    request = services.ServicesIntegrationTests.request
    enter_shop = services.ServicesIntegrationTests.enter_shop

    async def test_purchase_retry_and_encrypted_profile_survive_restart(self):
        session = await self.enter_shop()
        before = self.server.economy.profile_bytes(self.key)
        buy = named(0xB2, 0x14, session, "@Buy=/No=1")
        replies = await self.request(SHOP_RAW, buy)
        self.assertEqual(await self.request(SHOP_RAW, buy), replies)
        self.assertEqual(self.server.economy.profile_bytes(self.key), before)
        await self.request(SHOP_RAW, named(0xB2, 0x15, session, f"@Get=/No={session}"))
        await self.request(SHOP_RAW, named(0xB2, 0x17, session, f"@Quit=/No={session}"))
        self.assertEqual(self.server.economy.balance(self.key), 9000)
        self.assertEqual(len(self.server.economy.inventory(self.key)), 20)
        profile = ProfileServer(Path(self.folder) / "profile.jsonl", self.server)
        port = await profile.start(0)
        try:
            error, data = await transact(
                port,
                self.key,
                3,
                0,
                file_request(),
                context=(self.client.cipher, self.client.iv),
            )
            self.assertEqual(error, 0)
            self.assertEqual(data, checked(self.server.economy.profile_bytes(self.key)))
        finally:
            await profile.stop()
        await self.assert_restart()

    async def test_lost_database_connection_disconnects_without_success_ack(self):
        before = self.store.load()
        with self.store.connection.begin():
            pid = self.store.connection.scalar(sa.text("SELECT pg_backend_pid()"))
        with self.admin.connect() as connection:
            connection.execute(
                sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
            )
        await self.client.send(
            b"NOTICE "
            + service_nick(SHOP_RAW)
            + b" :"
            + options_request(nn=player_id(self.client.nick))
        )
        self.assertEqual(await self.client.read(), b"")
        self.assertIsNotNone(self.server.storage_failed)
        await self.disconnect()
        self.assertEqual(self.reopen().load(), before)


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class PostgresTradeTests(ProtocolFixture):
    flush = trades.TradeIntegrationTests.flush
    send = trades.TradeIntegrationTests.send
    control = trades.TradeIntegrationTests.control
    metadata = trades.TradeIntegrationTests.metadata
    enter = trades.TradeIntegrationTests.enter
    operation = trades.TradeIntegrationTests.operation
    settle = trades.TradeIntegrationTests.settle

    async def test_two_account_trade_and_duplicate_get(self):
        original = [self.server.economy.inventory(key) for key in self.keys]
        await self.settle()
        await self.operation(0, 0x1D, "@Get=/P=0")
        self.assertEqual(self.server.economy.inventory(self.keys[0]), original[0])
        await self.operation(1, 0x1D, "@Get=/P=1")
        for side, key in enumerate(self.keys):
            expected = original[side][1:] + original[1 - side][:1]
            self.assertEqual(
                Counter(c.reduced() for c in self.server.economy.inventory(key)),
                Counter(c.reduced() for c in expected),
            )
        committed = self.server.snapshot()
        await self.operation(0, 0x1D, "@Get=/P=0")
        self.assertEqual(self.server.snapshot(), committed)
        await self.assert_restart()


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class PostgresMatchTests(ProtocolFixture):
    player_count = 2
    options: ClassVar[dict] = {
        "match_mode": "versus",
        "card_prizes": True,
        "allow_wagers": True,
        "rating_mode": "local-history",
    }
    flush = prizes.PrizeIntegrationTests.flush
    send = prizes.PrizeIntegrationTests.send
    play = prizes.PrizeIntegrationTests.play
    choose = prizes.PrizeIntegrationTests.choose
    admit = wagers.WagerIntegrationTests.admit

    async def test_complete_match_prizes_stakes_ratings_and_restart(self):
        await self.admit()
        await self.play()
        await self.choose()
        self.assertEqual(
            sum(self.server.economy.balance(key) for key in self.keys), 20000
        )
        for side in range(2):
            await self.send(
                side, packet(0x43, 17, side, "@Continue=/Ans=1/PR=0/Conf=0")
            )
        expected = await self.assert_restart()
        self.assertTrue(expected["ratings"]["history"]["accounts"])
        self.assertTrue(expected["match_economy"]["rounds"])
        self.assertEqual(
            sum(len(self.server.economy.inventory(k)) for k in self.keys), 20
        )


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class PostgresAuctionTests(ProtocolFixture):
    send = auctions.AuctionPrizeIntegrationTests.send
    sell = auctions.AuctionPrizeIntegrationTests.sell
    enter_collection = auctions.AuctionPrizeIntegrationTests.enter_collection
    get_collection = auctions.AuctionPrizeIntegrationTests.get_collection

    async def asyncSetUp(self):
        self.now = 1700000000
        self.options = {"auction_clock": lambda: self.now}
        await super().asyncSetUp()
        self.game = self.server

    async def test_sale_and_collection_commit_inventory_money_and_receipts(self):
        item = await self.sell()
        for side in range(2):
            number, _ = await self.enter_collection(side)
            await self.get_collection(side, number)
            await self.get_collection(side, number)
        self.assertEqual(self.game.economy.balance(self.keys[0]), 10100)
        self.assertEqual(
            self.game.economy.inventory(self.keys[1])[-1].reduced(), item.card.reduced()
        )
        self.assertFalse(
            any(self.game.auction_ledger.pending(key) for key in self.keys)
        )
        await self.assert_restart()


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class PostgresEventTests(ProtocolFixture):
    request = services.ServicesIntegrationTests.request
    enter_room = events.EventIntegrationTests.enter_room

    async def asyncSetUp(self):
        from event_service import EventConfig, EventListing, event_data
        from identities import game_id

        config = EventConfig(
            "festival",
            EventListing(b"Local Lobby", b"Festival", b"Local event"),
            event_data(classes=(b"First", b"Next"), steps_per_class=4),
            100,
            500,
            game_id(0x1000),
            game_id(0x1001),
            game_id(0x1002),
            card_service_enabled=True,
        )
        self.now = 200
        self.options = {"event_configs": (config,), "event_clock": lambda: self.now}
        await super().asyncSetUp()

    async def test_partial_event_grant_and_money_are_not_repeated_on_restart(self):
        await self.enter_room()
        card = replace(self.server.economy.inventory(self.key)[0], deck=255)
        self.server.event_cards.grant(
            self.key, "festival", "earned1", cards=(card,) * 12, money=250
        )
        self.server.persist()
        view = self.server.economy.account_view(self.key)
        request = named(
            0x90,
            0,
            0,
            f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}",
        )
        replies = await self.request(0x1002, request)
        session = int(parse_named(replies[1])[3]["EcmInit"]["N"])
        await self.request(0x1002, named(0xB2, 0x15, session, f"@Get=/No={session}"))
        await self.request(0x1002, named(0xB2, 0x17, session, f"@Quit=/No={session}"))
        self.assertEqual(self.server.economy.balance(self.key), 10250)
        self.assertEqual(len(self.server.economy.inventory(self.key)), 19)
        await self.assert_restart()
        self.assertFalse(
            self.server.event_cards.grant(
                self.key, "festival", "earned1", cards=(card,) * 12, money=250
            )
        )


if __name__ == "__main__":
    unittest.main()
