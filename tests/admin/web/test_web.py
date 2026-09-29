"""Optional framework HTTP acceptance without a listener or native UI."""

import asyncio
import base64
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class AdminWebTests(unittest.TestCase):
    def setUp(self):
        # Optional dependencies must not be imported during base-suite discovery.
        from starlette.testclient import TestClient
        from tmserver.admin import queries
        from tmserver.admin.web import create_app

        self.origin = "http://127.0.0.1:8765"
        self.now = 100.0
        self.host = object()
        self.account = dict(
            id="account-a",
            name='Alice <script>alert("name")</script>',
            player_id=1,
            balance=100,
            card_count=1,
            rating=123,
            connected=True,
            busy=False,
        )
        self.claim = dict(
            id="claim-a",
            account_key="account-a",
            account_name="Alice",
            kind="Prize Center award",
            receipt="award-a",
            money=0,
            cards=0,
            prize_points=25,
        )
        self.room = dict(
            id="room-a",
            lobby="Local lobby",
            name="Practice",
            members=1,
            tables=2,
            occupied_tables=1,
            computer_enabled=True,
            event_id=None,
            rules="Owner chooses",
        )
        self.detail = self.account | dict(
            rating_mode="Preserve original rating",
            account_key="account-a",
            inventory=[
                dict(
                    name="Goblin",
                    model=1,
                    attack=7,
                    kind="Physical",
                    physical=2,
                    magical=3,
                    arrows="Up",
                    deck=1,
                )
            ],
            pending_claims=[self.claim],
        )
        self.overview = dict(
            captured_at="2026-09-28T08:00:00+00:00",
            version="test-build",
            uptime_seconds=120,
            storage="Ready",
            diagnostics="Healthy",
            connected_accounts=1,
            accounts=1,
            rooms=1,
            tables=2,
            occupied_tables=1,
        )
        self.loops = []

        def projection(value):
            def call(host, *args, **kwargs):
                self.assertIs(host, self.host)
                self.loops.append(asyncio.get_running_loop())
                return deepcopy(value)

            return call

        def account_detail(host, key):
            if key != "account-a":
                raise KeyError(key)
            return projection(self.detail)(host)

        callbacks = dict(
            account_rows=projection([self.account]),
            account_detail=account_detail,
            claim_rows=projection([self.claim]),
            room_rows=projection([self.room]),
            server_overview=projection(self.overview),
        )
        self.queries = {}
        for name, callback in callbacks.items():
            mock = self.enterContext(patch.object(queries, name, side_effect=callback, create=True))
            self.queries[name] = mock
        self.app = create_app(
            self.host,
            username="operator",
            password="explicit-test-credential",
            origin=self.origin,
            started_at=0,
            clock=lambda: self.now,
        )
        self.client = self.enterContext(
            TestClient(self.app, base_url=self.origin, follow_redirects=False)
        )

    def login(self, *, password="explicit-test-credential", remember=False):
        response = self.client.get("/admin/login")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["referrer-policy"], "same-origin")
        self.assertNotIn('name="remember_me"', response.text)
        return self.client.post(
            "/admin/login",
            headers={"Origin": self.origin},
            data={
                "username": "operator",
                "password": password,
                "remember_me": "on" if remember else "",
                "csrftoken": self.client.cookies["starlette_admin_csrftoken"],
            },
        )

    def test_authentication_protects_every_read_view(self):
        for path in (
            "/admin/",
            "/admin/accounts/list",
            "/admin/accounts/detail?pk=account-a",
            "/admin/claims/list",
            "/admin/rooms/list",
            "/admin/_api/accounts/relation-lookup",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 303)
                self.assertIn("/admin/login", response.headers["location"])
                self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertTrue(all(mock.call_count == 0 for mock in self.queries.values()))

    def test_credentials_csrf_and_cookie_contents(self):
        self.assertEqual(self.login(password="wrong-password").status_code, 400)
        bad = self.client.post(
            "/admin/login",
            headers={"Origin": self.origin},
            data={
                "username": "operator",
                "password": "explicit-test-credential",
            },
        )
        self.assertEqual(bad.status_code, 403)
        response = self.login()
        self.assertEqual(response.status_code, 303)
        cookie = self.client.cookies["tm_operator_8765"]
        session = json.loads(base64.b64decode(cookie.split(".")[0]))
        self.assertEqual(set(session), {"operator_sid"})
        self.assertGreaterEqual(len(session["operator_sid"]), 32)
        for value in (cookie, response.text, response.headers.get("set-cookie", "")):
            self.assertNotIn("explicit-test-credential", value)
            self.assertNotIn('"operator"', value)
        header = response.headers["set-cookie"].lower()
        self.assertIn("httponly", header)
        self.assertIn("samesite=strict", header)
        self.assertIn("path=/admin", header)
        self.assertIn("max-age=900", header)

    def test_session_expiry_is_fixed_even_after_refresh_and_remember_me(self):
        self.assertEqual(self.login(remember=True).status_code, 303)
        self.now = 999
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.now = 1000
        self.assertEqual(self.client.get("/admin/").status_code, 303)

    def test_logout_requires_csrf_and_revokes_a_copied_cookie(self):
        self.login()
        cookie = self.client.cookies["tm_operator_8765"]
        self.assertEqual(self.client.get("/admin/logout").status_code, 405)
        self.assertEqual(
            self.client.post("/admin/logout", headers={"Origin": self.origin}).status_code, 403
        )
        response = self.client.post(
            "/admin/logout",
            headers={"Origin": self.origin},
            data={
                "csrftoken": self.client.cookies["starlette_admin_csrftoken"],
            },
        )
        self.assertEqual(response.status_code, 303)
        self.client.cookies.clear()
        self.client.cookies.set("tm_operator_8765", cookie, domain="127.0.0.1", path="/admin")
        self.assertEqual(self.client.get("/admin/").status_code, 303)

    def test_exact_host_origin_and_duplicate_hosts_are_rejected(self):
        for host in ("attacker.invalid:8765", "127.0.0.1:8766", "localhost:8765"):
            with self.subTest(host=host):
                self.assertEqual(
                    self.client.get("/admin/login", headers={"Host": host}).status_code, 400
                )
        response = self.client.get(
            "/admin/login", headers=[("Host", "127.0.0.1:8765"), ("Host", "attacker.invalid")]
        )
        self.assertEqual(response.status_code, 400)
        for origin in ("https://attacker.invalid", "http://127.0.0.1:8766", "null"):
            with self.subTest(origin=origin):
                self.assertEqual(
                    self.client.get("/admin/login", headers={"Origin": origin}).status_code, 403
                )
                self.assertEqual(
                    self.client.post("/admin/login", headers={"Origin": origin}).status_code, 403
                )
        self.assertEqual(self.client.post("/admin/login").status_code, 403)

    def test_lists_details_escaping_assets_and_capture_consistency(self):
        self.login()
        response = self.client.get("/admin/accounts/list")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.queries["account_rows"].call_count, 1)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn('<script>alert("name")</script>', response.text)
        self.assertIn("Captured at", response.text)
        self.assertNotIn("https://rsms.me", response.text)
        self.assertNotIn(">Create<", response.text)
        detail = self.client.get("/admin/accounts/detail", params={"pk": "account-a"})
        self.assertEqual(detail.status_code, 200)
        for label in (
            "Goblin",
            "Pending claims",
            "Prize points",
            "award-a",
            "Preserve original rating",
        ):
            self.assertIn(label, detail.text)
        self.assertEqual(self.queries["account_detail"].call_count, 1)
        for path, text in (
            ("/admin/", "test-build"),
            ("/admin/claims/list", "Prize points"),
            ("/admin/rooms/list", "Computer available"),
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(text, response.text)
        asset = self.client.get("/admin/static/css/vendor/tabler.min.css")
        self.assertEqual(asset.status_code, 200)
        self.assertTrue(self.loops)
        self.assertTrue(all(loop is self.loops[0] for loop in self.loops))

    def test_search_sort_and_unknown_accounts_never_fall_back(self):
        self.login()
        self.assertEqual(
            self.client.get("/admin/accounts/list?q=alice&sort=name__desc").status_code, 200
        )
        for key in ("unknown", "account-a/../other", "", "x" * 129, "account-a\0"):
            with self.subTest(key=key):
                response = self.client.get("/admin/accounts/detail", params={"pk": key})
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Goblin", response.text)
        self.assertEqual(
            self.client.get("/admin/accounts/list", params={"q": "x" * 201}).status_code, 400
        )

    def test_direct_game_write_and_export_routes_are_denied(self):
        self.login()
        token = self.client.cookies["starlette_admin_csrftoken"]
        for method, path in (
            ("GET", "/admin/accounts/create"),
            ("GET", "/admin/accounts/edit?pk=account-a"),
            ("POST", "/admin/accounts/create"),
            ("POST", "/admin/accounts/edit?pk=account-a"),
            ("POST", "/admin/_api/accounts/import"),
            ("POST", "/admin/_api/accounts/action?name=delete&pks=account-a"),
            ("POST", "/admin/_api/accounts/action?name=export&pks=account-a"),
            ("POST", "/admin/_api/accounts/row-action?name=delete&pk=account-a"),
            ("POST", "/admin/_api/accounts/inline-edit?pk=account-a&field=balance"),
            ("DELETE", "/admin/accounts/detail?pk=account-a"),
        ):
            with self.subTest(method=method, path=path):
                response = self.client.request(
                    method, path, headers={"Origin": self.origin, "X-CSRFToken": token}
                )
                self.assertEqual(response.status_code, 403)
        self.assertTrue(all(mock.call_count == 0 for mock in self.queries.values()))

    def test_empty_and_failed_projections_have_clear_private_pages(self):
        self.login()
        self.queries["claim_rows"].side_effect = lambda host: []
        empty = self.client.get("/admin/claims/list")
        self.assertEqual(empty.status_code, 200)
        self.assertIn("No", empty.text)
        self.queries["account_rows"].side_effect = RuntimeError("secret database URL")
        failed = self.client.get("/admin/accounts/list")
        self.assertEqual(failed.status_code, 503)
        self.assertIn("temporarily unavailable", failed.text)
        self.assertNotIn("secret database URL", failed.text)
        self.assertEqual(failed.headers["cache-control"], "private, no-store")


class OperatorSessionTests(unittest.TestCase):
    def setUp(self):
        from tmserver.admin.auth import MAX_SESSIONS, OperatorAuth
        from tmserver.admin.web import create_app

        self.max_sessions = MAX_SESSIONS
        self.auth = OperatorAuth("operator", "credential", clock=lambda: 100)
        self.create_app = create_app

    def test_successful_logins_are_bounded_and_old_sessions_are_revoked(self):
        auth = self.auth

        async def exercise():
            first = SimpleNamespace(session={})
            await auth.login("operator", "credential", False, first)
            for _ in range(self.max_sessions):
                await auth.login("operator", "credential", False, SimpleNamespace(session={}))
            self.assertEqual(len(auth._sessions), self.max_sessions)
            self.assertIsNone(await auth.authenticate(first))

        asyncio.run(exercise())

    def test_factory_rejects_missing_credentials_and_nonlocal_origins(self):
        defaults = dict(
            username="operator", password="credential", origin="http://127.0.0.1:8765", started_at=0
        )
        for change in (
            {"username": ""},
            {"password": ""},
            {"origin": "http://0.0.0.0:8765"},
            {"origin": "https://127.0.0.1:8765"},
            {"origin": "http://127.0.0.1:8765/path"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.create_app(object(), **(defaults | change))


class LiveProjectionTests(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        from tmserver.admin.web import create_app
        from tmserver.network.server import Server

        self.host = Server()
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))
        self.before = deepcopy(self.host.snapshot())
        for method in (
            "ensure_account",
            "advance_auctions",
            "advance_rankings",
            "persist",
            "persist_accounts",
            "persist_changes",
        ):
            self.enterContext(patch.object(self.host, method, side_effect=AssertionError(method)))
        self.origin = "http://127.0.0.1:8765"
        app = create_app(
            self.host, username="operator", password="credential", origin=self.origin, started_at=0
        )
        self.client = self.enterContext(
            TestClient(app, base_url=self.origin, follow_redirects=False)
        )

    def test_existing_host_projections_render_without_game_mutation(self):
        self.client.get("/admin/login")
        self.assertEqual(
            self.client.post(
                "/admin/login",
                headers={"Origin": self.origin},
                data={
                    "username": "operator",
                    "password": "credential",
                    "csrftoken": self.client.cookies["starlette_admin_csrftoken"],
                },
            ).status_code,
            303,
        )
        for path in ("/admin/", "/admin/accounts/list", "/admin/claims/list", "/admin/rooms/list"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        detail = self.client.get("/admin/accounts/detail", params={"pk": self.key})
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Owned cards", detail.text)
        self.assertEqual(self.host.snapshot(), self.before)
