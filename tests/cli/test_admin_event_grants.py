"""Offline audited event grants queue assets without editing owned inventory."""

from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import patch

from tests.cli import test_admin as fixture
from tmserver.cli import admin
from tmserver.storage.json import JsonStateStore


class AuditedEventAdminTests(unittest.TestCase):
    setUp = fixture.AdminTests.setUp
    seed = fixture.AdminTests.seed
    load = fixture.AdminTests.load
    argv = fixture.AdminTests.argv
    execute = fixture.AdminTests.execute
    enable_events = fixture.AdminTests.enable_events

    def grant(self, command, *extra):
        return (
            command, "--account-key", "alice", "--event", "festival", "--id", "event-grant-1",
            "--request-id", "event-request-1", "--operator", "fixture-operator",
            "--reason", "Event participation award", "--money", "250",
            "--card", "0,50,0,40,30,1", *extra,
        )

    def test_preview_queue_reload_and_retry_keep_owned_assets_and_unrelated_journals(self):
        self.enable_events()
        before, original = self.path.read_bytes(), self.load()
        preview = self.execute(*self.grant("event-grant-preview"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(preview["request"]["source"], "cli")
        self.assertEqual(preview["cards"][0]["deck"], 255)
        self.assertEqual(preview["cards"][0]["points"], self.economy.catalog.points(0, 50, 40, 30))
        receipt = self.execute(*self.grant("event-grant-apply", "--digest", preview["digest"]))
        saved, committed = self.load(), self.path.read_bytes()
        self.assertEqual(receipt["action"], "event-grant")
        self.assertEqual(receipt["status"], "queued")
        self.assertEqual(saved["admin_audit"]["version"], 2)
        self.assertEqual(set(saved) - set(original), {"event_cards", "admin_audit"})
        for name, value in original.items():
            self.assertEqual(saved[name], value)
        grant = saved["event_cards"]["grants"][0]
        self.assertEqual((grant["money"], grant["money_claimed"], grant["card_offset"]), (250, 0, 0))
        with patch.object(JsonStateStore, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(
                self.execute(*self.grant("event-grant-apply", "--digest", "0" * 64)), receipt,
            )
            self.assertEqual(
                self.execute(*self.grant("event-grant-preview"))["already_committed"], receipt,
            )
            self.assertEqual(
                self.execute("operator-receipt", "--request-id", "event-request-1"), receipt,
            )
        self.assertEqual(self.path.read_bytes(), committed)

    def test_mixed_audit_keeps_original_prize_receipt_and_global_request_ids(self):
        self.enable_events()
        prize = (
            "--account-key", "bob", "--id", "prize-1", "--request-id", "prize-request-1",
            "--operator", "fixture-operator", "--reason", "Prize award", "--top30", "12",
        )
        preview = self.execute("prize-preview", *prize)
        first = self.execute("prize-apply", *prize, "--digest", preview["digest"])
        self.assertEqual(self.load()["admin_audit"]["version"], 1)
        preview = self.execute(*self.grant("event-grant-preview"))
        second = self.execute(*self.grant("event-grant-apply", "--digest", preview["digest"]))
        self.assertEqual(self.load()["admin_audit"], dict(version=2, receipts=[first, second]))
        self.assertEqual(self.execute("operator-receipt", "--request-id", "prize-request-1"), first)
        changed = list(self.grant("event-grant-preview"))
        changed[changed.index("--request-id") + 1] = "prize-request-1"
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "different facts"):
            self.execute(*changed)
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_grants_are_never_attributed_to_a_new_operator(self):
        self.enable_events()
        self.execute(
            "event-grant", "--account-key", "alice", "--event", "festival", "--id", "event-grant-1",
            "--money", "250", "--card", "0,50,0,40,30,1",
        )
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "without this audited request"):
            self.execute(*self.grant("event-grant-preview"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_inputs_and_stale_confirmation_leave_save_bytes_unchanged(self):
        self.enable_events()
        before = self.path.read_bytes()
        for field, value in (
            ("--money", "-1"), ("--card", "250,50,0,40,30,1"),
            ("--card", "0,256,0,40,30,1"), ("--card", "0,50,4,40,30,1"),
            ("--account-key", "bob"), ("--reason", "  "), ("--event", "missing"),
        ):
            args = list(self.grant("event-grant-preview"))
            args[args.index(field) + 1] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.execute(*args)
            self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "preview is stale"):
            self.execute(*self.grant("event-grant-apply", "--digest", "0" * 64))
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_queue_has_no_receipt_and_same_request_can_be_retried_after_reload(self):
        self.enable_events()
        preview = self.execute(*self.grant("event-grant-preview"))
        before = self.path.read_bytes()
        with patch.object(JsonStateStore, "save_changes", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                self.execute(*self.grant("event-grant-apply", "--digest", preview["digest"]))
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "not found"):
            self.execute("operator-receipt", "--request-id", "event-request-1")
        receipt = self.execute(*self.grant("event-grant-apply", "--digest", preview["digest"]))
        self.assertEqual(receipt["request"]["request_id"], "event-request-1")

    def test_audited_event_commands_require_actor_reason_request_and_confirmation(self):
        for field in ("--operator", "--reason", "--request-id", "--digest"):
            options = list(self.grant("event-grant-apply", "--digest", "0" * 64))
            index = options.index(field)
            del options[index:index + 2]
            with self.subTest(field=field), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    admin.arguments(self.argv(*options))
