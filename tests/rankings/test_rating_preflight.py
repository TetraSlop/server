"""Local rating policy rejects stale histories before listeners or recovery."""

from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from tests.rankings.test_rating_policy import economy, prepare
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.economy.service import EconomyService
from tmserver.network.server import Server
from tmserver.economy.errors import EconomyError
from tmserver.rankings.policy import RatingPolicy
from tmserver.rankings.stats import PracticeStats


class RatingPreflightTests(unittest.TestCase):
    def snapshot(self, *, stale=False):
        service = economy(2)
        policy = RatingPolicy(service, mode="local-history")
        rounds, stats, changes = prepare(service, policy, (7, 3))
        tokens = policy.commit_growth(rounds, stats, rating_changes_by_key=changes)
        if stale:
            policy = RatingPolicy(service, snapshot=policy.snapshot())
            rounds, stats, _ = prepare(
                service, policy, (3, 7), match_id="preserved", tokens=tokens
            )
            policy.commit_growth(rounds, stats)
        return dict(service.snapshot(), ratings=policy.snapshot())

    def test_startup_rejects_all_stale_accounts_before_any_save(self):
        saved = self.snapshot(stale=True)
        before = deepcopy(saved)
        store = Mock()
        store.load.return_value = saved
        with (
            patch.object(MatchEconomy, "recover_all", side_effect=AssertionError("recovery")),
            self.assertRaisesRegex(EconomyError, "first, second.*migration"),
        ):
            Server(store=store, rating_mode="local-history")
        self.assertEqual(store.method_calls, [call.load()])
        self.assertEqual(saved, before)

    def test_preserve_mode_and_compatible_history_still_construct_read_only(self):
        for mode, stale in (("preserve", True), ("local-history", False)):
            with self.subTest(mode=mode):
                saved = self.snapshot(stale=stale)
                before = deepcopy(saved)
                store = Mock()
                store.load.return_value = saved
                server = Server(store=store, rating_mode=mode)
                self.assertIsNone(server.listener)
                self.assertEqual(server.ratings.snapshot(), saved["ratings"])
                self.assertEqual(store.method_calls, [call.load()])
                self.assertEqual(saved, before)

    def test_unenrolled_and_initial_enrollment_preserve_existing_profile_rating(self):
        service = economy(2, sequence=119)
        policy = RatingPolicy(service, mode="local-history")
        policy.history.enroll("first", sequence=119)
        before = service.snapshot(), policy.snapshot()
        policy.validate_profiles()
        self.assertEqual((service.snapshot(), policy.snapshot()), before)

    def test_preflight_detects_same_sequence_with_changed_profile_rating(self):
        saved = self.snapshot()
        template = bytearray.fromhex(saved["accounts"]["first"]["template_hex"])
        # Reuse the domain profile constructor to change only its scalar rating.
        stats = PracticeStats.from_profile(template)
        saved["accounts"]["first"]["template_hex"] = replace(
            stats, rating=stats.rating + 1
        ).apply_to_profile(template).hex()
        service = EconomyService.restore(saved)
        policy = RatingPolicy(service, mode="local-history", snapshot=saved["ratings"])
        before = service.snapshot(), policy.snapshot()
        with self.assertRaisesRegex(EconomyError, "first.*migration"):
            policy.validate_profiles()
        self.assertEqual((service.snapshot(), policy.snapshot()), before)
