"""Startup forgets exactly-once IDs that no client or recovery path can replay."""

import json
import unittest
from unittest.mock import patch

from tests.support.golden_save import EPOCH, EVENT, SNAPSHOT, WEEK
from tests.support.memory_store import MemoryStore
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.economy.service import EconomyService
from tmserver.network.server import Server
from tmserver.storage.validation import SERVICE_NAMES

PRUNED = {"match_economy", "result_rewards", "auction"}


def after_restart(snapshot):
    """The save a restart keeps when startup recovery has nothing left to do."""
    pending = {(row["key"], row["id"]) for row in snapshot["result_rewards"]["pending"]}
    return dict(
        snapshot,
        match_economy=after_restart_rounds(snapshot["match_economy"]),
        result_rewards=dict(
            snapshot["result_rewards"],
            known=[
                row for row in snapshot["result_rewards"]["known"]
                if (row["key"], row["id"]) in pending
            ],
        ),
        auction=after_restart_auction(snapshot["auction"]),
    )


def after_restart_rounds(journal):
    return dict(journal, rounds=[row for row in journal["rounds"] if not row["closed"]])


def after_restart_auction(journal):
    return dict(journal, external_returns=[])


class JournalPruningTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, store):
        clock = lambda: EPOCH + WEEK  # noqa: E731
        server = Server(
            store=store,
            account_count=3,
            match_mode="versus",
            card_prizes=True,
            allow_wagers=True,
            rating_mode="local-history",
            weekly_rankings=True,
            event_configs=(EVENT,),
            event_clock=clock,
            auction_clock=clock,
            ranking_clock=clock,
        )
        self.addAsyncCleanup(server.stop)
        return server

    async def test_restart_prunes_closed_rounds_and_recovered_reward_ids(self):
        golden = json.loads(SNAPSHOT.read_text(encoding="utf8"))
        self.assertEqual([row["closed"] for row in golden["match_economy"]["rounds"]], [True])
        self.assertEqual(len(golden["result_rewards"]["pending"]), 2)
        store = MemoryStore(golden)
        await self.start(store)
        saved = store.load()
        self.assertEqual(saved["match_economy"], {"version": 1, "rounds": []})
        self.assertEqual(saved["result_rewards"], {"version": 1, "known": [], "pending": []})
        self.assertEqual(saved["auction"]["external_returns"], [])
        # The recovered earned cards still wait in the Returned Card collection.
        returned = [p for p in saved["auction"]["pending"] if p["kind"] == "returned"]
        self.assertEqual(len(returned), 2)
        self.assertEqual(saved["auction"]["next_pending"], golden["auction"]["next_pending"] + 2)
        for field in ("listings", "history", "next_id"):
            self.assertEqual(saved["auction"][field], golden["auction"][field])
        for name in SERVICE_NAMES - PRUNED:
            with self.subTest(journal=name):
                self.assertEqual(saved[name], golden[name])
        # A second restart has nothing to recover or prune, so it writes nothing.
        with patch.object(store, "save", side_effect=AssertionError("startup save")):
            await self.start(store)

    def test_reward_ids_of_pending_rows_are_kept(self):
        golden = json.loads(SNAPSHOT.read_text(encoding="utf8"))
        economy = EconomyService.restore(golden)
        auction = AuctionEconomy.restore(economy, golden["auction"])
        rewards = ResultRewardService.restore(economy, auction, golden["result_rewards"])
        self.assertEqual(rewards.prune_known(), 0)
        self.assertEqual(rewards.snapshot(), golden["result_rewards"])


if __name__ == "__main__":
    unittest.main()
