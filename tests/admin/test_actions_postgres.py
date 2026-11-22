"""Audited awards survive real PostgreSQL failure boundaries."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture, sa


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class AwardPostgresTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from tmserver.admin.actions import apply_prize_award, preview_prize_award
        from tmserver.network.server import Server

        self.preview = preview_prize_award
        self.apply = apply_prize_award
        self.host = Server(store=self.store)
        self.keys = [self.host.ensure_account(nick) for nick in self.host.accounts]
        self.host.persist()

    def request(self, **changes):
        from tmserver.admin.audit import PrizeAwardRequest

        fields = dict(
            request_id="pg-request.1", account_key=self.keys[0], award_id="pg-award.1",
            operator="fixture-operator", source="cli", reason="Correct a weekly award",
            top30=12, lucky=23, rookies=34, lucky_cards=(11, 22, 33),
        )
        fields.update(changes)
        return PrizeAwardRequest(**fields)

    def restart(self):
        from tmserver.network.server import Server

        self.host = Server(store=self.reopen())
        return self.host

    def redeem(self, key):
        from tmserver.economy.claim_transactions import prize_transaction
        from tmserver.economy.packets import named

        view = self.host.economy.account_view(key)
        with prize_transaction(self.host, key) as prize:
            prize.handle(key, named(
                0x70, 0, 0, f"@CvReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
            ))

    def test_preview_commit_redemption_restart_and_exact_retry(self):
        from tmserver.admin.actions import AdminActionConflict

        request = self.request()
        before, revision = self.store.load(), self.store.revision
        preview = self.preview(self.host, request)
        self.assertEqual((preview.current_points, preview.points_on_redemption), (0, 69))
        self.assertIsNone(preview.already_committed)
        self.assertEqual(self.preview(self.host, request), preview)
        self.assertEqual(self.store.load(), before)
        self.assertEqual(self.store.revision, revision)
        with self.assertRaises(AdminActionConflict):
            self.apply(self.host, request, "0" * 64)
        self.assertEqual(self.store.load(), before)

        receipt = self.apply(self.host, request, preview.digest)
        queued = self.store.load()
        self.assertEqual(self.store.revision, revision + 1)
        self.assertEqual(queued["accounts"], before["accounts"])
        self.assertEqual(queued["admin_audit"]["receipts"], [receipt.model_dump(mode="json")])
        self.assertEqual(receipt.request, request)
        self.assertEqual(receipt.preview_digest, preview.digest)
        self.assertEqual(self.host.economy.prize_points(request.account_key), 0)
        self.assertEqual(queued["prize"]["accounts"][request.account_key]["pending"], dict(
            id=request.award_id, top30=12, lucky=23, rookies=34, lucky_cards=[11, 22, 33],
        ))
        self.restart()
        self.assertEqual(self.host.admin_audit.get(request.request_id), receipt)
        self.redeem(request.account_key)
        self.restart()
        redeemed, revision = self.store.load(), self.store.revision
        self.assertEqual(self.host.economy.prize_points(request.account_key), 69)
        self.assertIsNone(redeemed["prize"]["accounts"][request.account_key]["pending"])
        token = self.host.economy.reserve_match(request.account_key)
        try:
            with patch.object(self.store, "save_changes", side_effect=AssertionError("retry wrote")):
                self.assertEqual(self.apply(self.host, request, "stale confirmation"), receipt)
                replay = self.preview(self.host, request)
                self.assertEqual(replay.already_committed, receipt)
                self.assertEqual((replay.current_points, replay.points_on_redemption), (0, 69))
                with self.assertRaises(AdminActionConflict):
                    self.apply(self.host, self.request(operator="different-operator"), preview.digest)
        finally:
            self.host.economy.release_match(token)
        self.assertEqual(self.store.load(), redeemed)
        self.assertEqual(self.store.revision, revision)

    def test_failure_between_journal_writes_rolls_back_award_and_receipt(self):
        from tmserver.storage.schema import service_state

        request = self.request()
        preview = self.preview(self.host, request)
        saved, live, revision = self.store.load(), deepcopy(self.host.snapshot()), self.store.revision
        save_services = self.store._save_services
        wrote = []

        def fail_between(snapshot, previous, *, names):
            self.assertEqual(set(names), {"prize", "admin_audit"})
            first = sorted(names)[0]
            save_services(snapshot, previous, names=(first,))
            # Establish that an actual journal write happened before the fault.
            inside = self.store.connection.scalar(
                sa.select(service_state.c.payload).where(service_state.c.name == first)
            )
            self.assertEqual(inside, snapshot[first])
            self.assertNotEqual(inside, previous[first])
            wrote.append(first)
            raise OSError("injected failure between award journal writes")

        with patch.object(self.store, "_save_services", fail_between), self.assertRaises(OSError):
            self.apply(self.host, request, preview.digest)
        self.assertEqual(len(wrote), 1)
        self.assertTrue(self.store.failed)
        self.assertIsNotNone(self.host.storage_failed)
        self.assertEqual(self.host.snapshot(), live)
        with self.assertRaises(OSError):
            self.apply(self.host, request, preview.digest)
        self.restart()
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.store.revision, revision)
        self.assertIsNone(self.host.admin_audit.get(request.request_id))
        self.assertEqual(self.preview(self.host, request), preview)

    def test_lost_commit_ack_requires_reload_then_returns_durable_exact_receipt(self):
        import psycopg

        request = self.request()
        preview = self.preview(self.host, request)
        before, revision = deepcopy(self.host.snapshot()), self.store.revision
        commit = psycopg.Connection.commit

        def committed_but_ack_lost(connection):
            commit(connection)
            raise psycopg.OperationalError("injected lost audited-award COMMIT acknowledgement")

        with (
            patch.object(psycopg.Connection, "commit", committed_but_ack_lost),
            self.assertRaises(OSError),
        ):
            self.apply(self.host, request, preview.digest)
        self.assertTrue(self.store.failed)
        self.assertIsNotNone(self.host.storage_failed)
        self.assertEqual(self.host.snapshot(), before)
        self.assertIsNone(self.host.admin_audit.get(request.request_id))
        with self.assertRaises(OSError):
            self.preview(self.host, request)
        with self.assertRaises(OSError):
            self.apply(self.host, request, preview.digest)
        self.restart()
        receipt = self.host.admin_audit.get(request.request_id)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.request, request)
        self.assertEqual(receipt.preview_digest, preview.digest)
        self.assertEqual(self.store.revision, revision + 1)
        saved = self.store.load()
        self.assertEqual(saved["admin_audit"]["receipts"], [receipt.model_dump(mode="json")])
        self.assertEqual(saved["prize"]["accounts"][request.account_key]["pending"]["id"],
                         request.award_id)
        with patch.object(self.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(self.apply(self.host, request, preview.digest), receipt)
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.store.revision, revision + 1)
