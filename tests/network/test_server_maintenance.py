"""Auction maintenance owns expiry and shutdown independently of ordinary tables."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.economy.test_auction_service import bid_request, exhibit_request
from tmserver.economy.claim_transactions import auction_transaction
from tmserver.rooms.config import OrdinaryRoomConfig
from tmserver.network.server import Server


class MaintenanceClock:
    """Release one maintenance interval without changing other asyncio tasks."""

    def __init__(self):
        self.now = 100
        self.waiting = asyncio.Queue()

    async def sleep(self, delay):
        pending = asyncio.get_running_loop().create_future()
        self.waiting.put_nowait(pending)
        await pending

    async def paused(self):
        return await asyncio.wait_for(self.waiting.get(), 2)


class ServerMaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = Path(
            self.enterContext(tempfile.TemporaryDirectory(prefix="tmserver-maintenance-"))
        )
        self.path = folder / "state.json"
        self.log_path = folder / "game.jsonl"
        self.clock = MaintenanceClock()
        # Patching asyncio.sleep itself would also intercept every table watchdog.
        server_asyncio = SimpleNamespace(**vars(asyncio))
        server_asyncio.sleep = self.clock.sleep
        self.enterContext(patch("tmserver.network.server.asyncio", server_asyncio))
        extra = OrdinaryRoomConfig(b"Second Room", 0x101, 0x201, b"#ROOM2", b"#TABLE2")
        self.server = Server(
            state_path=self.path,
            log_path=self.log_path,
            auction_clock=lambda: self.clock.now,
            ordinary_rooms=(extra,),
        )
        self.addAsyncCleanup(self.server.stop)
        self.seller, self.buyer = (
            self.server.ensure_account(nick) for nick in self.server.accounts
        )

    def exhibit(self, *, hours=1):
        with auction_transaction(self.server, (self.seller,)) as auction:
            auction.handle(
                self.seller,
                exhibit_request(self.server.economy, self.seller, hours=hours),
            )
        return self.server.auction_ledger.listings()[-1]

    def bid(self, item):
        with auction_transaction(self.server, (self.buyer,)) as auction:
            auction.handle(
                self.buyer,
                bid_request(self.server.economy, self.buyer, item.id, 100, item.revision),
            )

    async def test_idle_multitable_expiry_is_durable_and_stop_prevents_later_expiry(self):
        first = self.exhibit()
        later = self.exhibit(hours=2)
        self.bid(first)
        await self.server.start(0)
        self.assertEqual(len(self.server.ordinary_tables), 2)
        maintenance = self.server.auction_task
        pending = await self.clock.paused()

        self.clock.now = first.expires - 1
        pending.set_result(None)
        pending = await self.clock.paused()
        self.assertEqual(self.server.auction_ledger.listing(first.id).status, "active")
        self.assertEqual(self.server.auction_ledger.pending(self.seller), ())
        self.assertEqual(self.server.auction_ledger.pending(self.buyer), ())

        self.clock.now = first.expires
        pending.set_result(None)
        pending = await self.clock.paused()
        self.assertEqual(self.server.auction_ledger.listing(first.id).status, "sold")
        self.assertEqual(self.server.auction_ledger.listing(later.id).status, "active")
        payments = self.server.auction_ledger.pending(self.seller)
        purchases = self.server.auction_ledger.pending(self.buyer)
        self.assertEqual([(p.kind, p.amount) for p in payments], [("payment", 100)])
        self.assertEqual([(p.kind, p.card) for p in purchases], [("won", first.card)])
        saved = json.loads(self.path.read_text(encoding="utf8"))
        self.assertEqual(saved["auction"], self.server.auction_ledger.snapshot())

        before_stop = self.server.snapshot()
        before_bytes = self.path.read_bytes()
        await self.server.stop()
        self.assertTrue(maintenance.done())
        self.assertTrue(pending.cancelled())
        self.assertIsNone(self.server.auction_task)
        self.clock.now = later.expires + 1
        await asyncio.sleep(0)
        self.assertEqual(self.server.snapshot(), before_stop)
        self.assertEqual(self.path.read_bytes(), before_bytes)
        self.assertEqual(self.server.auction_ledger.listing(later.id).status, "active")

    async def test_expiry_write_failure_keeps_listing_and_stops_maintenance_without_retry(self):
        item = self.exhibit()
        self.bid(item)
        await self.server.start(0)
        maintenance = self.server.auction_task
        pending = await self.clock.paused()
        before = self.server.snapshot()
        before_bytes = self.path.read_bytes()
        failure = OSError("injected auction storage failure")

        with patch.object(self.server.store, "save_changes", side_effect=failure):
            self.clock.now = item.expires
            pending.set_result(None)
            await asyncio.wait_for(asyncio.shield(maintenance), 2)

        self.assertIs(self.server.storage_failed, failure)
        self.assertFalse(self.server.listener.is_serving())
        self.assertEqual(self.server.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), before_bytes)
        self.assertEqual(self.server.auction_ledger.listing(item.id).status, "active")
        self.assertEqual(self.server.auction_ledger.pending(self.seller), ())
        self.assertEqual(self.server.auction_ledger.pending(self.buyer), ())
        self.clock.now += 3600
        await asyncio.sleep(0)
        self.assertTrue(self.clock.waiting.empty())
        self.assertEqual(self.server.snapshot(), before)
        await self.server.stop()
        records = [
            json.loads(line) for line in self.log_path.read_text(encoding="utf8").splitlines()
        ]
        self.assertEqual(sum(row["event"] == "auction_storage_error" for row in records), 1)


if __name__ == "__main__":
    unittest.main()
