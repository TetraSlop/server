"""Audit history is a detached inspection, independent of remaining claims."""

from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.events.test_event_rooms import config
from tmserver.admin.actions import apply_prize_award, preview_prize_award
from tmserver.admin.audit import AdminAudit, EventGrantRequest, PrizeAwardRequest
from tmserver.admin.event_actions import apply_event_grant, preview_event_grant
from tmserver.admin.queries import receipt_rows
from tmserver.economy.claim_transactions import event_transaction, prize_transaction
from tmserver.economy.packets import named, parse_named
from tmserver.network.server import Server


def seed_receipts(host, key):
    host.events.admit_room(key, "festival")
    prize = PrizeAwardRequest(
        request_id="prize.request", award_id="prize.award", account_key=key,
        operator="prize-operator", source="cli", reason="Prize correction", top30=12,
    )
    event = EventGrantRequest(
        request_id="event.request", grant_id="event.grant", account_key=key,
        event_id="festival", operator="event-operator", source="web", money=25,
        reason='Event <script>alert("reason")</script>',
    )
    return (
        apply_prize_award(host, prize, preview_prize_award(host, prize).digest),
        apply_event_grant(host, event, preview_event_grant(host, event).digest),
    )


class ReceiptQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.options = dict(
            event_configs=(config(card_service_enabled=True),), event_clock=lambda: 200,
        )
        self.host = Server(state_path=self.directory / "state.json", **self.options)
        self.addAsyncCleanup(self.host.stop)
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))

    async def test_v1_and_mixed_history_are_detached_without_writes_or_delivery(self):
        prize, event = seed_receipts(self.host, self.key)
        before = deepcopy(self.host.snapshot())
        disk = (self.directory / "state.json").read_bytes()
        for method in (
            "ensure_account", "advance_auctions", "advance_rankings", "persist",
            "persist_accounts", "persist_changes",
        ):
            self.enterContext(patch.object(self.host, method, side_effect=AssertionError(method)))
        for service in (self.host.prize, self.host.event_cards):
            self.enterContext(patch.object(service, "handle", side_effect=AssertionError("claim")))
        rows = receipt_rows(self.host)
        self.assertEqual(rows, [
            dict(id=receipt.request.request_id, action=action, account_key=self.key,
                 operator=receipt.request.operator, source=receipt.request.source,
                 reason=receipt.request.reason, committed_at=receipt.committed_at)
            for receipt, action in ((prize, "Prize award"), (event, "Event grant"))
        ])
        rows[0]["reason"] = "changed"
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual((self.directory / "state.json").read_bytes(), disk)
        # Version 1 journals have no action discriminator on Prize receipts.
        legacy = AdminAudit().stage_receipt(prize)
        self.assertEqual(legacy.snapshot()["version"], 1)
        self.assertEqual(
            receipt_rows(SimpleNamespace(storage_failed=None, admin_audit=legacy))[0]["action"],
            "Prize award",
        )

    async def test_receipts_remain_after_collection_and_restart(self):
        seed_receipts(self.host, self.key)
        before = receipt_rows(self.host)
        view = self.host.economy.account_view(self.key)
        identity = f"/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
        with prize_transaction(self.host, self.key) as prize:
            replies = prize.handle(self.key, named(0x70, 0, 0, "@CvReq=" + identity))
            number = int(parse_named(replies[1])[3]["ExcInit"]["N"])
            prize.handle(self.key, named(0xB2, 0x17, number, f"@Quit=/No={number}"))
        with event_transaction(self.host, (self.key,)) as event:
            replies = event.cards.handle(
                self.key, named(0x90, 0, 0, "@CardReq=" + identity), event_id="festival"
            )
        number = int(parse_named(replies[1])[3]["EcmInit"]["N"])
        with event_transaction(self.host, (self.key,)) as event:
            event.cards.handle(
                self.key, named(0xB2, 0x17, number, f"@Quit=/No={number}"), event_id="festival"
            )
        self.assertEqual(receipt_rows(self.host), before)
        self.assertEqual(self.host.economy.prize_points(self.key), 12)
        self.assertEqual(self.host.economy.account_view(self.key).balance, view.balance + 25)
        self.assertEqual(
            self.host.event_cards.pending(self.key, "festival"), {"cards": 0, "money": 0}
        )
        await self.host.stop()
        self.host = Server(state_path=self.directory / "state.json", **self.options)
        self.addAsyncCleanup(self.host.stop)
        self.assertEqual(receipt_rows(self.host), before)

    async def test_failed_history_cannot_appear_empty(self):
        self.assertEqual(receipt_rows(self.host), [])
        audit = Mock()
        host = SimpleNamespace(storage_failed=OSError("secret database"), admin_audit=audit)
        with self.assertRaisesRegex(OSError, "Restart the server to reload saved receipts"):
            receipt_rows(host)
        audit.snapshot.assert_not_called()
