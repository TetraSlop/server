"""Construction owns its log even when validation or recovery cannot finish."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.support.memory_store import MemoryStore
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.network.diagnostics import JsonlLog
from tmserver.network.server import Server
from tmserver.rankings.stats import PracticeStats


class StartupLoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "traffic.jsonl"
        self.store = MemoryStore()
        self.streams = []
        self.failures = []  # Retain traceback frames; cleanup must not rely on GC.
        original_open = Path.open

        def tracked_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path == self.path:
                self.streams.append(stream)
                self.addCleanup(stream.close)
            return stream

        self.enterContext(patch.object(Path, "open", tracked_open))

    def failed_start(self, **options):
        try:
            Server(log_path=self.path, **options)
        except (OSError, ValueError) as error:
            self.failures.append(error)
            return error
        self.fail("invalid server unexpectedly completed construction")

    async def pending_recovery(self):
        host = Server()
        self.addAsyncCleanup(host.stop)
        key = host.ensure_account(next(iter(host.accounts)))
        hand = host.economy.inventory(key)[:5]
        reservation = host.economy.reserve_match(key)
        stats = PracticeStats.from_profile(host.economy.profile_bytes(key)).next_result(1)
        reservation = host.economy.commit_match_growth([(reservation, hand, hand)], {key: stats})[
            key
        ]
        host.result_rewards.stage(key, reservation, "startup.reward", 0, (hand[0],))
        self.store.save(host.snapshot())
        return key

    def test_malformed_saved_state_never_opens_the_log(self):
        self.store.save({"version": 1, "accounts": []})
        error = self.failed_start(store=self.store)
        self.assertIsInstance(error, ValueError)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.streams, [])

    def test_invalid_late_configuration_never_opens_log_or_closes_borrowed_resources(self):
        store, backend = Mock(), Mock()
        store.load.return_value = None
        error = self.failed_start(store=store, rules_backend=backend, ordinary_rooms=(object(),))
        self.assertIsInstance(error, ValueError)
        self.assertIn("ordinary room configuration", str(error))
        self.assertFalse(self.path.exists())
        store.save.assert_not_called()
        store.close.assert_not_called()
        backend.close.assert_not_called()

    async def test_log_open_failure_happens_before_recovery_or_durable_writes(self):
        await self.pending_recovery()
        before = self.store.load()
        self.path.mkdir()
        with (
            patch.object(MatchEconomy, "recover_all") as matches,
            patch.object(ResultRewardService, "recover_all") as rewards,
            patch.object(self.store, "save") as save,
        ):
            self.assertIsInstance(self.failed_start(store=self.store), OSError)
            matches.assert_not_called()
            rewards.assert_not_called()
            save.assert_not_called()
        self.assertEqual(self.store.load(), before)

    async def test_failed_recovery_commit_closes_log_without_closing_borrowed_owners(self):
        await self.pending_recovery()
        before = self.store.load()
        store, backend = Mock(wraps=self.store), Mock()
        store.close = Mock()
        failure = OSError("recovery save failed")
        store.save.side_effect = failure
        with patch("tmserver.network.server.asyncio.start_server") as listen:
            error = self.failed_start(store=store, rules_backend=backend)
        self.assertIs(error, failure)
        self.assertEqual(len(self.streams), 1)
        self.assertTrue(self.streams[0].closed)
        self.path.rename(self.root / "released.jsonl")
        self.assertEqual(self.store.load(), before)
        store.save.assert_called_once()
        store.close.assert_not_called()
        backend.close.assert_not_called()
        listen.assert_not_called()

    def test_recovery_exception_survives_a_second_error_closing_the_log(self):
        stream = Mock()
        stream.close.side_effect = OSError("log close failed")
        failure = ValueError("recovery validation failed")
        original_open = Path.open

        def opened(path, *args, **kwargs):
            return stream if path == self.path else original_open(path, *args, **kwargs)

        with (
            patch.object(Path, "open", opened),
            patch.object(MatchEconomy, "recover_all", side_effect=failure),
            patch.object(JsonlLog, "fail") as report,
        ):
            error = self.failed_start()
        self.assertIs(error, failure)
        stream.close.assert_called_once()
        report.assert_called_once_with(stream.close.side_effect)

    async def test_successful_recovery_keeps_log_open_until_stop(self):
        key = await self.pending_recovery()
        store, backend = Mock(wraps=self.store), Mock()
        store.close = Mock()
        host = Server(log_path=self.path, store=store, rules_backend=backend)
        self.addAsyncCleanup(host.stop)
        stream = host.log.stream
        self.assertFalse(stream.closed)
        self.assertIsNone(host.listener)
        self.assertIsNone(host.result_rewards.status(key))
        self.assertEqual(len(host.auction_ledger.pending(key)), 1)
        store.save.assert_called_once()
        host.log("constructed")
        self.assertEqual(json.loads(self.path.read_text())["event"], "constructed")
        await host.stop()
        await host.stop()
        self.assertTrue(stream.closed)
        self.assertIsNone(host.log.stream)
        store.close.assert_not_called()
        backend.close.assert_not_called()
