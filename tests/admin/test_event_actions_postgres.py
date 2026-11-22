"""Audited event grants survive real collection, PostgreSQL faults and reloads."""

from dataclasses import asdict
import unittest
from unittest.mock import patch

from tests.admin.test_actions_postgres import PostgresActionCases
from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class EventGrantPostgresTests(PostgresActionCases, DatabaseFixture, unittest.TestCase):
    journals = {"event_cards"}

    def setUp(self):
        super().setUp()
        from tests.events.test_event_rooms import config
        from tmserver.admin.actions import apply_event_grant
        from tmserver.network.server import Server

        self.apply = apply_event_grant
        self.options = dict(
            event_configs=(config(card_service_enabled=True),), event_clock=lambda: 200
        )
        self.host = Server(store=self.store, **self.options)
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))
        self.host.events.admit_room(self.key, "festival")
        self.host.persist()

    def request(self, **changes):
        from tmserver.admin.audit import EventGrantCard, EventGrantRequest

        fields = dict(
            request_id="pg-event-request.1",
            account_key=self.key,
            event_id="festival",
            grant_id="pg-event-grant.1",
            operator="fixture-operator",
            source="cli",
            reason="Correct an event award",
            money=250,
            cards=tuple(
                EventGrantCard(model=1, attack=50, kind=0, physical=40, magical=30, arrows=arrows)
                for arrows in range(12)
            ),
        )
        fields.update(changes)
        return EventGrantRequest(**fields)

    def canonical_cards(self, request):
        catalog = self.host.economy.catalog
        return [
            dict(
                **card.model_dump(),
                points=catalog.points(card.model, card.attack, card.physical, card.magical),
                deck=255,
            )
            for card in request.cards
        ]

    def restart(self):
        from tmserver.network.server import Server

        self.host = Server(store=self.reopen(), **self.options)

    def collect_visit(self):
        from tmserver.transactions import EVENT_SERVICES, transaction
        from tmserver.economy.packets import named, parse_named

        view = self.host.economy.account_view(self.key)
        request = named(
            0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
        )
        with transaction(self.host, (self.key,), *EVENT_SERVICES) as event:
            replies = event.event_cards.handle(self.key, request, event_id="festival")
        self.assertEqual(parse_named(replies[0])[3]["CardEnt"]["Ans"], "1")
        init = parse_named(replies[1])[3]["EcmInit"]
        number = int(init["N"])
        for subtype, token in ((0x15, "Get"), (0x17, "Quit")):
            with transaction(self.host, (self.key,), *EVENT_SERVICES) as event:
                event.event_cards.handle(self.key, named(0xB2, subtype, number, f"@{token}=/No={number}"))
        return int(init["C"]), int(init["PM"])

    def protected_row_versions(self):
        with self.store.connection.begin():
            result = {
                table: self.store.connection.exec_driver_sql(
                    f"SELECT xmin::text, ctid::text FROM {table} ORDER BY ctid"
                ).all()
                for table in ("accounts", "cards")
            }
            result["other_journals"] = self.store.connection.exec_driver_sql(
                "SELECT name, xmin::text, ctid::text FROM service_state "
                "WHERE name NOT IN ('admin_audit', 'event_cards') ORDER BY name"
            ).all()
        return result

    def test_queue_partial_and_full_collection_restart_then_exact_retry(self):
        request = self.request()
        rows = self.protected_row_versions()
        balance = self.host.economy.balance(self.key)
        inventory = self.host.economy.inventory(self.key)
        expected = self.canonical_cards(request)
        receipt = self.apply(self.host, request)
        self.assertEqual(self.protected_row_versions(), rows)
        self.assertEqual(self.store.load()["admin_audit"]["version"], 2)
        self.assertEqual([card.model_dump() for card in receipt.cards], expected)
        self.assertTrue(receipt.collection_enabled)
        self.assertEqual(self.host.economy.balance(self.key), balance)
        self.assertEqual(self.host.economy.inventory(self.key), inventory)

        self.restart()
        self.assertEqual(self.collect_visit(), (9, 250))
        self.assertEqual(
            self.host.event_cards.pending(self.key, "festival"), dict(cards=3, money=0)
        )
        self.restart()
        partial, partial_revision = self.store.load(), self.store.revision
        with patch.object(self.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(self.apply(self.host, request), receipt)
        self.assertEqual(self.store.load(), partial)
        self.assertEqual(self.store.revision, partial_revision)
        self.assertEqual(self.collect_visit(), (3, 0))
        self.restart()
        self.assertEqual(
            self.host.event_cards.pending(self.key, "festival"), dict(cards=0, money=0)
        )
        self.assertEqual(self.host.economy.balance(self.key), balance + 250)
        cards = self.host.economy.inventory(self.key)[len(inventory) :]
        self.assertEqual([asdict(card) for card in cards], expected)
        self.assertEqual(self.apply(self.host, request), receipt)

    def test_legacy_v1_prize_receipt_survives_mixed_v2_upgrade(self):
        from tmserver.admin.actions import apply_prize_award
        from tmserver.admin.audit import PrizeAwardRequest

        prize_request = PrizeAwardRequest(
            request_id="legacy-prize-request",
            account_key=self.key,
            award_id="legacy-prize",
            operator="fixture-operator",
            source="cli",
            reason="Legacy weekly correction",
            top30=7,
        )
        prize = apply_prize_award(self.host, prize_request)
        legacy, revision = self.store.load(), self.store.revision
        self.assertEqual(
            legacy["admin_audit"], dict(version=1, receipts=[prize.model_dump(mode="json")])
        )
        self.restart()
        self.assertEqual(self.host.admin_audit.get(prize_request.request_id), prize)
        self.assertEqual(self.store.load(), legacy)
        self.assertEqual(self.store.revision, revision)
        request = self.request()
        receipt = self.apply(self.host, request)
        mixed = self.store.load()
        self.assertEqual(
            mixed["admin_audit"],
            dict(
                version=2, receipts=[prize.model_dump(mode="json"), receipt.model_dump(mode="json")]
            ),
        )
        self.assertEqual(mixed["prize"], legacy["prize"])
        self.assertEqual(mixed["accounts"], legacy["accounts"])
        self.restart()
        self.assertEqual(self.host.admin_audit.get(prize_request.request_id), prize)
        self.assertEqual(self.host.admin_audit.get(request.request_id), receipt)
        self.assertEqual(self.collect_visit(), (9, 250))
        self.assertEqual(self.collect_visit(), (3, 0))
