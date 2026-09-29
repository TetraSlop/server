"""Audited awards commit once, with their receipts, or force a fresh owner."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from tmserver.admin.actions import (
    AdminActionConflict,
    AdminActionError,
    PrizeAwardRequest,
    apply_prize_award,
    preview_prize_award,
)
from tmserver.admin.audit import AdminAudit, PrizeAwardReceipt
from tmserver.economy.claim_transactions import prize_transaction
from tmserver.economy.packets import named
from tmserver.economy.prize_center import PrizeService
from tmserver.economy.service import EconomyService
from tmserver.network.server import Server
from tmserver.storage.validation import validate_snapshot


def request(**changes):
    fields = dict(
        request_id="request.1",
        account_key="one",
        award_id="week.1",
        operator="operator",
        source="cli",
        reason="Weekly award correction",
        top30=12,
        lucky=23,
        rookies=34,
        lucky_cards=(11, 22, 33),
    )
    fields.update(changes)
    return PrizeAwardRequest(**fields)


class AwardRequestTests(unittest.TestCase):
    def test_text_rejects_postgres_incompatible_nul_but_preserves_multiline_reasons(self):
        for field in ("account_key", "operator", "reason"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                request(**{field: "prefix\0suffix"})
        reason = "First line\nSecond line\twith details"
        self.assertEqual(request(reason=reason).reason, reason)

    def test_strict_bounded_inputs_and_json_confirmation_round_trip(self):
        valid = request()
        self.assertEqual(PrizeAwardRequest.model_validate_json(valid.model_dump_json()), valid)
        for fields in (
            dict(top30=True),
            dict(top30="12"),
            dict(lucky=1.0),
            dict(rookies=-1),
            dict(top30=65536),
            dict(lucky_cards=(True, 1, 2)),
            dict(lucky_cards=(1, 2, 250)),
            dict(lucky_cards=(1, 2)),
            dict(lucky_cards=[1, 2, 3]),
            dict(source="script"),
            dict(operator=" "),
            dict(reason="\n"),
            dict(reason="x" * 1001),
            dict(operator="x" * 129),
            dict(account_key=""),
            dict(account_key="x" * 129),
            dict(request_id="bad id"),
            dict(award_id="x" * 129),
            dict(unknown=1),
        ):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                request(**fields)
        with self.assertRaises(ValidationError):
            valid.reason = "changed"
        self.assertEqual(request(account_key=" exact ").account_key, " exact ")

    def test_redeemed_capped_receipts_cannot_default_missing_saved_facts(self):
        economy = EconomyService(seed=1)
        economy.provision("one", nn=1, name=b"one")
        prize = PrizeService(economy)
        prize.grant_award(
            "one",
            "week.1",
            top30=65535,
            lucky=65535,
            rookies=65535,
            lucky_cards=(11, 22, 33),
        )
        prize.handle(
            "one", named(0x70, 0, 0, "@CvReq=/NN=0000000000000001/HID=0/Dm=1/Vol=1/CN=6f6e65")
        )
        self.assertEqual(economy.prize_points("one"), 99999)
        self.assertIsNone(prize.snapshot()["accounts"]["one"]["pending"])
        receipt = PrizeAwardReceipt(
            request=request(top30=65535, lucky=65535, rookies=65535),
            preview_digest="0" * 64,
            committed_at="2026-09-28T12:00:00+00:00",
            current_points=0,
            points_on_redemption=99999,
        )
        saved = AdminAudit().stage_receipt(receipt).snapshot()
        self.assertEqual(AdminAudit.restore(economy, prize, saved).get("request.1"), receipt)
        for field in receipt.request.model_dump():
            corrupted = deepcopy(saved)
            del corrupted["receipts"][0]["request"][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                AdminAudit.restore(economy, prize, corrupted)
        del saved["receipts"][0]["status"]
        with self.assertRaises(ValueError):
            AdminAudit.restore(economy, prize, saved)


class PrizeAwardActionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.folder / "state.json"
        self.host = Server(state_path=self.path)
        for number, key in enumerate(("one", "two"), 1):
            self.host.economy.provision(key, nn=number, player_id=number, name=key.encode())
        self.host.persist()

    def award(self, **changes):
        value = request(**changes)
        preview = preview_prize_award(self.host, value)
        receipt = apply_prize_award(self.host, value, preview.digest)
        return value, preview, receipt

    def redeem(self, key="one"):
        view = self.host.economy.account_view(key)
        payload = named(
            0x70, 0, 0, f"@CvReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
        )
        with prize_transaction(self.host, key) as prize:
            prize.handle(key, payload)

    def test_preview_is_read_only_and_apply_writes_award_and_receipt_together(self):
        before = self.host.snapshot()
        disk = self.path.read_bytes()
        value = request()
        preview = preview_prize_award(self.host, value)
        self.assertEqual((preview.current_points, preview.points_on_redemption), (0, 69))
        self.assertIsNone(preview.already_committed)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), disk)
        with patch.object(
            self.host.store, "save_changes", wraps=self.host.store.save_changes
        ) as save:
            receipt = apply_prize_award(self.host, value, preview.digest)
        save.assert_called_once()
        self.assertEqual(set(save.call_args.args[1]), {"prize", "admin_audit"})
        self.assertEqual(set(save.call_args.args[0]), {"one"})
        self.assertEqual(receipt.request, value)
        self.assertEqual(receipt.preview_digest, preview.digest)
        self.assertEqual(receipt.status, "queued")
        self.assertEqual(self.host.economy.prize_points("one"), 0)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["admin_audit"]["receipts"], [receipt.model_dump(mode="json")])
        self.assertEqual(saved["prize"]["accounts"]["one"]["pending"]["top30"], 12)
        self.assertEqual(saved["accounts"]["two"], before["accounts"]["two"])
        with self.assertRaises(ValidationError):
            receipt.status = "changed"

    def test_exact_retry_precedes_busy_and_stale_checks_and_never_writes(self):
        value, preview, receipt = self.award()
        self.host.economy.reserve_match("one")
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertIs(apply_prize_award(self.host, value, "old digest"), receipt)
            replay = preview_prize_award(self.host, value)
        self.assertEqual(replay.already_committed, receipt)
        self.assertEqual(replay.digest, preview.digest)

    def test_retry_after_redemption_and_restart_keeps_original_receipt_and_preview(self):
        value, preview, receipt = self.award()
        self.redeem()
        self.assertEqual(self.host.economy.prize_points("one"), 69)
        self.host = Server(state_path=self.path)
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertEqual(apply_prize_award(self.host, value, "ignored"), receipt)
            replay = preview_prize_award(self.host, value)
        self.assertEqual(replay.already_committed, receipt)
        self.assertEqual((replay.current_points, replay.points_on_redemption), (0, 69))
        self.assertEqual(self.host.economy.prize_points("one"), 69)
        self.assertIsNone(self.host.prize.snapshot()["accounts"]["one"]["pending"])

    def test_global_request_id_rejects_changed_facts_including_actor(self):
        self.award()
        before = self.host.snapshot()
        for fields in (
            dict(account_key="two"),
            dict(award_id="week.2"),
            dict(top30=13),
            dict(lucky=24),
            dict(rookies=35),
            dict(lucky_cards=(1, 2, 3)),
            dict(operator="different"),
            dict(source="web"),
            dict(reason="Different reason"),
        ):
            with self.subTest(fields=fields), self.assertRaises(AdminActionConflict):
                apply_prize_award(self.host, request(**fields), "ignored")
        self.assertEqual(self.host.snapshot(), before)

    def test_legacy_award_id_cannot_invent_an_audit_receipt(self):
        with prize_transaction(self.host, "one") as prize:
            prize.grant_award("one", "week.1", top30=12)
        before = self.path.read_bytes()
        for operation in (
            lambda: preview_prize_award(self.host, request()),
            lambda: apply_prize_award(self.host, request(), "ignored"),
        ):
            with self.assertRaisesRegex(AdminActionConflict, "without this audited request"):
                operation()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.host.admin_audit.snapshot()["receipts"], [])
        self.redeem()
        with prize_transaction(self.host, "one") as prize:
            prize.disconnect("one")
        with self.assertRaises(AdminActionConflict):
            preview_prize_award(self.host, request())

    def test_other_request_id_cannot_reuse_an_audited_award_id(self):
        self.award()
        with self.assertRaises(AdminActionConflict):
            preview_prize_award(self.host, request(request_id="request.2"))

    def test_award_ids_are_account_scoped_while_request_ids_are_global(self):
        self.award()
        self.award(account_key="two", request_id="request.2")
        self.assertEqual(len(self.host.admin_audit.snapshot()["receipts"]), 2)

    def test_busy_account_rejected_both_before_preview_and_after_confirmation(self):
        value = request()
        preview = preview_prize_award(self.host, value)
        token = self.host.economy.reserve_match("one")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(AdminActionError, "busy"):
            preview_prize_award(self.host, value)
        with self.assertRaisesRegex(AdminActionError, "busy"):
            apply_prize_award(self.host, value, preview.digest)
        self.assertEqual(self.path.read_bytes(), before)
        self.host.economy.release_match(token)
        self.assertEqual(preview_prize_award(self.host, value).digest, preview.digest)

    def test_unknown_account_is_exact_and_is_not_provisioned(self):
        before = self.host.snapshot()
        for key in ("ONE", " one ", "missing"):
            with self.subTest(key=key), self.assertRaises(AdminActionError):
                preview_prize_award(self.host, request(account_key=key))
        self.assertEqual(self.host.snapshot(), before)

    def test_new_actions_require_persistent_storage(self):
        self.host.store = None
        with self.assertRaisesRegex(AdminActionError, "persistent"):
            preview_prize_award(self.host, request())
        with self.assertRaisesRegex(AdminActionError, "persistent"):
            apply_prize_award(self.host, request(), "ignored")

    def test_stale_confirmation_after_point_change_rejects_without_queuing(self):
        value = request()
        preview = preview_prize_award(self.host, value)
        token = self.host.economy.reserve_prize("one")
        self.host.economy.commit_prize(token, credit=100, keep_reserved=False)
        self.host.persist()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(AdminActionConflict, "stale"):
            apply_prize_award(self.host, value, preview.digest)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.host.admin_audit.snapshot()["receipts"], [])

    def test_preview_ignores_unrelated_account_and_unrelated_target_profile_changes(self):
        value = request()
        preview = preview_prize_award(self.host, value)
        with prize_transaction(self.host, "two") as prize:
            prize.grant_award("two", "other.week", lucky=1)
        self.host.economy.save_table_preferences(
            "one",
            game=dict(bm=100, du=0, st=0, cb=0, ca=0, gs=0, tl=0),
        )
        self.assertEqual(preview_prize_award(self.host, value).digest, preview.digest)
        apply_prize_award(self.host, value, preview.digest)

    def test_award_preserves_other_accounts_live_session_and_reservation_identity(self):
        self.redeem("two")
        session = self.host.prize._active["two"]
        reservation = self.host.economy._reservations["two"]
        before = self.host.economy.account_view("two")
        self.award()
        self.assertIs(self.host.prize._active["two"], session)
        self.assertIs(self.host.economy._reservations["two"], reservation)
        self.assertEqual(self.host.economy.account_view("two"), before)

    def test_zero_point_redemption_still_invalidates_an_older_preview(self):
        value = request()
        preview = preview_prize_award(self.host, value)
        self.award(request_id="other.request", award_id="other.award", top30=0, lucky=0, rookies=0)
        self.redeem()
        with prize_transaction(self.host, "one") as prize:
            prize.disconnect("one")
        self.assertEqual(self.host.economy.prize_points("one"), preview.current_points)
        self.assertFalse(self.host.economy.is_busy("one"))
        with self.assertRaisesRegex(AdminActionConflict, "stale"):
            apply_prize_award(self.host, value, preview.digest)

    def test_request_facts_and_account_identity_are_bound_to_confirmation(self):
        preview = preview_prize_award(self.host, request())
        with self.assertRaises(AdminActionConflict):
            apply_prize_award(self.host, request(top30=13), preview.digest)
        saved = self.host.economy.snapshot()
        saved["accounts"]["one"]["name_hex"] = b"renamed".hex()
        self.host.economy = EconomyService.restore(saved)
        with self.assertRaises(AdminActionConflict):
            apply_prize_award(self.host, request(), preview.digest)

    def test_pending_bundle_prevents_a_second_award_and_preview_reports_cap(self):
        value, preview, receipt = self.award(top30=65535, lucky=65535, rookies=65535)
        self.assertEqual(preview.points_on_redemption, 99999)
        self.assertEqual(receipt.points_on_redemption, 99999)
        with self.assertRaisesRegex(AdminActionError, "pending award"):
            preview_prize_award(self.host, request(request_id="request.2", award_id="week.2"))

    def test_precommit_failure_leaves_disk_and_live_state_unchanged_and_fails_closed(self):
        value = request()
        preview = preview_prize_award(self.host, value)
        before, disk = self.host.snapshot(), self.path.read_bytes()
        self.host.listener = Mock()
        peer = Mock()
        self.host.sessions.add(peer)
        with patch.object(Path, "replace", side_effect=OSError("injected replacement failure")):
            with self.assertRaises(OSError):
                apply_prize_award(self.host, value, preview.digest)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertIsNotNone(self.host.storage_failed)
        self.host.listener.close.assert_called_once()
        peer.writer.close.assert_called_once()
        with self.assertRaisesRegex(OSError, "restart"):
            apply_prize_award(self.host, value, preview.digest)
        self.host = Server(state_path=self.path)
        self.assertEqual(apply_prize_award(self.host, value, preview.digest).request, value)

    def test_lost_commit_ack_keeps_joint_durable_state_then_recovers_original_receipt(self):
        value = request()
        preview = preview_prize_award(self.host, value)
        before = self.host.snapshot()
        original_save = self.host.store.save_changes

        def committed_then_failed(accounts, journals):
            original_save(accounts, journals)
            raise OSError("commit acknowledgement lost")

        with patch.object(self.host.store, "save_changes", side_effect=committed_then_failed):
            with self.assertRaises(OSError):
                apply_prize_award(self.host, value, preview.digest)
        self.assertEqual(self.host.snapshot(), before)
        self.assertIsNotNone(self.host.storage_failed)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["prize"]["accounts"]["one"]["pending"]["top30"], value.top30)
        self.assertEqual(len(saved["admin_audit"]["receipts"]), 1)
        with self.assertRaises(OSError):
            preview_prize_award(self.host, value)
        self.host = Server(state_path=self.path)
        receipt = self.host.admin_audit.get(value.request_id)
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertEqual(apply_prize_award(self.host, value, "ignored"), receipt)
        self.assertEqual(receipt.model_dump(mode="json"), saved["admin_audit"]["receipts"][0])

    def test_missing_or_null_audit_journal_preserves_old_state(self):
        saved = json.loads(self.path.read_text())
        for mode in ("missing", "null"):
            with self.subTest(mode=mode):
                old = deepcopy(saved)
                old.pop("admin_audit")
                if mode == "null":
                    old["admin_audit"] = None
                self.path.write_text(json.dumps(old))
                validate_snapshot(old)
                host = Server(state_path=self.path)
                self.assertEqual(host.admin_audit.snapshot(), dict(version=1, receipts=[]))
                self.assertEqual(host.economy.snapshot(), self.host.economy.snapshot())

    def test_restore_rejects_extensions_strict_numbers_and_orphan_receipts(self):
        self.award()
        saved = json.loads(self.path.read_text())
        bad = []
        row = deepcopy(saved)
        row["admin_audit"]["extra"] = 1
        bad.append(row)
        for journal in ({}, dict(version=True, receipts=[])):
            row = deepcopy(saved)
            row["admin_audit"] = journal
            bad.append(row)
        for field, value in (
            ("extra", 1),
            ("current_points", True),
            ("committed_at", "2026-09-28T12:00:00"),
        ):
            row = deepcopy(saved)
            row["admin_audit"]["receipts"][0][field] = value
            bad.append(row)
        row = deepcopy(saved)
        row["admin_audit"]["receipts"][0]["request"]["top30"] = "12"
        bad.append(row)
        row = deepcopy(saved)
        row["admin_audit"]["receipts"][0]["request"]["top30"] = 13
        row["admin_audit"]["receipts"][0]["points_on_redemption"] = 70
        bad.append(row)
        row = deepcopy(saved)
        row["admin_audit"]["receipts"][0]["request"]["account_key"] = "missing"
        bad.append(row)
        row = deepcopy(saved)
        row["prize"] = dict(version=1, accounts={})
        bad.append(row)
        row = deepcopy(saved)
        row["admin_audit"]["receipts"].append(deepcopy(row["admin_audit"]["receipts"][0]))
        bad.append(row)
        row = deepcopy(bad[-1])
        row["admin_audit"]["receipts"][1]["request"]["request_id"] = "other.request"
        bad.append(row)
        for snapshot in bad:
            with self.subTest(snapshot=snapshot["admin_audit"]), self.assertRaises(ValueError):
                validate_snapshot(snapshot)
        self.assertEqual(
            AdminAudit.restore(self.host.economy, self.host.prize, saved["admin_audit"]).snapshot(),
            saved["admin_audit"],
        )

    def test_invalid_audit_rejects_startup_before_interrupted_match_recovery(self):
        self.award()
        tokens = {
            seat: self.host.economy.reserve_match(key) for seat, key in enumerate(("one", "two"))
        }
        self.host.match_economy.begin("interrupted", tokens, 100)
        saved = self.host.snapshot()
        saved["admin_audit"]["receipts"][0]["future_field"] = "must not be lost"
        self.path.write_text(json.dumps(saved))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            Server(state_path=self.path)
        self.assertEqual(self.path.read_bytes(), before)
