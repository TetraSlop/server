"""Offline preview/confirmation and receipt lookup share the live action."""

from contextlib import redirect_stderr
import io
from unittest.mock import patch
import unittest

from tests.cli import test_admin as fixture
from tmserver.cli import admin
from tmserver.storage.json import JsonStateStore


class AuditedAdminTests(unittest.TestCase):
    setUp = fixture.AdminTests.setUp
    seed = fixture.AdminTests.seed
    load = fixture.AdminTests.load
    argv = fixture.AdminTests.argv
    execute = fixture.AdminTests.execute

    def award(self, command, *extra):
        return (
            command, "--account-key", "alice", "--id", "audited-award-1",
            "--request-id", "operator-request-1", "--operator", "fixture-operator",
            "--reason", "Tournament award", "--top30", "500", *extra,
        )

    def test_preview_changes_no_bytes_and_confirmed_retry_returns_original_receipt(self):
        before = self.path.read_bytes()
        preview = self.execute(*self.award("prize-preview"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(preview["points_on_redemption"], 500)
        self.assertEqual(preview["request"]["source"], "cli")
        receipt = self.execute(*self.award("prize-apply", "--digest", preview["digest"]))
        self.assertEqual(receipt["status"], "queued")
        self.assertEqual(receipt["request"], preview["request"])
        saved = self.path.read_bytes()
        with patch.object(JsonStateStore, "save_changes", side_effect=AssertionError("duplicate write")):
            self.assertEqual(
                self.execute(*self.award("prize-apply", "--digest", "0" * 64)), receipt,
            )
            repeated = self.execute(*self.award("prize-preview"))
            self.assertEqual(repeated["already_committed"], receipt)
            self.assertEqual(
                self.execute("operator-receipt", "--request-id", "operator-request-1"), receipt,
            )
        self.assertEqual(self.path.read_bytes(), saved)

    def test_stale_confirmation_and_changed_retry_never_rewrite_the_save(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "preview is stale"):
            self.execute(*self.award("prize-apply", "--digest", "0" * 64))
        self.assertEqual(self.path.read_bytes(), before)
        preview = self.execute(*self.award("prize-preview"))
        self.execute(*self.award("prize-apply", "--digest", preview["digest"]))
        before = self.path.read_bytes()
        changed = list(self.award("prize-apply", "--digest", preview["digest"]))
        changed[changed.index("--reason") + 1] = "Different reason"
        with self.assertRaisesRegex(ValueError, "different facts"):
            self.execute(*changed)
        self.assertEqual(self.path.read_bytes(), before)

    def test_existing_legacy_award_is_never_attributed_to_a_new_operator(self):
        self.execute("prize-award", "--account-key", "alice", "--id", "audited-award-1", "--top30", "500")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "without this audited request"):
            self.execute(*self.award("prize-preview"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_write_has_no_durable_receipt_and_requires_same_request_retry(self):
        preview = self.execute(*self.award("prize-preview"))
        before = self.path.read_bytes()
        with patch.object(JsonStateStore, "save_changes", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                self.execute(*self.award("prize-apply", "--digest", preview["digest"]))
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "not found"):
            self.execute("operator-receipt", "--request-id", "operator-request-1")
        receipt = self.execute(*self.award("prize-apply", "--digest", preview["digest"]))
        self.assertEqual(receipt["request"]["request_id"], "operator-request-1")

    def test_audited_commands_require_explicit_actor_reason_request_and_confirmation(self):
        for field in ("--operator", "--reason", "--request-id", "--digest"):
            options = list(self.award("prize-apply", "--digest", "0" * 64))
            index = options.index(field)
            del options[index:index + 2]
            with self.subTest(field=field), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as rejected:
                    admin.arguments(self.argv(*options))
                self.assertEqual(rejected.exception.code, 2)
