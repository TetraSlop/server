"""Weekly provider values retain ranking flags, frozen scores and native limits."""

import struct
import unittest

from tests.rankings.test_rating_policy import economy
from tests.rankings.test_weekly_rating_policy import MONDAY, WEEK, commit, result
from tmserver.economy.service import EconomyService
from tmserver.network.local_accounts import MAX_ACCOUNTS
from tmserver.rankings.packets import RECORD_SIZE, SCORE_OFFSETS
from tmserver.rankings.models import RankingHistory
from tmserver.rankings.service import RankingService
from tmserver.rankings.policy import RatingPolicy


def rows(ranking, category):
    data = ranking.records(category)
    return [data[offset : offset + RECORD_SIZE] for offset in range(0, len(data), RECORD_SIZE)]


def scores(ranking, category):
    return [
        (
            struct.unpack_from("<Q", row, 0x10)[0],
            struct.unpack_from("<I", row, SCORE_OFFSETS[category])[0],
        )
        for row in rows(ranking, category)
    ]


class WeeklyRankingProjectionTests(unittest.TestCase):
    def test_live_weekly_dates_are_hidden_without_changing_restored_periods(self):
        service = economy()
        policy = RatingPolicy(service, mode="local-history", weekly=True, clock=lambda: MONDAY)
        commit(policy, result(service, policy, rewards={"first": 25, "second": 0}))
        before = service.snapshot(), policy.snapshot()
        for enabled in (True, False):
            restored = RatingPolicy(
                service, mode="local-history", weekly=enabled, snapshot=before[1],
            )
            ranking = RankingService(service, rating_policy=restored, update_time=MONDAY + WEEK)
            with self.subTest(weekly=enabled):
                self.assertEqual(
                    struct.unpack_from("<I", ranking.metadata(), 12)[0],
                    0 if enabled else MONDAY + WEEK,
                )
                self.assertEqual(restored.periods.update_time, MONDAY)
                self.assertEqual((service.snapshot(), restored.snapshot()), before)
                if enabled:
                    self.assertEqual(scores(ranking, 1), [(1, 25), (2, 0)])

    def test_provider_overlays_only_recorded_scores_and_retains_privacy_and_qualification(self):
        service = economy()
        policy = RatingPolicy(service, mode="local-history", weekly=True, clock=lambda: MONDAY)
        commit(policy, result(service, policy, rewards={"first": 25, "second": 0}))
        service.save_ranking_privacy("second", 1)
        ranking = RankingService(
            service,
            rating_policy=policy,
            historical_stats={
                "first": RankingHistory(
                    weekly_prize=999,
                    last_week_rating=99,
                    anonymous=True,
                    qualified=False,
                    rookie=True,
                    previous_ranks=(1, 2, 3, 4, 5),
                ),
                "third": RankingHistory(weekly_prize=777, last_week_rating=77, rookie=True),
            },
        )
        before = service.snapshot(), policy.snapshot()
        weekly = rows(ranking, 1)
        self.assertEqual(scores(ranking, 1), [(1, 25), (2, 0)])
        self.assertEqual((weekly[0][0xC0], weekly[0][0xC2]), (1, 0))
        self.assertEqual(list(weekly[0][0xC3:0xC8]), [5, 4, 3, 1, 2])
        self.assertEqual((weekly[1][0xC0], weekly[1][0xC2]), (1, 1))
        self.assertEqual([row[:16].split(b"\0")[0] for row in weekly], [b"first", b"second"])
        # Qualification applies to current rating; the provider must not reset
        # an explicit disqualification or admit the unplayed third account.
        self.assertEqual([identity for identity, _ in scores(ranking, 2)], [2])
        self.assertEqual(ranking.records(3), b"")
        self.assertEqual(ranking.records(4), b"")
        self.assertEqual(struct.unpack("<7I", ranking.metadata()), (0, 3, 2, 0, 0, 0, 0))
        self.assertEqual((service.snapshot(), policy.snapshot()), before)

    def test_last_week_rating_stays_frozen_when_current_order_reverses(self):
        service = economy(2)
        now = [MONDAY]
        policy = RatingPolicy(service, mode="local-history", weekly=True, clock=lambda: now[0])
        ranking = RankingService(service, rating_policy=policy)
        tokens = commit(policy, result(service, policy))
        previous = scores(ranking, 2)
        self.assertEqual(previous, [(1, 198), (2, 201)])
        now[0] += WEEK
        policy.advance_week()
        self.assertEqual(scores(ranking, 3), previous)
        for number in (2, 3):
            tokens = commit(
                policy,
                result(
                    service,
                    policy,
                    scores=(0, 10),
                    match_id=f"new-week:{number}",
                    rewards={"first": 1, "second": 2},
                    tokens=tokens,
                ),
            )
        self.assertEqual(scores(ranking, 2), [(2, 198), (1, 201)])
        self.assertEqual(scores(ranking, 3), previous)
        self.assertEqual(scores(ranking, 1), [(2, 4), (1, 2)])
        self.assertEqual(
            struct.unpack("<7I", ranking.metadata()), (0, 2, 2, 0, 0, 2, 0)
        )

    def test_full_account_pool_caps_rows_but_preserves_denominators_and_identity_ties(self):
        service = EconomyService(seed=1)
        now = [MONDAY]
        policy = RatingPolicy(service, mode="local-history", weekly=True, clock=lambda: now[0])
        ranking = RankingService(service, rating_policy=policy)
        expected_money, expected_rating = [], []
        cards = None
        for index in range(MAX_ACCOUNTS):
            key = f"player{index:03d}"
            identity = 1000 + MAX_ACCOUNTS - index  # Opposite account-key/insertion order.
            service.provision(key, nn=identity, player_id=index + 1, name=key.encode(), cards=cards)
            if cards is None:
                cards = service.inventory(key)
            reward = index % 5
            batch = result(
                service,
                policy,
                keys=(key,),
                match_id=f"cpu:{index}",
                rewards={key: reward},
                scores=(7, 3) if index < MAX_ACCOUNTS // 2 else (3, 7),
            )
            commit(policy, batch)
            expected_money.append((identity, reward))
            expected_rating.append((identity, batch[1][key].rating))
        expected_money.sort(key=lambda row: (-row[1], row[0]))
        expected_rating.sort(key=lambda row: (row[1], row[0]))
        self.assertEqual(scores(ranking, 1), expected_money[:100])
        self.assertEqual(
            struct.unpack("<7I", ranking.metadata()),
            (0, MAX_ACCOUNTS, MAX_ACCOUNTS, 0, 0, 0, 0),
        )
        now[0] += WEEK
        policy.advance_week()
        self.assertEqual(scores(ranking, 3), expected_rating[:30])
        self.assertEqual(ranking.records(1), b"")
        self.assertEqual(ranking.records(4), b"")
        self.assertEqual(
            struct.unpack("<7I", ranking.metadata()),
            (0, MAX_ACCOUNTS, 0, 0, 0, MAX_ACCOUNTS, 0),
        )


if __name__ == "__main__":
    unittest.main()
