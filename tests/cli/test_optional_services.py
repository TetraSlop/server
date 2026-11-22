"""Required native runtime, optional admin configuration and resource ownership."""

import io
import os
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tmserver.cli.serve import admin_options, arguments, running_services
from tmserver.game.original.image import OriginalCodeError
from tmserver.network.server import Server
from tmserver.profiles.service import ProfileServer
from tmserver.storage.json import writer_lease


class OptionalConfigurationTests(unittest.TestCase):
    def test_argument_inspection_does_not_enable_admin_or_load_native_code(self):
        with patch.dict(os.environ, {"TM_ADMIN_PASSWORD": "must-not-enable-admin"}, clear=True):
            args = arguments([])
            self.assertIsNone(admin_options(args))
        self.assertIsNone(args.rules_image)
        self.assertIsNone(args.rules_bridge)
        self.assertIsNone(args.admin_port)

    def test_native_paths_use_environment_and_explicit_arguments_override_them(self):
        with patch.dict(os.environ, {"TM_ORIGINAL_IMAGE": "original.dll", "TM_NATIVE_BRIDGE": "native.dll"}):
            args = arguments([])
            self.assertEqual((args.rules_image, args.rules_bridge), (Path("original.dll"), Path("native.dll")))
            args = arguments(["--rules-image", "other.dll", "--rules-bridge", "bridge.dll"])
            self.assertEqual((args.rules_image, args.rules_bridge), (Path("other.dll"), Path("bridge.dll")))

    def test_rejects_removed_backend_switch_and_invalid_admin_options(self):
        for options in (
            ["--rules-backend", "native-hybrid"],
            ["--rules-backend", "python"],
            ["--admin-port", "-1"], ["--admin-port", "65536"],
            ["--admin-password-file", "password.txt"],
            ["--admin-actions"],
            ["--auth-port", "8081"], ["--auth-insecure-loopback"],
            ["--auth-config", "auth.json", "--auth-port", "65536"],
        ):
            with self.subTest(options=options), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    arguments(options)
                self.assertEqual(raised.exception.code, 2)


class OptionalLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state.json"
        self.args = arguments([
            "--state", str(self.state), "--log-dir", str(self.root / "logs"),
            "--game-port", "0", "--profile-port", "0",
            "--rules-image", "image.dll", "--rules-bridge", "bridge.dll",
        ])

    async def test_native_context_outlives_both_services_even_when_shutdown_fails(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                order = []
                backend = MagicMock()
                backend.__enter__.return_value = backend
                backend.__exit__.side_effect = lambda *_: order.append("native")

                def tracked(name, original):
                    async def stop(host):
                        backend.__exit__.assert_not_called()
                        order.append(name)
                        await original(host)
                        if failure and name == "profile":
                            raise OSError("simulated profile shutdown failure")
                    return stop

                with (
                    patch("tmserver.game.original.native.NativeRules", return_value=backend) as create,
                    patch.object(ProfileServer, "stop", tracked("profile", ProfileServer.stop)),
                    patch.object(Server, "stop", tracked("game", Server.stop)),
                ):
                    try:
                        async with running_services(self.args) as services:
                            self.assertIs(services.game.rules_backend, backend)
                            backend.__exit__.assert_not_called()
                    except OSError:
                        self.assertTrue(failure)
                    else:
                        self.assertFalse(failure)
                create.assert_called_once_with(Path("image.dll"), Path("bridge.dll"))
                self.assertEqual(order, ["profile", "game", "native"])
                backend.__exit__.assert_called_once()
                with writer_lease(self.state):
                    pass

    async def test_missing_native_paths_reject_before_any_server_side_effect(self):
        for missing in ("rules_image", "rules_bridge"):
            with (self.subTest(missing=missing), patch.object(self.args, missing, None),
                  patch.dict(os.environ, {}, clear=True)):
                with (
                    patch("tmserver.cli.serve.Server") as create,
                    patch("tmserver.cli.serve.writer_lease") as lease,
                ):
                    with self.assertRaisesRegex(OriginalCodeError, "Native rules require"):
                        async with running_services(self.args):
                            self.fail("server started without native rules")
                    create.assert_not_called()
                    lease.assert_not_called()
        self.assertFalse(self.args.log_dir.exists())
        self.assertFalse(self.state.exists())

    async def test_native_preflight_failure_never_opens_store_listeners_or_creates_logs(self):
        with (
            patch("tmserver.game.original.native.NativeRules", side_effect=OriginalCodeError("wrong ABI")),
            patch("tmserver.cli.serve.Server") as create,
            patch("tmserver.cli.serve.writer_lease") as lease,
        ):
            with self.assertRaisesRegex(OriginalCodeError, "wrong ABI"):
                async with running_services(self.args):
                    self.fail("invalid native context started")
            create.assert_not_called()
            lease.assert_not_called()
        self.assertFalse(self.args.log_dir.exists())
        self.assertFalse(self.state.exists())

    async def test_failed_host_construction_closes_native_context_and_writer_lease(self):
        backend = MagicMock()
        backend.__enter__.return_value = backend
        backend.__exit__.return_value = False
        with (
            patch("tmserver.game.original.native.NativeRules", return_value=backend),
            patch("tmserver.cli.serve.Server", side_effect=ValueError("invalid saved state")),
        ):
            with self.assertRaisesRegex(ValueError, "invalid saved state"):
                async with running_services(self.args):
                    self.fail("invalid host started")
        backend.__exit__.assert_called_once()
        with writer_lease(self.state):
            pass

    async def test_admin_missing_credentials_rejects_before_any_server_side_effect(self):
        self.args.admin_port = 0
        with patch.dict(os.environ, {}, clear=True), patch("tmserver.cli.serve.Server") as create:
            with self.assertRaisesRegex(ValueError, "nonempty, single-line password"):
                async with running_services(self.args):
                    self.fail("unprotected panel started")
            create.assert_not_called()
        self.assertFalse(self.args.log_dir.exists())
        self.assertFalse(self.state.exists())
