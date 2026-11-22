"""Original result-field checks and atomic practice/profile persistence tests."""

import asyncio
import json
import struct
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests import ROOT
from tmserver.cards.model import Card
from tmserver.economy.errors import EconomyError
from tmserver.economy.service import EconomyService
from tmserver.game.original.runtime import default_rules
from tmserver.game.rules import GrowthBaseline
from tests.support.memory_store import MemoryStore
from tmserver.network.local_accounts import account_specs
from tmserver.network.server import Server
from tmserver.profiles.service import ProfileServer
from tmserver.rankings.errors import RankingError
from tmserver.rankings.stats import PracticeStats

SYNTHETIC_ACCOUNTS = account_specs()

SEEDED = PracticeStats(
    rating=223,
    count=9,
    sequence=12,
    max_reward=400,
    average_reward=100,
    reward_sum=900,
    streak=3,
    best_streak=7,
    rn=b"Existing Rank",
    vs_player_games=8,
)


def economy():
    service = EconomyService(seed=14)
    for i, key in enumerate(SYNTHETIC_ACCOUNTS):
        stats = (
            SEEDED if i == 0 else replace(SEEDED, count=29, sequence=40, streak=-3, rn=b"Second")
        )
        service.provision(
            key,
            nn=i + 1,
            player_id=i + 1,
            name=(b"One", b"Two", b"Three")[i],
            balance=7000 + i,
            profile_template=stats.apply_to_profile(bytes(0x3028)),
        )
    return service


class ResultStatsTests(unittest.TestCase):
    def test_profile_roundtrip_and_unrelated_bytes_preserved(self):
        original = bytes([0xA5]) * 0x3028
        encoded = SEEDED.apply_to_profile(original)
        self.assertEqual(PracticeStats.from_profile(encoded), SEEDED)
        offsets = set()
        for offset, length in (
            (0x30, 4),
            (0x40, 2),
            (0x44, 2),
            (0x48, 12),
            (0x78, 4),
            (0x7D, 32),
            (0x130, 2),
        ):
            offsets.update(range(offset, offset + length))
        self.assertTrue(
            all(encoded[i] == original[i] for i in range(len(original)) if i not in offsets)
        )

    def test_no_stakes_transition_preserves_awards_and_previous_totals(self):
        for outcome, streak in ((1, 4), (0, 0), (-1, -1)):
            after = SEEDED.next_result(outcome)
            self.assertEqual(
                (after.count, after.sequence, after.streak, after.best_streak), (10, 13, streak, 7)
            )
            self.assertEqual(after.average_reward, 90)
            for field in (
                "rating",
                "reward_sum",
                "max_reward",
                "rn",
                "vs_player_games",
            ):
                self.assertEqual(getattr(after, field), getattr(SEEDED, field))
        self.assertEqual(SEEDED.count, 9)

    def test_word_boundaries_and_nonzero_divisor(self):
        self.assertEqual(PracticeStats().next_result(0).count, 1)
        after = replace(SEEDED, count=65535, sequence=65535, streak=32767).next_result(1)
        self.assertEqual(
            (after.count, after.sequence, after.streak, after.best_streak), (65535, 0, -32768, 7)
        )
        self.assertEqual(replace(SEEDED, streak=-32768).next_result(-1).streak, 32767)
        self.assertEqual(replace(SEEDED, streak=-1).next_result(1).streak, 1)

    def test_invalid_inputs_and_bounded_rn(self):
        for kwargs in (
            {"count": 65536},
            {"sequence": -1},
            {"rating": -1},
            {"streak": 32768},
            {"rn": b"x" * 32},
            {"rn": b"a\0b"},
        ):
            with self.assertRaises(RankingError):
                PracticeStats(**kwargs)
        for outcome in (-2, 2, True):
            with self.assertRaises(RankingError):
                SEEDED.next_result(outcome)
        with self.assertRaises(RankingError):
            PracticeStats().result_section(0)
        self.assertTrue(SEEDED.result_section(2).startswith("@No2="))
        with self.assertRaises(RankingError):
            SEEDED.result_section(3)
        template = bytearray(0x3028)
        template[0x7D:0x9D] = b"x" * 32
        self.assertEqual(PracticeStats.from_profile(template).rn, b"x" * 31)


class ResultStatsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.service = economy()
        self.keys = tuple(SYNTHETIC_ACCOUNTS)

    def test_cards_and_stats_commit_restore_and_seed_second_table(self):
        key = self.keys[0]
        original = self.service.inventory(key)[:5]
        token = self.service.reserve_match(key)
        baseline = GrowthBaseline.from_catalog(
            self.service.catalog.cards[original[0].model], self.service.catalog.prices
        )
        grown = Card(*default_rules().grow_card(original[0], baseline, 1, 0).card.full())
        updated = (grown,) + original[1:]
        final = SEEDED.next_result(1)
        refreshed = self.service.commit_match_growth([(token, original, updated)], {key: final})[
            key
        ]
        self.assertEqual(self.service.inventory(key)[:5], updated)
        self.assertEqual(PracticeStats.from_profile(self.service.profile_bytes(key)), final)
        self.assertEqual(self.service.balance(key), 7000)
        with self.assertRaises(EconomyError):
            self.service.commit_match_growth([(token, original, updated)], {key: final})
        self.service.release_match(refreshed)
        restored = EconomyService.restore(json.loads(json.dumps(self.service.snapshot())))
        self.assertEqual(restored.profile_bytes(key), self.service.profile_bytes(key))
        token = restored.reserve_match(key)
        cards = restored.inventory(key)[:5]
        next_result = PracticeStats.from_profile(restored.profile_bytes(key)).next_result(-1)
        restored.commit_match_growth([(token, cards, cards)], {key: next_result})
        self.assertEqual(
            (next_result.count, next_result.sequence, next_result.streak), (11, 14, -1)
        )
        self.assertEqual(PracticeStats.from_profile(restored.profile_bytes(key)), next_result)
        self.assertEqual(PracticeStats.from_profile(restored.profile_bytes(self.keys[1])).count, 29)

    def test_invalid_second_stats_rejects_both_accounts_atomically(self):
        rounds = []
        stats = {}
        for key in self.keys:
            token = self.service.reserve_match(key)
            cards = self.service.inventory(key)[:5]
            rounds.append((token, cards, cards))
            stats[key] = PracticeStats.from_profile(self.service.profile_bytes(key)).next_result(1)
        before = self.service.snapshot()
        stats[self.keys[1]] = replace(stats[self.keys[1]], rating=200)
        with self.assertRaises(EconomyError):
            self.service.commit_match_growth(rounds, stats)
        self.assertEqual(self.service.snapshot(), before)
        self.assertTrue(
            all(self.service.validate_match_selection(token, cards) for token, cards, _ in rounds)
        )

    def test_missing_extra_stale_or_award_mutation_stats_rejected(self):
        key = self.keys[0]
        token = self.service.reserve_match(key)
        cards = self.service.inventory(key)[:5]
        rounds = [(token, cards, cards)]
        before = self.service.snapshot()
        final = SEEDED.next_result(0)
        for stats in (
            {},
            {key: SEEDED},
            {key: final, "extra": final},
            {key: replace(final, reward_sum=final.reward_sum + 1)},
        ):
            with self.subTest(stats=stats), self.assertRaises(EconomyError):
                self.service.commit_match_growth(rounds, stats)
            self.assertEqual(self.service.snapshot(), before)

    def test_options_during_round_preserved_by_stats_commit(self):
        key = self.keys[0]
        token = self.service.reserve_match(key)
        cards = self.service.inventory(key)[:5]
        values = dict(
            Ar=1, Cu=0, Vi=0, Se=3, Bgm=3, Per=1, Ran=0, CMD=1, CAD=1, CL=8, CT=60, HNSS=1
        )
        self.service.save_options(key, values)
        self.service.commit_match_growth([(token, cards, cards)], {key: SEEDED.next_result(1)})
        self.assertEqual(self.service.profile_bytes(key)[0x126], 1)


class PaidResultStatsTests(unittest.TestCase):
    def test_rewards_refunds_middle_finish_and_word_dword_boundaries(self):
        win = SEEDED.next_result(1, 900)
        self.assertEqual(
            (win.current_reward, win.reward_sum, win.max_reward, win.average_reward),
            (900, 1800, 900, 180),
        )
        draw = SEEDED.next_result(0, 1000, fully_tied=True)
        self.assertEqual(
            (draw.current_reward, draw.reward_sum, draw.max_reward, draw.average_reward),
            (1000, 900, 400, 90),
        )
        middle = SEEDED.next_result(0, 600, fully_tied=False)
        self.assertEqual(
            (middle.reward_sum, middle.max_reward, middle.average_reward), (1500, 600, 150)
        )
        wrap = replace(SEEDED, reward_sum=0xFFFFFF9B, count=65535).next_result(1, 200)
        self.assertEqual((wrap.reward_sum, wrap.count, wrap.average_reward), (99, 65535, 0))
        self.assertEqual(win.next_result(-1).current_reward, 0)

    def test_current_reward_is_transient_and_default_practice_wire_unchanged(self):
        final = SEEDED.next_result(1, 900)
        loaded = PracticeStats.from_profile(final.apply_to_profile(bytes(0x3028)))
        self.assertEqual(loaded, replace(final, current_reward=0))
        self.assertIn("/D=0|900|223|", final.result_section(0))
        self.assertIn("/D=0|0|223|", loaded.result_section(0))
        for reward in (-1, 100000000, True):
            with self.assertRaises(RankingError):
                SEEDED.next_result(1, reward)
        with self.assertRaises(RankingError):
            SEEDED.next_result(1, 100, fully_tied=True)
        with self.assertRaises(RankingError):
            SEEDED.next_result(0, 100, fully_tied=1)

    def test_nonzero_statistics_require_exact_authoritative_credit_mapping(self):
        service = economy()
        key = next(iter(SYNTHETIC_ACCOUNTS))
        token = service.reserve_match(key)
        cards = service.inventory(key)[:5]
        rounds = [(token, cards, cards)]
        final = SEEDED.next_result(1, 900)
        before = service.snapshot()
        for payouts in (None, {}, {key: 899}, {key: 900, "other": 0}, {key: True}):
            with self.assertRaises(EconomyError):
                service.commit_match_growth(rounds, {key: final}, payouts)
            self.assertEqual(service.snapshot(), before)
        fresh = service.commit_match_growth(rounds, {key: final}, {key: 900})
        self.assertEqual(service.balance(key), 7000)  # Money coordinator owns the credit.
        self.assertEqual(
            PracticeStats.from_profile(service.profile_bytes(key)), replace(final, current_reward=0)
        )
        self.assertTrue(service.match_account(fresh[key]))

    def test_actual_quote_growth_money_and_reconnect_cohere_for_cpu_and_versus(self):
        from tmserver.economy.match_ledger import MatchEconomy

        for mode, scores, stake in (
            ("computer", (10, 0), 100),
            ("versus", (6, 6, 3), 101),
            ("versus", (5, 5), 100),
        ):
            service = economy()
            keys = tuple(SYNTHETIC_ACCOUNTS)
            n = len(scores)
            if n > len(keys):
                service.provision(
                    "third",
                    nn=3,
                    player_id=3,
                    name=b"Three",
                    balance=7002,
                    profile_template=SEEDED.apply_to_profile(bytes(0x3028)),
                )
                keys += ("third",)
            seats = (0,) if mode == "computer" else range(n)
            tokens = {s: service.reserve_match(keys[s]) for s in seats}
            ledger = MatchEconomy(service)
            tokens = ledger.begin("paid", tokens, stake, mode=mode, player_count=n)
            quote = ledger.quote("paid", tokens, scores, [300] * n)
            stats = {}
            rounds = []
            before = {}
            for s, token in tokens.items():
                key = keys[s]
                before[key] = service.balance(key)
                prior = PracticeStats.from_profile(service.profile_bytes(key))
                outcome = (scores[s] == max(scores)) - (scores[s] == min(scores))
                stats[key] = prior.next_result(outcome, quote[s], fully_tied=len(set(scores)) == 1)
                selected = service.inventory(key)[:5]
                rounds.append((token, selected, selected))
            fresh = service.commit_match_growth(
                rounds, stats, {keys[s]: q for s, q in quote.items()}
            )
            tokens = {s: fresh[keys[s]] for s in tokens}
            tokens, paid = ledger.record_result("paid", tokens, scores=scores, ratings=[300] * n)
            self.assertEqual(paid, quote)
            restored = EconomyService.restore(json.loads(json.dumps(service.snapshot())))
            MatchEconomy.restore(restored, json.loads(json.dumps(ledger.snapshot())))
            for s in tokens:
                key = keys[s]
                self.assertEqual(restored.balance(key), before[key] + quote[s])
                self.assertEqual(
                    PracticeStats.from_profile(restored.profile_bytes(key)),
                    replace(stats[key], current_reward=0),
                )
            if mode == "versus":
                self.assertEqual(
                    sum(restored.balance(keys[s]) for s in tokens), sum(7000 + s for s in tokens)
                )

    def test_wrong_second_paid_stat_rejects_every_account_atomically(self):
        service = economy()
        keys = tuple(SYNTHETIC_ACCOUNTS)[:2]
        rounds = []
        stats = {}
        payouts = {}
        for key, reward in zip(keys, (200, 0)):
            token = service.reserve_match(key)
            cards = service.inventory(key)[:5]
            rounds.append((token, cards, cards))
            stats[key] = PracticeStats.from_profile(service.profile_bytes(key)).next_result(
                1, reward
            )
            payouts[key] = reward
        stats[keys[1]] = replace(stats[keys[1]], max_reward=999)
        before = service.snapshot()
        with self.assertRaises(EconomyError):
            service.commit_match_growth(rounds, stats, payouts)
        self.assertEqual(service.snapshot(), before)


class ResultStatsProfileTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnected_profile_reads_persisted_result_for_right_account(self):
        from tests.profiles.test_profile_accounts import file_request, transact

        asyncio.get_running_loop().slow_callback_duration = 1.0
        service = economy()
        key = next(iter(SYNTHETIC_ACCOUNTS))
        token = service.reserve_match(key)
        cards = service.inventory(key)[:5]
        final = SEEDED.next_result(1)
        service.commit_match_growth([(token, cards, cards)], {key: final})
        service = EconomyService.restore(json.loads(json.dumps(service.snapshot())))
        game = Server(store=MemoryStore(service.snapshot()))
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as directory:
            profile = ProfileServer(Path(directory) / "traffic.jsonl", game)
            try:
                port = await profile.start(0)
                for account in SYNTHETIC_ACCOUNTS:
                    error, data = await transact(port, account, 3, 0, file_request())
                    self.assertEqual(error, 0)
                    self.assertEqual(data[:-4], service.profile_bytes(account))
                    if account == key:
                        self.assertEqual(PracticeStats.from_profile(data[:-4]), final)
                    else:
                        self.assertEqual(PracticeStats.from_profile(data[:-4]).count, 29)
            finally:
                await profile.stop()
                await game.stop()


if __name__ == "__main__":
    unittest.main()
