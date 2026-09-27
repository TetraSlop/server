"""A lost collection response cannot undo or duplicate a saved delivery."""

import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.test_auction_service import bid_request, collection_request, exhibit_request
from tests.test_event_rooms import config
from tests.test_server import Client
from tmserver.claim_transactions import auction_transaction, event_transaction, prize_transaction
from tmserver.economy_service import Card, named, parse_named
from tmserver.identities import SHOP_RAW, service_nick
from tmserver.server import Server


SHOP = service_nick(SHOP_RAW)
EVENT = service_nick(0x1002)


class CollectionPartialDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1

    @asynccontextmanager
    async def case(self):
        with tempfile.TemporaryDirectory() as directory:
            self.path = Path(directory) / "state.json"
            self.now = 200
            self.config = config(card_service_enabled=True)
            self.game = Server(
                state_path=self.path,
                account_count=3,
                event_configs=(self.config,),
                event_clock=lambda: self.now,
                auction_clock=lambda: self.now,
            )
            self.keys = [self.game.ensure_account(nick) for nick in self.game.accounts]
            self.key, self.other, self.buyer = self.keys
            self.port = await self.game.start(0)
            self.clients = []
            try:
                self.client = await self.connect(self.key)
                self.witness = await self.connect(self.other)
                replies = await self.request(self.witness, SHOP, self.prize_entry(self.other))
                self.witness_number = int(parse_named(replies[1])[3]["ExcInit"]["N"])
                self.witness_token = self.game.economy._reservations[self.other]
                self.witness_account = self.game.economy.snapshot()["accounts"][self.other]
                yield
            finally:
                for client in self.clients:
                    await client.close()
                await self.game.stop()

    async def connect(self, key):
        client = Client()
        self.clients.append(client)
        await client.open(self.port, account=key.encode())
        self.assertIn(b" 422 ", await client.read())
        return client

    async def request(self, client, target, payload):
        await client.send(b"NOTICE " + target + b" :" + payload)
        await client.send(b"PING :collection-barrier")
        replies = []
        for _ in range(16):
            line = await client.read()
            self.assertTrue(line, "unexpected disconnect")
            if line == b"PONG collection-barrier":
                return replies
            self.assertIn(b" NOTICE " + client.nick + b" :GTM0G", line)
            replies.append(line.split(b" :", 1)[1])
        self.fail("collection response limit exceeded")

    def prize_entry(self, key):
        view = self.game.economy.account_view(key)
        return named(0x70, 0, 0, f"@CvReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}")

    def event_entry(self):
        view = self.game.economy.account_view(self.key)
        return named(
            0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
        )

    async def interrupt_entry(self, target, payload, cutoff):
        peer = self.game.nicks[self.client.nick]
        send, close = peer.send, peer.close
        closed = asyncio.Event()
        attempted = 0

        async def write_failure(message):
            nonlocal attempted
            if not peer.closed:
                index = attempted
                attempted += 1
                if index == cutoff:
                    raise ConnectionResetError("injected collection response write failure")
            await send(message)

        async def finish_close():
            try:
                await close()
            finally:
                closed.set()

        # Earlier packets use the real encrypted writer. The chosen packet
        # fails before framing; normal socket-error handling owns all cleanup.
        with (
            patch.object(peer, "send", side_effect=write_failure),
            patch.object(peer, "close", side_effect=finish_close),
        ):
            await self.client.send(b"NOTICE " + target + b" :" + payload)
            for _ in range(cutoff):
                line = await self.client.read()
                self.assertIn(b" NOTICE " + self.client.nick + b" :GTM0G", line)
            self.assertEqual(await self.client.read(), b"")
            await asyncio.wait_for(closed.wait(), 10)
        self.assertEqual(attempted, cutoff + 1)
        self.assertNotIn(self.client.nick, self.game.nicks)
        self.assertFalse(self.game.economy.is_busy(self.key))
        self.assertFalse(self.game.auction.collection_active(self.key))
        self.assertFalse(self.game.prize.is_active(self.key))
        self.assertFalse(self.game.event_cards.is_active(self.key))
        self.assertIsNone(self.game.storage_failed)
        await self.assert_witness()
        self.assert_saved()
        self.client = await self.connect(self.key)

    async def assert_witness(self):
        self.assertIs(self.game.economy._reservations[self.other], self.witness_token)
        self.assertEqual(self.game.economy.snapshot()["accounts"][self.other], self.witness_account)
        self.assertEqual(self.game.prize.status(self.other)["session"], self.witness_number)
        await self.witness.send(b"PING :unrelated-collection")
        self.assertEqual(await self.witness.read(), b"PONG unrelated-collection")

    def assert_saved(self):
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")),
            json.loads(json.dumps(self.game.snapshot())),
        )

    async def barrier(self, target, subtype, number):
        token = "Get" if subtype == 0x15 else "Quit"
        return await self.request(
            self.client, target, named(0xB2, subtype, number, f"@{token}=/No={number}")
        )

    async def test_prize_entry_losses_preserve_redeemed_points_and_allow_one_purchase(self):
        for cutoff in range(2):  # CvEnter, ExcInit.
            with self.subTest(failed_response=cutoff):
                async with self.case():
                    with prize_transaction(self.game, self.key) as prize:
                        prize.grant_award(self.key, "partial.prize", top30=5000)
                    before = self.game.economy.inventory(self.key)
                    await self.interrupt_entry(SHOP, self.prize_entry(self.key), cutoff)
                    self.assertEqual(self.game.economy.prize_points(self.key), 5000)
                    self.assertEqual(self.game.economy.inventory(self.key), before)
                    replies = await self.request(self.client, SHOP, self.prize_entry(self.key))
                    fields = parse_named(replies[1])[3]["ExcInit"]
                    self.assertEqual(fields["PP"], "5000|0|0|0")
                    number = int(fields["N"])
                    await self.request(self.client, SHOP, named(0xB2, 0x14, number, "@Buy=/No=45"))
                    await self.barrier(SHOP, 0x15, number)
                    await self.barrier(SHOP, 0x17, number)
                    self.assertEqual(len(self.game.economy.inventory(self.key)), len(before) + 1)
                    self.assertEqual(
                        self.game.economy.prize_points(self.key),
                        5000 - self.game.economy.catalog.packs[45]["price"],
                    )
                    self.assertFalse(
                        self.game.prize.grant_award(self.key, "partial.prize", top30=5000)
                    )
                    self.assertFalse(self.game.economy.is_busy(self.key))
                    self.assert_saved()
                    await self.assert_witness()

    async def test_event_entry_losses_preserve_cards_across_two_visits_without_repaying(self):
        for cutoff in range(2):  # CardEnt, EcmInit with nine cards.
            with self.subTest(failed_response=cutoff):
                async with self.case():
                    card = replace(self.game.economy.inventory(self.key)[0], deck=255)
                    with event_transaction(self.game, (self.key,)) as state:
                        state.events.admit_room(self.key, "festival")
                        state.cards.grant(
                            self.key, "festival", "partial.event", cards=(card,) * 12, money=75
                        )
                    before = self.game.economy.inventory(self.key)
                    await self.interrupt_entry(EVENT, self.event_entry(), cutoff)
                    self.assertEqual(self.game.economy.balance(self.key), 10075)
                    self.assertEqual(self.game.economy.inventory(self.key), before)
                    self.assertEqual(
                        self.game.event_cards.pending(self.key, "festival"),
                        {"cards": 12, "money": 0},
                    )
                    for count in (9, 3):
                        replies = await self.request(self.client, EVENT, self.event_entry())
                        fields = parse_named(replies[1])[3]["EcmInit"]
                        self.assertEqual(
                            (fields["C"], fields["M"], fields["PM"]), (str(count), "10075", "0")
                        )
                        number = int(fields["N"])
                        await self.barrier(EVENT, 0x15, number)
                        await self.barrier(EVENT, 0x17, number)
                    self.assertEqual(self.game.economy.inventory(self.key), before + (card,) * 12)
                    self.assertEqual(self.game.economy.balance(self.key), 10075)
                    self.assertEqual(
                        self.game.event_cards.pending(self.key, "festival"),
                        {"cards": 0, "money": 0},
                    )
                    self.assertFalse(self.game.economy.is_busy(self.key))
                    self.assert_saved()
                    await self.assert_witness()

    async def test_auction_entry_losses_preserve_all_pages_without_repaying_seller(self):
        for cutoff in range(4):  # Init, Data/payment, two Returned Cards pages.
            with self.subTest(failed_response=cutoff):
                async with self.case():
                    with auction_transaction(self.game, (self.key, self.buyer)) as auction:
                        auction.handle(self.key, exhibit_request(auction.economy, self.key))
                        auction.handle(
                            self.buyer, bid_request(auction.economy, self.buyer, 1, 100, 0)
                        )
                        catalog = auction.economy.catalog
                        card = Card(249, 255, 3, 255, 255, catalog.points(249, 255, 255, 255), 255)
                        auction.ledger.enqueue_returned(self.key, "partial.return", (card,) * 30)
                    self.now = self.game.auction_ledger.listing(1).expires
                    self.game.advance_auctions()
                    before = self.game.economy.inventory(self.key)
                    buyer_pending = self.game.auction_ledger.pending(self.buyer)
                    await self.interrupt_entry(
                        SHOP, collection_request(self.game.economy, self.key), cutoff
                    )
                    self.assertEqual(self.game.economy.balance(self.key), 10100)
                    self.assertEqual(self.game.economy.inventory(self.key), before)
                    pending = self.game.auction_ledger.pending(self.key)
                    self.assertEqual(len(pending), 30)
                    self.assertTrue(all(entry.kind == "returned" for entry in pending))
                    replies = await self.request(
                        self.client, SHOP, collection_request(self.game.economy, self.key)
                    )
                    self.assertEqual(len(replies), 4)
                    self.assertEqual(parse_named(replies[1])[3]["Data"]["M"], "0")
                    self.assertEqual(
                        sum(int(parse_named(reply)[3]["Card"]["C"]) for reply in replies[2:]), 30
                    )
                    number = int(parse_named(replies[0])[3]["Init"]["N"])
                    await self.barrier(SHOP, 0x15, number)
                    await self.barrier(SHOP, 0x15, number)
                    self.assertEqual(self.game.economy.inventory(self.key), before + (card,) * 30)
                    self.assertEqual(self.game.economy.balance(self.key), 10100)
                    self.assertFalse(self.game.auction_ledger.pending(self.key))
                    self.assertEqual(self.game.auction_ledger.pending(self.buyer), buyer_pending)
                    self.assertFalse(self.game.economy.is_busy(self.key))
                    self.assert_saved()
                    await self.assert_witness()
