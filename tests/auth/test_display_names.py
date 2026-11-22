"""Discord labels reach saved game profiles without changing account ownership."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.support.memory_store import MemoryStore
from tmserver.network.server import Server
from tmserver.profiles.service import ProfileServer


class DiscordProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from tmserver.auth.config import AuthConfig
        from tmserver.auth.provider import SupabaseVerifier
        from tmserver.auth.web import create_app

        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = MemoryStore()
        self.host = Server(store=self.store)
        self.addAsyncCleanup(self.host.stop)
        self.profile = ProfileServer(self.root / "profile.log", self.host)
        self.addAsyncCleanup(self.profile.stop)
        self.key = next(iter(self.host.account_specs))
        self.host.ensure_account(self.key.encode())
        self.uuid = "11111111-1111-4111-8111-111111111111"
        self.name = "Discord Name"

        def user(request):
            return httpx.Response(200, json={
                "id": self.uuid,
                "identities": [{"provider": "discord", "identity_data": {"global_name": self.name}}],
            })

        config = AuthConfig(auto_register=False, supabase_url="https://fixture.supabase.co",
                            publishable_key="sb_publishable_fixture", public_origin="https://play.test",
                            bindings={self.uuid: "EEEEEEEF"})
        provider = await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.MockTransport(user)))
        verifier = SupabaseVerifier(config.supabase_url, config.publishable_key, client=provider)
        self.app = create_app(self.host, self.profile, config, verifier)
        self.addCleanup(self.app.state.play_sessions.close)
        self.http = await self.enterAsyncContext(httpx.AsyncClient(
            base_url=config.public_origin, transport=httpx.ASGITransport(app=self.app), trust_env=False))
        self.headers = {"Authorization": "Bearer fixture-user"}

    async def ticket(self):
        response = await self.http.post("/v1/play/tickets", json={"protocol": 1}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["ticket"]

    async def play(self):
        return await self.http.post("/v1/play/sessions", json={"ticket": await self.ticket()})

    async def test_play_persists_only_name_and_both_profile_formats_use_it(self):
        before, disk = deepcopy(self.host.snapshot()), self.store.load()
        ticket = await self.ticket()
        self.assertEqual(self.store.load(), disk)  # Sign-in alone never edits the game account.
        result = await self.http.post("/v1/play/sessions", json={"ticket": ticket})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["account_key"], self.key)
        after = self.host.snapshot()
        expected = deepcopy(before)
        expected["accounts"][self.key]["name_hex"] = b"Discord Name".hex()
        self.assertEqual(after, expected)
        self.assertEqual(self.profile._identity(self.key)[2], b"Discord Name")
        self.assertEqual(self.host.economy.profile_bytes(self.key)[0x10:0x20], b"Discord Name".ljust(16, b"\0"))
        restored = Server(store=self.store)
        try:
            self.assertEqual(restored.snapshot(), after)
        finally:
            await restored.stop()

    async def test_rename_waits_for_next_play_and_does_not_break_renewal_or_revoke(self):
        first = await self.play()
        self.assertEqual(first.status_code, 201)
        token = first.json()["session_token"]
        disk = self.store.load()
        self.name = "New Name"
        renewed = await self.http.post("/v1/play/renew", json={"session_token": token}, headers=self.headers)
        self.assertEqual(renewed.status_code, 200)
        self.assertEqual((await self.play()).status_code, 409)
        self.assertEqual(self.store.load(), disk)
        self.assertEqual((await self.http.delete("/v1/play/account-session", headers=self.headers)).status_code, 204)
        self.assertEqual((await self.play()).status_code, 201)
        self.assertEqual(self.host.economy.account_view(self.key).name, b"New Name")

    async def test_unchanged_or_missing_name_does_not_write(self):
        self.host.ensure_account(self.key.encode(), name=b"Discord Name")
        for name in ("Discord Name", "✨"):
            with self.subTest(name=name), patch.object(self.host, "persist_changes", side_effect=AssertionError("unexpected write")):
                self.name = name
                self.assertEqual((await self.play()).status_code, 201)
                self.assertEqual((await self.http.delete("/v1/play/account-session", headers=self.headers)).status_code, 204)
                self.assertEqual(self.host.economy.account_view(self.key).name, b"Discord Name")

    async def test_storage_failure_rolls_back_name_and_revokes_new_play_session(self):
        before, disk = deepcopy(self.host.snapshot()), self.store.load()
        with patch.object(self.host.store, "save_changes", side_effect=OSError("write failed")):
            response = await self.play()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "storage_unavailable"})
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.store.load(), disk)
        self.assertFalse(self.app.state.play_sessions.sessions)
        self.assertIsNotNone(self.host.storage_failed)

    async def test_busy_or_connected_accounts_keep_their_current_name_until_idle(self):
        reservation = self.host.economy.reserve_match(self.key)
        disk = self.store.load()
        self.assertEqual((await self.play()).status_code, 201)
        self.assertEqual(self.store.load(), disk)
        self.host.economy.release_match(reservation)
        await self.http.delete("/v1/play/account-session", headers=self.headers)
        self.host.nicks[self.key.encode()] = object()
        try:
            self.assertEqual((await self.play()).status_code, 201)
            self.assertEqual(self.store.load(), disk)
        finally:
            self.host.nicks.clear()
        await self.http.delete("/v1/play/account-session", headers=self.headers)
        self.assertEqual((await self.play()).status_code, 201)
        self.assertEqual(self.host.economy.account_view(self.key).name, b"Discord Name")

    async def test_lost_save_acknowledgement_never_returns_a_play_token_and_restart_loads_name(self):
        before = deepcopy(self.host.snapshot())
        save = self.host.store.save_changes

        def commit_then_fail(accounts, services):
            save(accounts, services)
            raise OSError("commit acknowledgement lost")

        with patch.object(self.host.store, "save_changes", side_effect=commit_then_fail):
            response = await self.play()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "storage_unavailable"})
        self.assertFalse(self.app.state.play_sessions.sessions)
        self.assertEqual(self.host.snapshot(), before)
        restored = Server(store=self.store)
        try:
            expected = deepcopy(before)
            expected["accounts"][self.key]["name_hex"] = b"Discord Name".hex()
            self.assertEqual(restored.snapshot(), expected)
        finally:
            await restored.stop()

    async def test_unenrolled_identity_cannot_rename_a_profile(self):
        before, disk = deepcopy(self.host.snapshot()), self.store.load()
        self.uuid = "22222222-2222-4222-8222-222222222222"
        response = await self.http.post("/v1/play/tickets", json={"protocol": 1}, headers=self.headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "account_not_linked"})
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.store.load(), disk)
