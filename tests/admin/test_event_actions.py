"""Audited event grants queue exactly once and retain facts after collection."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from tests.admin.test_actions import request as prize_request
from tests.events.test_event_rooms import config, presence
from tests.network import test_services_integration as service_tests
from tests.network.test_server import Client
from tmserver.admin.actions import (
    AdminActionConflict,
    AdminActionError,
    apply_prize_award,
    preview_prize_award,
)
from tmserver.admin.audit import AdminAudit, GrantedEventCard
from tmserver.admin.event_actions import (
    EventGrantCard,
    EventGrantRequest,
    apply_event_grant,
    preview_event_grant,
)
from tmserver.economy.claim_transactions import event_transaction
from tmserver.economy.packets import named, parse_named
from tmserver.network.server import Server
from tmserver.storage.validation import validate_snapshot


CARD = EventGrantCard(model=0, attack=10, kind=0, physical=20, magical=30, arrows=1)


def request(**changes):
    fields = dict(
        request_id="event.request.1",
        account_key="one",
        event_id="festival",
        grant_id="grant.1",
        operator="operator",
        source="cli",
        reason="Explicit event award correction",
        money=25,
        cards=(CARD,),
    )
    return EventGrantRequest(**(fields | changes))


class EventGrantRequestTests(unittest.TestCase):
    def test_strict_bounded_inputs_and_confirmation_json_roundtrip(self):
        value = request()
        self.assertEqual(EventGrantRequest.model_validate_json(value.model_dump_json()), value)
        for changes in (
            dict(money=True),
            dict(money="25"),
            dict(money=25.0),
            dict(money=-1),
            dict(money=100000000),
            dict(cards=[CARD]),
            dict(cards=(CARD,) * 31),
            dict(event_id="bad id"),
            dict(grant_id="x" * 129),
            dict(request_id="bad id"),
            dict(operator=" "),
            dict(reason="\n"),
            dict(reason="x" * 1001),
            dict(operator="x" * 129),
            dict(source="script"),
            dict(account_key=""),
            dict(account_key="x" * 129),
            dict(extra=1),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                request(**changes)
        for field in ("account_key", "operator", "reason"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                request(**{field: "text\0text"})
        for field, value in (
            ("model", 250),
            ("model", True),
            ("attack", "10"),
            ("kind", 4),
            ("physical", 256),
            ("magical", -1),
            ("arrows", 1.0),
            ("deck", 255),
            ("points", 1),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                EventGrantCard(**(CARD.model_dump() | {field: value}))
        self.assertEqual(request(reason="First\nSecond\tline").reason, "First\nSecond\tline")
        self.assertEqual(request(account_key=" exact ").account_key, " exact ")
        with self.assertRaises(ValidationError):
            CARD.attack = 20
        with self.assertRaises(ValidationError):
            GrantedEventCard(**CARD.model_dump(), points=1, deck=255.0)


class EventGrantActionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.folder / "state.json"
        self.now = 200
        self.configs = (
            config(card_service_enabled=True),
            config("second", 0x1100, name=b"Second"),
        )
        self.host = self.server()
        for number, key in enumerate(("one", "two"), 1):
            self.host.economy.provision(key, nn=number, player_id=number, name=key.encode())
            self.host.events.admit_room(key, "festival")
        self.host.events.admit_room("one", "second")
        self.host.persist()

    def server(self):
        return Server(
            state_path=self.path, event_configs=self.configs, event_clock=lambda: self.now
        )

    def grant(self, **changes):
        value = request(**changes)
        preview = preview_event_grant(self.host, value)
        receipt = apply_event_grant(self.host, value, preview.digest)
        return value, preview, receipt

    def enter_collection(self, key="one"):
        view = self.host.economy.account_view(key)
        with event_transaction(self.host, (key,)) as state:
            replies = state.cards.handle(
                key,
                named(
                    0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
                ),
                event_id="festival",
            )
        fields = parse_named(replies[1])[3]["EcmInit"]
        return int(fields["N"]), int(fields["C"]), int(fields["PM"])

    def finish_collection(self, number, key="one"):
        for subtype, token in ((0x15, "Get"), (0x17, "Quit")):
            with event_transaction(self.host, (key,)) as state:
                state.cards.handle(
                    key, named(0xB2, subtype, number, f"@{token}=/No={number}"), event_id="festival"
                )

    def test_preview_is_read_only_and_apply_only_commits_grant_and_audit(self):
        before, disk = self.host.snapshot(), self.path.read_bytes()
        value = request()
        preview = preview_event_grant(self.host, value)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(preview.cards[0].points, self.host.economy.catalog.points(0, 10, 20, 30))
        self.assertEqual(preview.cards[0].deck, 255)
        self.assertEqual((preview.eligibility, preview.collection_enabled), (0, True))
        self.assertIsNone(preview.already_committed)
        original_save = self.host.store.save_changes

        def save_before_publication(accounts, journals):
            self.assertEqual(self.host.snapshot(), before)
            original_save(accounts, journals)

        with patch.object(
            self.host.store, "save_changes", side_effect=save_before_publication
        ) as save:
            receipt = apply_event_grant(self.host, value, preview.digest)
        save.assert_called_once()
        self.assertEqual(save.call_args.args[0], {})
        self.assertEqual(set(save.call_args.args[1]), {"event_cards", "admin_audit"})
        self.assertEqual(receipt.cards, preview.cards)
        self.assertEqual((receipt.action, receipt.status), ("event-grant", "queued"))
        after = self.host.snapshot()
        for name in before.keys() - {"event_cards", "admin_audit"}:
            self.assertEqual(after[name], before[name])
        self.assertEqual(
            self.host.event_cards.pending("one", "festival"), {"cards": 1, "money": 25}
        )
        self.assertEqual(
            after["admin_audit"], dict(version=2, receipts=[receipt.model_dump(mode="json")])
        )
        self.assertEqual(json.loads(self.path.read_text()), after)

    def test_exact_retry_precedes_busy_and_stale_checks_without_writes(self):
        value, preview, receipt = self.grant()
        token = self.host.economy.reserve_match("one")
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertIs(apply_event_grant(self.host, value, "stale"), receipt)
            replay = preview_event_grant(self.host, value)
        self.assertEqual(replay.already_committed, receipt)
        self.assertEqual(replay.digest, preview.digest)
        self.assertIs(self.host.economy._reservations["one"], token)

    def test_partial_and_full_collection_restart_keep_original_receipt(self):
        before = self.host.economy.account_view("one")
        value, preview, receipt = self.grant(cards=(CARD,) * 12)
        number, cards, money = self.enter_collection()
        self.assertEqual((cards, money), (9, 25))
        self.assertEqual(self.host.economy.inventory("one"), before.cards)
        self.assertEqual(self.host.economy.balance("one"), before.balance + 25)
        self.finish_collection(number)
        self.assertEqual(len(self.host.economy.inventory("one")), len(before.cards) + 9)
        self.host = self.server()
        self.assertEqual(apply_event_grant(self.host, value, "ignored"), receipt)
        self.assertEqual(preview_event_grant(self.host, value).digest, preview.digest)
        number, cards, money = self.enter_collection()
        self.assertEqual((cards, money), (3, 0))
        self.finish_collection(number)
        self.host = self.server()
        self.assertEqual(apply_event_grant(self.host, value, "ignored"), receipt)
        self.assertEqual(len(self.host.economy.inventory("one")), len(before.cards) + 12)
        self.assertEqual(self.host.economy.balance("one"), before.balance + 25)
        self.assertEqual(self.host.event_cards.pending("one", "festival"), {"cards": 0, "money": 0})

    def test_cross_action_request_ids_and_changed_facts_conflict(self):
        self.grant()
        for changes in (
            dict(account_key="two"),
            dict(event_id="second"),
            dict(grant_id="different"),
            dict(operator="different"),
            dict(source="web"),
            dict(reason="Different reason"),
            dict(money=26),
            dict(cards=()),
        ):
            with self.subTest(changes=changes), self.assertRaises(AdminActionConflict):
                apply_event_grant(self.host, request(**changes), "ignored")
        with self.assertRaises(AdminActionConflict):
            preview_prize_award(self.host, prize_request(request_id="event.request.1"))
        prize = prize_request()
        apply_prize_award(self.host, prize, preview_prize_award(self.host, prize).digest)
        with self.assertRaises(AdminActionConflict):
            preview_event_grant(self.host, request(request_id=prize.request_id, grant_id="new"))

    def test_legacy_grant_ids_never_gain_invented_audit_receipts(self):
        self.host.event_cards.grant("one", "festival", "grant.1", money=25)
        self.host.persist()
        for money in (25, 26):
            with self.subTest(money=money), self.assertRaises(AdminActionConflict):
                preview_event_grant(self.host, request(cards=(), money=money))
        number, _, _ = self.enter_collection()
        self.finish_collection(number)
        self.host = self.server()
        with self.assertRaises(AdminActionConflict):
            preview_event_grant(self.host, request(cards=()))
        self.assertEqual(self.host.admin_audit.snapshot(), dict(version=1, receipts=[]))

    def test_new_grants_require_existing_member_idle_account_and_persistence(self):
        for changes in (
            dict(account_key="missing"),
            dict(event_id="missing"),
            dict(account_key="two", event_id="second"),
        ):
            with self.subTest(changes=changes), self.assertRaises(AdminActionError):
                preview_event_grant(self.host, request(**changes))
        preview = preview_event_grant(self.host, request())
        self.host.economy.reserve_match("one")
        with self.assertRaisesRegex(AdminActionError, "busy"):
            apply_event_grant(self.host, request(), preview.digest)
        with self.assertRaisesRegex(AdminActionError, "persistent"):
            preview_event_grant(Server(), request())

    def test_empty_card_stats_rejected_by_existing_catalog_without_changes(self):
        before = self.host.snapshot()
        with self.assertRaisesRegex(AdminActionError, "empty card stats"):
            preview_event_grant(
                self.host,
                request(
                    cards=(
                        EventGrantCard(model=0, attack=0, kind=0, physical=0, magical=0, arrows=0),
                    )
                ),
            )
        self.assertEqual(self.host.snapshot(), before)

    def test_excluded_ended_disabled_collection_still_queues_explicit_grant(self):
        self.host.events.set_eligibility("one", "second", -1)
        self.now = 600
        self.host.persist()
        _, preview, receipt = self.grant(event_id="second")
        self.assertEqual((preview.eligibility, preview.collection_enabled), (-1, False))
        self.assertEqual((receipt.eligibility, receipt.collection_enabled), (-1, False))
        self.assertEqual(self.host.event_cards.pending("one", "second"), {"cards": 1, "money": 25})

    def test_preview_ignores_unrelated_grants_progress_preferences_and_balance(self):
        value = request()
        preview = preview_event_grant(self.host, value)
        self.host.event_cards.grant("two", "festival", "other.account", money=1)
        self.host.event_cards.grant("one", "second", "other.event", money=2)
        self.host.events.update_progress("one", "festival", "progress", steps_delta=1)
        self.host.economy.save_table_preferences(
            "one", game=dict(bm=100, du=0, st=0, cb=0, ca=0, gs=0, tl=0)
        )
        token = self.host.economy.reserve_auction("one")
        self.host.economy.commit_auction(token, credit=3)
        self.host.persist()
        self.assertEqual(preview_event_grant(self.host, value).digest, preview.digest)
        apply_event_grant(self.host, value, preview.digest)

    def test_same_event_history_and_collection_invalidate_preview(self):
        self.grant(request_id="first.request", grant_id="first.grant")
        value = request()
        preview = preview_event_grant(self.host, value)
        number, _, _ = self.enter_collection()
        self.finish_collection(number)
        with self.assertRaisesRegex(AdminActionConflict, "stale"):
            apply_event_grant(self.host, value, preview.digest)
        preview = preview_event_grant(self.host, value)
        self.grant(request_id="empty.request", grant_id="empty.grant", money=0, cards=())
        with self.assertRaisesRegex(AdminActionConflict, "stale"):
            apply_event_grant(self.host, value, preview.digest)

    def test_request_and_member_eligibility_are_bound_to_confirmation(self):
        preview = preview_event_grant(self.host, request())
        with self.assertRaises(AdminActionConflict):
            apply_event_grant(self.host, request(money=26), preview.digest)
        self.host.events.set_eligibility("one", "festival", -1)
        with self.assertRaises(AdminActionConflict):
            apply_event_grant(self.host, request(), preview.digest)

    def test_account_identity_is_bound_to_confirmation(self):
        preview = preview_event_grant(self.host, request())
        saved = json.loads(self.path.read_text())
        saved["accounts"]["one"]["name_hex"] = b"renamed".hex()
        self.path.write_text(json.dumps(saved))
        self.host = self.server()
        with self.assertRaisesRegex(AdminActionConflict, "stale"):
            apply_event_grant(self.host, request(), preview.digest)

    def test_other_accounts_active_collection_and_reservation_keep_identity(self):
        self.enter_collection("two")
        session = self.host.event_cards._active["two"]
        token = self.host.economy._reservations["two"]
        self.grant()
        self.assertIs(self.host.event_cards._active["two"], session)
        self.assertIs(self.host.economy._reservations["two"], token)

    def test_v1_prize_receipts_remain_unchanged_in_mixed_v2_history(self):
        value = prize_request()
        old = apply_prize_award(self.host, value, preview_prize_award(self.host, value).digest)
        v1 = self.host.admin_audit.snapshot()
        self.assertEqual(v1["version"], 1)
        self.assertEqual(AdminAudit.restore(self.host.economy, self.host.prize, v1).snapshot(), v1)
        self.grant()
        self.host = self.server()
        saved = self.host.admin_audit.snapshot()
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["receipts"][0], old.model_dump(mode="json"))
        self.assertEqual(apply_prize_award(self.host, value, "ignored"), old)

    def test_precommit_failure_publishes_neither_journal_and_closes_owner(self):
        value = request()
        preview = preview_event_grant(self.host, value)
        before, disk = self.host.snapshot(), self.path.read_bytes()
        self.host.listener = Mock()
        peer = Mock()
        self.host.sessions.add(peer)
        with patch.object(Path, "replace", side_effect=OSError("injected replacement failure")):
            with self.assertRaises(OSError):
                apply_event_grant(self.host, value, preview.digest)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.host.listener.close.assert_called_once()
        peer.writer.close.assert_called_once()
        with self.assertRaisesRegex(OSError, "restart"):
            apply_event_grant(self.host, value, preview.digest)
        self.host = self.server()
        self.assertEqual(apply_event_grant(self.host, value, preview.digest).request, value)

    def test_lost_acknowledgement_requires_reload_then_exact_retry(self):
        value = request()
        preview = preview_event_grant(self.host, value)
        before = self.host.snapshot()
        save = self.host.store.save_changes

        def committed_then_failed(accounts, journals):
            save(accounts, journals)
            raise OSError("commit acknowledgement lost")

        with patch.object(self.host.store, "save_changes", side_effect=committed_then_failed):
            with self.assertRaises(OSError):
                apply_event_grant(self.host, value, preview.digest)
        self.assertEqual(self.host.snapshot(), before)
        saved = json.loads(self.path.read_text())
        self.assertEqual(len(saved["event_cards"]["grants"]), 1)
        self.assertEqual(len(saved["admin_audit"]["receipts"]), 1)
        with self.assertRaises(OSError):
            preview_event_grant(self.host, value)
        self.host = self.server()
        receipt = self.host.admin_audit.get(value.request_id)
        with patch.object(
            self.host.store, "save_changes", side_effect=AssertionError("retry wrote")
        ):
            self.assertEqual(apply_event_grant(self.host, value, "ignored"), receipt)

    def test_restore_rejects_missing_normalized_fields_even_after_collection(self):
        self.grant()
        number, _, _ = self.enter_collection()
        self.finish_collection(number)
        saved = self.host.snapshot()
        original = saved["admin_audit"]["receipts"][0]
        for location in ((), ("request",), ("cards", 0), ("request", "cards", 0)):
            target = original
            for key in location:
                target = target[key]
            for field in target:
                corrupted = deepcopy(saved)
                row = corrupted["admin_audit"]["receipts"][0]
                for key in location:
                    row = row[key]
                del row[field]
                with self.subTest(location=location, field=field), self.assertRaises(ValueError):
                    validate_snapshot(corrupted, event_configs=self.configs)

    def test_empty_grant_saved_input_defaults_cannot_be_invented(self):
        self.grant(money=0, cards=())
        saved = self.host.snapshot()
        for field in ("money", "cards"):
            changed = deepcopy(saved)
            del changed["admin_audit"]["receipts"][0]["request"][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "normalized"):
                validate_snapshot(changed, event_configs=self.configs)

    def test_restore_rejects_orphans_conflicting_canonical_cards_and_strict_number_corruption(self):
        self.grant()
        saved = self.host.snapshot()
        bad = []
        for field, value in (("deck", 255.0), ("points", True), ("points", 255), ("attack", 11)):
            changed = deepcopy(saved)
            changed["admin_audit"]["receipts"][0]["cards"][0][field] = value
            bad.append(changed)
        for field, value in (("money", 26), ("event_id", "second"), ("grant_id", "missing")):
            changed = deepcopy(saved)
            changed["admin_audit"]["receipts"][0]["request"][field] = value
            bad.append(changed)
        changed = deepcopy(saved)
        changed["event_cards"]["grants"] = []
        bad.append(changed)
        # Version 3 adds membership receipts and still accepts existing grants.
        upgraded = dict(saved["admin_audit"], version=3)
        restored = AdminAudit.restore(
            self.host.economy, self.host.prize, upgraded, event_cards=self.host.event_cards,
        )
        self.assertEqual(restored.snapshot()["receipts"], saved["admin_audit"]["receipts"])
        for version in (1, True, 2.0, 4, [], {}):
            changed = deepcopy(saved)
            changed["admin_audit"]["version"] = version
            bad.append(changed)
        changed = deepcopy(saved)
        changed["admin_audit"]["receipts"].append(deepcopy(changed["admin_audit"]["receipts"][0]))
        bad.append(changed)
        changed = deepcopy(changed)
        changed["admin_audit"]["receipts"][1]["request"]["request_id"] = "other.request"
        bad.append(changed)
        for snapshot in bad:
            with self.subTest(snapshot=snapshot["admin_audit"]), self.assertRaises(ValueError):
                validate_snapshot(snapshot, event_configs=self.configs)
        with self.assertRaisesRegex(ValueError, "jointly recorded"):
            AdminAudit.restore(self.host.economy, self.host.prize, saved["admin_audit"])


class EncryptedEventGrantTests(unittest.IsolatedAsyncioTestCase):
    request = service_tests.ServicesIntegrationTests.request

    async def test_audited_grant_is_only_delivered_by_original_encrypted_f_collection(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = folder / "state.json"
        event = config(card_service_enabled=True)
        self.server = Server(state_path=path, event_configs=(event,), event_clock=lambda: 200)
        self.addAsyncCleanup(self.server.stop)
        port = await self.server.start(0)
        self.client = Client()
        await self.client.open(port)
        self.addAsyncCleanup(self.client.close)
        self.assertIn(b" 422 ", await self.client.read())
        key = self.client.nick.decode()
        before = self.server.economy.account_view(key)
        await self.client.join(self.server.event_rooms.rooms[0].room_channel)
        await self.request(
            0x1000, presence(event.room_id, before.nn, before.player_id, before.name)
        )
        value = request(account_key=key)
        preview = preview_event_grant(self.server, value)
        receipt = apply_event_grant(self.server, value, preview.digest)
        self.assertEqual(self.server.economy.account_view(key), before)

        replies = await self.request(
            0x1002,
            named(
                0x90, 0, 0, f"@CardReq=/NN={before.nn:016X}/HID=0/Dm=1/Vol=1/CN={before.name.hex()}"
            ),
        )
        self.assertEqual(parse_named(replies[0])[3]["CardEnt"]["Ans"], "1")
        fields = parse_named(replies[1])[3]["EcmInit"]
        self.assertEqual((fields["C"], fields["PM"]), ("1", "25"))
        self.assertEqual(
            tuple(map(int, parse_named(replies[1])[3]["N0"]["D"].split("|"))),
            receipt.cards[0].full(),
        )
        self.assertEqual(self.server.economy.inventory(key), before.cards)
        self.assertEqual(self.server.economy.balance(key), before.balance + 25)
        number = int(fields["N"])
        self.assertEqual(
            await self.request(0x1002, named(0xB2, 0x15, number, f"@Get=/No={number}")), []
        )
        await self.request(0x1002, named(0xB2, 0x17, number, f"@Quit=/No={number}"))
        self.assertEqual(len(self.server.economy.inventory(key)), len(before.cards) + 1)
        self.assertEqual(apply_event_grant(self.server, value, "ignored"), receipt)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["event_cards"]["grants"][0]["card_offset"], 1)
        self.assertEqual(saved["event_cards"]["grants"][0]["money_claimed"], 25)
        self.assertEqual(saved["admin_audit"]["receipts"], [receipt.model_dump(mode="json")])


if __name__ == "__main__":
    unittest.main()
