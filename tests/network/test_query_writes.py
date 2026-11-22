"""Auction searches and profile probes preserve query/write boundaries."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tests.economy.auctions.test_auction_service import (
    bid_request,
    collection_request,
    exhibit_request,
    request as auction_request,
)
from tests.network.test_server import Client
from tests.profiles.test_new_profile import request as init_request
from tmserver.economy.packets import named
from tmserver.network.dispatch import dispatch
from tmserver.network.server import Server
from tmserver.protocol.commands import Command, encode_service, parse
from tmserver.protocol.identities import ROOM_RAW, SHOP_RAW, player_id, service_nick


def shop_entry(economy, key):
    view = economy.account_view(key)
    return named(0xB0, 0, 0, f"@ShReq=/NN={view.nn:016X}/HID=1/Dm=1/Vol=1/CN={view.name.hex()}")


class QueryWriteBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Mock()
        self.store.load.return_value = None
        self.now = 1700000000
        self.host = Server(store=self.store, auction_clock=lambda: self.now)
        self.nick, other = tuple(self.host.accounts)[:2]
        self.key = self.host.ensure_account(self.nick)
        self.other = self.host.ensure_account(other)
        self.peer = Mock(server=self.host, nick=self.nick, send=AsyncMock())
        self.target = service_nick(ROOM_RAW)
        self.store.reset_mock()

    async def send(self, payload, target=None):
        self.peer.send.reset_mock()
        handled = await dispatch(self.peer, target or self.target, payload)
        replies = [call.args[0].split(b" :", 1)[1] for call in self.peer.send.call_args_list]
        return handled, replies

    async def test_unowned_auction_commands_never_advance_or_save(self):
        before = self.host.snapshot()
        for payload in (encode_service(b"A", [Command(b"ZZ", b"")]), b"GTM0A"):
            with (
                self.subTest(payload=payload),
                patch.object(self.host, "advance_auctions") as advance,
            ):
                self.assertEqual(await self.send(payload), (False, []))
                advance.assert_not_called()
                self.assertEqual(self.store.method_calls, [])
        self.assertEqual(self.host.snapshot(), before)

    async def test_searches_keep_private_snapshot_lifecycle_without_saving(self):
        self.host.auction.handle(self.key, exhibit_request(self.host.economy, self.key))
        listing = self.host.auction_ledger.listings()[0]
        self.host.auction.handle(
            self.other, bid_request(self.host.economy, self.other, listing.id, 100, 0)
        )
        token = self.host.economy.reserve_match(self.key)
        before = self.host.snapshot()
        paths = []
        for operation, size in ((b"SI", 0xC0), (b"BH", 0x48)):
            handled, replies = await self.send(
                auction_request(self.host.economy, self.key, operation)
            )
            self.assertTrue(handled)
            commands = parse(replies[0][5:])
            path = commands[0].payload.decode()
            paths.append(path)
            data = self.host.auction.file(self.key, path)
            self.assertEqual(len(data), size)
            self.assertIsNone(self.host.auction.file(self.other, path))
            self.assertEqual(self.store.method_calls, [])
            self.assertEqual(self.host.snapshot(), before)
            self.assertIs(self.host.economy._reservations[self.key], token)
        self.assertNotEqual(*paths)
        self.assertTrue(self.host.auction.delete(self.key, paths[0]))
        self.assertIsNone(self.host.auction.file(self.key, paths[0]))
        self.assertIsNotNone(self.host.auction.file(self.key, paths[1]))
        self.host.auction.disconnect(self.key)
        self.assertIsNone(self.host.auction.file(self.key, paths[1]))
        self.assertIs(self.host.economy._reservations[self.key], token)

    async def test_search_expiry_commits_once_before_its_snapshot(self):
        self.host.auction.handle(self.key, exhibit_request(self.host.economy, self.key))
        listing = self.host.auction_ledger.listings()[0]
        self.now = listing.expires
        order = []
        self.store.save_changes.side_effect = lambda *_: order.append("save")
        self.peer.send.side_effect = lambda *_: order.append("reply")
        handled, replies = await self.send(auction_request(self.host.economy, self.key, b"SI"))
        self.assertTrue(handled)
        self.assertEqual(order, ["save", "reply"])
        self.store.save_changes.assert_called_once_with(
            {}, {"auction": self.host.auction_ledger.snapshot()}
        )
        self.assertEqual(self.host.auction_ledger.listing(listing.id).status, "returned")
        self.assertEqual(parse(replies[0][5:])[-1].payload, b"0")

    async def test_shop_entry_commits_only_its_account_not_the_auction_journal(self):
        payload = shop_entry(self.host.economy, self.key)
        handled, replies = await self.send(payload, service_nick(SHOP_RAW))
        self.assertTrue(handled)
        self.assertTrue(replies)
        self.store.save_changes.assert_called_once_with(
            {self.key: self.host.snapshot()["accounts"][self.key]}, {}
        )

    async def test_invalid_named_envelopes_and_forged_shop_identity_never_save(self):
        request = shop_entry(self.host.economy, self.key)
        cases = (
            request.rsplit(b"/CN=", 1)[0] + b"/CN=466F72676564",
            request + b"/CN=00",
            b"GTM0GB0",
            named(0xB0, 2, 0, "@Unknown=") + b"/broken",
            b"GTM0A<ZZ>missing-delimiters",
            encode_service(b"A", [Command(b"SI", b"forged")]),
        )
        before = self.host.snapshot()
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                await self.send(payload, service_nick(SHOP_RAW))
            self.assertEqual(self.store.method_calls, [])
            self.peer.send.assert_not_awaited()
        self.assertEqual(self.host.snapshot(), before)

    async def test_valid_unowned_named_subtype_does_not_touch_auction_storage(self):
        self.assertEqual(await self.send(named(0xB0, 2, 0, "@Unknown=")), (False, []))
        self.assertEqual(self.store.method_calls, [])

    async def test_collection_keeps_priority_without_saving_unowned_families(self):
        await self.send(collection_request(self.host.economy, self.key))
        reservation = self.host.economy._reservations[self.key]
        before = self.host.snapshot()
        self.store.reset_mock()
        view = self.host.economy.account_view(self.key)
        prize = named(
            0x70, 0, 0, f"@CvReq=/NN={view.nn:016X}/HID=1/Dm=1/Vol=1/CN={view.name.hex()}"
        )
        for payload in (prize, named(0xB0, 2, 0, "@Unknown=")):
            with self.subTest(payload=payload):
                self.assertEqual(await self.send(payload), (False, []))
                self.assertEqual(self.store.method_calls, [])
                self.assertEqual(self.host.snapshot(), before)
                self.assertTrue(self.host.auction.collection_active(self.key))
                self.assertFalse(self.host.prize.is_active(self.key))
                self.assertIs(self.host.economy._reservations[self.key], reservation)

    async def test_profile_probe_and_same_guild_retry_do_not_save_or_release_reservations(self):
        guild = self.host.economy.account_view(self.key).guild
        self.assertNotEqual(guild, 0)
        token = self.host.economy.reserve_match(self.key)
        before = self.host.snapshot()
        for selection in (0, guild):
            handled, replies = await self.send(init_request(self.host.economy, self.key, selection))
            self.assertTrue(handled)
            self.assertEqual(replies, [named(0x81, 0, 0, f"@InitAns=/Ans={2 if selection else 1}")])
            self.assertEqual(self.store.method_calls, [])
            self.assertEqual(self.host.snapshot(), before)
            self.assertIs(self.host.economy._reservations[self.key], token)


class EncryptedQueryWriteBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.json"
        self.now = 1700000000
        self.host = Server(state_path=self.path, auction_clock=lambda: self.now)
        self.nick = next(iter(self.host.accounts))
        self.key = self.nick.decode()
        self.host.economy.provision(self.key, nn=player_id(self.nick), guild=0)
        self.host.account_keys.add(self.key)
        self.host.persist()
        self.client = Client()
        await self.client.open(await self.host.start(0), account=self.nick)
        self.assertIn(b" 422 ", await self.client.read())

    async def asyncTearDown(self):
        await self.client.close()
        await self.host.stop()
        self.temp.cleanup()

    async def send(self, payload):
        await self.client.send(b"NOTICE " + service_nick(ROOM_RAW) + b" :" + payload)
        await self.client.send(b"PING :query-barrier")
        replies = []
        for _ in range(20):
            line = await self.client.read()
            self.assertTrue(line, "query unexpectedly closed the encrypted connection")
            if line == b"PONG query-barrier":
                return replies
            replies.append(line.split(b" :", 1)[1])
        self.fail("response limit exceeded")

    async def test_reads_succeed_with_failing_store_and_registration_remains_durable(self):
        for guild in (0, 2):
            if guild:
                self.assertEqual(
                    await self.send(init_request(self.host.economy, self.key, guild)),
                    [named(0x81, 0, 0, "@InitAns=/Ans=2")],
                )
                self.assertEqual(self.host.store.load()["accounts"][self.key]["guild"], guild)
            before = self.path.read_bytes()
            requests = (
                init_request(self.host.economy, self.key, guild),
                auction_request(self.host.economy, self.key, b"SI"),
                auction_request(self.host.economy, self.key, b"BH"),
                encode_service(b"A", [Command(b"ZZ", b"")]),
            )
            with (
                patch.object(
                    self.host.store, "save_changes", side_effect=OSError("unavailable")
                ) as changes,
            ):
                for payload in requests:
                    await self.send(payload)
                changes.assert_not_called()
            self.assertIsNone(self.host.storage_failed)
            self.assertEqual(self.path.read_bytes(), before)

    async def test_exhibit_and_query_expiry_remain_durable(self):
        replies = await self.send(exhibit_request(self.host.economy, self.key))
        self.assertEqual(parse(replies[0][5:])[0].token, b"ES")
        self.assertEqual(self.host.store.load(), self.host.snapshot())
        listing = self.host.auction_ledger.listings()[0]
        self.now = listing.expires
        with patch.object(
            self.host.store, "save_changes", wraps=self.host.store.save_changes
        ) as save:
            replies = await self.send(auction_request(self.host.economy, self.key, b"SI"))
            save.assert_called_once()
        self.assertEqual(self.host.auction_ledger.listing(listing.id).status, "returned")
        self.assertEqual(parse(replies[0][5:])[-1].payload, b"0")
        self.assertEqual(self.host.store.load(), self.host.snapshot())

    async def test_failed_expiry_sends_no_query_snapshot_and_preserves_last_save(self):
        await self.send(exhibit_request(self.host.economy, self.key))
        listing = self.host.auction_ledger.listings()[0]
        self.now = listing.expires
        before = self.path.read_bytes(), self.host.snapshot()
        with patch.object(self.host.store, "save_changes", side_effect=OSError("expiry failure")):
            await self.client.send(
                b"NOTICE "
                + service_nick(ROOM_RAW)
                + b" :"
                + auction_request(self.host.economy, self.key, b"SI")
            )
            self.assertEqual(await self.client.read(), b"")
        self.assertIsNotNone(self.host.storage_failed)
        self.assertEqual((self.path.read_bytes(), self.host.snapshot()), before)
        self.assertEqual(self.host.auction._snapshots, {})
