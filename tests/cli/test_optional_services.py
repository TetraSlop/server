"""Optional admin configuration and resource ownership."""

import io
import os
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.support.memory_store import DATABASE, MemoryStore, opened_by
from tmserver.cli.serve import admin_options, arguments, running_services
from tmserver.network.server import Server
from tmserver.profiles.service import ProfileServer


class OptionalConfigurationTests(unittest.TestCase):
    def test_argument_inspection_does_not_enable_admin(self):
        with patch.dict(os.environ, {"TM_ADMIN_PASSWORD": "must-not-enable-admin"}, clear=True):
            args = arguments(DATABASE)
            self.assertIsNone(admin_options(args))
        self.assertIsNone(args.admin_port)

    def test_rejects_removed_backend_switch_and_invalid_admin_options(self):
        for options in (
            ["--rules-backend", "native-hybrid"],
            ["--rules-backend", "python"],
            ["--rules-image", "image.dll"], ["--rules-bridge", "bridge.dll"],
            ["--admin-port", "-1"], ["--admin-port", "65536"],
            ["--admin-password-file", "password.txt"],
            ["--admin-actions"],
            ["--auth-port", "8081"], ["--auth-insecure-loopback"],
            ["--auth-config", "auth.json", "--auth-port", "65536"],
        ):
            with self.subTest(options=options), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    arguments([*DATABASE, *options])
                self.assertEqual(raised.exception.code, 2)

    def test_database_url_is_required_except_to_list_accounts(self):
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
                arguments([])
            self.assertIn("TM_DATABASE_URL", error.getvalue())
            self.assertIsNone(arguments(["--list-accounts"]).database_url)
        with patch.dict(os.environ, {"TM_DATABASE_URL": "postgresql+psycopg://from-env"}):
            self.assertEqual(arguments([]).database_url, "postgresql+psycopg://from-env")


class OptionalLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.args = arguments([
            *DATABASE, "--log-dir", str(self.root / "logs"),
            "--game-port", "0", "--profile-port", "0",
        ])

    async def test_store_outlives_both_services_even_when_shutdown_fails(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                order = []
                store = MemoryStore()

                def tracked(name, original):
                    async def stop(host):
                        self.assertFalse(store.closed)
                        order.append(name)
                        await original(host)
                        if failure and name == "profile":
                            raise OSError("simulated profile shutdown failure")
                    return stop

                with (
                    opened_by("tmserver.cli.serve", store),
                    patch.object(ProfileServer, "stop", tracked("profile", ProfileServer.stop)),
                    patch.object(Server, "stop", tracked("game", Server.stop)),
                ):
                    try:
                        async with running_services(self.args):
                            pass
                    except OSError:
                        self.assertTrue(failure)
                    else:
                        self.assertFalse(failure)
                self.assertEqual(order, ["profile", "game"])
                self.assertTrue(store.closed)

    async def test_failed_host_construction_closes_the_store(self):
        store = MemoryStore()
        with (
            opened_by("tmserver.cli.serve", store),
            patch("tmserver.cli.serve.Server", side_effect=ValueError("invalid saved state")),
        ):
            with self.assertRaisesRegex(ValueError, "invalid saved state"):
                async with running_services(self.args):
                    self.fail("invalid host started")
        self.assertTrue(store.closed)

    async def test_admin_missing_credentials_rejects_before_any_server_side_effect(self):
        self.args.admin_port = 0
        with patch.dict(os.environ, {}, clear=True), patch("tmserver.cli.serve.Server") as create:
            with self.assertRaisesRegex(ValueError, "nonempty, single-line password"):
                async with running_services(self.args):
                    self.fail("unprotected panel started")
            create.assert_not_called()
        self.assertFalse(self.args.log_dir.exists())
