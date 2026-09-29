"""Browser corrections retain authentication, confirmation and recovery guards."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tests.admin.web import test_event_grants as fixture
from tests.admin.web.test_awards import Inputs


class EventMemberFormTests(unittest.IsolatedAsyncioTestCase):
    browser = fixture.EventGrantFormTests.browser
    post = fixture.EventGrantFormTests.post
    login = fixture.EventGrantFormTests.login
    restart = fixture.EventGrantFormTests.restart

    async def asyncSetUp(self):
        await fixture.EventGrantFormTests.asyncSetUp(self)
        self.command = dict(
            action="event-progress", request_id="correction", account_key=self.key,
            event_id="festival", update_id="progress", steps_delta="3", completed_missions="0",
            reason='<script>alert("reason")</script>',
        )

    async def confirmation(self, command=None):
        response = await self.post("/admin/event-members/preview", command or self.command)
        self.assertEqual(response.status_code, 200, response.text[:600])
        return response, Inputs(response.text).values

    async def test_both_actions_preview_confirm_reload_and_link_receipts(self):
        await self.login()
        draft = await self.client.get("/admin/event-members", params={"account": self.key})
        self.assertIn(f'value="{self.key}" selected', draft.text)
        self.assertIn('name="value"', draft.text)
        draft = await self.client.get("/admin/event-members?action=event-progress")
        self.assertIn('name="steps_delta"', draft.text)
        commands = (
            self.command,
            {key: self.command[key] for key in ("account_key", "event_id", "reason")}
            | dict(action="event-eligibility", request_id="exclude", value="-1"),
        )
        for command in commands:
            before = self.host.snapshot()
            response, fields = await self.confirmation(command)
            self.assertEqual(self.host.snapshot(), before)
            self.assertNotIn('<script>alert("reason")</script>', response.text)
            self.assertIn("&lt;script&gt;", response.text)
            applied = await self.post("/admin/event-members/apply", fields)
            self.assertEqual(applied.status_code, 303, applied.text)
            receipt = await self.client.get(applied.headers["location"])
            self.assertIn("Correction applied and audit receipt saved", receipt.text)
            self.assertEqual((await self.post("/admin/event-members/apply", fields)).status_code, 303)
        await self.restart()
        self.assertEqual(self.host.events.member(self.key, "festival").steps, 3)
        self.assertEqual(self.host.events.member(self.key, "festival").eligibility, -1)
        history = await self.client.get("/admin/receipts/list")
        self.assertEqual(history.status_code, 200)
        receipt = await self.client.get("/admin/event-members/receipt?id=correction")
        self.assertIn("Recorded membership before and after", receipt.text)

    async def test_default_readonly_and_auth_origin_csrf_method_guards(self):
        self.assertEqual((await self.client.get("/admin/event-members")).status_code, 303)
        await self.login()
        before = deepcopy(self.host.snapshot())
        for path in ("/admin/event-members/preview", "/admin/event-members/apply"):
            self.assertEqual((await self.post(path, self.command, csrf=False)).status_code, 403)
            self.assertEqual((await self.client.post(path, data=self.command,
                                                   headers={"Origin": "null"})).status_code, 403)
            self.assertEqual((await self.client.get(path)).status_code, 405)
        readonly = await self.browser(actions=False)
        await self.login(readonly)
        self.assertEqual((await readonly.get("/admin/event-members")).status_code, 404)
        self.assertEqual((await self.post("/admin/event-members/preview", self.command,
                                         client=readonly)).status_code, 403)
        self.assertEqual(self.host.snapshot(), before)

    async def test_changed_fields_operator_and_stale_confirmation_do_not_apply(self):
        await self.login()
        _, fields = await self.confirmation()
        before = self.host.snapshot()
        for change in (dict(operator="another"), dict(source="cli"), dict(steps_delta="3")):
            altered = json.loads(fields["command"]) | change
            response = await self.post("/admin/event-members/apply", fields | {
                "command": json.dumps(altered, separators=(",", ":")),
            })
            self.assertEqual(response.status_code, 400, response.text[:500])
        for change in (dict(value="1"), dict(steps_delta="03"), dict(completed_missions="-1")):
            response = await self.post("/admin/event-members/preview", self.command | change)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.snapshot(), before)
        self.host.events.update_progress(self.key, "festival", "match", steps_delta=1)
        response = await self.post("/admin/event-members/apply", fields)
        self.assertEqual(response.status_code, 409)
        self.assertIn("preview is stale", response.text)
        self.assertIsNone(self.host.admin_audit.get("correction"))

    async def test_failed_save_retains_retry_and_reloads_exact_receipt(self):
        await self.login()
        _, fields = await self.confirmation()
        save = self.host.store.save_changes

        def lost_ack(accounts, journals):
            save(accounts, journals)
            raise OSError("lost acknowledgement")

        with patch.object(self.host.store, "save_changes", side_effect=lost_ack):
            response = await self.post("/admin/event-members/apply", fields)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Retry this same correction after recovery", response.text)
        self.assertIn('id="event-member-retry"', response.text)
        self.assertIn('this.elements.csrftoken.value = Cookies.get("starlette_admin_csrftoken")', response.text)
        retained = Inputs(response.text).values
        self.assertEqual(retained["command"], fields["command"])
        self.assertEqual(retained["digest"], fields["digest"])
        self.assertEqual((await self.client.get("/admin/event-members/receipt?id=correction")).status_code, 503)
        await self.restart()
        # A retained form is stale after restart even after a fresh login. Model
        # the submit hook changing only CSRF, never the confirmed command/digest.
        stale = await self.post("/admin/event-members/apply", retained, csrf=False)
        self.assertEqual(stale.status_code, 403)
        retry = retained | {"csrftoken": self.client.cookies["starlette_admin_csrftoken"]}
        with patch.object(self.host.store, "save_changes", side_effect=AssertionError("retry wrote")):
            response = await self.post("/admin/event-members/apply", retry, csrf=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.host.events.member(self.key, "festival").steps, 3)

    async def test_receipt_route_does_not_accept_other_action_types(self):
        await self.login()
        self.assertEqual((await self.client.get("/admin/event-members/receipt?id=missing")).status_code, 404)
