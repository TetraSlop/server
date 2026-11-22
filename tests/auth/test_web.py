"""HTTP authentication boundaries use real service owners and a fake identity provider."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.support.memory_store import MemoryStore


class AuthHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Optional web dependencies must remain absent from portable test collection.
        import httpx
        from tmserver.auth.config import AuthConfig
        from tmserver.auth.provider import InvalidToken, ProviderUnavailable, Subject
        from tmserver.auth.web import create_app
        from tmserver.network.server import Server
        from tmserver.profiles.service import ProfileServer

        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = MemoryStore()
        self.host = Server(store=self.store)
        self.addAsyncCleanup(self.host.stop)
        self.profile = ProfileServer(self.folder / "profile.jsonl", self.host)
        self.addAsyncCleanup(self.profile.stop)
        self.keys = tuple(self.host.account_specs)
        for key in self.keys:
            self.host.ensure_account(key.encode("ascii"))
        self.host.persist()
        self.origin = "https://play.example.test"
        self.issuer = "https://fixture.supabase.co/auth/v1"
        uuids = (
            "372656c5-a384-4a7f-8988-7be72f1e009d",
            "ee038d88-59a1-490c-95c4-c7c31d1128b6",
        )
        self.identities = {
            name: Subject(issuer=self.issuer, subject=uuid)
            for name, uuid in zip(("one", "two"), uuids)
        }
        self.identities["unlinked"] = Subject(
            issuer=self.issuer, subject="c6d56a02-5678-4500-b0cd-72df02eb13dc"
        )
        self.identities["other-issuer"] = Subject(
            issuer="https://other.supabase.co/auth/v1", subject=uuids[0]
        )
        self.config = AuthConfig(
            auto_register=False,
            supabase_url="https://fixture.supabase.co",
            publishable_key="sb_publishable_test_only",
            public_origin=self.origin,
            bindings={
                uuid: self.host.account_specs[key][0].decode("ascii")
                for uuid, key in zip(uuids, self.keys)
            },
        )
        self.verify_calls, self.verify_hook = [], None
        case = self

        class FakeVerifier:
            issuer = case.issuer

            async def verify(self, token):
                case.verify_calls.append(token)
                await asyncio.sleep(0)
                if case.verify_hook is not None:
                    case.verify_hook()
                if token == "provider-down":
                    raise ProviderUnavailable("secret provider detail")
                if token not in case.identities:
                    raise InvalidToken("secret invalid credential")
                return case.identities[token]

        self.app = create_app(self.host, self.profile, self.config, FakeVerifier())
        self.browser = "https://app.example.test"
        browser_config = AuthConfig(**self.config.model_dump() | {"browser_origins": [self.browser]})
        self.browser_app = create_app(self.host, self.profile, browser_config, FakeVerifier())
        self.addCleanup(self.browser_app.state.play_sessions.close)
        self.before, self.disk = deepcopy(self.host.snapshot()), self.store.load()
        for method in ("ensure_account", "persist", "persist_changes", "advance_auctions"):
            self.enterContext(patch.object(self.host, method, side_effect=AssertionError(method)))
        self.sessions = self.app.state.play_sessions
        self.addCleanup(self.sessions.close)
        self.now = 100.0
        self.sessions.clock = lambda: self.now
        self.browser_app.state.play_sessions.clock = lambda: self.now
        self.client, self.browser_client = (
            httpx.AsyncClient(
                base_url=self.origin, transport=httpx.ASGITransport(app=app), trust_env=False
            )
            for app in (self.app, self.browser_app)
        )
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.browser_client.aclose)

    async def asyncTearDown(self):
        self.assertEqual(self.host.snapshot(), self.before)
        self.assertEqual(self.store.load(), self.disk)

    def headers(self, token="one"):
        headers = {"Origin": self.origin}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        return headers

    async def post(self, path, data, token="one"):
        return await self.client.post(path, json=data, headers=self.headers(token))

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json(), {"error": code})
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def ticket(self, token="one"):
        response = await self.post("/v1/play/tickets", {"protocol": 1}, token)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["expires_in"], 60)
        return response.json()["ticket"]

    async def session(self, token="one"):
        ticket = await self.ticket(token)
        response = await self.post("/v1/play/sessions", {"ticket": ticket}, token=None)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_public_configuration_contains_only_bootstrap_facts(self):
        response = await self.client.get("/v1/auth/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.json(),
            {
                "protocol": 1,
                "provider": "discord",
                "supabase_url": "https://fixture.supabase.co",
                "publishable_key": "sb_publishable_test_only",
                "ticket_ttl_seconds": 60,
                "session_ttl_seconds": 3600,
                "stream_subprotocol": "tm-stream-v1",
                "max_chunk_bytes": 65536,
            },
        )
        self.assertEqual(self.verify_calls, [])
        self.assertNotIn("bindings", response.text)
        self.assertNotIn("password", response.text)

    async def test_ticket_redeems_once_into_only_its_bound_account(self):
        ticket = await self.ticket("two")
        response = await self.post("/v1/play/sessions", {"ticket": ticket}, token=None)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["account_key"], self.keys[1])
        self.assertEqual(
            body["legacy"]["pol_id"], self.host.account_specs[self.keys[1]][0].decode()
        )
        self.assertEqual(
            body["streams"],
            {
                "game": "wss://play.example.test/v1/streams/game",
                "profile": "wss://play.example.test/v1/streams/profile",
            },
        )
        self.assertEqual(body["expires_in"], 3600)
        self.assertNotEqual(body["session_token"], ticket)
        replay = await self.post("/v1/play/sessions", {"ticket": ticket}, token=None)
        self.assert_error(replay, 401, "invalid_ticket")
        self.assertEqual(len(self.sessions.sessions), 1)
        self.assertEqual(self.verify_calls, ["two"])

    async def test_missing_unlinked_wrong_issuer_and_invalid_provider_credentials(self):
        for token, status, code in (
            (None, 401, "invalid_token"),
            ("not-verified", 401, "invalid_token"),
            ("unlinked", 403, "account_not_linked"),
            ("other-issuer", 403, "account_not_linked"),
            ("provider-down", 503, "auth_unavailable"),
        ):
            with self.subTest(token=token):
                response = await self.post("/v1/play/tickets", {"protocol": 1}, token)
                self.assert_error(response, status, code)
                self.assertNotIn("secret", response.text)
        self.assertEqual(self.sessions.tickets, {})
        self.assertEqual(self.sessions.sessions, {})

    async def test_only_latest_ticket_survives_and_live_account_has_one_session(self):
        old, latest = await self.ticket(), await self.ticket()
        self.assert_error(
            await self.post("/v1/play/sessions", {"ticket": old}, None), 401, "invalid_ticket"
        )
        first = await self.post("/v1/play/sessions", {"ticket": latest}, None)
        self.assertEqual(first.status_code, 201)
        pending = await self.ticket()
        self.assert_error(
            await self.post("/v1/play/sessions", {"ticket": pending}, None), 409, "session_conflict"
        )
        other = await self.session("two")
        self.assertEqual(other["account_key"], self.keys[1])
        self.assertEqual(len(self.sessions.sessions), 2)

    async def test_renewal_requires_same_verified_owner_and_extends_existing_capability(self):
        token = (await self.session())["session_token"]
        session = self.sessions.get(token)
        original_deadline = session.access.deadline
        self.now += 50
        rejected = await self.post("/v1/play/renew", {"session_token": token}, "two")
        self.assert_error(rejected, 403, "session_owner_mismatch")
        self.assertEqual(session.access.deadline, original_deadline)
        accepted = await self.post("/v1/play/renew", {"session_token": token}, "one")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(session.access.deadline, self.now + 3600)
        self.assertIs(self.sessions.get(token), session)

    async def test_session_logout_revokes_only_presented_session(self):
        first, second = await self.session(), await self.session("two")
        old = self.sessions.get(first["session_token"])
        response = await self.client.delete(
            "/v1/play/session", headers=self.headers(first["session_token"])
        )
        self.assertEqual(response.status_code, 204)
        self.assertTrue(old.access.revoked.is_set())
        self.assertEqual(len(self.sessions.sessions), 1)
        self.assertEqual(
            self.sessions.get(second["session_token"]).access.account_key, self.keys[1]
        )
        repeated = await self.client.delete(
            "/v1/play/session", headers=self.headers(first["session_token"])
        )
        self.assert_error(repeated, 401, "invalid_session")

    async def test_account_logout_revokes_pending_ticket_and_session_without_affecting_other_user(
        self,
    ):
        first, other = await self.session(), await self.session("two")
        old = self.sessions.get(first["session_token"])
        pending = await self.ticket()
        response = await self.client.delete("/v1/play/account-session", headers=self.headers("one"))
        self.assertEqual(response.status_code, 204)
        self.assertTrue(old.access.revoked.is_set())
        self.assert_error(
            await self.post("/v1/play/sessions", {"ticket": pending}, None), 401, "invalid_ticket"
        )
        self.assertEqual(self.sessions.get(other["session_token"]).access.account_key, self.keys[1])

    async def test_ticket_and_session_expire_exactly_at_deadline(self):
        ticket = await self.ticket()
        self.now += 60
        self.assert_error(
            await self.post("/v1/play/sessions", {"ticket": ticket}, None), 401, "invalid_ticket"
        )
        token = (await self.session())["session_token"]
        old = self.sessions.get(token)
        self.now = old.access.deadline
        self.assert_error(
            await self.post("/v1/play/renew", {"session_token": token}), 401, "invalid_session"
        )
        self.assertTrue(old.access.revoked.is_set())
        self.assertEqual(self.sessions.sessions, {})

    async def test_json_shape_protocol_and_unknown_fields_are_strict(self):
        for document in (
            {},
            {"protocol": True},
            {"protocol": 1.0},
            {"protocol": "1"},
            {"protocol": 2},
            {"protocol": 1, "account_key": self.keys[1]},
            [],
        ):
            with self.subTest(document=document):
                self.assert_error(
                    await self.post("/v1/play/tickets", document), 400, "invalid_request"
                )
        for path, data in (
            ("/v1/play/sessions", {"ticket": "x" * 32, "account_key": self.keys[1]}),
            ("/v1/play/renew", {"session_token": "x" * 32, "extra": "ignored?"}),
        ):
            self.assert_error(await self.post(path, data), 400, "invalid_request")
        self.assertEqual(self.verify_calls, [])

    async def test_duplicate_authorization_host_origin_and_query_are_rejected(self):
        for headers, status, code in (
            (
                [("Authorization", "Bearer one"), ("Authorization", "Bearer two")],
                401,
                "invalid_token",
            ),
            ([("Host", "play.example.test"), ("Host", "other.test")], 403, "invalid_origin"),
            ([("Origin", self.origin), ("Origin", self.origin)], 403, "invalid_origin"),
            ([("Origin", "https://other.test")], 403, "invalid_origin"),
            ([("Origin", "null")], 403, "invalid_origin"),
            ([("Host", "other.test")], 403, "invalid_origin"),
        ):
            with self.subTest(headers=headers):
                response = await self.client.post(
                    "/v1/play/tickets", json={"protocol": 1}, headers=headers
                )
                self.assert_error(response, status, code)
        response = await self.post("/v1/play/tickets?access_token=secret", {"protocol": 1})
        self.assert_error(response, 403, "invalid_origin")
        self.assertEqual(self.verify_calls, [])

    async def test_body_type_size_and_bearer_shape_are_bounded(self):
        response = await self.client.post(
            "/v1/play/tickets", content='{"protocol":1}', headers=self.headers()
        )
        self.assert_error(response, 415, "json_required")
        response = await self.client.post(
            "/v1/play/tickets",
            content=b"x" * 16385,
            headers=self.headers() | {"Content-Type": "application/json"},
        )
        self.assert_error(response, 413, "request_too_large")
        response = await self.client.post(
            "/v1/play/tickets",
            content=b"not JSON",
            headers=self.headers() | {"Content-Type": "application/json"},
        )
        self.assert_error(response, 400, "invalid_request")
        for value in ("", "Basic one", "Bearer ", "Bearer one two", "Bearer " + "x" * 8193):
            with self.subTest(length=len(value)):
                response = await self.client.post(
                    "/v1/play/tickets", json={"protocol": 1}, headers={"Authorization": value}
                )
                self.assert_error(response, 401, "invalid_token")
        self.assertEqual(self.verify_calls, [])

    async def test_storage_failure_blocks_all_http_actions_and_config_without_provider_call(self):
        self.host.storage_failed = OSError("secret database URL")
        self.assert_error(await self.client.get("/v1/auth/config"), 503, "storage_unavailable")
        for path, data in (
            ("/v1/play/tickets", {"protocol": 1}),
            ("/v1/play/sessions", {"ticket": "x" * 32}),
            ("/v1/play/renew", {"session_token": "x" * 32}),
        ):
            self.assert_error(await self.post(path, data), 503, "storage_unavailable")
        for path in ("/v1/play/session", "/v1/play/account-session"):
            self.assert_error(
                await self.client.delete(path, headers=self.headers()), 503, "storage_unavailable"
            )
        self.assertEqual(self.verify_calls, [])

    async def test_storage_failure_during_provider_await_creates_no_ticket_or_renewal(self):
        token = (await self.session())["session_token"]
        session = self.sessions.get(token)
        deadline = session.access.deadline
        self.now += 50
        self.verify_hook = lambda: setattr(self.host, "storage_failed", OSError("secret detail"))
        for path, data in (
            ("/v1/play/tickets", {"protocol": 1}),
            ("/v1/play/renew", {"session_token": token}),
        ):
            self.host.storage_failed = None
            self.assert_error(await self.post(path, data), 503, "storage_unavailable")
        self.assertEqual(session.access.deadline, deadline)
        self.assertEqual(self.sessions.tickets, {})

    async def test_storage_failure_during_body_read_does_not_consume_ticket(self):
        ticket = await self.ticket()

        async def body():
            yield b'{"ticket":'
            self.host.storage_failed = OSError("fixture failure while receiving")
            yield json.dumps(ticket).encode() + b"}"

        response = await self.client.post(
            "/v1/play/sessions",
            content=body(),
            headers={"Content-Type": "application/json"},
        )
        self.assert_error(response, 503, "storage_unavailable")
        self.assertEqual(len(self.sessions.tickets), 1)
        self.assertEqual(self.sessions.sessions, {})

    def preflight(self, client, origin, path="/v1/play/tickets", method="POST"):
        return client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    async def test_allowlisted_browser_origin_gets_cors_on_config_ticket_and_session_routes(self):
        page = {"Origin": self.browser}
        for path, method in (
            ("/v1/auth/config", "GET"),
            ("/v1/play/tickets", "POST"),
            ("/v1/play/sessions", "POST"),
            ("/v1/play/renew", "POST"),
            ("/v1/play/session", "DELETE"),
        ):
            with self.subTest(path=path):
                response = await self.preflight(self.browser_client, self.browser, path, method)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["access-control-allow-origin"], self.browser)
                self.assertIn(method, response.headers["access-control-allow-methods"])
                allowed = response.headers["access-control-allow-headers"].lower()
                self.assertIn("authorization", allowed)
                self.assertNotIn("access-control-allow-credentials", response.headers)
        configuration = await self.browser_client.get("/v1/auth/config", headers=page)
        issued = await self.browser_client.post(
            "/v1/play/tickets", json={"protocol": 1}, headers=page | {"Authorization": "Bearer one"}
        )
        self.assertEqual(issued.status_code, 201, issued.text)
        redeemed = await self.browser_client.post(
            "/v1/play/sessions", json={"ticket": issued.json()["ticket"]}, headers=page
        )
        self.assertEqual(redeemed.status_code, 201, redeemed.text)
        replay = await self.browser_client.post(
            "/v1/play/sessions", json={"ticket": issued.json()["ticket"]}, headers=page
        )
        self.assert_error(replay, 401, "invalid_ticket")
        for response in (configuration, issued, redeemed, replay):
            self.assertEqual(response.headers["access-control-allow-origin"], self.browser)
            self.assertIn("Origin", response.headers["vary"])

    async def test_origin_not_on_the_browser_list_gets_no_cors_and_is_refused(self):
        for client, origin in (
            (self.browser_client, "https://other.test"),
            (self.browser_client, "null"),
            (self.browser_client, "https://app.example.test.other.test"),
            (self.client, self.browser),  # An empty list keeps browsers out entirely.
        ):
            with self.subTest(origin=origin, allowlist=client is self.browser_client):
                preflight = await self.preflight(client, origin)
                self.assertEqual(preflight.status_code, 405)
                self.assertNotIn("access-control-allow-origin", preflight.headers)
                response = await client.post(
                    "/v1/play/tickets",
                    json={"protocol": 1},
                    headers={"Origin": origin, "Authorization": "Bearer one"},
                )
                self.assert_error(response, 403, "invalid_origin")
                self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertEqual(self.verify_calls, [])
        self.assertEqual(self.sessions.tickets, {})

    async def test_loader_requests_are_byte_identical_with_a_browser_allowlist(self):
        async def exchange(client, method, path, **options):
            response = await client.request(method, path, **options)
            return response.status_code, response.headers.multi_items(), response.content

        for method, path, options in (
            ("GET", "/v1/auth/config", {}),
            ("GET", "/v1/auth/config", dict(headers={"Origin": self.origin})),
            ("POST", "/v1/play/tickets", dict(json={"protocol": 1})),
            ("POST", "/v1/play/tickets", dict(json={"protocol": 2}, headers=self.headers())),
            ("POST", "/v1/play/tickets", dict(json={"protocol": 1}, headers=self.headers("unlinked"))),
            ("POST", "/v1/play/sessions", dict(json={"ticket": "x" * 32})),
            ("POST", "/v1/play/renew", dict(json={"session_token": "x" * 32}, headers=self.headers())),
            ("DELETE", "/v1/play/session", dict(headers=self.headers("x" * 32))),
            ("OPTIONS", "/v1/play/tickets", {}),
            ("GET", "/v1/streams/game", {}),
        ):
            with self.subTest(method=method, path=path, options=options):
                self.assertEqual(
                    await exchange(self.client, method, path, **options),
                    await exchange(self.browser_client, method, path, **options),
                )
        ticket = {"json": {"protocol": 1}}
        loader = await self.client.post("/v1/play/tickets", **ticket, headers=self.headers("one"))
        browser = await self.browser_client.post(
            "/v1/play/tickets", **ticket, headers=self.headers("two")
        )
        self.assertEqual(loader.status_code, browser.status_code)
        self.assertEqual(loader.headers.multi_items(), browser.headers.multi_items())
        self.assertEqual(loader.json().keys(), browser.json().keys())
