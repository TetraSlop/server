"""Authenticated history inspection shares existing read-only framework controls."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.admin.test_receipt_queries import seed_receipts
from tests.events.test_event_rooms import config
from tmserver.admin import queries
from tmserver.admin.audit import AdminAudit
from tmserver.network.server import Server


class ReceiptHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.host = Server(
            state_path=self.directory / "state.json",
            event_configs=(config(card_service_enabled=True),), event_clock=lambda: 200,
        )
        self.addAsyncCleanup(self.host.stop)
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))
        seed_receipts(self.host, self.key)
        self.before = deepcopy(self.host.snapshot())
        for method in (
            "ensure_account", "advance_auctions", "advance_rankings", "persist",
            "persist_changes",
        ):
            self.enterContext(patch.object(self.host, method, side_effect=AssertionError(method)))
        self.origin = "http://127.0.0.1:8765"
        self.client = await self.browser()

    async def browser(self, *, actions=False):
        import httpx
        from tmserver.admin.web import create_app

        app = create_app(
            self.host, username="operator", password="credential", origin=self.origin,
            started_at=0, actions_enabled=actions,
        )
        client = httpx.AsyncClient(
            base_url=self.origin, transport=httpx.ASGITransport(app=app), trust_env=False,
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def login(self, client=None):
        client = client or self.client
        await client.get("/admin/login")
        response = await client.post(
            "/admin/login", headers={"Origin": self.origin},
            data=dict(username="operator", password="credential",
                      csrftoken=client.cookies["starlette_admin_csrftoken"]),
        )
        self.assertEqual(response.status_code, 303)

    async def test_readonly_history_and_details_authenticate_escape_and_capture_once(self):
        self.assertEqual((await self.client.get("/admin/receipts/list")).status_code, 303)
        await self.login()
        with patch.object(queries, "receipt_rows", wraps=queries.receipt_rows) as project:
            page = await self.client.get("/admin/receipts/list")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(project.call_count, 1)
        for label in ("Receipt history", "Prize award", "Event grant", "Saved at (UTC)"):
            self.assertIn(label, page.text)
        self.assertIn("&lt;script&gt;", page.text)
        self.assertNotIn('<script>alert("reason")</script>', page.text)
        self.assertNotIn("/admin/awards/receipt", page.text)
        self.assertNotIn("/admin/event-grants/receipt", page.text)
        for key in ("prize.request", "event.request"):
            detail = await self.client.get("/admin/receipts/detail", params={"pk": key})
            self.assertEqual(detail.status_code, 200)
            self.assertIn(key, detail.text)
            self.assertIn("This view is read-only", detail.text)
        missing = await self.client.get("/admin/receipts/detail", params={"pk": "missing"})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.host.snapshot(), self.before)

    async def test_enabled_actions_link_each_row_to_its_typed_receipt(self):
        client = await self.browser(actions=True)
        await self.login(client)
        page = await client.get("/admin/receipts/list")
        self.assertEqual(page.status_code, 200)
        for path, key, text in (
            ("awards", "prize.request", "Award queued and audit receipt saved"),
            ("event-grants", "event.request", "Event grant queued and audit receipt saved"),
        ):
            url = f"{self.origin}/admin/{path}/receipt?id={key}"
            self.assertIn(url, page.text)
            detail = await client.get("/admin/receipts/detail", params={"pk": key})
            self.assertIn(url, detail.text)
            receipt = await client.get(url)
            self.assertEqual(receipt.status_code, 200)
            self.assertIn(text, receipt.text)
        self.assertEqual(self.host.snapshot(), self.before)

    async def test_search_and_sort_preserve_exact_request_ids(self):
        await self.login()
        for term, expected, absent in (
            ("Prize award", "prize.request", "event.request"),
            ("event-operator", "event.request", "prize.request"),
            ("cli", "prize.request", "event.request"),
            ("correction", "prize.request", "event.request"),
            ("event.request", "event.request", "prize.request"),
        ):
            with self.subTest(term=term):
                page = await self.client.get("/admin/receipts/list", params={"q": term})
                self.assertEqual(page.status_code, 200)
                self.assertIn(expected, page.text)
                self.assertNotIn(absent, page.text)
        page = await self.client.get("/admin/receipts/list?sort=operator__desc")
        self.assertLess(page.text.index("prize.request"), page.text.index("event.request"))
        page = await self.client.get("/admin/receipts/list", params={"q": self.key})
        self.assertIn("prize.request", page.text)
        self.assertIn("event.request", page.text)

    async def test_storage_failure_returns_unavailable_even_for_empty_or_unknown_history(self):
        await self.login()
        self.host.admin_audit = AdminAudit()
        empty = await self.client.get("/admin/receipts/list")
        self.assertEqual(empty.status_code, 200)
        self.host.storage_failed = OSError("secret database URL")
        for path in (
            "/admin/receipts/list", "/admin/receipts/detail?pk=event.request",
            "/admin/receipts/detail?pk=missing",
        ):
            with self.subTest(path=path):
                page = await self.client.get(path)
                self.assertEqual(page.status_code, 503)
                self.assertIn("Restart the server to reload saved receipts", page.text)
                self.assertNotIn("secret database URL", page.text)
                self.assertEqual(page.headers["cache-control"], "private, no-store")

    async def test_history_never_enables_crud_or_export(self):
        await self.login()
        for method, path in (
            ("GET", "/admin/receipts/create"),
            ("GET", "/admin/receipts/edit?pk=prize.request"),
            ("POST", "/admin/_api/receipts/action?name=delete&pks=prize.request"),
            ("POST", "/admin/_api/receipts/action?name=export&pks=prize.request"),
            ("DELETE", "/admin/receipts/detail?pk=prize.request"),
        ):
            with self.subTest(path=path):
                response = await self.client.request(
                    method, path, headers={"Origin": self.origin,
                                           "X-CSRFToken": self.client.cookies[
                                               "starlette_admin_csrftoken"]},
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.host.snapshot(), self.before)
