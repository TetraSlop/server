"""Audited event grants queue exactly once and retain facts after collection."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from tests.admin.test_actions import AuditedActionCases, request as prize_request
from tests.events.test_event_rooms import config, presence
from tests.network import test_services_integration as service_tests
from tests.support.irc import Client
from tmserver.admin.actions import (
    AdminActionConflict,
    AdminActionError,
    apply_event_grant,
    apply_prize_award,
)
from tmserver.admin.audit import AdminAudit, EventGrantCard, EventGrantRequest, GrantedEventCard
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


class EventGrantActionTests(AuditedActionCases, unittest.TestCase):
    apply = staticmethod(apply_event_grant)
    request = staticmethod(request)
    journals = {"event_cards"}
    changes = (
        dict(event_id="second"), dict(grant_id="different"), dict(money=26), dict(cards=()),
    )

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
        return value, apply_event_grant(self.host, value)

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

    def test_grant_queues_canonical_cards_without_touching_the_account(self):
        before = self.host.economy.account_view("one")
        _, receipt = self.grant()
        self.assertEqual(receipt.cards[0].points, self.host.economy.catalog.points(0, 10, 20, 30))
        self.assertEqual(receipt.cards[0].deck, 255)
        self.assertEqual((receipt.eligibility, receipt.collection_enabled), (0, True))
        self.assertEqual((receipt.action, receipt.status), ("event-grant", "queued"))
        self.assertEqual(self.host.economy.account_view("one"), before)
        self.assertEqual(
            self.host.event_cards.pending("one", "festival"), {"cards": 1, "money": 25}
        )
        self.assertEqual(
            self.host.admin_audit.snapshot(),
            dict(version=2, receipts=[receipt.model_dump(mode="json")]),
        )

    def test_partial_and_full_collection_restart_keep_original_receipt(self):
        before = self.host.economy.account_view("one")
        value, receipt = self.grant(cards=(CARD,) * 12)
        number, cards, money = self.enter_collection()
        self.assertEqual((cards, money), (9, 25))
        self.assertEqual(self.host.economy.inventory("one"), before.cards)
        self.assertEqual(self.host.economy.balance("one"), before.balance + 25)
        self.finish_collection(number)
        self.assertEqual(len(self.host.economy.inventory("one")), len(before.cards) + 9)
        self.host = self.server()
        self.assertEqual(apply_event_grant(self.host, value), receipt)
        number, cards, money = self.enter_collection()
        self.assertEqual((cards, money), (3, 0))
        self.finish_collection(number)
        self.host = self.server()
        self.assertEqual(apply_event_grant(self.host, value), receipt)
        self.assertEqual(len(self.host.economy.inventory("one")), len(before.cards) + 12)
        self.assertEqual(self.host.economy.balance("one"), before.balance + 25)
        self.assertEqual(self.host.event_cards.pending("one", "festival"), {"cards": 0, "money": 0})

    def test_request_ids_are_shared_with_prize_awards(self):
        self.grant()
        with self.assertRaises(AdminActionConflict):
            apply_prize_award(self.host, prize_request(request_id="event.request.1"))
        prize = apply_prize_award(self.host, prize_request())
        with self.assertRaises(AdminActionConflict):
            apply_event_grant(
                self.host, request(request_id=prize.request.request_id, grant_id="new")
            )

    def test_legacy_grant_ids_never_gain_invented_audit_receipts(self):
        self.host.event_cards.grant("one", "festival", "grant.1", money=25)
        self.host.persist()
        for money in (25, 26):
            with self.subTest(money=money), self.assertRaises(AdminActionConflict):
                apply_event_grant(self.host, request(cards=(), money=money))
        number, _, _ = self.enter_collection()
        self.finish_collection(number)
        self.host = self.server()
        with self.assertRaises(AdminActionConflict):
            apply_event_grant(self.host, request(cards=()))
        self.assertEqual(self.host.admin_audit.snapshot(), dict(version=1, receipts=[]))

    def test_new_grants_require_an_existing_member(self):
        for changes in (dict(event_id="missing"), dict(account_key="two", event_id="second")):
            with self.subTest(changes=changes), self.assertRaises(AdminActionError):
                apply_event_grant(self.host, request(**changes))

    def test_empty_card_stats_rejected_by_existing_catalog_without_changes(self):
        before = self.host.snapshot()
        with self.assertRaisesRegex(AdminActionError, "empty card stats"):
            apply_event_grant(
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
        _, receipt = self.grant(event_id="second")
        self.assertEqual((receipt.eligibility, receipt.collection_enabled), (-1, False))
        self.assertEqual(self.host.event_cards.pending("one", "second"), {"cards": 1, "money": 25})

    def test_other_accounts_active_collection_and_reservation_keep_identity(self):
        self.enter_collection("two")
        session = self.host.event_cards._active["two"]
        token = self.host.economy._reservations["two"]
        self.grant()
        self.assertIs(self.host.event_cards._active["two"], session)
        self.assertIs(self.host.economy._reservations["two"], token)

    def test_v1_prize_receipts_remain_unchanged_in_mixed_v2_history(self):
        value = prize_request()
        old = apply_prize_award(self.host, value)
        v1 = self.host.admin_audit.snapshot()
        self.assertEqual(v1["version"], 1)
        self.assertEqual(AdminAudit.restore(self.host.economy, self.host.prize, v1).snapshot(), v1)
        self.grant()
        self.host = self.server()
        saved = self.host.admin_audit.snapshot()
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["receipts"][0], old.model_dump(mode="json"))
        self.assertEqual(apply_prize_award(self.host, value), old)

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
        receipt = apply_event_grant(self.server, value)
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
        self.assertEqual(apply_event_grant(self.server, value), receipt)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["event_cards"]["grants"][0]["card_offset"], 1)
        self.assertEqual(saved["event_cards"]["grants"][0]["money_claimed"], 25)
        self.assertEqual(saved["admin_audit"]["receipts"], [receipt.model_dump(mode="json")])


if __name__ == "__main__":
    unittest.main()
