"""Weekly tracking is explicit and reaches the packaged server startup path."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tests.support.memory_store import DATABASE, MemoryStore, opened_by
from tmserver.cli.serve import arguments, running_services


class WeeklyRankingConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_flag_requires_local_history_ratings(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            arguments([*DATABASE, "--weekly-rankings"])
        self.assertFalse(arguments(DATABASE).weekly_rankings)
        args = arguments([*DATABASE, "--ratings", "local-history", "--weekly-rankings"])
        self.assertTrue(args.weekly_rankings)
        self.assertEqual(args.ratings, "local-history")

    async def test_packaged_startup_initializes_week_before_opening_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = MemoryStore()
            self.enterContext(opened_by("tmserver.cli.serve", store))
            args = arguments([
                *DATABASE, "--log-dir", str(root / "logs"),
                "--game-port", "0", "--profile-port", "0",
                "--ratings", "local-history", "--weekly-rankings",
            ])
            async with running_services(args) as services:
                self.assertTrue(services.game.ratings.weekly)
                saved = store.load()
                self.assertEqual(saved["ratings"]["version"], 2)
                self.assertEqual(saved["ratings"], services.game.ratings.snapshot())
                self.assertTrue(services.game.listener.is_serving())
                self.assertTrue(services.profile.listener.is_serving())
                self.assertFalse(services.game.advance_rankings())
            before = store.load()
            # Saved v2 data does not implicitly enable a runtime policy.
            args = arguments([
                *DATABASE, "--log-dir", str(root / "logs"),
                "--game-port", "0", "--profile-port", "0",
            ])
            async with running_services(args) as services:
                self.assertFalse(services.game.ratings.weekly)
                self.assertEqual(services.game.ratings.weekly_history(), {})
                self.assertEqual(store.load(), before)


if __name__ == "__main__":
    unittest.main()
