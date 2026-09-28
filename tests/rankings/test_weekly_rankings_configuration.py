"""Weekly tracking is explicit and reaches the packaged server startup path."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tmserver.cli.serve import arguments, running_services


class WeeklyRankingConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_flag_requires_local_history_ratings(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            arguments(["--weekly-rankings"])
        self.assertFalse(arguments([]).weekly_rankings)
        args = arguments(["--ratings", "local-history", "--weekly-rankings"])
        self.assertTrue(args.weekly_rankings)
        self.assertEqual(args.ratings, "local-history")

    async def test_packaged_startup_initializes_week_before_opening_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / "state.json"
            args = arguments([
                "--state", str(state), "--log-dir", str(root / "logs"),
                "--game-port", "0", "--profile-port", "0",
                "--ratings", "local-history", "--weekly-rankings",
            ])
            async with running_services(args) as services:
                self.assertTrue(services.game.ratings.weekly)
                saved = json.loads(state.read_text())
                self.assertEqual(saved["ratings"]["version"], 2)
                self.assertEqual(saved["ratings"], services.game.ratings.snapshot())
                self.assertTrue(services.game.listener.is_serving())
                self.assertTrue(services.profile.listener.is_serving())
                self.assertFalse(services.game.advance_rankings())
            before = state.read_bytes()
            # Saved v2 data does not implicitly enable a runtime policy.
            args = arguments([
                "--state", str(state), "--log-dir", str(root / "logs"),
                "--game-port", "0", "--profile-port", "0",
            ])
            async with running_services(args) as services:
                self.assertFalse(services.game.ratings.weekly)
                self.assertEqual(services.game.ratings.weekly_history(), ({}, 0))
                self.assertEqual(state.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
