"""Offline audited actions and receipt lookup share the live action functions."""

from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import patch

from tests.cli import test_admin as fixture
from tmserver.cli import admin
from tmserver.storage.json import JsonStateStore

COMMON = (
    "--account-key", "alice", "--request-id", "operator-request-1",
    "--operator", "fixture-operator", "--reason", "Operator correction",
)
# Command, its own options and the journals it may change besides admin_audit.
ACTIONS = (
    ("prize-award", ("--id", "audited-award-1", "--top30", "500"), {"prize"}),
    ("event-grant", ("--event", "festival", "--id", "grant-1", "--money", "250",
                     "--card", "0,50,0,40,30,1"), {"event_cards"}),
    ("event-progress", ("--event", "festival", "--id", "progress-1", "--steps", "3"),
     {"events"}),
    ("event-eligibility", ("--event", "festival", "--value", "-1"), {"events"}),
)


class AuditedAdminCommandTests(unittest.TestCase):
    setUp = fixture.AdminTests.setUp
    seed = fixture.AdminTests.seed
    load = fixture.AdminTests.load
    argv = fixture.AdminTests.argv
    execute = fixture.AdminTests.execute
    enable_events = fixture.AdminTests.enable_events

    def test_apply_saves_once_and_retry_or_lookup_returns_the_original_receipt(self):
        self.enable_events()
        for command, options, journals in ACTIONS:
            with self.subTest(command=command):
                self.setUp()
                self.enable_events()
                original = self.load()
                receipt = self.execute(command, *COMMON, *options)
                self.assertEqual(receipt["request"]["source"], "cli")
                self.assertRegex(receipt["preview_digest"], "^[0-9a-f]{64}$")
                saved, committed = self.load(), self.path.read_bytes()
                self.assertEqual(saved["admin_audit"]["receipts"], [receipt])
                for name in set(original) - journals - {"admin_audit"}:
                    self.assertEqual(saved[name], original[name])
                with patch.object(
                    JsonStateStore, "save_changes", side_effect=AssertionError("retry wrote")
                ):
                    self.assertEqual(self.execute(command, *COMMON, *options), receipt)
                    self.assertEqual(
                        self.execute("operator-receipt", "--request-id", "operator-request-1"),
                        receipt,
                    )
                changed = [*COMMON, *options]
                changed[changed.index("--reason") + 1] = "Different reason"
                with self.assertRaisesRegex(ValueError, "different facts"):
                    self.execute(command, *changed)
                self.assertEqual(self.path.read_bytes(), committed)

    def test_failed_write_leaves_no_receipt_and_the_same_request_can_retry(self):
        self.enable_events()
        for number, (command, options, _) in enumerate(ACTIONS):
            arguments = [*COMMON, *options]
            arguments[arguments.index("--request-id") + 1] = f"request-{number}"
            with self.subTest(command=command):
                before = self.path.read_bytes()
                with patch.object(
                    JsonStateStore, "save_changes", side_effect=OSError("fixture failure")
                ):
                    with self.assertRaises(OSError):
                        self.execute(command, *arguments)
                self.assertEqual(self.path.read_bytes(), before)
                with self.assertRaisesRegex(ValueError, "not found"):
                    self.execute("operator-receipt", "--request-id", f"request-{number}")
                receipt = self.execute(command, *arguments)
                self.assertEqual(receipt["request"]["request_id"], f"request-{number}")

    def test_actions_require_an_explicit_actor_reason_and_request_id(self):
        for command, options, _ in ACTIONS:
            for field in ("--operator", "--reason", "--request-id"):
                arguments = [*COMMON, *options]
                index = arguments.index(field)
                del arguments[index:index + 2]
                with self.subTest(command=command, field=field), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as rejected:
                        admin.arguments(self.argv(command, *arguments))
                    self.assertEqual(rejected.exception.code, 2)

    def test_request_ids_are_global_across_actions(self):
        self.enable_events()
        (award, award_options, _), (grant, grant_options, _) = ACTIONS[:2]
        self.execute(award, *COMMON, *award_options)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "different facts"):
            self.execute(grant, *COMMON, *grant_options)
        self.assertEqual(self.path.read_bytes(), before)
