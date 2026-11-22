"""Joint claim rollback and lost-COMMIT recovery against real PostgreSQL."""

import unittest
from unittest.mock import patch

from tests.economy.auctions import test_auction_prize_integration as auctions
from tests.events import test_event_integration as events
from tests.storage import test_postgres_protocol as protocol
from tests.network import test_services_integration as services
from tests.economy.auctions.test_auction_service import exhibit_request
from tests.economy.test_claim_transactions import ClaimFixture
from tests.events.test_event_rooms import config
from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture
from tmserver.transactions import EVENT_SERVICES, transaction
from tmserver.economy.packets import named, parse_named
from tmserver.protocol.identities import SHOP_RAW, service_nick
from tmserver.network.server import Server


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class ClaimDatabaseTests(DatabaseFixture, ClaimFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.prepare(self.store)

    def test_lost_new_account_ack_is_reloaded_without_reprovisioning(self):
        import psycopg

        # A new host over this database has additional configured identities.
        self.server = Server(store=self.store, account_count=4, event_configs=(self.config,))
        nick = tuple(self.server.accounts)[3]
        key = nick.decode()
        commit = psycopg.Connection.commit
        before = self.server.snapshot()

        def lost(connection):
            commit(connection)
            raise psycopg.OperationalError("lost account creation acknowledgement")

        with patch.object(psycopg.Connection, "commit", lost), self.assertRaises(OSError):
            self.server.ensure_account(nick)
        self.assertEqual(self.server.snapshot(), before)
        self.assertNotIn(key, self.server.account_keys)
        restored = Server(store=self.reopen(), account_count=4, event_configs=(self.config,))
        self.assertIn(key, restored.account_keys)
        profile = restored.economy.profile_bytes(key)
        revision = self.store.revision
        restored.ensure_account(nick)
        self.assertEqual(restored.economy.profile_bytes(key), profile)
        self.assertEqual(self.store.revision, revision)

    def test_character_profile_rollback_keeps_the_previous_record(self):
        from tests.profiles.test_character_profiles import request
        from tmserver.profiles.transactions import character_profile_request
        from tmserver.protocol.commands import Command

        view = self.server.economy.account_view(self.key)
        payload = request(b"CR", view.nn, extras=(Command(b"AN", b"NewName"),))
        self.rollback(lambda: character_profile_request(self.server, self.key, payload))

    def test_lost_options_ack_reloads_the_committed_preference_bytes(self):
        from tests.profiles.test_options_service import request
        from tmserver.profiles.options import save_options

        view = self.server.economy.account_view(self.key)

        def save():
            with self.server.commerce.edit_account(self.key) as staged:
                save_options(staged, self.key, request(nn=view.nn, name=view.name))

        self.lose_ack(save)
        self.assertEqual(self.server.economy.profile_bytes(self.key)[0x126], 1)

    def lose_ack(self, operation):
        import psycopg

        commit = psycopg.Connection.commit
        before = self.server.snapshot()

        def lost(connection):
            commit(connection)
            raise psycopg.OperationalError("injected lost claim COMMIT acknowledgement")

        with patch.object(psycopg.Connection, "commit", lost), self.assertRaises(OSError):
            operation()
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.server.snapshot(), before)
        self.restart()

    def restart(self):
        self.server = Server(
            store=self.reopen(),
            account_count=3,
            event_configs=(self.config,),
            event_clock=lambda: self.now,
            auction_clock=lambda: self.now,
        )
        expected = self.server.snapshot()
        self.server = Server(
            store=self.reopen(),
            account_count=3,
            event_configs=(self.config,),
            event_clock=lambda: self.now,
            auction_clock=lambda: self.now,
        )
        self.assertEqual(self.server.snapshot(), expected)

    def rollback(self, operation):
        before, durable = self.server.snapshot(), self.store.load()
        original = self.store._save_services

        def fail(journals):
            original(journals)
            self.store.connection.exec_driver_sql("SELECT 1 / 0")

        with (
            patch.object(self.store, "_save_services", side_effect=fail),
            self.assertRaises(OSError),
        ):
            operation()
        self.assertEqual(self.server.snapshot(), before)
        self.assertEqual(self.reopen().load(), durable)

    def test_lost_exhibit_ack_keeps_one_durable_listing_and_card_debit(self):
        self.lose_ack(
            lambda: self.auction(self.key, exhibit_request(self.server.economy, self.key))
        )
        self.assertEqual(len(self.server.economy.inventory(self.key)), 9)
        self.assertEqual(len(self.server.auction_ledger.listings()), 1)

    def test_payment_rollback_preserves_money_and_claim_receipt(self):
        self.sell()
        self.rollback(lambda: self.collect(self.key))

    def test_lost_payment_ack_does_not_pay_the_seller_twice(self):
        self.sell()
        self.lose_ack(lambda: self.collect(self.key))
        self.assertEqual(self.server.economy.balance(self.key), 10100)
        self.collect(self.key)
        self.assertEqual(self.server.economy.balance(self.key), 10100)

    def test_lost_won_card_ack_does_not_restore_the_claim(self):
        self.sell()
        number = self.collect(self.buyer)
        self.auction_get(self.buyer, number)
        self.lose_ack(lambda: self.auction_get(self.buyer, number))
        self.assertEqual(len(self.server.economy.inventory(self.buyer)), 11)
        self.assertFalse(self.server.auction_ledger.pending(self.buyer))

    def test_lost_prize_redemption_ack_does_not_redeem_award_twice(self):
        self.prize_award()
        self.lose_ack(self.enter_prize)
        self.assertEqual(self.server.economy.prize_points(self.key), 5000)
        self.enter_prize()
        self.assertEqual(self.server.economy.prize_points(self.key), 5000)

    def prepare_purchase(self):
        self.prize_award()
        number = self.enter_prize()
        self.prize(named(0xB2, 0x14, number, "@Buy=/No=45"))
        return named(0xB2, 0x15, number, f"@Get=/No={number}")

    def test_prize_get_rollback_preserves_points_inventory_and_pending_purchase(self):
        payload = self.prepare_purchase()
        pending = self.server.prize._active[self.key]
        self.rollback(lambda: self.prize(payload))
        self.assertIs(self.server.prize._active[self.key], pending)

    def test_lost_prize_get_ack_preserves_one_purchase(self):
        payload = self.prepare_purchase()
        cost = self.server.prize._active[self.key].pending.cost
        self.lose_ack(lambda: self.prize(payload))
        self.enter_prize()
        self.assertEqual(len(self.server.economy.inventory(self.key)), 11)
        self.assertEqual(self.server.economy.prize_points(self.key), 5000 - cost)

    def test_event_get_rollback_preserves_cards_and_grant_offset(self):
        self.event_grant()
        number = self.enter_event()
        self.rollback(lambda: self.event(named(0xB2, 0x15, number, f"@Get=/No={number}")))

    def test_lost_event_entry_ack_keeps_one_money_credit_and_unclaimed_cards(self):
        self.event_grant()
        self.lose_ack(self.enter_event)
        self.enter_event()
        self.assertEqual(self.server.economy.balance(self.key), 10250)
        self.assertEqual(len(self.server.economy.inventory(self.key)), 10)
        self.assertEqual(
            self.server.event_cards.pending(self.key, "festival"), {"money": 0, "cards": 12}
        )

    def test_lost_partial_event_get_ack_resumes_with_only_remaining_cards(self):
        self.event_grant()
        number = self.enter_event()
        self.lose_ack(lambda: self.event(named(0xB2, 0x15, number, f"@Get=/No={number}")))
        self.assertEqual(len(self.server.economy.inventory(self.key)), 19)
        self.assertEqual(
            self.server.event_cards.pending(self.key, "festival"), {"money": 0, "cards": 3}
        )
        number = self.enter_event()
        self.event(named(0xB2, 0x15, number, f"@Get=/No={number}"))
        self.assertEqual(len(self.server.economy.inventory(self.key)), 22)
        self.assertEqual(self.server.economy.balance(self.key), 10250)

    def test_event_result_rollback_preserves_membership_and_receipts(self):
        self.event_grant()
        self.rollback(self.record_result)

    def test_lost_event_result_ack_does_not_repeat_progress(self):
        self.event_grant()
        self.lose_ack(self.record_result)
        self.record_result()
        self.assertEqual(
            [self.server.events.member(k, "festival").steps for k in self.keys[:2]], [1, 1]
        )
        self.assertEqual(len(self.server.event_results.snapshot()["rounds"]), 1)


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class ScopedClaimProtocolTests(protocol.ProtocolFixture):
    send = auctions.AuctionPrizeIntegrationTests.send
    sell = auctions.AuctionPrizeIntegrationTests.sell
    enter_collection = auctions.AuctionPrizeIntegrationTests.enter_collection
    get_collection = auctions.AuctionPrizeIntegrationTests.get_collection
    prize_entry = auctions.AuctionPrizeIntegrationTests.prize_entry
    enter_prize = auctions.AuctionPrizeIntegrationTests.enter_prize
    request = services.ServicesIntegrationTests.request
    enter_room = events.EventIntegrationTests.enter_room

    async def asyncSetUp(self):
        self.now = 200
        self.options = {
            "event_configs": (config(card_service_enabled=True),),
            "event_clock": lambda: self.now,
            "auction_clock": lambda: self.now,
        }
        await super().asyncSetUp()
        self.game = self.server

    async def test_encrypted_sale_and_collection_use_scoped_writes(self):
        with patch.object(self.server, "persist", side_effect=AssertionError("full save")):
            await self.sell()
            for side in range(2):
                number, _ = await self.enter_collection(side)
                await self.get_collection(side, number)
                await self.get_collection(side, number)
        self.assertEqual(self.server.economy.balance(self.keys[0]), 10100)
        self.assertEqual(len(self.server.economy.inventory(self.keys[1])), 11)
        await self.assert_restart()

    async def test_encrypted_prize_redemption_and_purchase_use_scoped_writes(self):
        with transaction(self.server, (self.key,), "prize") as staged:
            staged.prize.grant_award(self.key, "wire-prize", top30=5000)
        with patch.object(self.server, "persist", side_effect=AssertionError("full save")):
            number, _ = await self.enter_prize()
            for subtype, body in (
                (0x14, "@Buy=/No=45"),
                (0x15, f"@Get=/No={number}"),
                (0x17, f"@Quit=/No={number}"),
            ):
                await self.send(
                    0, named(0xB2, subtype, number, body), source=service_nick(SHOP_RAW)
                )
        self.assertEqual(len(self.server.economy.inventory(self.key)), 11)
        await self.assert_restart()

    async def test_encrypted_event_membership_and_claim_use_scoped_writes(self):
        with patch.object(self.server, "persist", side_effect=AssertionError("full save")):
            await self.enter_room()
            with transaction(self.server, (self.key,), *EVENT_SERVICES) as staged:
                card = staged.event_cards.economy.inventory(self.key)[0]
                staged.event_cards.grant(self.key, "festival", "wire-event", cards=(card,), money=250)
            view = self.server.economy.account_view(self.key)
            replies = await self.request(
                0x1002,
                named(
                    0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
                ),
            )
            number = int(parse_named(replies[1])[3]["EcmInit"]["N"])
            await self.request(0x1002, named(0xB2, 0x15, number, f"@Get=/No={number}"))
            await self.request(0x1002, named(0xB2, 0x17, number, f"@Quit=/No={number}"))
        self.assertEqual(self.server.economy.balance(self.key), 10250)
        self.assertEqual(len(self.server.economy.inventory(self.key)), 11)
        await self.assert_restart()
