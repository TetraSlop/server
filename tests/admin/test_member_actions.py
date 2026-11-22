"""Event corrections commit once, retain history and survive failed saves."""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from tests.admin import test_event_actions as fixture
from tests.admin.test_actions import AuditedActionCases
from tests.support.memory_store import failing_commits
from tmserver.economy.errors import EconomyError
from tmserver.admin.actions import AdminActionConflict, AdminActionError, apply_event_member
from tmserver.admin.audit import EventEligibilityRequest, EventProgressRequest
from tmserver.admin.queries import receipt_rows
from tmserver.economy.packets import named
from tmserver.storage.validation import validate_snapshot


def request(*, kind="progress", **changes):
    fields = dict(request_id="correction.1", account_key="one", event_id="festival",
                  operator="operator", source="cli", reason="Correct recorded participation")
    if kind == "eligibility":
        return EventEligibilityRequest(**(fields | dict(value=-1) | changes))
    return EventProgressRequest(**(fields | dict(update_id="progress.1", steps_delta=3) | changes))


class EventMemberActionTests(AuditedActionCases, unittest.TestCase):
    setUp = fixture.EventGrantActionTests.setUp
    server = fixture.EventGrantActionTests.server
    apply = staticmethod(apply_event_member)
    request = staticmethod(request)
    journals = {"events"}
    accounts = {"one"}
    changes = (dict(steps_delta=4), dict(kind="eligibility"), dict(update_id="other"))

    def correct(self, command):
        return apply_event_member(self.host, command)

    def test_progress_receipt_records_member_before_and_after(self):
        receipt = self.correct(request())
        self.assertEqual((receipt.before.steps, receipt.after.steps), (0, 3))
        self.assertEqual(receipt.status, "applied")
        self.assertEqual(self.host.admin_audit.snapshot()["version"], 3)
        self.assertEqual(receipt_rows(self.host)[0]["action"], "Event progress")

    def test_retries_after_later_changes_and_restart_do_not_reapply(self):
        progress = request()
        first = self.correct(progress)
        exclude = request(kind="eligibility", request_id="exclude")
        second = self.correct(exclude)
        self.correct(request(kind="eligibility", request_id="restore", value=0))
        self.correct(request(request_id="later", update_id="later", steps_delta=4))
        self.host = self.server()
        self.host.economy.reserve_match("one")
        with patch.object(self.host.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(apply_event_member(self.host, progress), first)
            self.assertEqual(apply_event_member(self.host, exclude), second)
        member = self.host.events.member("one", "festival")
        self.assertEqual((member.steps, member.eligibility), (7, 0))

    def test_legacy_and_duplicate_update_ids_cannot_invent_history(self):
        self.host.events.update_progress("one", "festival", "progress.1", steps_delta=3)
        with self.assertRaisesRegex(AdminActionConflict, "without this audited request"):
            self.correct(request())
        self.correct(request(request_id="other", update_id="other"))
        with self.assertRaises(AdminActionConflict):
            self.correct(request(request_id="new", update_id="other"))

    def test_missing_and_excluded_members_reject_without_changes(self):
        for changes in (dict(event_id="missing"), dict(account_key="two", event_id="second")):
            with self.subTest(changes=changes), self.assertRaises(EconomyError):
                self.correct(request(**changes))
        self.host.events.set_eligibility("two", "festival", -1)
        with self.assertRaisesRegex(EconomyError, "excluded"):
            self.correct(request(account_key="two"))

    def test_precommit_failure_keeps_the_live_event_admission(self):
        view = self.host.economy.account_view("one")
        payload = named(
            0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
        )
        self.host.events.handle("one", payload, event_id="festival", route="card")
        admission = self.host.events.admission("one")
        self.assertIsNotNone(admission)
        command = request(kind="eligibility")
        with failing_commits("injected save failure"):
            with self.assertRaises(OSError):
                self.correct(command)
        self.assertIs(self.host.events.admission("one"), admission)
        self.host = self.server()
        self.host.events.handle("one", payload, event_id="festival", route="card")
        self.assertIsNotNone(self.host.events.admission("one"))
        self.assertEqual(self.correct(command).after.eligibility, -1)
        self.assertIsNone(self.host.events.admission("one"))

    def test_v1_and_v2_receipts_survive_v3_upgrade(self):
        from tests.admin.test_actions import request as prize_request
        from tmserver.admin.actions import apply_prize_award

        apply_prize_award(self.host, prize_request())
        fixture.EventGrantActionTests.grant(self)
        previous = self.host.admin_audit.snapshot()["receipts"]
        self.correct(request())
        self.host = self.server()
        self.assertEqual(self.host.admin_audit.snapshot()["receipts"][:2], previous)

    def test_restore_rejects_incomplete_or_contradictory_audit_facts(self):
        self.correct(request())
        saved = self.host.snapshot()
        mutations = (
            lambda row: row["after"].update(steps=4),
            lambda row: row["request"].update(steps_delta=2),
            lambda row: row.update(member_identity=999),
            lambda row: row["request"].pop("completed_missions"),
            lambda row: row.pop("action"),
        )
        for mutation in mutations:
            altered = deepcopy(saved)
            mutation(altered["admin_audit"]["receipts"][0])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_snapshot(altered, event_configs=self.configs)

    def test_strict_inputs_and_domain_bounds(self):
        for changes in (dict(steps_delta=True), dict(steps_delta="2"), dict(steps_delta=-1),
                        dict(steps_delta=0x80000000), dict(completed_missions=1 << 32),
                        dict(update_id="bad id"), dict(reason=" ")):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                request(**changes)
        with self.assertRaises(ValidationError):
            request(kind="eligibility", value=0x80000000)
        with self.assertRaisesRegex(EconomyError, "display domain"):
            self.correct(request(completed_missions=0x80000000))
        self.correct(request(steps_delta=0x7FFFFFFF))
        with self.assertRaisesRegex(EconomyError, "overflow"):
            self.correct(request(request_id="overflow", update_id="overflow"))
