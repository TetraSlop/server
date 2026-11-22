"""Authenticated event forms queue audited grants without collecting them."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from tests.admin.web.test_awards import Inputs
from tests.events.test_event_rooms import config
from tmserver.admin.actions import apply_prize_award
from tmserver.admin.audit import PrizeAwardRequest
from tmserver.economy.claim_transactions import event_transaction
from tmserver.economy.packets import named, parse_named
from tmserver.network.server import Server


class EventGrantFormTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from tmserver.admin.web import create_app

        self.httpx, self.create_app = httpx, create_app
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.options = dict(
            event_configs=(config(card_service_enabled=True),), event_clock=lambda: 200
        )
        self.host = Server(state_path=self.directory / "state.json", **self.options)
        self.addAsyncCleanup(self.host.stop)
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))
        self.host.events.admit_room(self.key, "festival")
        self.host.persist()
        self.origin = "http://127.0.0.1:8765"
        self.client = await self.browser()
        self.command = dict(
            request_id="event-request-1",
            grant_id="event-grant-1",
            account_key=self.key,
            event_id="festival",
            money="250",
            row_count="1",
            intent="apply",
            reason='Fixture <script>alert("reason")</script>',
            card_0_model="1",
            card_0_attack="50",
            card_0_kind="0",
            card_0_physical="40",
            card_0_magical="30",
            card_0_arrows="7",
        )

    async def browser(self, *, actions=True, cookies=None):
        app = self.create_app(
            self.host,
            username="operator",
            password="test-password",
            origin=self.origin,
            started_at=0,
            actions_enabled=actions,
        )
        client = self.httpx.AsyncClient(
            base_url=self.origin,
            transport=self.httpx.ASGITransport(app=app),
            cookies=cookies,
            trust_env=False,
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
            "/admin/login",
            dict(username="operator", password="test-password"),
            client=client,
        )
        self.assertEqual(result.status_code, 303)

    async def restart(self):
        await self.host.stop()
        self.host = Server(state_path=self.directory / "state.json", **self.options)
        self.addAsyncCleanup(self.host.stop)
        self.client = await self.browser()
        await self.login()

    def collect_visit(self):
        view = self.host.economy.account_view(self.key)
        payload = named(
            0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
        )
        with event_transaction(self.host, (self.key,)) as event:
            replies = event.cards.handle(self.key, payload, event_id="festival")
        fields = parse_named(replies[1])[3]["EcmInit"]
        number = int(fields["N"])
        for subtype, token in ((0x15, "Get"), (0x17, "Quit")):
            with event_transaction(self.host, (self.key,)) as event:
                event.cards.handle(
                    self.key,
                    named(0xB2, subtype, number, f"@{token}=/No={number}"),
                    event_id="festival",
                )
        return int(fields["C"]), int(fields["PM"])

    async def submit(self, command=None):
        return await self.post("/admin/event-grants/apply", command or self.command)

    async def test_default_readonly_does_not_mount_or_permit_grants(self):
        browser = await self.browser(actions=False)
        await self.login(browser)
        self.assertEqual((await browser.get("/admin/event-grants")).status_code, 404)
        response = await self.post("/admin/event-grants/apply", self.command, client=browser)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Queue event grant", (await browser.get("/admin/")).text)

    async def test_authentication_csrf_origin_and_methods_guard_the_form(self):
        before = deepcopy(self.host.snapshot())
        for path in ("/admin/event-grants", "/admin/event-grants/receipt?id=unknown"):
            self.assertEqual((await self.client.get(path)).status_code, 303)
        response = await self.post("/admin/event-grants/apply", self.command, csrf=False)
        self.assertEqual(response.status_code, 403)
        await self.login()
        path = "/admin/event-grants/apply"
        self.assertEqual((await self.post(path, self.command, csrf=False)).status_code, 403)
        response = await self.client.post(path, data=self.command, headers={"Origin": "null"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.client.get(path)).status_code, 405)
        self.assertEqual(self.host.snapshot(), before)

    async def test_account_shortcut_selects_only_the_account_and_requires_actions(self):
        await self.login()
        detail = await self.client.get("/admin/accounts/detail", params={"pk": self.key})
        self.assertEqual(detail.status_code, 200)
        self.assertIn(f"/admin/event-grants?account={self.key}", detail.text)
        self.assertIn("Queue an event grant", detail.text)
        draft = await self.client.get("/admin/event-grants", params={"account": self.key})
        self.assertIn(f'value="{self.key}" selected', draft.text)
        self.assertNotIn('value="festival" selected', draft.text)
        readonly = await self.browser(actions=False)
        await self.login(readonly)
        detail = await readonly.get("/admin/accounts/detail", params={"pk": self.key})
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("Queue an event grant", detail.text)

    async def test_named_draft_rows_preserve_id_and_values_without_mutation(self):
        await self.login()
        page = await self.client.get("/admin/event-grants")
        self.assertIn("Card name", page.text)
        self.assertIn("Physical defense", page.text)
        self.assertIn("Festival", page.text)
        self.assertIn(self.host.economy.catalog.cards[1]["name"], page.text)
        before = deepcopy(self.host.snapshot())
        response = await self.submit(self.command | {"intent": "add-card"})
        self.assertEqual(response.status_code, 200)
        values = Inputs(response.text).values
        self.assertEqual(values["request_id"], self.command["request_id"])
        self.assertEqual(values["grant_id"], self.command["grant_id"])
        self.assertEqual(values["row_count"], "2")
        self.assertEqual(values["card_0_attack"], "50")
        self.assertEqual(values["card_1_attack"], "")
        self.assertEqual(self.host.snapshot(), before)
        money_only = self.command | {name: "" for name in self.command if name.startswith("card_")}
        applied = await self.submit(money_only)
        self.assertEqual(applied.status_code, 303)
        receipt = await self.client.get(applied.headers["location"])
        self.assertIn("No cards in this grant", receipt.text)
        self.assertEqual(self.host.admin_audit.get(self.command["request_id"]).cards, ())

    async def test_canonical_cards_receipt_refresh_and_retry(self):
        await self.login()
        before = deepcopy(self.host.snapshot())
        response = await self.submit()
        self.assertEqual(response.status_code, 303, response.text[:600])
        receipt = self.host.admin_audit.get(self.command["request_id"])
        self.assertEqual(receipt.request.operator, "operator")
        self.assertEqual(receipt.request.source, "web")
        self.assertEqual(receipt.cards[0].points, self.host.economy.catalog.points(1, 50, 40, 30))
        self.assertEqual(receipt.cards[0].deck, 255)
        saved = deepcopy(self.host.snapshot())
        self.assertEqual(saved["accounts"], before["accounts"])
        self.assertEqual(saved["events"], before["events"])
        for _ in range(2):
            page = await self.client.get(response.headers["location"])
            self.assertEqual(page.status_code, 200)
            self.assertIn("Event grant queued and audit receipt saved", page.text)
            self.assertIn(self.host.economy.catalog.cards[1]["name"], page.text)
            self.assertIn("Event eligibility was Eligible", page.text)
            self.assertIn("&lt;script&gt;", page.text)
            self.assertNotIn('<script>alert("reason")</script>', page.text)
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertEqual((await self.submit()).status_code, 303)
        self.assertEqual(self.host.snapshot(), saved)
        self.assertEqual(self.host.store.load(), saved)

    async def test_partial_and_full_collection_retries_keep_original_receipt(self):
        await self.login()
        command = self.command | {"row_count": "12"}
        for index in range(12):
            command.update(
                {
                    f"card_{index}_{name}": self.command[f"card_0_{name}"]
                    for name in ("model", "attack", "kind", "physical", "magical", "arrows")
                }
            )
        applied = await self.submit(command)
        self.assertEqual(applied.status_code, 303)
        for visit in ((9, 250), (3, 0)):
            self.assertEqual(self.collect_visit(), visit)
            await self.restart()
            before = deepcopy(self.host.snapshot())
            with patch.object(
                self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
            ):
                self.assertEqual((await self.submit(command)).status_code, 303)
            page = await self.client.get(applied.headers["location"])
            self.assertEqual(page.status_code, 200)
            self.assertIn("including after collection", page.text)
            self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(
            self.host.event_cards.pending(self.key, "festival"), {"cards": 0, "money": 0}
        )

    async def test_receipt_shows_collection_blockers(self):
        await self.login()
        self.host.events.set_eligibility(self.key, "festival", -7)
        applied = await self.submit()
        self.assertEqual(applied.status_code, 303)
        page = await self.client.get(applied.headers["location"])
        self.assertIn("Event eligibility was Excluded", page.text)
        self.assertIn("cannot collect while excluded", page.text)

    async def test_form_fields_and_whole_numbers_are_exact(self):
        await self.login()
        before = deepcopy(self.host.snapshot())
        for changes in (
            {"operator": "forged"},
            {"source": "cli"},
            {"money": "01"},
            {"money": "1.0"},
            {"money": "+1"},
            {"money": "1e2"},
            {"money": "100000000"},
            {"row_count": "31"},
            {"intent": "preview"},
            {"card_0_model": "250"},
            {"card_0_kind": "4"},
            {"card_0_attack": "256"},
            {"card_0_attack": ""},
            {"card_0_attack": " 1"},
            {"card_0_attack": "0", "card_0_physical": "0", "card_0_magical": "0"},
            {"event_id": "missing"},
            {"account_key": "unknown"},
        ):
            with self.subTest(changes=changes):
                response = await self.submit(self.command | changes)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    Inputs(response.text).values["request_id"], self.command["request_id"]
                )
        missing = dict(self.command)
        del missing["card_0_arrows"]
        self.assertEqual((await self.submit(missing)).status_code, 400)
        duplicated = list(self.command.items()) + [
            ("card_0_attack", "60"),
            ("csrftoken", self.client.cookies["starlette_admin_csrftoken"]),
        ]
        response = await self.client.post(
            "/admin/event-grants/apply",
            content=urlencode(duplicated),
            headers={"Origin": self.origin, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.host.snapshot(), before)

    async def test_changed_facts_under_a_recorded_request_id_conflict(self):
        await self.login()
        self.assertEqual((await self.submit()).status_code, 303)
        before = deepcopy(self.host.snapshot())
        conflict = await self.submit(self.command | {"money": "251"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(Inputs(conflict.text).values["card_0_attack"], "50")
        self.assertEqual(self.host.snapshot(), before)

    async def test_receipt_routes_cannot_confuse_prize_and_event_actions(self):
        await self.login()
        prize = PrizeAwardRequest(
            request_id="prize-request",
            award_id="prize-award",
            account_key=self.key,
            operator="operator",
            source="web",
            reason="fixture",
            top30=1,
        )
        apply_prize_award(self.host, prize)
        receipt = await self.client.get("/admin/event-grants/receipt?id=prize-request")
        self.assertEqual(receipt.status_code, 404)
        self.assertEqual((await self.submit()).status_code, 303)
        for path in ("/admin/awards/receipt?id=event-request-1",
                     "/admin/event-grants/receipt?id=unknown"):
            self.assertEqual((await self.client.get(path)).status_code, 404)

    async def test_storage_failure_preserves_the_request_without_claiming_success(self):
        await self.login()
        before = deepcopy(self.host.snapshot())
        with patch.object(
            self.host.store, "save_changes", side_effect=OSError("secret database URL")
        ):
            response = await self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertIn(self.command["request_id"], response.text)
        self.assertNotIn("secret database URL", response.text)
        self.assertNotIn("Event grant queued and audit receipt saved", response.text)
        self.assertEqual(Inputs(response.text).values["request_id"], self.command["request_id"])
        self.assertEqual(Inputs(response.text).values["card_0_attack"], "50")
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual((await self.submit()).status_code, 503)
        receipt = await self.client.get("/admin/event-grants/receipt?id=event-request-1")
        self.assertEqual(receipt.status_code, 503)

    async def test_ambiguous_commit_requires_reload_then_exact_retry_finds_saved_receipt(self):
        await self.assert_restart_retry(fail_after_commit=True)

    async def test_precommit_failure_retries_preserved_form_after_fresh_login(self):
        await self.assert_restart_retry(fail_after_commit=False)

    async def assert_restart_retry(self, *, fail_after_commit):
        login_page = await self.client.get("/admin/login")
        old_login = Inputs(login_page.text).values | dict(
            username="operator", password="test-password"
        )
        self.assertEqual(
            (await self.post("/admin/login", old_login, csrf=False)).status_code, 303
        )
        save = self.host.store.save_changes

        def save_then_fail(*args):
            if fail_after_commit:
                save(*args)
            raise OSError("simulated lost acknowledgement")

        with patch.object(self.host.store, "save_changes", side_effect=save_then_fail):
            failed = await self.submit()
        self.assertEqual(failed.status_code, 503)
        preserved = Inputs(failed.text).values
        self.assertEqual(preserved["request_id"], self.command["request_id"])
        self.assertIn('id="event-grant-form"', failed.text)
        self.assertIn(f'href="{self.origin}/admin/login" target="_blank"', failed.text)
        self.assertIn(
            'this.elements.csrftoken.value = Cookies.get("starlette_admin_csrftoken")', failed.text
        )
        receipt = await self.client.get("/admin/event-grants/receipt?id=event-request-1")
        self.assertEqual(receipt.status_code, 503)
        cookies = self.httpx.Cookies(self.client.cookies)
        await self.host.stop()
        self.host = Server(state_path=self.directory / "state.json", **self.options)
        self.addAsyncCleanup(self.host.stop)
        self.client = await self.browser(cookies=cookies)
        old_token = self.client.cookies["starlette_admin_csrftoken"]
        # A stale login submission cannot bypass CSRF; a fresh login GET repairs its cookie.
        stale_login = await self.post("/admin/login", old_login, csrf=False)
        self.assertEqual(stale_login.status_code, 403)
        self.assertEqual(self.client.cookies["starlette_admin_csrftoken"], old_token)
        login_page = await self.client.get("/admin/login")
        self.assertEqual(login_page.status_code, 200)
        new_login = Inputs(login_page.text).values | dict(
            username="operator", password="test-password"
        )
        self.assertNotEqual(new_login["csrftoken"], old_token)
        self.assertEqual(
            (await self.post("/admin/login", new_login, csrf=False)).status_code, 303
        )
        # The old page remains invalid even after login. No HTTP helper substitutes its token.
        stale = self.command | {"csrftoken": preserved["csrftoken"]}
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            stale_retry = await self.post("/admin/event-grants/apply", stale, csrf=False)
        self.assertEqual(stale_retry.status_code, 403)
        # Model the page's submit handler: only its CSRF field reads the current cookie.
        with patch.object(
            self.host.store, "save_changes", wraps=self.host.store.save_changes
        ) as saves:
            applied = await self.submit()
        self.assertEqual(saves.call_count, 0 if fail_after_commit else 1)
        self.assertEqual(applied.status_code, 303)
        receipt = await self.client.get(applied.headers["location"])
        self.assertEqual(receipt.status_code, 200)
        self.assertIn("Event grant queued and audit receipt saved", receipt.text)
        self.assertEqual(len(self.host.admin_audit.snapshot()["receipts"]), 1)
