"""Browser corrections retain authentication, retry and recovery guards."""

from copy import deepcopy
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

    async def submit(self, command=None):
        return await self.post("/admin/event-members/apply", command or self.command)

    async def test_both_actions_apply_reload_and_link_receipts(self):
        await self.login()
        draft = await self.client.get("/admin/event-members", params={"account": self.key})
        self.assertIn(f'value="{self.key}" selected', draft.text)
        self.assertIn('name="value"', draft.text)
        draft = await self.client.get("/admin/event-members?action=event-progress")
        self.assertIn('name="steps_delta"', draft.text)
        self.assertIn("Apply correction", draft.text)
        commands = (
            self.command,
            {key: self.command[key] for key in ("account_key", "event_id", "reason")}
            | dict(action="event-eligibility", request_id="exclude", value="-1"),
        )
        for command in commands:
            applied = await self.submit(command)
            self.assertEqual(applied.status_code, 303, applied.text)
            receipt = await self.client.get(applied.headers["location"])
            self.assertIn("Correction applied and audit receipt saved", receipt.text)
            self.assertNotIn('<script>alert("reason")</script>', receipt.text)
            self.assertIn("&lt;script&gt;", receipt.text)
            self.assertEqual((await self.submit(command)).status_code, 303)
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
        path = "/admin/event-members/apply"
        self.assertEqual((await self.post(path, self.command, csrf=False)).status_code, 403)
        self.assertEqual((await self.client.post(path, data=self.command,
                                               headers={"Origin": "null"})).status_code, 403)
        self.assertEqual((await self.client.get(path)).status_code, 405)
        readonly = await self.browser(actions=False)
        await self.login(readonly)
        self.assertEqual((await readonly.get("/admin/event-members")).status_code, 404)
        self.assertEqual((await self.post(path, self.command, client=readonly)).status_code, 403)
        self.assertEqual(self.host.snapshot(), before)

    async def test_forged_fields_invalid_numbers_and_changed_retries_do_not_apply(self):
        await self.login()
        before = self.host.snapshot()
        for change in (dict(operator="another"), dict(source="cli"), dict(value="1"),
                       dict(steps_delta="03"), dict(completed_missions="-1")):
            with self.subTest(change=change):
                self.assertEqual((await self.submit(self.command | change)).status_code, 400)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual((await self.submit()).status_code, 303)
        before = self.host.snapshot()
        response = await self.submit(self.command | dict(steps_delta="4"))
        self.assertEqual(response.status_code, 409)
        self.assertIn("different facts", response.text)
        self.assertEqual(self.host.snapshot(), before)

    async def test_failed_save_retains_the_form_and_reloads_the_exact_receipt(self):
        await self.login()
        save = self.host.store.save_changes

        def lost_ack(accounts, journals):
            save(accounts, journals)
            raise OSError("lost acknowledgement")

        with patch.object(self.host.store, "save_changes", side_effect=lost_ack):
            response = await self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertIn('id="event-member-form"', response.text)
        self.assertIn('this.elements.csrftoken.value = Cookies.get("starlette_admin_csrftoken")', response.text)
        retained = Inputs(response.text).values
        self.assertEqual(retained["request_id"], self.command["request_id"])
        self.assertEqual(retained["steps_delta"], "3")
        self.assertEqual((await self.client.get("/admin/event-members/receipt?id=correction")).status_code, 503)
        await self.restart()
        # A retained form is stale after restart even after a fresh login. Model
        # the submit hook changing only CSRF, never the request fields.
        stale = await self.post("/admin/event-members/apply", retained | {
            "reason": self.command["reason"], "account_key": self.key, "event_id": "festival",
        }, csrf=False)
        self.assertEqual(stale.status_code, 403)
        with patch.object(self.host.store, "save_changes", side_effect=AssertionError("retry wrote")):
            response = await self.submit()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.host.events.member(self.key, "festival").steps, 3)

    async def test_receipt_route_does_not_accept_other_action_types(self):
        await self.login()
        self.assertEqual((await self.client.get("/admin/event-members/receipt?id=missing")).status_code, 404)
