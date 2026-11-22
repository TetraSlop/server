"""Optional rating/profile/growth coherence and atomic joint-journal tests."""

import json
import unittest
from dataclasses import replace

from tmserver.economy.errors import EconomyError
from tmserver.economy.service import EconomyService
from tmserver.game.departure_rules import result_classes
from tmserver.rankings.history import HistoryState, LocalRatingHistory
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.rankings.errors import RankingError
from tmserver.rankings.policy import RatingPolicy
from tmserver.rankings.stats import PracticeStats, result_outcome

KEYS = ("first", "second", "third")


def economy(n=3, sequence=65535):
    service = EconomyService(seed=1)
    for index, key in enumerate(KEYS[:n]):
        stats = PracticeStats(
            rating=300,
            count=17 + index,
            sequence=sequence,
            reward_sum=120,
            max_reward=100,
            average_reward=7,
            streak=-2,
            best_streak=4,
            rn=b"Opaque Existing Rank",
            vs_player_games=9,
        )
        service.provision(
            key,
            nn=index + 1,
            player_id=index + 1,
            name=key.encode(),
            profile_template=stats.apply_to_profile(bytes([0xA5]) * 0x3028),
        )
    return service


def prepare(
    service, policy, scores=(6, 6, 3), *, keys=None, match_id="table:first", tokens=None,
    payouts=None, vs_player_game=False,
):
    """Quote a round for keys in seat order; returns (rounds, stats, rating changes)."""
    keys = KEYS[: len(scores)] if keys is None else keys
    tokens = {key: service.reserve_match(key) for key in keys} if tokens is None else tokens
    quotes = policy.quote(match_id, dict(enumerate(keys)), scores)
    changes = None if quotes is None else {keys[seat]: value for seat, value in quotes.items()}
    classes = result_classes(scores)
    stats = {}
    rounds = []
    for seat, key in enumerate(keys):
        prior = PracticeStats.from_profile(service.profile_bytes(key))
        stats[key] = prior.next_result(
            result_outcome(classes[seat]),
            0 if payouts is None else payouts[key],
            fully_tied=len(set(scores)) == 1,
            rating_change=None if changes is None else changes[key],
            vs_player_game=vs_player_game,
        )
        selected = service.inventory(key)[:5]
        rounds.append((tokens[key], selected, selected))
    return rounds, stats, changes


class RatingPolicyTests(unittest.TestCase):
    def test_preserved_default_keeps_ratings(self):
        service = economy(2)
        policy = RatingPolicy(service)
        before = policy.snapshot()
        rounds, stats, changes = prepare(service, policy, (7, 3))
        self.assertIsNone(changes)
        self.assertTrue(all(value.rating == 300 for value in stats.values()))
        policy.commit_growth(rounds, stats)
        self.assertEqual(policy.snapshot(), before)
        self.assertEqual(PracticeStats.from_profile(service.profile_bytes("first")).rating, 300)

    def test_explicit_existing_account_enrollment_never_resets_profile(self):
        service = economy(2, sequence=119)
        policy = RatingPolicy(service, mode="local-history")
        profiles = {key: service.profile_bytes(key) for key in KEYS[:2]}
        self.assertEqual(policy.snapshot()["history"]["accounts"], {})
        quotes = policy.quote("round", {0: "first", 1: "second"}, (7, 3))
        self.assertEqual({key: service.profile_bytes(key) for key in profiles}, profiles)
        self.assertEqual(policy.history.state("first"), HistoryState(sequence=119))
        self.assertEqual((quotes[0].d2, quotes[1].d2), (198, 201))
        self.assertEqual(quotes[0].after.sequence, 120)
        self.assertEqual(policy.quote("round", {0: "first", 1: "second"}, (7, 3)), quotes)

    def test_three_account_commit_wrap_restart_second_table_and_opaque_fields(self):
        service = economy()
        policy = RatingPolicy(service, mode="local-history")
        original = {key: service.profile_bytes(key) for key in KEYS}
        rounds, stats, changes = prepare(service, policy)
        fresh = policy.commit_growth(rounds, stats, rating_changes_by_key=changes)
        for key in KEYS:
            self.assertEqual(service.profile_bytes(key), stats[key].apply_to_profile(original[key]))
            self.assertEqual(stats[key].sequence, 0)
            self.assertEqual(stats[key].rn, b"Opaque Existing Rank")
            self.assertEqual(stats[key].vs_player_games, 9)
            for at in (0x128, 0x134):  # RT points and prize points are never written.
                self.assertEqual(service.profile_bytes(key)[at : at + 4], original[key][at : at + 4])
            self.assertEqual(policy.history.state(key).sequence, 0)
        with self.assertRaises(EconomyError):
            policy.commit_growth(rounds, stats, rating_changes_by_key=changes)
        joint = json.loads(json.dumps(dict(service.snapshot(), ratings=policy.snapshot())))
        restored = EconomyService.restore(joint)
        policy = RatingPolicy(restored, mode="local-history", snapshot=joint["ratings"])
        rounds, stats, changes = prepare(restored, policy, (0, 15, 0), match_id="different-table:1")
        policy.commit_growth(rounds, stats, rating_changes_by_key=changes)
        for key in KEYS:
            self.assertEqual(policy.history.state(key).sequence, 1)
            self.assertEqual(policy.history.state(key).revision, 2)
            self.assertEqual(PracticeStats.from_profile(restored.profile_bytes(key)), stats[key])

    def test_invalid_last_profile_rejects_cards_and_entire_journal(self):
        service = economy()
        policy = RatingPolicy(service, mode="local-history")
        rounds, stats, changes = prepare(service, policy)
        stats["third"] = None
        before = service.snapshot(), policy.snapshot()
        with self.assertRaises(EconomyError):
            policy.commit_growth(rounds, stats, rating_changes_by_key=changes)
        self.assertEqual((service.snapshot(), policy.snapshot()), before)
        for key in KEYS:
            self.assertEqual(policy.history.state(key).revision, 0)

    def test_missing_foreign_or_forged_proof_rejects_without_mutation(self):
        service = economy()
        policy = RatingPolicy(service, mode="local-history")
        rounds, stats, changes = prepare(service, policy)
        attempts = [
            None,
            {},
            {"first": changes["first"]},
            dict(changes, first=changes["second"]),
            dict(changes, third=replace(changes["third"], result_class=0)),
        ]
        before = service.snapshot(), policy.snapshot()
        for mapping in attempts:
            with self.assertRaises(EconomyError):
                policy.commit_growth(rounds, stats, rating_changes_by_key=mapping)
            self.assertEqual((service.snapshot(), policy.snapshot()), before)

    def test_stale_quote_cannot_advance_with_refreshed_capabilities(self):
        service = economy(2)
        policy = RatingPolicy(service, mode="local-history")
        first_round, first_stats, first_changes = prepare(service, policy, (7, 3))
        stale_round, stale_stats, stale_changes = prepare(
            service,
            policy,
            (5, 5),
            match_id="stale",
            tokens={key: row[0] for key, row in zip(KEYS, first_round)},
        )
        tokens = policy.commit_growth(first_round, first_stats, rating_changes_by_key=first_changes)
        stale_round = [
            (tokens[key], original, updated)
            for key, (_, original, updated) in zip(KEYS, stale_round)
        ]
        before = service.snapshot(), policy.snapshot()
        with self.assertRaises(EconomyError):
            policy.commit_growth(stale_round, stale_stats, rating_changes_by_key=stale_changes)
        self.assertEqual((service.snapshot(), policy.snapshot()), before)

    def test_money_rating_and_growth_share_one_reloadable_document(self):
        service = economy()
        policy = RatingPolicy(service, mode="local-history")
        money = MatchEconomy(service)
        tokens = {seat: service.reserve_match(key) for seat, key in enumerate(KEYS)}
        tokens = money.begin("paid", tokens, 137, player_count=3)
        scores, ratings = (6, 6, 3), (300, 300, 300)
        payouts = money.quote("paid", tokens, scores, ratings)
        rounds, stats, changes = prepare(
            service,
            policy,
            scores,
            match_id="paid",
            tokens={KEYS[seat]: token for seat, token in tokens.items()},
            payouts={KEYS[seat]: value for seat, value in payouts.items()},
        )
        fresh = policy.commit_growth(
            rounds,
            stats,
            {KEYS[seat]: value for seat, value in payouts.items()},
            rating_changes_by_key=changes,
        )
        money.record_result(
            "paid",
            {seat: fresh[key] for seat, key in enumerate(KEYS)},
            scores=scores,
            ratings=ratings,
        )
        joint = json.loads(
            json.dumps(
                dict(service.snapshot(), ratings=policy.snapshot(), match_economy=money.snapshot())
            )
        )
        restored = EconomyService.restore(joint)
        rating = RatingPolicy(restored, mode="local-history", snapshot=joint["ratings"])
        MatchEconomy.restore(restored, joint["match_economy"])
        self.assertEqual(sum(restored.balance(key) for key in KEYS), 30000)
        for seat, key in enumerate(KEYS):
            saved = PracticeStats.from_profile(restored.profile_bytes(key))
            self.assertEqual(saved, replace(stats[key], current_reward=0))
            self.assertEqual(restored.balance(key), 9863 + payouts[seat])
            self.assertEqual(rating.history.state(key).rating, saved.rating)

    def test_departures_and_profile_history_mismatch_require_explicit_action(self):
        service = economy(2)
        policy = RatingPolicy(service, mode="local-history")
        with self.assertRaises(RankingError):
            policy.quote("departed", {0: "first", 1: "second"}, (8, 2), departed_seats=(1,))
        self.assertEqual(policy.snapshot()["history"]["accounts"], {})
        rounds, stats, changes = prepare(service, policy, (7, 3))
        tokens = policy.commit_growth(rounds, stats, rating_changes_by_key=changes)
        preserved = RatingPolicy(service, snapshot=policy.snapshot())
        rounds, stats, changes = prepare(
            service, preserved, (3, 7), match_id="preserved", tokens=tokens
        )
        preserved.commit_growth(rounds, stats)
        enabled = RatingPolicy(service, mode="local-history", snapshot=preserved.snapshot())
        with self.assertRaises(RankingError):
            enabled.quote("reenabled", {0: "first", 1: "second"}, (7, 3))

    def test_quote_all_accounts_validate_before_any_enrollment(self):
        service = economy(2)
        policy = RatingPolicy(service, mode="local-history")
        for seats in (
            {0: "first", 1: "missing"},
            {0: "first", 2: "second"},
            {0: "first", 1: "first"},
        ):
            with self.assertRaises(EconomyError):
                policy.quote("bad", seats, (7, 3))
            self.assertEqual(policy.snapshot()["history"]["accounts"], {})

    def test_quote_sets_d2_and_mismatched_keys_are_rejected(self):
        history = LocalRatingHistory()
        history.enroll("a")
        quote = history.quote("a", "r", (6, 6, 3), 0)
        self.assertEqual(PracticeStats().next_result(1, rating_change=quote).rating, quote.d2)
        service = economy(2)
        policy = RatingPolicy(service, mode="local-history")
        rounds, stats, changes = prepare(service, policy, (7, 3))
        swapped = {"first": changes["second"], "second": changes["first"]}
        before = service.snapshot(), policy.snapshot()
        with self.assertRaises(EconomyError):
            policy.commit_growth(rounds, stats, rating_changes_by_key=swapped)
        self.assertEqual((service.snapshot(), policy.snapshot()), before)


if __name__ == "__main__":
    unittest.main()
