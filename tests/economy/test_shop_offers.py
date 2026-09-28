"""Original offer consumers and encrypted catalogue-priced purchases."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.network import test_services_integration as services
from tests.network.test_server import Client
from tmserver.economy.service import Card, Catalog, EconomyService, named, parse_named
from tmserver.protocol.identities import SHOP_RAW, service_nick
from tmserver.network.server import Server
from tmserver.economy.shop_offers import ShopOffers, load_shop_offers

OFFERS = ShopOffers(3, b"Alice", (76, 77), True)


def enter(service, key="a"):
    account = service.account_view(key)
    return service.handle(
        key,
        named(0xB0, 0, 0, f"@ShReq=/NN={account.nn:016X}/HID=0/Dm=0/Vol=0/CN={account.name.hex()}"),
    )


def number(replies):
    return int(parse_named(replies[1])[3]["Init"]["N"])


class ShopOfferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        donor = EconomyService(seed=1, catalog=cls.catalog)
        donor.provision("donor")
        cls.cards = donor.inventory("donor")

    def shop(self, *, offers=OFFERS, guild=1, balance=1_000_000, count=10):
        service = EconomyService(seed=1, catalog=self.catalog, shop_offers=offers)
        service.provision(
            "a", nn=1, name=b"A", guild=guild, balance=balance, cards=self.cards[:count]
        )
        return service

    def test_guild_zero_exposes_all_three_original_guild_packs(self):
        for guild, allowed in ((0, {21, 22, 23}), (1, {22}), (2, {23}), (3, {21})):
            service = self.shop(offers=ShopOffers(), guild=guild)
            session = number(enter(service))
            for pack_id in (21, 22, 23):
                before = service.balance("a")
                count = int(
                    parse_named(
                        service.handle("a", named(0xB2, 0x14, session, f"@Buy=/No={pack_id}"))[0]
                    )[3]["Card"]["S"]
                )
                self.assertEqual(count, 3 if pack_id in allowed else 0)
                service.handle("a", named(0xB2, 0x15, session, f"@Get=/No={session}"))
                self.assertEqual(
                    service.balance("a"),
                    before - (self.catalog.packs[pack_id]["price"] if count else 0),
                )

    def test_pauper_requires_both_strict_money_and_card_thresholds(self):
        for balance in (0, 999, 1000, 1001):
            for count in (0, 4, 5):
                service = self.shop(offers=ShopOffers(), balance=balance, count=count)
                session = number(enter(service))
                expected = balance < 1000 and count < 5
                self.assertEqual(
                    0 in ShopOffers().visible(self.catalog, 1, balance, count), expected
                )
                reply = service.handle("a", named(0xB2, 0x14, session, "@Buy=/No=0"))
                self.assertEqual(int(parse_named(reply[0])[3]["Card"]["S"]), 5 if expected else 0)
                service.handle("a", named(0xB2, 0x15, session, f"@Get=/No={session}"))
                self.assertEqual(
                    (service.balance("a"), len(service.inventory("a"))),
                    (balance, count + 5 * expected),
                )

    def test_changed_configuration_is_cached_until_next_shop_session(self):
        service = self.shop()
        first = enter(service)
        session = number(first)
        service.shop_offers = ShopOffers()
        self.assertEqual(enter(service), first)
        response = service.handle("a", named(0xB2, 0x14, session, "@Buy=/No=76"))
        self.assertEqual(parse_named(response[0])[3]["Card"]["S"], "3")
        service.handle("a", named(0xB2, 0x15, session, f"@Get=/No={session}"))
        service.handle("a", named(0xB2, 0x17, session, f"@Quit=/No={session}"))
        second = enter(service)
        self.assertNotEqual(number(second), session)
        self.assertEqual(parse_named(second[1])[3]["Init"]["HD"], "0")
        refused = service.handle("a", named(0xB2, 0x14, number(second), "@Buy=/No=76"))
        self.assertEqual(parse_named(refused[0])[3], {"Card": {"S": "0"}})

    def test_offer_config_rejects_unroundtrippable_and_ambiguous_fields(self):
        for values in (
            {"special_flag": 4},
            {"special_flag": True},
            {"special_flag": 3},
            {"special_name": b"12345678"},
            {"special_name": b"A\nB"},
            {"highlighted": (78,)},
            {"highlighted": (76, 76)},
            {"highlighted": [76]},
            {"highlighted_available": 1},
            {"highlighted": (5,), "highlighted_available": True},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ShopOffers(**values)
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as directory:
            path = Path(directory) / "offers.json"
            path.write_text(
                json.dumps(
                    dict(
                        special_flag=3,
                        special_name="Alice",
                        highlighted=[76, 77],
                        highlighted_available=True,
                    )
                )
            )
            self.assertEqual(load_shop_offers(path), OFFERS)
            path.write_text(json.dumps(dict(special_name="Alice", unauthorized_price=1)))
            with self.assertRaises(ValueError):
                load_shop_offers(path)


class EncryptedShopOfferTests(unittest.IsolatedAsyncioTestCase):
    request = services.ServicesIntegrationTests.request

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.server = Server(state_path=self.path, shop_offers=OFFERS)
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()
        self.server.ensure_account(self.client.nick)
        token = self.server.economy.reserve_match(self.key)
        token = self.server.economy.commit_match_money([(token, 0, 990000)])[self.key]
        self.server.economy.release_match(token)
        self.server.persist()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def enter(self):
        account = self.server.economy.account_view(self.key)
        replies = await self.request(
            SHOP_RAW,
            named(
                0xB0, 0, 0, f"@ShReq=/NN={account.nn:016X}/HID=0/Dm=0/Vol=0/CN={account.name.hex()}"
            ),
        )
        self.assertEqual(len(replies), 2)
        self.assertEqual(parse_named(replies[0])[3], {"ShEnter": {"EN": "1"}})
        self.assertEqual(parse_named(replies[1])[2], 0)
        self.session = number(replies)
        return replies

    async def buy(self, pack_id):
        replies = await self.request(
            SHOP_RAW, named(0xB2, 0x14, self.session, f"@Buy=/No={pack_id}")
        )
        self.assertEqual(len(replies), 1)
        return replies[0]

    async def get(self):
        self.assertEqual(
            await self.request(
                SHOP_RAW, named(0xB2, 0x15, self.session, f"@Get=/No={self.session}")
            ),
            [],
        )

    async def quit(self):
        replies = await self.request(
            SHOP_RAW, named(0xB2, 0x17, self.session, f"@Quit=/No={self.session}")
        )
        self.assertEqual(parse_named(replies[0])[3]["Quit"]["N"], str(self.session))

    async def test_rank_and_HD_purchases_use_native_prices_then_survive_restart(self):
        replies = await self.enter()
        init = parse_named(replies[1])[3]["Init"]
        self.assertEqual(
            (init["SP"], init["SN"], init["CMP"], init["HD"]), ("3", "416C696365", "1", "2|76|77")
        )
        expected_money, expected_count = 1_000_000, 10
        for pack_id, price, count in ((5, 100000, 3), (76, 300000, 3), (77, 300000, 4)):
            before = self.server.economy.inventory(self.key)
            reply = await self.buy(pack_id)
            self.assertEqual(
                await self.buy(pack_id),
                reply,
                "pending retransmission must not reroll or charge twice",
            )
            sections = parse_named(reply)[3]
            self.assertEqual(int(sections["Card"]["S"]), count)
            incoming = tuple(
                Card(*map(int, sections[f"N{i}"]["D"].split("|"))) for i in range(count)
            )
            self.assertEqual(self.server.economy.inventory(self.key), before)
            self.assertEqual(self.server.economy.balance(self.key), expected_money)
            await self.get()
            expected_money -= price
            expected_count += count
            self.assertEqual(self.server.economy.balance(self.key), expected_money)
            self.assertEqual(self.server.economy.inventory(self.key), before + incoming)
            for card in incoming:
                self.server.economy.catalog.validate(card)
        await self.quit()
        restored = Server(state_path=self.path, shop_offers=ShopOffers())
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
            self.assertEqual(
                (restored.economy.balance(self.key), len(restored.economy.inventory(self.key))),
                (300000, expected_count),
            )
            session = number(enter(restored.economy, self.key))
            refusal = restored.economy.handle(self.key, named(0xB2, 0x14, session, "@Buy=/No=76"))
            self.assertEqual(parse_named(refusal[0])[3], {"Card": {"S": "0"}})
        finally:
            await restored.stop()

    async def test_default_refusals_and_active_session_offer_cache(self):
        first = await self.enter()
        self.server.economy.shop_offers = ShopOffers()
        self.assertEqual(await self.enter(), first)
        self.assertEqual(parse_named(await self.buy(5))[3]["Card"]["S"], "3")
        await self.get()
        await self.quit()
        current = await self.enter()
        self.assertEqual(parse_named(current[1])[3]["Init"]["SP"], "0")
        before = self.server.economy.snapshot()
        for pack_id in (5, 76, 77):
            self.assertEqual(parse_named(await self.buy(pack_id))[3], {"Card": {"S": "0"}})
            self.assertEqual(self.server.economy.snapshot(), before)

    async def test_failed_get_save_closes_before_ack_and_keeps_prior_purchase(self):
        await self.enter()
        await self.buy(5)
        await self.get()
        await self.buy(76)
        saved = self.path.read_bytes()
        expected = self.server.economy.snapshot()
        with patch.object(Path, "replace", side_effect=OSError("injected offer Get save failure")):
            await self.client.send(
                b"NOTICE "
                + service_nick(SHOP_RAW)
                + b" :"
                + named(0xB2, 0x15, self.session, f"@Get=/No={self.session}")
            )
            self.assertEqual(await self.client.read(), b"")
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.path.read_bytes(), saved)
        restored = Server(state_path=self.path, shop_offers=OFFERS)
        try:
            self.assertEqual(restored.economy.snapshot(), expected)
            self.assertEqual(
                (restored.economy.balance(self.key), len(restored.economy.inventory(self.key))),
                (900000, 13),
            )
        finally:
            await restored.stop()


if __name__ == "__main__":
    unittest.main()
