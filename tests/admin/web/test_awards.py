"""Authenticated award forms reuse the durable application action."""

from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.support.memory_store import MemoryStore
from tmserver.network.server import Server


class Inputs(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.values = {}
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "name" in attrs:
            self.values[attrs["name"]] = attrs.get("value", "")


class AwardFormTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from tmserver.admin.web import create_app

        self.httpx, self.create_app = httpx, create_app
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = MemoryStore()
        self.event_store = MemoryStore()
        self.host = Server(store=self.store)
        self.addAsyncCleanup(self.host.stop)
        self.key = next(iter(self.host.accounts)).decode()
        self.host.ensure_account(self.key.encode())
        self.origin = "http://127.0.0.1:8765"
        self.client = await self.browser(actions=True)
        self.command = dict(
            request_id="operator-request-1", award_id="operator-award-1", account_key=self.key,
            top30="20", lucky="10", rookies="5", lucky1="1", lucky2="65535", lucky3="65535",
            reason='Fixture <script>alert("reason")</script>',
        )

    async def browser(self, *, actions):
        app = self.create_app(
            self.host, username="operator", password="test-password", origin=self.origin,
            started_at=0, actions_enabled=actions,
        )
        client = self.httpx.AsyncClient(
            base_url=self.origin, transport=self.httpx.ASGITransport(app=app), trust_env=False,
        )
        await client.__aenter__()
        self.addAsyncCleanup(client.aclose)
        return client

    async def post(self, path, values, *, client=None, csrf=True):
        client = client or self.client
        data = dict(values)
        if csrf:
            data["csrftoken"] = client.cookies["starlette_admin_csrftoken"]
        return await client.post(path, data=data, headers={"Origin": self.origin})

    async def login(self, client=None):
        client = client or self.client
        self.assertEqual((await client.get("/admin/login")).status_code, 200)
        result = await self.post(
            "/admin/login", dict(username="operator", password="test-password"), client=client,
        )
        self.assertEqual(result.status_code, 303)

    async def test_submit_receipt_refresh_and_retry_preserve_exactly_one_award(self):
        await self.login()
        form = await self.client.get("/admin/awards")
        self.assertEqual(form.status_code, 200)
        self.assertIn("Queue award", form.text)
        self.assertIn('name="request_id"', form.text)
        response = await self.post("/admin/awards/apply", self.command)
        self.assertEqual(response.status_code, 303, response.text[:500])
        saved = deepcopy(self.host.snapshot())
        self.assertEqual(len(saved["admin_audit"]["receipts"]), 1)
        self.assertEqual(saved["prize"]["accounts"][self.key]["pending"]["top30"], 20)
        receipt = await self.client.get(response.headers["location"])
        self.assertEqual(receipt.status_code, 200)
        self.assertIn("Award queued and audit receipt saved", receipt.text)
        self.assertIn("operator-request-1", receipt.text)
        self.assertIn("Points after redemption", receipt.text)
        self.assertIn("&lt;script&gt;", receipt.text)
        self.assertNotIn('<script>alert("reason")</script>', receipt.text)
        retry = await self.post("/admin/awards/apply", self.command)
        self.assertEqual(retry.status_code, 303)
        self.assertEqual(self.host.snapshot(), saved)
        self.assertEqual(self.host.store.load()["admin_audit"], saved["admin_audit"])

    async def test_reads_and_writes_require_login_and_post_requires_csrf(self):
        before = deepcopy(self.host.snapshot())
        self.assertEqual((await self.client.get("/admin/awards")).status_code, 303)
        self.assertEqual((await self.client.get("/admin/awards/receipt?id=unknown")).status_code, 303)
        self.assertEqual((await self.post("/admin/awards/apply", self.command, csrf=False)).status_code, 403)
        await self.login()
        self.assertEqual((await self.post("/admin/awards/apply", self.command, csrf=False)).status_code, 403)
        self.assertEqual((await self.client.get("/admin/awards/apply")).status_code, 405)
        self.assertEqual((await self.post("/admin/awards/preview", self.command)).status_code, 403)
        self.assertEqual(self.host.snapshot(), before)

    async def test_default_read_only_boundary_never_mounts_or_permits_award_actions(self):
        browser = await self.browser(actions=False)
        await self.login(browser)
        self.assertEqual((await browser.get("/admin/awards")).status_code, 404)
        response = await self.post("/admin/awards/apply", self.command, client=browser)
        self.assertEqual(response.status_code, 403)
        page = await browser.get("/admin/")
        self.assertNotIn("Queue prize award", page.text)

    async def test_changed_facts_under_a_recorded_request_id_conflict(self):
        await self.login()
        self.assertEqual((await self.post("/admin/awards/apply", self.command)).status_code, 303)
        before = deepcopy(self.host.snapshot())
        conflict = await self.post("/admin/awards/apply", self.command | {"top30": "21"})
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("different facts", conflict.text)
        self.assertIn('value="21"', conflict.text)
        self.assertEqual(self.host.snapshot(), before)

    async def test_invalid_input_returns_useful_form_without_a_write(self):
        await self.login()
        for change in ({"top30": "1.0"}, {"lucky1": "250"}, {"reason": "  "},
                       {"account_key": "unknown"}, {"operator": "forged"}):
            with self.subTest(change=change):
                response = await self.post("/admin/awards/apply", self.command | change)
                self.assertEqual(response.status_code, 400)
                self.assertIn("Queue award", response.text)
        self.assertEqual(len(self.host.admin_audit.snapshot()["receipts"]), 0)

    async def test_storage_failure_returns_no_success_and_keeps_the_original_request_id(self):
        await self.login()
        before = deepcopy(self.host.snapshot())
        with patch.object(self.host.store, "save_changes", side_effect=OSError("secret database URL")):
            response = await self.post("/admin/awards/apply", self.command)
        self.assertEqual(response.status_code, 503)
        self.assertIn("operator-request-1", response.text)
        self.assertIn('value="operator-request-1"', response.text)
        self.assertNotIn("secret database URL", response.text)
        self.assertNotIn("Award queued and audit receipt saved", response.text)
        self.assertEqual(self.host.snapshot(), before)
        again = await self.post("/admin/awards/apply", self.command)
        self.assertEqual(again.status_code, 503)
        self.assertIn("Storage did not confirm", again.text)
        self.assertEqual(self.host.snapshot(), before)

    async def test_receipt_lookup_is_unavailable_after_ambiguous_commit_until_reload(self):
        await self.login()
        save_changes = self.host.store.save_changes

        def committed_then_disconnected(*args, **kwargs):
            save_changes(*args, **kwargs)
            raise OSError("simulated lost commit acknowledgement")

        with patch.object(self.host.store, "save_changes", side_effect=committed_then_disconnected):
            applied = await self.post("/admin/awards/apply", self.command)
        self.assertEqual(applied.status_code, 503)
        path = "/admin/awards/receipt?id=operator-request-1"
        uncertain = await self.client.get(path)
        self.assertEqual(uncertain.status_code, 503)
        self.assertIn("may already have been saved", uncertain.text)
        await self.host.stop()
        self.host = Server(store=self.store)
        self.addAsyncCleanup(self.host.stop)
        self.client = await self.browser(actions=True)
        await self.login()
        receipt = await self.client.get(path)
        self.assertEqual(receipt.status_code, 200)
        self.assertIn("Award queued and audit receipt saved", receipt.text)
        retried = await self.post("/admin/awards/apply", self.command)
        self.assertEqual(retried.status_code, 303)
        self.assertEqual(len(self.host.admin_audit.snapshot()["receipts"]), 1)

    async def test_event_receipt_is_not_rendered_as_a_prize_award(self):
        from tests.events.test_event_rooms import config
        from tmserver.admin.actions import apply_event_grant
        from tmserver.admin.audit import EventGrantRequest

        await self.host.stop()
        self.host = Server(
            store=self.event_store,
            event_configs=(config(card_service_enabled=True),), event_clock=lambda: 200,
        )
        self.addAsyncCleanup(self.host.stop)
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))
        self.host.events.admit_room(self.key, "festival")
        self.host.persist()
        apply_event_grant(self.host, EventGrantRequest(
            request_id="event-request", account_key=self.key, event_id="festival",
            grant_id="event-grant", operator="operator", source="cli",
            reason="Event award", money=25,
        ))
        self.client = await self.browser(actions=True)
        await self.login()
        before = deepcopy(self.host.snapshot())
        response = await self.client.get("/admin/awards/receipt?id=event-request")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Award queued and audit receipt saved", response.text)
        self.assertEqual(self.host.snapshot(), before)
