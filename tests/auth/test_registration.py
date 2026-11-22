"""First Discord Play creates durable ownership and works on both legacy streams."""

import asyncio
from contextlib import AsyncExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.accounts.test_provisioning import ISSUER, USER, SECOND
from tests.auth.test_transport import local_bridge
from tests.support.irc import Client
from tests.profiles.test_profile_accounts import file_request, transact
from tmserver.network.server import Server
from tmserver.profiles.service import ProfileServer


class AutomaticRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tmserver.auth.config import AuthConfig

        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "state.json"
        self.config = AuthConfig(supabase_url=ISSUER.removesuffix("/auth/v1"),
                                 publishable_key="sb_publishable_fixture", public_origin="https://play.test")
        self.assertTrue(self.config.auto_register)
        self.assertEqual(self.config.bindings, {})
        self.discord, self.name = True, "Same Name"
        await self.start()

    async def start(self):
        import httpx
        from tmserver.auth.provider import InvalidToken, Subject
        from tmserver.auth.web import create_app
        from tmserver.network.http import serving_http

        self.resources = AsyncExitStack()
        self.addAsyncCleanup(self.resources.aclose)
        self.host = Server(state_path=self.path)
        self.resources.push_async_callback(self.host.stop)
        self.profile = ProfileServer(self.root / "profile.jsonl", self.host)
        self.resources.push_async_callback(self.profile.stop)
        self.game_port = await self.host.start(0)
        self.profile_port = await self.profile.start(0)
        case = self

        class Verifier:
            issuer = ISSUER

            async def verify(self, token):
                await asyncio.sleep(0)
                if token not in (USER, SECOND):
                    raise InvalidToken("invalid fixture user")
                return Subject(issuer=ISSUER, subject=token, discord=case.discord, display_name=case.name)

        def app(port):
            self.origin = f"http://127.0.0.1:{port}"
            self.app = create_app(self.host, self.profile, self.config, Verifier(), public_origin=self.origin)
            return self.app

        await self.resources.enter_async_context(serving_http(app, port=0, websockets=True))
        self.resources.callback(self.app.state.play_sessions.close)
        self.http = await self.resources.enter_async_context(httpx.AsyncClient(base_url=self.origin, trust_env=False))

    async def restart(self):
        await self.resources.aclose()
        await self.start()

    async def ticket(self, user=USER):
        return await self.http.post("/v1/play/tickets", json={"protocol": 1},
                                    headers={"Authorization": "Bearer " + user})

    async def launch(self, user=USER):
        ticket = await self.ticket(user)
        self.assertEqual(ticket.status_code, 201, ticket.text)
        response = await self.http.post("/v1/play/sessions", json={"ticket": ticket.json()["ticket"]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_first_play_works_on_game_and_profile_and_survives_restart(self):
        play = await self.launch()
        key = play["account_key"]
        legacy = play["legacy"]
        account = self.host.economy.account_view(key)
        self.assertEqual((account.name, account.balance, len(account.cards)), (b"Same Name", 10000, 10))
        self.assertEqual(self.host.identities.account(ISSUER, USER), key)
        async with local_bridge(play["streams"]["game"], play["session_token"]) as port:
            client = Client()
            await client.open(port, key.encode(), legacy["password"].encode())
            try:
                self.assertIn(b" 422 ", await client.read())
                async with local_bridge(play["streams"]["profile"], play["session_token"]) as profile_port:
                    error, data = await transact(
                        profile_port, key, 3, 0, file_request(), context=(client.cipher, client.iv),
                        password=legacy["password"].encode(), account_id=legacy["pol_id"].encode(),
                    )
                    self.assertEqual(error, 0)
                    self.assertEqual(len(data), 0x302C)
                    self.assertIn(b"Same Name\0", data)
                    self.assertEqual((await transact(profile_port, key, 4, 6,
                        bytes(0x18), password=legacy["password"].encode(),
                        account_id=legacy["pol_id"].encode()))[0], 0)
            finally:
                await client.close()
        await self.restart()
        again = await self.launch()
        self.assertEqual((again["account_key"], again["legacy"]["pol_id"]), (key, legacy["pol_id"]))
        self.assertEqual(self.host.economy.account_view(key), account)
        self.assertEqual(len(self.host.account_keys), 1)

    async def test_same_name_creates_distinct_owners_and_concurrent_retries_create_once(self):
        with patch.object(self.host.store, "save_changes", wraps=self.host.store.save_changes) as save:
            issued = await asyncio.gather(*(self.ticket() for _ in range(12)))
        self.assertTrue(all(response.status_code == 201 for response in issued))
        self.assertEqual(save.call_count, 1)
        first = self.host.identities.account(ISSUER, USER)
        second = (await self.launch(SECOND))["account_key"]
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.host.account_keys), 2)
        self.assertEqual(self.host.economy.account_view(first).name, self.host.economy.account_view(second).name)

    async def test_invalid_or_non_discord_identity_cannot_create_an_account(self):
        before = deepcopy(self.host.snapshot())
        self.assertEqual((await self.ticket("invalid")).status_code, 401)
        self.discord = False
        response = await self.ticket()
        self.assertEqual((response.status_code, response.json()), (403, {"error": "discord_required"}))
        self.assertEqual(self.host.snapshot(), before)
        self.assertFalse(self.path.exists())

    async def test_registration_can_close_without_locking_out_existing_owners(self):
        first = await self.launch()
        self.config = self.config.model_copy(update={"auto_register": False})
        await self.restart()
        self.assertEqual((await self.launch())["account_key"], first["account_key"])
        response = await self.ticket(SECOND)
        self.assertEqual((response.status_code, response.json()), (403, {"error": "account_not_linked"}))

    async def test_compatibility_credentials_cannot_bypass_authenticated_streams(self):
        play = await self.launch()
        key, legacy = play["account_key"], play["legacy"]
        client = Client()
        await client.open(self.game_port, key.encode(), legacy["password"].encode())
        try:
            self.assertEqual(await client.read(), b"")
        finally:
            await client.close()
        error, _ = await transact(self.profile_port, key, 0, 9,
                                   password=legacy["password"].encode(), account_id=legacy["pol_id"].encode())
        self.assertEqual(error, 0xE4)
        self.assertNotIn(key.encode(), self.host.nicks)

    async def test_precommit_failure_returns_no_ticket_and_no_account(self):
        before = deepcopy(self.host.snapshot())
        with patch.object(self.host.store, "save_changes", side_effect=OSError("save failed")):
            response = await self.ticket()
        self.assertEqual((response.status_code, response.json()), (503, {"error": "storage_unavailable"}))
        self.assertEqual(self.host.snapshot(), before)
        self.assertFalse(self.app.state.play_sessions.tickets)
        await self.restart()
        self.assertEqual(len(self.host.account_keys), 0)
        await self.launch()
        self.assertEqual(len(self.host.account_keys), 1)

    async def test_lost_ack_reload_returns_original_account_without_new_starter_assets(self):
        original_save = self.host.store.save_changes

        def lost_ack(accounts, journals):
            original_save(accounts, journals)
            raise OSError("save acknowledgement lost")

        with patch.object(self.host.store, "save_changes", side_effect=lost_ack):
            response = await self.ticket()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.host.account_keys), 0)
        self.assertFalse(self.app.state.play_sessions.tickets)
        await self.restart()
        existing = next(iter(self.host.account_keys))
        with patch.object(self.host.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual((await self.launch())["account_key"], existing)
        self.assertEqual(len(self.host.economy.inventory(existing)), 10)
