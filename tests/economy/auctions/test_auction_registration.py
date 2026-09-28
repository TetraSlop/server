"""Guild registration preserves historical auction actors across JSON restart."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.economy.auctions import test_auction_prize_integration as exchange_tests
from tests.economy.auctions.test_auction_service import bid_request, exhibit_request
from tests.profiles.test_new_profile import request as registration_request
from tests.network.test_server import Client
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.auctions.models import AuctionError
from tmserver.economy.service import EconomyService
from tmserver.protocol.identities import player_id
from tmserver.network.server import Server
from tmserver.protocol.commands import parse


def auction_fixture():
    economy = EconomyService(seed=37)
    for nn, key in enumerate(("seller", "buyer"), 1):
        economy.provision(key, nn=nn, player_id=nn, name=key.encode(), guild=0)
    ledger = AuctionEconomy(economy)
    item = ledger.exhibit("seller", economy.inventory("seller")[0], 100, 20, 1, 0, now=100)
    ledger.bid("buyer", item.id, 100, 0, now=101)
    return economy, ledger


class AuctionRegistrationTests(unittest.TestCase):
    def test_registered_profiles_preserve_unregistered_seller_and_bid_history(self):
        for guild in (1, 2, 3):
            with self.subTest(guild=guild):
                economy, ledger = auction_fixture()
                original = ledger.snapshot()
                for key in ("seller", "buyer"):
                    economy.initialize_profile(key, registration_request(economy, key, guild=guild))
                state = json.loads(json.dumps({"economy": economy.snapshot(), "auction": original}))
                restored = EconomyService.restore(state["economy"], catalog=economy.catalog)
                history = AuctionEconomy.restore(restored, state["auction"])
                self.assertEqual(history.snapshot(), original)
                self.assertEqual(
                    [restored.account_view(k).guild for k in ("seller", "buyer")], [guild] * 2
                )

    def test_restore_rejects_forged_identity_and_impossible_historical_guild(self):
        economy, ledger = auction_fixture()
        for key in ("seller", "buyer"):
            economy.initialize_profile(key, registration_request(economy, key, guild=2))
        original = ledger.snapshot()
        for location in ("seller", "bidder", "history"):
            for field, value in (
                ("nn", 99),
                ("player_id", 99),
                ("name", b"forged".hex()),
                ("key", "missing"),
                *(("guild", value) for value in (True, False, 0.0, "0", None, -1, 4, 1, 3)),
            ):
                with self.subTest(location=location, field=field, value=value):
                    bad = copy.deepcopy(original)
                    actor = (
                        bad["history"][0]["bidder"]
                        if location == "history"
                        else bad["listings"][0][location]
                    )
                    actor[field] = value
                    with self.assertRaises(AuctionError):
                        AuctionEconomy.restore(economy, bad)
        # Registration is one-way: a saved registered actor cannot become guild0.
        unregistered, ledger = auction_fixture()
        bad = ledger.snapshot()
        bad["listings"][0]["seller"]["guild"] = 1
        with self.assertRaises(AuctionError):
            AuctionEconomy.restore(unregistered, bad)


class AuctionRegistrationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    send = exchange_tests.AuctionPrizeIntegrationTests.send

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.now = 1700000000
        self.game = Server(state_path=self.path, auction_clock=lambda: self.now)
        self.keys = [nick.decode() for nick in self.game.accounts]
        for character, key in enumerate(self.keys, 1):
            self.game.economy.provision(
                key, nn=player_id(key.encode()), player_id=character, name=key.encode(), guild=0
            )
            self.game.account_keys.add(key)
        self.game.persist()
        port = await self.game.start(0)
        self.clients = []
        for key in self.keys:
            client = Client()
            await client.open(port, account=key.encode())
            self.clients.append(client)
            self.assertIn(b" 422 ", await client.read())

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.game.stop()
        self.temp.cleanup()

    async def test_encrypted_registration_and_later_bid_restore_unchanged_history(self):
        seller, buyer = self.keys
        reply = await self.send(0, exhibit_request(self.game.economy, seller))
        self.assertEqual(parse(reply[0][5:])[0].token, b"ES")
        reply = await self.send(1, bid_request(self.game.economy, buyer, 1, 100, 0))
        self.assertEqual(parse(reply[0][5:])[0].token, b"BS")
        before = self.game.auction_ledger.snapshot()
        for side, guild in enumerate((2, 3)):
            reply = await self.send(
                side, registration_request(self.game.economy, self.keys[side], guild=guild)
            )
            self.assertTrue(reply[0].endswith(b"@InitAns=/Ans=2"))
            self.assertEqual(self.game.auction_ledger.snapshot(), before)
            # Each acknowledged Init80 has already saved a restartable joint state.
            restored = Server(state_path=self.path, auction_clock=lambda: self.now)
            try:
                self.assertEqual(restored.auction_ledger.snapshot(), before)
                self.assertEqual(restored.economy.account_view(self.keys[side]).guild, guild)
            finally:
                await restored.stop()
        self.now += 1
        reply = await self.send(1, bid_request(self.game.economy, buyer, 1, 150, 1))
        self.assertEqual(parse(reply[0][5:])[0].token, b"BS")
        history = self.game.auction_ledger.snapshot()
        self.assertEqual([row["bidder"]["guild"] for row in history["history"]], [0, 3])
        restored = Server(state_path=self.path, auction_clock=lambda: self.now)
        try:
            self.assertEqual(restored.auction_ledger.snapshot(), history)
            self.assertEqual(
                restored.economy.inventory(seller), self.game.economy.inventory(seller)
            )
            self.assertEqual(restored.economy.balance(buyer), self.game.economy.balance(buyer))
        finally:
            await restored.stop()
