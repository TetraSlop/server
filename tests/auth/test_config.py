"""Registration policy and legacy bindings are validated before save/log acquisition."""

from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from tmserver.network.local_accounts import account_specs


class AuthConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tmserver.auth.config import AuthConfig

        self.model = AuthConfig
        self.data = dict(
            supabase_url="https://example.supabase.co", publishable_key="sb_publishable_test_only",
            public_origin="HTTPS://TM.EXAMPLE:443/",
            bindings={"11111111-1111-4111-8111-111111111111": "EEEEEEEF"},
        )

    def test_canonical_origins_and_exact_unique_enrollment(self):
        config = self.model.model_validate(self.data)
        self.assertEqual(config.public_origin, "https://tm.example")
        self.assertEqual(list(config.account_bindings(account_specs()).values()), ["UGITW5T4U"])
        for updates in (
            dict(bindings={"not-a-uuid": "EEEEEEEF"}),
            dict(bindings=dict(self.data["bindings"], **{
                "22222222-2222-4222-8222-222222222222": "EEEEEEEF"})),
            dict(public_origin="https://tm.example/path"),
            dict(public_origin="http://tm.example"),
            dict(supabase_url="https://example.supabase.co/?url=other"),
            dict(unexpected=True),
        ):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.model.model_validate(dict(self.data, **updates))

    async def test_closed_registration_starts_discovery_but_denies_new_players(self):
        import httpx
        from tmserver.auth.provider import Subject
        from tmserver.auth.web import create_app
        from tmserver.cli.serve import arguments, running_services

        config = self.model.model_validate(dict(self.data, bindings={}, auto_register=False))
        self.assertEqual(config.account_bindings(account_specs()), {})

        class VerifiedUser:
            issuer = "https://example.supabase.co/auth/v1"

            async def verify(self, token):
                return Subject(issuer=self.issuer,
                               subject="11111111-1111-4111-8111-111111111111")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "auth.json"
            path.write_text(config.model_dump_json())
            args = arguments(["--auth-config", str(path), "--auth-insecure-loopback",
                              "--auth-port", "0", "--game-port", "0", "--profile-port", "0",
                              "--state", str(root / "state.json"), "--log-dir", str(root / "logs")])
            async with running_services(args) as services:
                async with httpx.AsyncClient(trust_env=False) as client:
                    result = await client.get(f"http://127.0.0.1:{services.auth.port}/v1/auth/config")
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()["provider"], "discord")
                # Closing registration denies a verified user with no saved account.
                before = deepcopy(services.game.snapshot())
                app = create_app(services.game, services.profile, config, VerifiedUser())
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url=config.public_origin) as client:
                    denied = await client.post("/v1/play/tickets", json={"protocol": 1},
                                               headers={"Authorization": "Bearer verified-user"})
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(denied.json(), {"error": "account_not_linked"})
                self.assertEqual(app.state.play_sessions.tickets, {})
                self.assertEqual(app.state.play_sessions.sessions, {})
                self.assertEqual(services.game.snapshot(), before)

    def test_accidental_secret_key_does_not_appear_in_configuration_error(self):
        secret = "sb_secret_do_not_log_this_value"
        with self.assertRaises(ValueError) as raised:
            self.model.model_validate(dict(self.data, publishable_key=secret))
        self.assertNotIn(secret, str(raised.exception))

    async def test_disabled_account_binding_fails_before_save_or_logs_are_created(self):
        from tmserver.cli.serve import arguments, running_services

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = deepcopy(self.data)
            data["bindings"][next(iter(data["bindings"]))] = "NOTREAL1"
            config = root / "auth.json"
            config.write_text(json.dumps(data))
            args = arguments(["--auth-config", str(config), "--state", str(root / "state.json"),
                              "--log-dir", str(root / "logs")])
            with self.assertRaisesRegex(ValueError, "not enabled"):
                async with running_services(args):
                    self.fail("invalid enrollment started services")
            self.assertFalse((root / "state.json").exists())
            self.assertFalse((root / "logs").exists())
