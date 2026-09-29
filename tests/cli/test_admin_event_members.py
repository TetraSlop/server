"""Offline corrections use the same typed, audited actions as the live panel."""

import unittest

from tests.cli import test_admin as fixture


class EventMemberCliTests(unittest.TestCase):
    setUp = fixture.AdminTests.setUp
    seed = fixture.AdminTests.seed
    load = fixture.AdminTests.load
    argv = fixture.AdminTests.argv
    execute = fixture.AdminTests.execute
    enable_events = fixture.AdminTests.enable_events

    def test_preview_apply_receipt_and_retry_for_both_commands(self):
        self.enable_events()
        original = self.load()
        common = ("--account-key", "alice", "--event", "festival", "--operator", "fixture",
                  "--reason", "Correct participation")
        for kind, values in (
            ("progress", ("--id", "admin-progress", "--steps", "3")),
            ("eligibility", ("--value", "-1")),
        ):
            args = (*common, "--request-id", kind, *values)
            disk = self.path.read_bytes()
            preview = self.execute(f"event-{kind}-preview", *args)
            self.assertEqual(self.path.read_bytes(), disk)
            receipt = self.execute(f"event-{kind}-apply", *args, "--digest", preview["digest"])
            self.assertEqual(receipt["status"], "applied")
            committed = self.path.read_bytes()
            self.assertEqual(self.execute(f"event-{kind}-apply", *args, "--digest", "old"), receipt)
            self.assertEqual(self.execute("operator-receipt", "--request-id", kind), receipt)
            self.assertEqual(self.path.read_bytes(), committed)
        saved = self.load()
        for name in set(original) - {"events", "admin_audit"}:
            self.assertEqual(saved[name], original[name])

    def test_legacy_progress_is_not_relabelled_as_audited(self):
        self.enable_events()
        args = ("--account-key", "alice", "--event", "festival", "--id", "legacy", "--steps", "2")
        self.execute("event-progress", *args)
        disk = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "without this audited request"):
            self.execute("event-progress-preview", *args, "--request-id", "new",
                         "--operator", "fixture", "--reason", "Existing correction")
        self.assertEqual(self.path.read_bytes(), disk)
