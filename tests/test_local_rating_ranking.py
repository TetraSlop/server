"""Ranking eligibility and order for the explicitly enabled local ring policy."""

import unittest

from tests.test_rating_policy import economy, prepare
from tests.test_ranking_service import request
from tmserver.economy_service import EconomyService
from tmserver.ranking_service import RECORD_SIZE, RankingHistory, RankingService
from tmserver.rating_policy import RatingPolicy
from tmserver.tm_protocol import parse


def names(data):
    return [data[i:i + 16].split(b"\0")[0] for i in range(0, len(data), RECORD_SIZE)]


class LocalRatingRankingTests(unittest.TestCase):
    def setUp(self):
        self.economy = economy(2, sequence=0)
        self.economy.provision("new", nn=3, player_id=3, name=b"new")
        self.policy = RatingPolicy(self.economy, mode="local-history")
        self.ranking = RankingService(self.economy, rating_policy=self.policy)

    def complete(self, scores=(7, 3)):
        rounds, stats, changes = prepare(self.economy, self.policy, scores)
        return self.policy.commit_growth(rounds, stats, rating_changes_by_key=changes)

    def test_unrated_and_quoted_accounts_do_not_rank_or_mutate_profiles(self):
        before = self.economy.snapshot(), self.policy.snapshot()
        self.assertEqual(self.ranking.records(2), b"")
        self.assertEqual((self.economy.snapshot(), self.policy.snapshot()), before)
        self.policy.quote("pending", {0: "first", 1: "second"}, (7, 3))
        self.assertEqual(self.ranking.records(2), b"")
        self.assertEqual(self.economy.snapshot(), before[0])
        self.assertEqual(names(self.ranking.records(0)), [b"first", b"second", b"new"])

    def test_winner_ranks_first_and_fresh_zero_rating_does_not_win(self):
        self.complete()
        self.assertEqual(names(self.ranking.records(2)), [b"first", b"second"])
        self.assertEqual(names(self.ranking.records(0)), [b"first", b"second", b"new"])
        # No invented weekly histories or saved rating changes from querying.
        before = self.economy.snapshot(), self.policy.snapshot()
        for category in (1, 3, 4):
            self.assertEqual(self.ranking.records(category), b"")
        self.assertEqual((self.economy.snapshot(), self.policy.snapshot()), before)
        restored = EconomyService.restore(self.economy.snapshot())
        policy = RatingPolicy(restored, mode="local-history", snapshot=self.policy.snapshot())
        ranking = RankingService(restored, rating_policy=policy)
        self.assertEqual(ranking.records(2), self.ranking.records(2))

    def test_rating_history_ascends_while_money_descends(self):
        self.complete()
        self.ranking = RankingService(
            self.economy, rating_policy=self.policy,
            historical_stats={
                "first": RankingHistory(last_week_rating=198, weekly_prize=10, rookie=True),
                "second": RankingHistory(last_week_rating=201, weekly_prize=20, rookie=True),
            },
        )
        for category in (2, 3, 4):
            self.assertEqual(names(self.ranking.records(category)), [b"first", b"second"])
        self.assertEqual(names(self.ranking.records(1)), [b"second", b"first"])

    def test_ties_snapshot_immutability_and_explicit_disqualification(self):
        reply = self.ranking.handle("first", request(2, 1))[0]
        empty_path = parse(reply[5:])[0].payload.decode()
        self.complete((5, 5))
        self.assertEqual(names(self.ranking.records(2)), [b"first", b"second"])
        self.assertEqual(self.ranking.file("first", empty_path), b"")
        disqualified = RankingService(
            self.economy, rating_policy=self.policy,
            historical_stats={"first": RankingHistory(qualified=False)},
        )
        self.assertEqual(names(disqualified.records(2)), [b"second"])

    def test_preserved_policy_retains_existing_order_and_stale_ring_is_unranked(self):
        tokens = self.complete()
        preserved = RatingPolicy(self.economy, snapshot=self.policy.snapshot())
        ranking = RankingService(self.economy, rating_policy=preserved)
        self.assertEqual(names(ranking.records(2)), [b"second", b"first", b"new"])
        rounds, stats, _ = prepare(
            self.economy, preserved, (3, 7), match_id="preserved", tokens=tokens,
        )
        preserved.commit_growth(rounds, stats)
        self.assertEqual(self.ranking.records(2), b"")
        self.assertEqual(names(ranking.records(2)), [b"second", b"first", b"new"])


if __name__ == "__main__":
    unittest.main()
