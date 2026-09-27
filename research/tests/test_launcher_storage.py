"""Launcher storage selection, with all networking and process launch mocked."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import run_local_game as launcher


class LauncherStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(launcher, "ROOT", Path(self.folder)))
        self.game = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        self.profiles = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        self.make_game = self.enterContext(patch.object(launcher, "Server", return_value=self.game))
        self.enterContext(patch.object(launcher, "ProfileServer", return_value=self.profiles))
        self.child = SimpleNamespace(wait=AsyncMock(return_value=0), returncode=0)
        self.launch = self.enterContext(
            patch.object(
                launcher.asyncio, "create_subprocess_exec", AsyncMock(return_value=self.child)
            )
        )
        self.events = []
        events = self.events

        class Store:
            def __enter__(self):
                events.append("store opened")
                return self

            def __exit__(self, *exc):
                events.append("store closed")

        self.make_store = Mock(side_effect=lambda url: Store())
        self.enterContext(
            patch.dict(
                "sys.modules",
                {
                    "postgres_store": SimpleNamespace(PostgresStateStore=self.make_store),
                },
            )
        )
        self.game.stop.side_effect = lambda: events.append("game stopped")

    async def test_default_remains_json_even_when_database_environment_is_set(self):
        with patch.dict(os.environ, {"TM_DATABASE_URL": "private-database-url"}):
            self.assertEqual(await launcher.run(), 0)
        self.make_store.assert_not_called()
        self.assertEqual(
            self.make_game.call_args.args[1], Path(self.folder) / "server/local_state.json"
        )
        self.assertIsNone(self.make_game.call_args.kwargs["store"])
        self.assertNotIn("TM_DATABASE_URL", self.launch.call_args.kwargs["env"])

    async def test_explicit_database_lives_until_game_shutdown_and_credentials_stay_in_server(self):
        with patch.dict(os.environ, {"TM_DATABASE_URL": "private-database-url"}):
            self.assertEqual(await launcher.run(database=True), 0)
        self.make_store.assert_called_once_with("private-database-url")
        self.assertIsNone(self.make_game.call_args.args[1])
        self.assertIsNotNone(self.make_game.call_args.kwargs["store"])
        self.assertEqual(self.events, ["store opened", "game stopped", "store closed"])
        self.assertNotIn("TM_DATABASE_URL", self.launch.call_args.kwargs["env"])

    async def test_missing_configuration_fails_before_network_or_process_launch(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            await launcher.run(database=True)
        self.make_game.assert_not_called()
        self.make_store.assert_not_called()
        self.launch.assert_not_awaited()

    async def test_constructor_failure_releases_database_without_launching(self):
        self.make_game.side_effect = ValueError("invalid server configuration")
        with (
            patch.dict(os.environ, {"TM_DATABASE_URL": "private-database-url"}),
            self.assertRaises(ValueError),
        ):
            await launcher.run(database=True)
        self.assertEqual(self.events, ["store opened", "store closed"])
        self.launch.assert_not_awaited()

    async def test_launch_failure_stops_services_before_releasing_store(self):
        self.launch.side_effect = OSError("injected process failure")
        with (
            patch.dict(os.environ, {"TM_DATABASE_URL": "private-database-url"}),
            self.assertRaises(OSError),
        ):
            await launcher.run(database=True)
        self.profiles.stop.assert_awaited_once()
        self.assertEqual(self.events, ["store opened", "game stopped", "store closed"])


if __name__ == "__main__":
    unittest.main()
