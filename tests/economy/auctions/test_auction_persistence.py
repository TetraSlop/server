"""Auction save schemas preserve records while rejecting coercion and corruption."""

import copy
import json
import unittest
from dataclasses import FrozenInstanceError, is_dataclass, replace

from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.auctions.models import AuctionError
from tmserver.economy.service import EconomyService


class AuctionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.economy = EconomyService(seed=32)
        for number, key in enumerate(("seller", "buyer", "third"), 1):
            self.economy.provision(key, nn=number, player_id=number, name=key.encode())
        self.auction = AuctionEconomy(self.economy)
        listing = self.auction.exhibit(
            "seller", self.economy.inventory("seller")[0], 100, 20, 1, 0, now=100
        )
        self.auction.bid("buyer", listing.id, 100, 0, now=101)
        self.auction.bid("third", listing.id, 120, 1, now=102)
        self.auction.advance(listing.expires)
        self.auction.enqueue_returned("buyer", "earned:1", self.economy.inventory("buyer")[:1])
        self.saved = self.auction.snapshot()

    def assert_rejected(self, saved):
        before, accounts = copy.deepcopy(saved), self.economy.snapshot()
        with self.assertRaises(AuctionError):
            AuctionEconomy.restore(self.economy, saved)
        self.assertEqual(saved, before)
        self.assertEqual(self.economy.snapshot(), accounts)
        self.assertEqual(self.auction.snapshot(), self.saved)

    def test_roundtrip_preserves_bytes_arrays_and_frozen_records(self):
        before = copy.deepcopy(self.saved)
        restored = AuctionEconomy.restore(self.economy, json.loads(json.dumps(self.saved)))
        self.assertEqual(restored.snapshot(), self.saved)
        self.assertEqual(self.saved, before)
        row = self.saved["listings"][0]
        self.assertEqual(row["seller"]["name"], b"seller".hex())
        self.assertIsInstance(row["card"], list)
        self.assertIsInstance(row["item_metadata"], list)
        listing = restored.listings(active_only=False)[0]
        self.assertTrue(is_dataclass(listing))
        self.assertEqual(replace(listing, revision=3).revision, 3)
        with self.assertRaises(FrozenInstanceError):
            listing.revision = 3

    def test_legacy_optional_returns_and_hex_spelling_remain_supported(self):
        legacy = copy.deepcopy(self.saved)
        del legacy["external_returns"]
        restored = AuctionEconomy.restore(self.economy, legacy)
        self.assertEqual(restored.snapshot(), dict(legacy, external_returns=[]))
        modified = copy.deepcopy(self.saved)
        modified["listings"][0]["seller"]["name"] = "73 65 6C 6C 65 72"
        self.assertEqual(AuctionEconomy.restore(self.economy, modified).snapshot(), self.saved)
        # Card's original positional API defaults an omitted eighth/deck field.
        modified["listings"][0]["card"].pop()
        modified["external_returns"][0]["cards"][0].pop()
        for pending in modified["pending"]:
            if pending["card"] is not None:
                pending["card"].pop()
        self.assertEqual(AuctionEconomy.restore(self.economy, modified).snapshot(), self.saved)
        self.assertEqual(AuctionEconomy.restore(self.economy).snapshot()["listings"], [])

    def test_integer_and_card_fields_never_coerce_strings_floats_or_booleans(self):
        for path, value in (
            (("version",), True),
            (("next_id",), "2"),
            (("next_pending",), 5.0),
            (("listings", 0, "id"), True),
            (("listings", 0, "seller", "nn"), True),
            (("listings", 0, "seller", "guild"), False),
            (("listings", 0, "seller", "rating"), "300"),
            (("listings", 0, "increment"), 20.0),
            (("listings", 0, "card", 2), False),
            (("listings", 0, "item_metadata"), ""),
            (("history", 0, "revision"), 1.0),
            (("history", 0, "timestamp"), "101"),
            (("pending", 0, "amount"), "100"),
            (("external_returns", 0, "cards", 0, 7), 255.0),
        ):
            saved = copy.deepcopy(self.saved)
            target = saved
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path, value=value):
                self.assert_rejected(saved)

    def test_unknown_and_required_fields_are_checked_at_each_record_boundary(self):
        for path in (
            (),
            ("listings", 0),
            ("listings", 0, "seller"),
            ("listings", 0, "bidder"),
            ("history", 0),
            ("history", 0, "bidder"),
            ("pending", 0),
            ("external_returns", 0),
        ):
            saved = copy.deepcopy(self.saved)
            target = saved
            for key in path:
                target = target[key]
            target["unknown"] = 1
            with self.subTest(path=path):
                self.assert_rejected(saved)
        for section, field in (("listings", "bidder"), ("pending", "card")):
            saved = copy.deepcopy(self.saved)
            del saved[section][0][field]
            with self.subTest(section=section, field=field):
                self.assert_rejected(saved)

    def test_cross_record_checks_still_reject_well_typed_corruption_atomically(self):
        for mutate in (
            lambda s: s["listings"][0]["seller"].update(nn=999),
            lambda s: s["listings"][0].update(expires=3701),
            lambda s: s["listings"][0].update(reoffers=1),
            lambda s: s["history"].reverse(),
            lambda s: s["history"][0].update(amount=119),
            lambda s: s["history"][1].update(timestamp=100),
            lambda s: s["pending"].append(s["pending"][0]),
            lambda s: s["pending"][0].update(card=s["listings"][0]["card"]),
            lambda s: s["external_returns"].append(s["external_returns"][0]),
            lambda s: s["external_returns"][0]["cards"][0].__setitem__(7, 1),
        ):
            saved = copy.deepcopy(self.saved)
            mutate(saved)
            with self.subTest(saved=saved):
                self.assert_rejected(saved)
