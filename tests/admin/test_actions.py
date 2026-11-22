"""Audited actions commit once, with their receipts, or force a fresh owner."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from tests.support.memory_store import MemoryStore, failing_commits
from tmserver.admin.actions import AdminActionConflict, AdminActionError, apply_prize_award
from tmserver.admin.audit import AdminAudit, PrizeAwardReceipt, PrizeAwardRequest
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

    def test_strict_bounded_inputs_and_json_round_trip(self):
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


class AuditedActionCases:
    """Shared contract for every audited action; subclasses supply the fixture.

    Subclasses define ``apply``, ``request(**changes)``, ``server()``, the
    journals and accounts each commit writes, and ``changes`` that must conflict
    with an already recorded request ID.
    """

    accounts = frozenset()

    def test_apply_saves_change_and_receipt_in_one_commit_before_publishing(self):
        before, value = self.host.snapshot(), self.request()
        original_save = self.host.store.save_changes

        def save_before_publication(accounts, journals):
            self.assertEqual(self.host.snapshot(), before)
            original_save(accounts, journals)

        with patch.object(
            self.host.store, "save_changes", side_effect=save_before_publication
        ) as save:
            receipt = self.apply(self.host, value)
        save.assert_called_once()
        self.assertEqual(set(save.call_args.args[0]), self.accounts)
        self.assertEqual(set(save.call_args.args[1]), self.journals | {"admin_audit"})
        self.assertEqual(receipt.request, value)
        self.assertRegex(receipt.preview_digest, "^[0-9a-f]{64}$")
        after = self.host.snapshot()
        for name in before.keys() - self.journals - {"admin_audit", "accounts"}:
            self.assertEqual(after[name], before[name])
        saved = self.store.load()
        self.assertEqual(saved, json.loads(json.dumps(after)))
        self.assertEqual(saved["admin_audit"]["receipts"][-1], receipt.model_dump(mode="json"))
        with self.assertRaises(ValidationError):
            receipt.status = "changed"
        self.host = self.server()
        self.assertEqual(self.host.admin_audit.get(value.request_id), receipt)

    def test_exact_retry_precedes_busy_check_and_never_writes(self):
        receipt = self.apply(self.host, self.request())
        self.host.economy.reserve_match("one")
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertEqual(self.apply(self.host, self.request()), receipt)

    def test_recorded_request_id_rejects_changed_facts(self):
        self.apply(self.host, self.request())
        before = self.host.snapshot()
        for changes in self.changes + (
            dict(account_key="two"), dict(operator="different"), dict(source="web"),
            dict(reason="Different reason"),
        ):
            with self.subTest(changes=changes), self.assertRaises(AdminActionConflict):
                self.apply(self.host, self.request(**changes))
        self.assertEqual(self.host.snapshot(), before)

    def test_busy_unknown_and_memory_only_hosts_are_rejected_without_changes(self):
        before = self.host.snapshot()
        for key in ("ONE", " one ", "missing"):
            with self.subTest(key=key), self.assertRaises(AdminActionError):
                self.apply(self.host, self.request(account_key=key))
        token = self.host.economy.reserve_match("one")
        with self.assertRaisesRegex(AdminActionError, "busy"):
            self.apply(self.host, self.request())
        self.host.economy.release_match(token)
        self.assertEqual(self.host.snapshot(), before)
        self.host.store = None
        with self.assertRaisesRegex(AdminActionError, "persistent"):
            self.apply(self.host, self.request())

    def test_precommit_failure_publishes_nothing_and_closes_the_owner(self):
        value = self.request()
        before, disk = self.host.snapshot(), self.store.load()
        self.host.listener = Mock()
        peer = Mock()
        self.host.sessions.add(peer)
        with failing_commits("injected replacement failure"):
            with self.assertRaises(OSError):
                self.apply(self.host, value)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.store.load(), disk)
        self.assertIsNotNone(self.host.storage_failed)
        self.host.listener.close.assert_called_once()
        peer.writer.close.assert_called_once()
        with self.assertRaisesRegex(OSError, "restart"):
            self.apply(self.host, value)
        self.host = self.server()
        self.assertEqual(self.apply(self.host, value).request, value)

    def test_lost_commit_ack_needs_reload_then_returns_the_saved_receipt(self):
        value = self.request()
        before = self.host.snapshot()
        original_save = self.host.store.save_changes

        def committed_then_failed(accounts, journals):
            original_save(accounts, journals)
            raise OSError("commit acknowledgement lost")

        with patch.object(self.host.store, "save_changes", side_effect=committed_then_failed):
            with self.assertRaises(OSError):
                self.apply(self.host, value)
        self.assertEqual(self.host.snapshot(), before)
        saved = self.store.load()
        self.assertEqual(len(saved["admin_audit"]["receipts"]), 1)
        with self.assertRaises(OSError):
            self.apply(self.host, value)
        self.host = self.server()
        receipt = self.host.admin_audit.get(value.request_id)
        self.assertEqual(receipt.model_dump(mode="json"), saved["admin_audit"]["receipts"][0])
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertEqual(self.apply(self.host, value), receipt)


class PrizeAwardActionTests(AuditedActionCases, unittest.TestCase):
    apply = staticmethod(apply_prize_award)
    request = staticmethod(request)
    journals = {"prize"}
    accounts = {"one"}
    changes = (
        dict(award_id="week.2"), dict(top30=13), dict(lucky=24), dict(rookies=35),
        dict(lucky_cards=(1, 2, 3)),
    )

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = MemoryStore()
        self.host = self.server()
        for number, key in enumerate(("one", "two"), 1):
            self.host.economy.provision(key, nn=number, player_id=number, name=key.encode())
        self.host.persist()

    def server(self):
        return Server(store=self.store)

    def award(self, **changes):
        return apply_prize_award(self.host, request(**changes))

    def redeem(self, key="one"):
        view = self.host.economy.account_view(key)
        payload = named(
            0x70, 0, 0, f"@CvReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
        )
        with prize_transaction(self.host, key) as prize:
            prize.handle(key, payload)

    def test_award_queues_points_without_crediting_them(self):
        receipt = self.award()
        self.assertEqual((receipt.current_points, receipt.points_on_redemption), (0, 69))
        self.assertEqual(receipt.status, "queued")
        self.assertEqual(self.host.economy.prize_points("one"), 0)
        saved = self.store.load()
        self.assertEqual(saved["prize"]["accounts"]["one"]["pending"]["top30"], 12)

    def test_retry_after_redemption_and_restart_keeps_original_receipt(self):
        receipt = self.award()
        self.redeem()
        self.assertEqual(self.host.economy.prize_points("one"), 69)
        self.host = Server(store=self.store)
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertEqual(apply_prize_award(self.host, request()), receipt)
        self.assertEqual(self.host.economy.prize_points("one"), 69)
        self.assertIsNone(self.host.prize.snapshot()["accounts"]["one"]["pending"])

    def test_legacy_award_id_cannot_invent_an_audit_receipt(self):
        with prize_transaction(self.host, "one") as prize:
            prize.grant_award("one", "week.1", top30=12)
        before = self.store.load()
        with self.assertRaisesRegex(AdminActionConflict, "without this audited request"):
            apply_prize_award(self.host, request())
        self.assertEqual(self.store.load(), before)
        self.assertEqual(self.host.admin_audit.snapshot()["receipts"], [])
        self.redeem()
        with prize_transaction(self.host, "one") as prize:
            prize.disconnect("one")
        with self.assertRaises(AdminActionConflict):
            apply_prize_award(self.host, request())

    def test_award_ids_are_account_scoped_while_request_ids_are_global(self):
        self.award()
        with self.assertRaises(AdminActionConflict):
            self.award(request_id="request.2")
        self.award(account_key="two", request_id="request.2")
        self.assertEqual(len(self.host.admin_audit.snapshot()["receipts"]), 2)

    def test_award_preserves_other_accounts_live_session_and_reservation_identity(self):
        self.redeem("two")
        session = self.host.prize._active["two"]
        reservation = self.host.economy._reservations["two"]
        before = self.host.economy.account_view("two")
        self.award()
        self.assertIs(self.host.prize._active["two"], session)
        self.assertIs(self.host.economy._reservations["two"], reservation)
        self.assertEqual(self.host.economy.account_view("two"), before)

    def test_pending_bundle_prevents_a_second_award_and_receipt_reports_cap(self):
        receipt = self.award(top30=65535, lucky=65535, rookies=65535)
        self.assertEqual(receipt.points_on_redemption, 99999)
        with self.assertRaisesRegex(AdminActionError, "pending award"):
            self.award(request_id="request.2", award_id="week.2")

    def test_missing_or_null_audit_journal_preserves_old_state(self):
        saved = self.store.load()
        for mode in ("missing", "null"):
            with self.subTest(mode=mode):
                old = deepcopy(saved)
                old.pop("admin_audit")
                if mode == "null":
                    old["admin_audit"] = None
                self.store.save(old)
                validate_snapshot(old)
                host = Server(store=self.store)
                self.assertEqual(host.admin_audit.snapshot(), dict(version=1, receipts=[]))
                self.assertEqual(host.economy.snapshot(), self.host.economy.snapshot())

    def test_restore_rejects_extensions_strict_numbers_and_orphan_receipts(self):
        self.award()
        saved = self.store.load()
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
        self.store.save(saved)
        before = self.store.load()
        with self.assertRaises(ValueError):
            Server(store=self.store)
        self.assertEqual(self.store.load(), before)


if __name__ == "__main__":
    unittest.main()
