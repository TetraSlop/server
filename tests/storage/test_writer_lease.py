"""Cooperative JSON ownership across services and offline administration."""

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tmserver.cli import play as local_play
from tmserver.cli.serve import arguments, running_services
from tmserver.storage.json import JsonStateStore, writer_lease


class WriterLeaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.folder) / "state.json"

    def child(self, source):
        environment = dict(os.environ)
        source_path = str(ROOT / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, (source_path, environment.get("PYTHONPATH")))
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", source, str(self.path)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).stdout.strip()

    def test_canonical_aliases_share_ownership_but_other_saves_do_not(self):
        alias = self.path.parent / "unused" / ".." / self.path.name
        with writer_lease(self.path):
            with self.assertRaisesRegex(OSError, "another server or admin"):
                with writer_lease(alias):
                    self.fail("alias acquired a second lease")
            with writer_lease(self.path.with_name("other.json")):
                pass
        with writer_lease(alias):
            pass
        self.assertFalse(self.path.exists())
        self.assertTrue(self.path.with_name("state.json.writer.lock").exists())

    def test_separate_process_is_excluded_before_loading_and_release_allows_it(self):
        source = """
import sys
from tmserver.storage.json import writer_lease
try:
    with writer_lease(sys.argv[1]):
        print('owned')
except OSError:
    print('busy')
"""
        with writer_lease(self.path):
            self.assertEqual(self.child(source), "busy")
        self.assertEqual(self.child(source), "owned")

    def test_process_death_releases_lease_and_leaves_stable_sidecar(self):
        self.child("""
import os
import sys
from tmserver.storage.json import writer_lease
with writer_lease(sys.argv[1]):
    os._exit(0)
""")
        self.assertTrue(self.path.with_name("state.json.writer.lock").exists())
        with writer_lease(self.path):
            pass

    def test_body_errors_propagate_unchanged_and_release_lease(self):
        error = OSError("save operation failed")
        with self.assertRaises(OSError) as caught:
            with writer_lease(self.path):
                raise error
        self.assertIs(caught.exception, error)
        with writer_lease(self.path):
            pass

    def test_lease_does_not_interfere_with_existing_commit_lock_or_store_semantics(self):
        snapshot = {"version": 1, "accounts": {"one": {"balance": 100}}}
        with writer_lease(self.path):
            store = JsonStateStore(self.path)
            store.save(snapshot)
            self.assertEqual(store.load(), snapshot)
            # Low-level stores still permit direct cooperative commits. Their
            # fingerprint rejects stale memory even while a launcher owns a lease.
            JsonStateStore(self.path).save_changes({"one": {"balance": 200}}, {})
            with self.assertRaises(OSError):
                store.save(snapshot)
        self.assertEqual(JsonStateStore(self.path).load()["accounts"]["one"]["balance"], 200)
        self.assertTrue(self.path.with_name("state.json.lock").exists())


class RunningServicesLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "save" / "state.json"
        self.args = arguments(
            [
                "--state",
                str(self.path),
                "--log-dir",
                str(self.root / "logs"),
                "--game-port",
                "0",
                "--profile-port",
                "0",
            ]
        )

    def assert_owned(self):
        with self.assertRaisesRegex(OSError, "JSON writer lease"):
            with writer_lease(self.path):
                self.fail("service lifetime lease was released too early")

    async def assert_listeners_closed(self, services):
        for port in (services.game_port, services.profile_port):
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)

    async def test_busy_lease_rejects_before_server_construction_or_recovery(self):
        with writer_lease(self.path), patch("tmserver.cli.serve.Server") as create:
            with self.assertRaisesRegex(OSError, "JSON writer lease"):
                async with running_services(self.args):
                    self.fail("busy server started")
            create.assert_not_called()
        self.assertFalse(self.path.exists())

    async def test_constructor_failure_releases_lease_without_creating_save(self):
        def fail(*args, **kwargs):
            self.assert_owned()
            raise ValueError("invalid saved data")

        with patch("tmserver.cli.serve.Server", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "invalid saved data"):
                async with running_services(self.args):
                    self.fail("invalid server started")
        with writer_lease(self.path):
            self.assertFalse(self.path.exists())

    async def test_profile_bind_failure_closes_game_and_releases_lease(self):
        occupied = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
        self.addAsyncCleanup(occupied.wait_closed)
        self.addCleanup(occupied.close)
        self.args.profile_port = occupied.sockets[0].getsockname()[1]
        from tmserver.network.server import Server

        start = Server.start
        ports = []

        async def capture_start(game, port):
            self.assert_owned()
            actual = await start(game, port)
            ports.append(actual)
            return actual

        with patch.object(Server, "start", capture_start):
            with self.assertRaises(OSError):
                async with running_services(self.args):
                    self.fail("occupied profile listener started")
        self.assertEqual(len(ports), 1)
        with self.assertRaises(OSError):
            await asyncio.open_connection("127.0.0.1", ports[0])
        with writer_lease(self.path):
            pass
        self.assertTrue(occupied.is_serving())

    async def test_lease_covers_both_stops_even_when_either_stop_raises(self):
        for failing in (None, "profile", "game"):
            with self.subTest(failing=failing):
                stopped = []
                error = OSError("shutdown failure")

                def stop(name, original):
                    async def wrapped():
                        self.assert_owned()
                        stopped.append(name)
                        await original()
                        if failing == name:
                            raise error

                    return wrapped

                try:
                    async with running_services(self.args) as services:
                        services.profile.stop = stop("profile", services.profile.stop)
                        services.game.stop = stop("game", services.game.stop)
                        self.assert_owned()
                except OSError as caught:
                    self.assertIs(caught, error)
                else:
                    self.assertIsNone(failing)
                self.assertEqual(stopped, ["profile", "game"])
                await self.assert_listeners_closed(services)
                with writer_lease(self.path):
                    pass

    async def test_normal_play_service_context_uses_the_shared_lease(self):
        play = local_play.arguments(["--project-dir", str(self.root), "--state", str(self.path)])
        settings = local_play.service_settings(play, self.root / "logs")
        settings.rooms = None
        with local_play.session_lock(self.path):
            async with running_services(settings) as services:
                self.assert_owned()
                self.assertTrue(services.game.listener.is_serving())
                self.assertTrue(services.profile.listener.is_serving())
        await self.assert_listeners_closed(services)
        with writer_lease(self.path):
            pass
        self.assertTrue(self.path.with_name("state.json.play.lock").exists())


if __name__ == "__main__":
    unittest.main()
