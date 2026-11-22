"""Audited event grants survive real collection, PostgreSQL faults and reloads."""

from copy import deepcopy
from dataclasses import asdict
import unittest
from unittest.mock import patch

from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture, sa


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class EventGrantPostgresTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from tests.events.test_event_rooms import config
        from tmserver.admin.event_actions import apply_event_grant, preview_event_grant
        from tmserver.network.server import Server

        self.preview, self.apply = preview_event_grant, apply_event_grant
        self.options = dict(
            event_configs=(config(card_service_enabled=True),), event_clock=lambda: 200
        )
        self.host = Server(store=self.store, **self.options)
        self.key = self.host.ensure_account(next(iter(self.host.accounts)))
        self.host.events.admit_room(self.key, "festival")
        self.host.persist()

    def request(self, **changes):
        from tmserver.admin.event_actions import EventGrantCard, EventGrantRequest

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
        from tmserver.economy.claim_transactions import event_transaction
        from tmserver.economy.packets import named, parse_named

        view = self.host.economy.account_view(self.key)
        request = named(
            0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
        )
        with event_transaction(self.host, (self.key,)) as event:
            replies = event.cards.handle(self.key, request, event_id="festival")
        self.assertEqual(parse_named(replies[0])[3]["CardEnt"]["Ans"], "1")
        init = parse_named(replies[1])[3]["EcmInit"]
        number = int(init["N"])
        for subtype, token in ((0x15, "Get"), (0x17, "Quit")):
            with event_transaction(self.host, (self.key,)) as event:
                event.cards.handle(self.key, named(0xB2, subtype, number, f"@{token}=/No={number}"))
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
        from tmserver.admin.actions import AdminActionConflict

        request = self.request()
        before, revision = self.store.load(), self.store.revision
        rows = self.protected_row_versions()
        balance = self.host.economy.balance(self.key)
        inventory = self.host.economy.inventory(self.key)
        expected = self.canonical_cards(request)
        preview = self.preview(self.host, request)
        self.assertEqual([card.model_dump() for card in preview.cards], expected)
        self.assertTrue(preview.collection_enabled)
        self.assertIsNone(preview.already_committed)
        self.assertEqual(self.preview(self.host, request), preview)
        self.assertEqual(self.store.load(), before)
        self.assertEqual(self.store.revision, revision)
        with self.assertRaises(AdminActionConflict):
            self.apply(self.host, request, "0" * 64)
        with patch.object(self.host, "persist_changes", wraps=self.host.persist_changes) as persist:
            receipt = self.apply(self.host, request, preview.digest)
        persist.assert_called_once()
        accounts, services = persist.call_args.args
        self.assertEqual(accounts, {})
        self.assertEqual(set(services), {"event_cards", "admin_audit"})
        self.assertEqual(self.protected_row_versions(), rows)
        queued = self.store.load()
        self.assertEqual(self.store.revision, revision + 1)
        self.assertEqual(
            {k: v for k, v in queued.items() if k not in services},
            {k: v for k, v in before.items() if k not in services},
        )
        self.assertEqual(queued["admin_audit"]["version"], 2)
        self.assertEqual(queued["admin_audit"]["receipts"], [receipt.model_dump(mode="json")])
        self.assertEqual([card.model_dump() for card in receipt.cards], expected)
        self.assertEqual(receipt.request, request)
        self.assertEqual(receipt.preview_digest, preview.digest)
        self.assertEqual(
            (receipt.eligibility, receipt.collection_enabled),
            (preview.eligibility, preview.collection_enabled),
        )
        self.assertEqual(self.host.economy.balance(self.key), balance)
        self.assertEqual(self.host.economy.inventory(self.key), inventory)

        self.restart()
        self.assertEqual(self.collect_visit(), (9, 250))
        self.assertEqual(
            self.host.event_cards.pending(self.key, "festival"), dict(cards=3, money=0)
        )
        self.assertEqual(len(self.host.economy.inventory(self.key)), len(inventory) + 9)
        self.assertEqual(self.host.economy.balance(self.key), balance + 250)
        self.restart()
        self.assertEqual(self.host.admin_audit.get(request.request_id), receipt)
        partial, partial_revision = self.store.load(), self.store.revision
        with patch.object(self.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(self.apply(self.host, request, "old confirmation"), receipt)
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
        collected, revision = self.store.load(), self.store.revision
        token = self.host.economy.reserve_match(self.key)
        try:
            with patch.object(
                self.store, "save_changes", side_effect=AssertionError("retry wrote")
            ):
                self.assertEqual(self.apply(self.host, request, "old confirmation"), receipt)
                replay = self.preview(self.host, request)
                self.assertEqual(replay.already_committed, receipt)
                self.assertEqual(replay.cards, receipt.cards)
                with self.assertRaises(AdminActionConflict):
                    self.apply(self.host, self.request(money=251), preview.digest)
        finally:
            self.host.economy.release_match(token)
        self.assertEqual(self.store.load(), collected)
        self.assertEqual(self.store.revision, revision)

    def test_failure_between_journals_rolls_back_grant_and_receipt(self):
        from tmserver.storage.schema import service_state

        request = self.request()
        preview = self.preview(self.host, request)
        saved, live, revision = (
            self.store.load(),
            deepcopy(self.host.snapshot()),
            self.store.revision,
        )
        save_services = self.store._save_services
        wrote = []

        def fail_between(snapshot, previous, *, names):
            self.assertEqual(set(names), {"event_cards", "admin_audit"})
            save_services(snapshot, previous, names=("event_cards",))
            inside = self.store.connection.scalar(
                sa.select(service_state.c.payload).where(service_state.c.name == "event_cards")
            )
            self.assertEqual(inside, snapshot["event_cards"])
            self.assertNotEqual(inside, previous["event_cards"])
            wrote.append(inside)
            raise OSError("injected failure between event grant journals")

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

    def test_lost_commit_ack_fails_closed_until_exact_receipt_is_reloaded(self):
        import psycopg

        request = self.request()
        preview = self.preview(self.host, request)
        before, revision = deepcopy(self.host.snapshot()), self.store.revision
        commit = psycopg.Connection.commit

        def committed_but_ack_lost(connection):
            commit(connection)
            raise psycopg.OperationalError("injected lost event-grant COMMIT acknowledgement")

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
        self.assertEqual(
            [card.model_dump() for card in receipt.cards], self.canonical_cards(request)
        )
        self.assertEqual(
            self.host.event_cards.pending(self.key, "festival"), dict(cards=12, money=250)
        )
        self.assertEqual(self.store.revision, revision + 1)
        saved = self.store.load()
        self.assertEqual(saved["accounts"], before["accounts"])
        self.assertEqual(saved["admin_audit"]["receipts"], [receipt.model_dump(mode="json")])
        with patch.object(self.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(self.apply(self.host, request, preview.digest), receipt)
        self.assertEqual(self.store.load(), saved)
        self.assertEqual(self.store.revision, revision + 1)

    def test_legacy_v1_prize_receipt_survives_mixed_v2_upgrade(self):
        from tmserver.admin.actions import apply_prize_award, preview_prize_award
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
        prize = apply_prize_award(
            self.host, prize_request, preview_prize_award(self.host, prize_request).digest
        )
        legacy, revision = self.store.load(), self.store.revision
        self.assertEqual(
            legacy["admin_audit"], dict(version=1, receipts=[prize.model_dump(mode="json")])
        )
        self.restart()
        self.assertEqual(self.host.admin_audit.get(prize_request.request_id), prize)
        self.assertEqual(self.store.load(), legacy)
        self.assertEqual(self.store.revision, revision)
        request = self.request()
        receipt = self.apply(self.host, request, self.preview(self.host, request).digest)
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
