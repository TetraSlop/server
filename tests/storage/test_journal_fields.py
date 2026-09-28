"""Reject journal extensions before restore or recovery can discard their data."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tmserver.cli import admin
from tmserver.economy.auction_ledger import AuctionEconomy
from tmserver.profiles.characters import CharacterProfiles
from tmserver.cards.catalog import Catalog
from tmserver.economy.service import EconomyService
from tmserver.economy.prize_center import PrizeService
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.network.server import Server
from tmserver.storage.validation import validate_snapshot


LOSSY_PATHS = (
    ("auction",),
    ("auction", "external_returns", 0),
    ("prize",),
    ("prize", "accounts", "a"),
    ("prize", "accounts", "a", "pending"),
    ("result_rewards",),
    ("result_rewards", "known", 0),
    ("result_rewards", "pending", 0),
    ("character_profiles",),
)


class JournalFieldsTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "state.json"
        self.economy = EconomyService(seed=1, catalog=self.catalog)
        for number, key in enumerate(("a", "b"), 1):
            self.economy.provision(key, nn=number, player_id=number, name=key.encode())
        auction = AuctionEconomy(self.economy)
        listing = auction.exhibit("a", self.economy.inventory("a")[0], 100, 20, 1, 0, now=100)
        auction.bid("b", listing.id, 100, 0, now=101)
        auction.advance(listing.expires)
        auction.enqueue_returned("a", "returned-1", self.economy.inventory("a")[:1])
        prize = PrizeService(self.economy)
        prize.grant_award("a", "award-1", top30=10)
        rewards = ResultRewardService(self.economy, auction)
        token = self.economy.reserve_match("b")
        rewards.stage("b", token, "reward-1", 0, self.economy.inventory("b")[:1])
        self.economy.release_match(token)
        profiles = CharacterProfiles(self.economy)
        profiles.records["a"] = tuple(profiles._fields("a"))
        self.saved = json.loads(
            json.dumps(
                dict(
                    self.economy.snapshot(),
                    auction=auction.snapshot(),
                    prize=prize.snapshot(),
                    result_rewards=rewards.snapshot(),
                    character_profiles=profiles.snapshot(),
                )
            )
        )

    def extended(self, parts):
        snapshot = copy.deepcopy(self.saved)
        row = snapshot
        for part in parts:
            row = row[part]
        row["future_field"] = {"keep": [123, "not understood by this version"]}
        return snapshot

    def restore_journal(self, name, snapshot):
        if name == "result_rewards":
            return ResultRewardService.restore(self.economy, AuctionEconomy(self.economy), snapshot)
        service = {
            "auction": AuctionEconomy,
            "prize": PrizeService,
            "character_profiles": CharacterProfiles,
        }[name]
        return service.restore(self.economy, snapshot)

    def seed(self, snapshot):
        self.path.write_text(json.dumps(snapshot), encoding="utf8")
        return self.path.read_bytes()

    def test_service_restore_rejects_every_manually_decoded_extension_without_mutation(self):
        economy_before = self.economy.snapshot()
        for parts in LOSSY_PATHS:
            with self.subTest(path=parts):
                snapshot = self.extended(parts)
                before = copy.deepcopy(snapshot)
                with self.assertRaises(ValueError):
                    self.restore_journal(parts[0], snapshot[parts[0]])
                self.assertEqual(snapshot, before)
                self.assertEqual(self.economy.snapshot(), economy_before)

    def test_shared_import_validation_rejects_the_same_extensions(self):
        for parts in LOSSY_PATHS:
            with self.subTest(path=parts), self.assertRaises(ValueError):
                validate_snapshot(self.extended(parts))

    async def test_startup_rejects_before_interrupted_rewards_rewrite_the_save(self):
        for parts in LOSSY_PATHS:
            with self.subTest(path=parts):
                original = self.seed(self.extended(parts))
                with self.assertRaises(ValueError):
                    game = Server(state_path=self.path)
                    self.addAsyncCleanup(game.stop)
                self.assertEqual(self.path.read_bytes(), original)

    def test_admin_inspection_and_mutation_reject_without_rewriting(self):
        for command in (
            ["accounts"],
            ["prize-award", "--account-key", "b", "--id", "award-2", "--top30", "5"],
        ):
            for parts in LOSSY_PATHS:
                with self.subTest(command=command[0], path=parts):
                    original = self.seed(self.extended(parts))
                    args = admin.arguments(["--state", str(self.path), *command])
                    with self.assertRaises(ValueError):
                        admin.execute(args)
                    self.assertEqual(self.path.read_bytes(), original)

    def test_current_journals_roundtrip_exactly_without_mutating_input(self):
        before = copy.deepcopy(self.saved)
        for name in ("auction", "prize", "result_rewards", "character_profiles"):
            with self.subTest(journal=name):
                restored = self.restore_journal(name, self.saved[name])
                self.assertEqual(json.loads(json.dumps(restored.snapshot())), self.saved[name])
        self.assertEqual(self.saved, before)
        validate_snapshot(self.saved)

    def test_existing_dataclass_records_also_reject_unknown_fields(self):
        for parts in (
            ("auction", "listings", 0),
            ("auction", "listings", 0, "seller"),
            ("auction", "listings", 0, "bidder"),
            ("auction", "history", 0),
            ("auction", "history", 0, "bidder"),
            ("auction", "pending", 0),
        ):
            with self.subTest(path=parts), self.assertRaises(ValueError):
                snapshot = self.extended(parts)
                AuctionEconomy.restore(self.economy, snapshot["auction"])

    def test_legacy_optional_external_returns_and_absent_journals_remain_supported(self):
        legacy = copy.deepcopy(self.saved["auction"])
        del legacy["external_returns"]
        before = copy.deepcopy(legacy)
        restored = AuctionEconomy.restore(self.economy, legacy)
        expected = dict(legacy, external_returns=[])
        self.assertEqual(json.loads(json.dumps(restored.snapshot())), expected)
        self.assertEqual(legacy, before)
        for name in ("auction", "prize", "result_rewards", "character_profiles"):
            with self.subTest(journal=name):
                self.restore_journal(name, None)
        validate_snapshot(self.economy.snapshot())
        validate_snapshot(
            dict(
                self.economy.snapshot(),
                auction=None,
                prize=None,
                result_rewards=None,
                character_profiles=None,
            )
        )


if __name__ == "__main__":
    unittest.main()
