"""Event corrections commit once, retain history and survive failed saves."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from tests.admin import test_event_actions as fixture
from tmserver.admin.actions import AdminActionConflict, AdminActionError
from tmserver.admin.audit import EventEligibilityRequest, EventProgressRequest
from tmserver.admin.member_actions import apply_event_member, preview_event_member
from tmserver.admin.queries import receipt_rows
from tmserver.storage.validation import validate_snapshot


def request(*, kind="progress", **changes):
    fields = dict(request_id="correction.1", account_key="one", event_id="festival",
                  operator="operator", source="cli", reason="Correct recorded participation")
    if kind == "eligibility":
        return EventEligibilityRequest(**(fields | dict(value=-1) | changes))
    return EventProgressRequest(**(fields | dict(update_id="progress.1", steps_delta=3) | changes))


class EventMemberActionTests(unittest.TestCase):
    setUp = fixture.EventGrantActionTests.setUp
    server = fixture.EventGrantActionTests.server

    def apply(self, command):
        return apply_event_member(self.host, command, preview_event_member(self.host, command).digest)

    def test_preview_and_commit_touch_only_member_and_audit_journals(self):
        before, disk = self.host.snapshot(), self.path.read_bytes()
        command = request()
        preview = preview_event_member(self.host, command)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual((preview.before.steps, preview.after.steps), (0, 3))
        save = self.host.store.save_changes

        def staged_save(accounts, journals):
            self.assertEqual(accounts, {})
            self.assertEqual(set(journals), {"events", "admin_audit"})
            self.assertEqual(self.host.snapshot(), before)
            save(accounts, journals)

        with patch.object(self.host.store, "save_changes", side_effect=staged_save):
            receipt = apply_event_member(self.host, command, preview.digest)
        saved = self.host.snapshot()
        for name in set(before) - {"events", "admin_audit"}:
            self.assertEqual(saved[name], before[name])
        self.assertEqual(receipt.status, "applied")
        self.assertEqual(saved["admin_audit"]["version"], 3)
        self.assertEqual(receipt_rows(self.host)[0]["action"], "Event progress")
        self.host = self.server()
        self.assertEqual(self.host.admin_audit.get(command.request_id), receipt)

    def test_retries_after_later_changes_and_restart_do_not_reapply(self):
        progress = request()
        first = self.apply(progress)
        exclude = request(kind="eligibility", request_id="exclude")
        second = self.apply(exclude)
        self.apply(request(kind="eligibility", request_id="restore", value=0))
        self.apply(request(request_id="later", update_id="later", steps_delta=4))
        self.host = self.server()
        self.host.economy.reserve_match("one")
        with patch.object(self.host.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(apply_event_member(self.host, progress, "old digest"), first)
            self.assertEqual(apply_event_member(self.host, exclude, "old digest"), second)
            self.assertEqual(preview_event_member(self.host, progress).already_committed, first)
        member = self.host.events.member("one", "festival")
        self.assertEqual((member.steps, member.eligibility), (7, 0))

    def test_stale_member_and_configuration_previews_do_not_write(self):
        command = request()
        preview = preview_event_member(self.host, command)
        self.apply(request(kind="eligibility", request_id="change", value=2))
        disk = self.path.read_bytes()
        with self.assertRaisesRegex(AdminActionConflict, "stale"):
            apply_event_member(self.host, command, preview.digest)
        self.assertEqual(self.path.read_bytes(), disk)
        preview = preview_event_member(self.host, command)
        config = self.host.events.config("festival")
        self.host.events.configs["festival"] = replace(config, end=config.end + 1)
        with self.assertRaisesRegex(AdminActionConflict, "stale"):
            apply_event_member(self.host, command, preview.digest)
        self.assertEqual(self.path.read_bytes(), disk)

    def test_legacy_and_duplicate_update_ids_cannot_invent_history(self):
        self.host.events.update_progress("one", "festival", "progress.1", steps_delta=3)
        with self.assertRaisesRegex(AdminActionConflict, "without this audited request"):
            preview_event_member(self.host, request())
        self.apply(request(request_id="other", update_id="other"))
        with self.assertRaises(AdminActionConflict):
            preview_event_member(self.host, request(request_id="new", update_id="other"))

    def test_request_ids_are_global_and_bind_operator_and_facts(self):
        self.apply(request())
        for changes in (dict(operator="different"), dict(steps_delta=4), dict(reason="changed"),
                        dict(account_key="two"), dict(source="web"), dict(kind="eligibility")):
            with self.subTest(changes=changes), self.assertRaises(AdminActionConflict):
                preview_event_member(self.host, request(**changes))

    def test_busy_missing_and_excluded_members_reject_without_changes(self):
        self.host.economy.reserve_match("one")
        with self.assertRaisesRegex(AdminActionError, "busy"):
            preview_event_member(self.host, request(kind="eligibility"))
        for changes in (dict(account_key="missing"), dict(event_id="missing"),
                        dict(account_key="two", event_id="second")):
            with self.subTest(changes=changes), self.assertRaises(AdminActionError):
                preview_event_member(self.host, request(**changes))
        self.host.events.set_eligibility("two", "festival", -1)
        with self.assertRaisesRegex(AdminActionError, "excluded"):
            preview_event_member(self.host, request(account_key="two"))

    def test_precommit_failure_retains_live_and_saved_state(self):
        from tmserver.economy.packets import named

        view = self.host.economy.account_view("one")
        self.host.events.handle(
            "one", named(0x90, 0, 0,
                         f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"),
            event_id="festival", route="card",
        )
        admission = self.host.events.admission("one")
        self.assertIsNotNone(admission)
        command = request(kind="eligibility")
        preview = preview_event_member(self.host, command)
        self.assertIs(self.host.events.admission("one"), admission)
        before, disk = self.host.snapshot(), self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("injected save failure")):
            with self.assertRaises(OSError):
                apply_event_member(self.host, command, preview.digest)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertIs(self.host.events.admission("one"), admission)
        with self.assertRaises(OSError):
            preview_event_member(self.host, command)
        self.host = self.server()
        self.host.events.handle(
            "one", named(0x90, 0, 0,
                         f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"),
            event_id="festival", route="card",
        )
        self.assertIsNotNone(self.host.events.admission("one"))
        self.assertEqual(self.apply(command).after.eligibility, -1)
        self.assertIsNone(self.host.events.admission("one"))

    def test_lost_acknowledgement_reloads_receipt_before_retry(self):
        command = request()
        preview = preview_event_member(self.host, command)
        before = self.host.snapshot()
        save = self.host.store.save_changes

        def lost_ack(accounts, journals):
            save(accounts, journals)
            raise OSError("lost acknowledgement")

        with patch.object(self.host.store, "save_changes", side_effect=lost_ack):
            with self.assertRaises(OSError):
                apply_event_member(self.host, command, preview.digest)
        self.assertEqual(self.host.snapshot(), before)
        with self.assertRaises(OSError):
            apply_event_member(self.host, command, preview.digest)
        self.host = self.server()
        receipt = self.host.admin_audit.get(command.request_id)
        with patch.object(self.host.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(apply_event_member(self.host, command, preview.digest), receipt)
        self.assertEqual(self.host.events.member("one", "festival").steps, 3)

    def test_v1_and_v2_receipts_survive_v3_upgrade(self):
        from tests.admin.test_actions import request as prize_request
        from tmserver.admin.actions import apply_prize_award, preview_prize_award

        prize = prize_request()
        apply_prize_award(self.host, prize, preview_prize_award(self.host, prize).digest)
        fixture.EventGrantActionTests.grant(self)
        previous = self.host.admin_audit.snapshot()["receipts"]
        self.apply(request())
        self.host = self.server()
        self.assertEqual(self.host.admin_audit.snapshot()["receipts"][:2], previous)

    def test_restore_rejects_incomplete_or_contradictory_audit_facts(self):
        self.apply(request())
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
        with self.assertRaisesRegex(AdminActionError, "display domain"):
            preview_event_member(self.host, request(completed_missions=0x80000000))
        self.apply(request(steps_delta=0x7FFFFFFF))
        with self.assertRaisesRegex(AdminActionError, "overflow"):
            preview_event_member(self.host, request(request_id="overflow", update_id="overflow"))
