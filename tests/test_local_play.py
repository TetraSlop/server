"""Launcher lifecycle checks use headless protocol peers, never the native game."""

import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tmserver import local_play
from tmserver.economy_service import EconomyService
from tmserver.run_server import running_services
from tmserver.server import Server

ROOT = Path(__file__).resolve().parents[1]


class LocalPlayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        (self.root / "examples").mkdir()
        shutil.copyfile(
            ROOT / "examples/auto_rooms.example.json",
            self.root / "examples/auto_rooms.example.json",
        )
        self.args = local_play.arguments(["--project-dir", str(self.root), "--clients", "3"])

    async def test_three_owned_headless_peers_authenticate_both_services_save_and_exit(self):
        actual_spawn = asyncio.create_subprocess_exec
        commands = []

        async def spawn(*command, **options):
            commands.append(command)
            options["cwd"] = ROOT
            return await actual_spawn(
                sys.executable, "-B", "-m", "tests.local_play_peer", *command[1:], **options
            )

        with (
            patch.object(local_play, "preflight", AsyncMock()),
            patch.object(local_play.asyncio, "create_subprocess_exec", spawn),
        ):
            self.assertEqual(await local_play.run(self.args), 0)
        self.assertEqual(len(commands), 3)
        snapshot = json.loads(self.args.state.read_text())
        restored = EconomyService.restore(snapshot)
        self.assertEqual(len(snapshot["accounts"]), 3)
        for key in snapshot["accounts"]:
            self.assertEqual(restored.profile_bytes(key)[0xB0], 1)
        session = next((self.root / "var/sessions").iterdir())
        metadata = json.loads((session / "session.json").read_text())
        self.assertEqual(json.loads((session / "exits.json").read_text()), [0, 0, 0])
        self.assertEqual(len(set(metadata["accounts"])), 3)
        self.assertEqual((metadata["ratings"], metadata["weekly_rankings"]), ("preserve", False))
        for port in (metadata["game_port"], metadata["profile_port"]):
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
        for index, command in enumerate(commands, 1):
            self.assertIn(f"logs/{metadata['session']}/player-{index}.log", command)

    async def test_check_and_failed_preflight_never_start_services_or_create_state(self):
        self.args.check = True
        with (
            patch.object(local_play, "preflight", AsyncMock()),
            patch.object(local_play, "running_services") as start,
        ):
            self.assertEqual(await local_play.run(self.args), 0)
            start.assert_not_called()
        self.assertFalse((self.root / "var").exists())
        self.args.check = False
        with (
            patch.object(local_play, "preflight", AsyncMock(side_effect=ValueError("bad DLL"))),
            patch.object(local_play, "running_services") as start,
        ):
            with self.assertRaisesRegex(ValueError, "bad DLL"):
                await local_play.run(self.args)
            start.assert_not_called()
        self.assertFalse((self.root / "var").exists())

    async def test_second_launch_failure_retires_only_its_already_started_child(self):
        child = AsyncMock()
        child.returncode = None
        child.kill = unittest.mock.Mock()
        services = unittest.mock.Mock(game_port=41000, profile_port=41001)
        with patch.object(
            local_play.asyncio,
            "create_subprocess_exec",
            AsyncMock(side_effect=[child, OSError("spawn failed")]),
        ):
            with self.assertRaisesRegex(OSError, "spawn failed"):
                await local_play.play_clients(self.args, services, "fixture", self.root)
        child.kill.assert_called_once()
        child.wait.assert_awaited_once()

    async def test_profile_bind_failure_releases_already_started_game_listener(self):
        occupied = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        self.addAsyncCleanup(occupied.wait_closed)
        self.addCleanup(occupied.close)
        settings = local_play.service_settings(self.args, self.root / "logs")
        settings.profile_port = occupied.sockets[0].getsockname()[1]
        ports = []
        original = Server.start

        async def start(game, port):
            actual = await original(game, port)
            ports.append(actual)
            return actual

        with patch.object(Server, "start", start):
            with self.assertRaises(OSError):
                async with running_services(settings):
                    self.fail("both services should not have started")
        self.assertEqual(len(ports), 1)
        with self.assertRaises(OSError):
            await asyncio.open_connection("127.0.0.1", ports[0])
        self.assertTrue(occupied.is_serving())

    def test_session_lock_rejects_second_owner_and_releases_on_error(self):
        with self.assertRaisesRegex(ValueError, "simulated"):
            with local_play.session_lock(self.args.state):
                with self.assertRaisesRegex(OSError, "Another Play session"):
                    with local_play.session_lock(self.args.state):
                        self.fail("second owner admitted")
                raise ValueError("simulated")
        with local_play.session_lock(self.args.state):
            pass

    def test_local_launch_ignores_database_env_and_scrubs_child_environment(self):
        with patch.dict(
            os.environ,
            {"TM_DATABASE_URL": "secret", "TM_PROBE_ADVANCE": "1", "__COMPAT_LAYER": "RUNASADMIN"},
        ):
            settings = local_play.service_settings(self.args, self.root / "logs")
            self.assertIsNone(settings.database_url)
            self.assertEqual(
                (settings.accounts, settings.game_port, settings.profile_port), (3, 0, 0)
            )
            child = local_play.child_environment()
            self.assertNotIn("TM_DATABASE_URL", child)
            self.assertNotIn("TM_PROBE_ADVANCE", child)
            self.assertNotIn("__COMPAT_LAYER", child)
        self.assertFalse(settings.card_prizes)
        self.args.rewards = True
        settings = local_play.service_settings(self.args, self.root / "logs")
        self.assertTrue(settings.card_prizes and settings.allow_cbc and settings.wagers)

    def test_rating_flags_validate_and_reach_service_arguments(self):
        for flags, expected in (
            ([], ("preserve", False)),
            (["--ratings", "preserve"], ("preserve", False)),
            (["--ratings", "local-history"], ("local-history", False)),
            (["--ratings", "local-history", "--weekly-rankings"], ("local-history", True)),
        ):
            with self.subTest(flags=flags):
                args = local_play.arguments(["--project-dir", str(self.root), *flags])
                settings = local_play.service_settings(args, self.root / "logs")
                self.assertEqual((settings.ratings, settings.weekly_rankings), expected)
        for flags in (
            ["--weekly-rankings"],
            ["--ratings", "preserve", "--weekly-rankings"],
            ["--ratings", "unknown"],
        ):
            with self.subTest(flags=flags), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as rejected:
                    local_play.arguments(["--project-dir", str(self.root), *flags])
                self.assertEqual(rejected.exception.code, 2)
        self.assertFalse((self.root / "var").exists())

    async def test_weekly_policy_reaches_runtime_and_session_then_can_be_paused(self):
        async def clients(args, services, session, log_dir):
            self.assertTrue(services.game.ratings.enabled)
            self.assertEqual(services.game.ratings.weekly, args.weekly_rankings)
            return [0]

        saved = None
        for weekly in (True, False):
            args = local_play.arguments([
                "--project-dir", str(self.root), "--ratings", "local-history",
                *(["--weekly-rankings"] if weekly else []),
            ])
            with (
                patch.object(local_play, "preflight", AsyncMock()),
                patch.object(local_play, "play_clients", AsyncMock(side_effect=clients)),
            ):
                self.assertEqual(await local_play.run(args), 0)
            current = json.loads(args.state.read_text())["ratings"]
            self.assertEqual(current["version"], 2)
            if saved is not None:
                self.assertEqual(current, saved)
            saved = current
        sessions = [json.loads(path.read_text())
                    for path in (self.root / "var/sessions").glob("*/session.json")]
        self.assertEqual(len(sessions), 2)
        self.assertEqual({(s["ratings"], s["weekly_rankings"]) for s in sessions},
                         {("local-history", True), ("local-history", False)})
