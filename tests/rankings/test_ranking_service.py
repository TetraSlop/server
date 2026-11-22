"""Local ranking policy plus original x86 request/callback/file/UI consumers."""

import struct
import sys
import unittest
from pathlib import Path

from tmserver.economy.models import PROFILE_SIZE
from tmserver.economy.service import EconomyService
from tmserver.protocol.identities import game_id
from tmserver.rankings.packets import METADATA_PATH, RECORD_SIZE, RESULT_PATH
from tmserver.rankings.service import MAX_SNAPSHOTS, RankingService
from tmserver.protocol.commands import Command, encode, parse


def request(category, identity, *, directory=1, version=1, selector=0):
    return b"GTM0R" + encode(
        [
            Command(b"RR", str(category).encode()),
            Command(b"PI", f"0x{identity:016X}\x06{directory}\x06{version}\x06{selector}".encode()),
        ]
    )


def fixture():
    economy = EconomyService(seed=1)
    for key, raw, name, rating, total in [
        ("alice", 1, b"Alice", 400, 900),
        ("bob", 2, b"Bob", 500, 300),
    ]:
        template = bytearray(PROFILE_SIZE)
        struct.pack_into("<I", template, 0x30, rating)
        struct.pack_into("<I", template, 0x50, total)
        economy.provision(
            key, nn=game_id(raw), player_id=raw, name=name, profile_template=bytes(template)
        )
    return economy


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.economy = fixture()
        self.service = RankingService(self.economy)

    def query(self, category=2, who="alice"):
        reply = self.service.handle(who, request(category, self.economy.account_view(who).nn))[0]
        parsed = parse(reply[5:])
        self.assertEqual([x.token for x in parsed], [b"RF", b"LN"])
        path, count = parsed[0].payload.decode(), int(parsed[1].payload)
        data = self.service.file(who, path)
        self.assertEqual(len(data), count * RECORD_SIZE)
        return path, data

    def test_current_rating_and_total_prize_use_distinct_committed_fields(self):
        _, rating = self.query(2)
        self.assertEqual(
            [rating[i : i + 16].split(b"\0")[0] for i in range(0, len(rating), RECORD_SIZE)],
            [b"Bob", b"Alice"],
        )
        self.assertEqual(
            [
                struct.unpack_from("<I", rating, i + 0xA0)[0]
                for i in range(0, len(rating), RECORD_SIZE)
            ],
            [500, 400],
        )
        _, total = self.query(0)
        self.assertEqual(total[:6], b"Alice\0")
        self.assertEqual(struct.unpack_from("<I", total, 0x94)[0], 900)
        self.assertNotEqual(struct.unpack_from("<I", total, 0x94)[0], self.economy.balance("alice"))

    def test_historical_categories_are_empty_without_weekly_play(self):
        for category in (1, 3, 4):
            with self.subTest(category=category):
                _, data = self.query(category)
                self.assertEqual(data, b"")
        self.assertEqual(struct.unpack("<7I", self.service.metadata()), (0, 2, 0, 0, 0, 0, 0))

    def test_name_termination_and_unused_record_bytes(self):
        self.economy.provision("long", nn=game_id(3), player_id=3, name=b"ABCDEFGHIJKLMNOP")
        _, data = self.query(2)
        self.assertEqual(len(data), 3 * RECORD_SIZE)
        self.assertIn(b"ABCDEFGHIJKLMNO\0", data)
        for row in range(0, len(data), RECORD_SIZE):
            self.assertEqual(data[row + 0xC2], 1)
            self.assertEqual(data[row + 0xC3 : row + 0xC8], bytes(5))
        self.assertEqual(self.economy.account_view("long").name, b"ABCDEFGHIJKLMNOP")

    def test_prior_query_and_other_account_snapshots_remain_immutable(self):
        first, before = self.query(2)
        bob_path, bob_data = self.query(0, "bob")
        self.economy.provision("new", nn=game_id(3), player_id=3, name=b"New")
        second, after = self.query(0)
        self.assertNotEqual(first, second)
        self.assertEqual(self.service.file("alice", first), before)
        self.assertEqual(len(after), 3 * RECORD_SIZE)
        self.assertIsNone(self.service.file("bob", first))
        self.assertIsNone(self.service.file("alice", bob_path))
        self.assertEqual(self.service.file("bob", bob_path), bob_data)

    def test_profile_privacy_controls_new_files_without_renaming_account(self):
        path, before = self.query(0)
        self.economy.save_ranking_privacy("alice", 1)
        _, hidden = self.query(0)
        self.assertEqual(hidden[0xC0], 1)
        self.assertEqual(hidden[:16], before[:16])
        self.assertEqual(self.service.file("alice", path), before)
        self.economy.save_ranking_privacy("alice", 0)
        _, shown = self.query(0)
        self.assertEqual(shown[0xC0], 0)
        self.assertEqual(self.economy.account_view("alice").name, b"Alice")

    def test_exact_delete_and_canonical_cleanup_are_account_scoped(self):
        first, _ = self.query()
        second, _ = self.query(0)
        bob, _ = self.query(0, "bob")
        self.assertTrue(self.service.delete("alice", first))
        self.assertTrue(self.service.delete("alice", first))
        self.assertIsNone(self.service.file("alice", first))
        self.assertIsNotNone(self.service.file("alice", second))
        self.assertTrue(self.service.delete("alice", RESULT_PATH))
        self.assertIsNone(self.service.file("alice", second))
        self.assertIsNotNone(self.service.file("bob", bob))
        self.assertFalse(self.service.delete("alice", METADATA_PATH))
        self.assertFalse(self.service.delete("alice", "../" + RESULT_PATH))

    def test_capacity_failure_does_not_evict_old_file_and_delete_recovers(self):
        for _ in range(MAX_SNAPSHOTS - 1):
            self.query()
        path, old = self.query()
        reply = self.service.handle("alice", request(0, game_id(1)))[0]
        self.assertEqual(parse(reply[5:]), [Command(b"RG", b"-8809")])
        self.assertEqual(self.service.file("alice", path), old)
        self.service.delete("alice", RESULT_PATH)
        self.query(0)

    def test_invalid_rank_returns_mapped_error_without_snapshot(self):
        reply = self.service.handle("alice", request(5, game_id(1)))[0]
        self.assertEqual(parse(reply[5:]), [Command(b"RG", b"-8811")])
        self.assertIsNone(self.service.file("alice", RESULT_PATH + "_00000001"))

    def test_malformed_or_other_owner_requests_never_store_data(self):
        cases = [
            request(2, game_id(2)),
            request(2, game_id(1), directory=256),
            request(2, game_id(1), version=65536),
            request(2, game_id(1), selector=1),
            request(2, game_id(1)).replace(b"<PI>", b"<RR>"),
            request(2, game_id(1)).replace(b"0xB62D", b"B62D"),
            request(2, game_id(1)) + b"garbage",
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.service.handle("alice", data)
        path, _ = self.query()
        self.assertEqual(path, RESULT_PATH + "_00000001")
        self.assertIsNone(self.service.handle("alice", b"GTM0GE1000000@TeachDV="))
        self.assertIsNone(
            self.service.handle("alice", b"GTM0R" + encode([Command(b"GO", b"opaque")]))
        )

    def test_saved_account_restore_gives_same_order_and_scores(self):
        before = self.economy.snapshot()
        restored = EconomyService.restore(before, catalog=self.economy.catalog)
        other = RankingService(restored)
        for category in range(5):
            self.assertEqual(other.records(category), self.service.records(category))
        self.assertEqual(self.economy.snapshot(), before)

    def test_equal_scores_have_stable_identity_then_character_order(self):
        self.economy.provision("z", nn=game_id(3), player_id=3, name=b"Z")
        self.economy.provision("a", nn=game_id(4), player_id=4, name=b"A")
        data = self.service.records(0)
        zero = [
            struct.unpack_from("<QI", data, i + 0x10)
            for i in range(2 * RECORD_SIZE, len(data), RECORD_SIZE)
        ]
        self.assertEqual(zero, sorted(zero))

    def test_reader_limits_and_top30_limit_are_explicit_policy(self):
        for index in range(3, 105):
            self.economy.provision(
                str(index), nn=game_id(index), player_id=index, name=str(index).encode(), cards=[]
            )
        self.assertEqual(len(self.service.records(0)), 100 * RECORD_SIZE)
        self.assertEqual(len(self.service.records(2)), 100 * RECORD_SIZE)
        self.assertEqual(struct.unpack_from("<I", self.service.metadata(), 4)[0], 104)


if __name__ == "__main__":
    unittest.main()
