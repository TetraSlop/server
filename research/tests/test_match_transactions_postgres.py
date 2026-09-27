"""Real database rollback and ambiguous-commit recovery for match settlements."""

import unittest
from copy import deepcopy
from unittest.mock import patch

import test_postgres_protocol as protocol
from database_schema import service_state
from server import Server
from test_match_transactions import TableFixture
from test_postgres_store import TEST_URL, DatabaseFixture, sa


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class MatchDatabaseTests(DatabaseFixture, TableFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.prepare_table(self.store)

    def lose_ack(self, operation):
        import psycopg

        commit = psycopg.Connection.commit

        def committed_but_lost(connection):
            commit(connection)
            raise psycopg.OperationalError("injected lost match COMMIT acknowledgement")

        before = self.server.snapshot()
        tokens = dict(self.server.match_reservations)
        with (
            patch.object(psycopg.Connection, "commit", committed_but_lost),
            self.assertRaises(OSError),
        ):
            operation()
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.server.snapshot(), before)
        self.assertEqual(self.server.match_reservations, tokens)
        for token in tokens.values():
            self.assertIs(self.server.economy._reservations[token.account.key], token)
        self.server.release_match_accounts()

    def restart_twice(self):
        recovered = Server(store=self.reopen(), account_count=3, rating_mode="local-history")
        expected = recovered.snapshot()
        again = Server(store=self.reopen(), account_count=3, rating_mode="local-history")
        self.assertEqual(again.snapshot(), expected)
        return recovered

    def test_joint_write_touches_only_supplied_accounts_and_journals(self):
        before = self.store.load()
        one, untouched = self.keys[0], self.keys[2]
        change = deepcopy(before["accounts"][one])
        change["balance"] -= 100

        class Unvisited:
            def __eq__(self, other):
                raise AssertionError("unrelated state was compared")

            def __deepcopy__(self, memo):
                raise AssertionError("unrelated state was copied")

        self.store.snapshot["accounts"][untouched] = Unvisited()
        self.store.snapshot["events"] = Unvisited()
        journal = {"version": 1, "known": [], "pending": []}
        self.store.save_changes({one: change}, {"result_rewards": journal})
        before["accounts"][one] = change
        before["result_rewards"] = journal
        self.assertEqual(self.reopen().load(), before)

    def test_sql_failure_after_accounts_and_journals_rolls_back_entire_result(self):
        self.begin()
        self.prepare_result()
        memory = self.server.snapshot()
        durable = self.store.load()
        original = self.store._save_services

        def fail(snapshot, previous, **kwargs):
            original(snapshot, previous, **kwargs)
            row = self.store.connection.scalar(
                sa.select(service_state.c.payload).where(service_state.c.name == "match_economy")
            )
            self.assertEqual(row["rounds"][0]["money"], "paid")
            self.store.connection.exec_driver_sql("SELECT 1 / 0")

        with (
            patch.object(self.store, "_save_services", side_effect=fail),
            self.assertRaises(OSError),
        ):
            self.server.commit_match_results(self.match)
        self.assertEqual(self.server.snapshot(), memory)
        self.assertFalse(self.server.match_committed)
        self.assertEqual(self.reopen().load(), durable)

    def test_lost_stake_ack_refunds_only_the_durable_debit_on_restart(self):
        self.lose_ack(self.begin)
        restored = self.restart_twice()
        self.assertEqual([restored.economy.balance(k) for k in self.keys], [10000] * 3)
        row = restored.match_economy.snapshot()["rounds"][0]
        self.assertEqual(row["money"], "refunded")

    def test_lost_result_ack_preserves_one_payout_rating_and_cbc_award(self):
        self.begin()
        self.prepare_result()
        self.lose_ack(lambda: self.server.commit_match_results(self.match))
        restored = self.restart_twice()
        self.assertEqual([restored.economy.balance(k) for k in self.keys[:2]], [10100, 9900])
        for key in self.keys[:2]:
            self.assertEqual(restored.ratings.history.state(key).revision, 1)
            self.assertEqual(len(restored.economy.inventory(key)), 10)
        self.assertEqual(len(restored.auction_ledger.pending(self.keys[0])), 1)

    def test_lost_prize_ack_recovers_the_transferred_card_once(self):
        self.begin()
        self.result()
        award = self.prepare_prize()
        self.lose_ack(lambda: self.server.commit_match_prizes(self.match))
        self.match.confirm_prize_commit.assert_not_called()
        restored = self.restart_twice()
        self.assertEqual(len(restored.economy.inventory(self.keys[1])), 9)
        self.assertEqual(len(restored.economy.inventory(self.keys[0])), 10)
        returned = restored.auction_ledger.pending(self.keys[0])
        self.assertEqual(len(returned), 2)
        self.assertEqual(returned[1].card, award.card)

    def test_lost_continue_ack_does_not_recover_already_collected_awards(self):
        from match_transactions import match_transaction

        self.begin()
        self.result()
        self.prepare_prize()
        self.server.commit_match_prizes(self.match)

        def accept():
            with match_transaction(self.server, (self.keys[0],)) as state:
                state.result_rewards.commit(self.keys[0])

        self.lose_ack(accept)
        restored = self.restart_twice()
        self.assertEqual(len(restored.economy.inventory(self.keys[0])), 12)
        self.assertEqual(len(restored.economy.inventory(self.keys[1])), 9)
        self.assertFalse(restored.auction_ledger.pending(self.keys[0]))

    def test_joint_commit_updates_baseline_and_rejects_unknown_journals(self):
        self.begin()
        revision = self.store.revision
        self.store.save(self.server.snapshot())
        self.assertEqual(self.store.revision, revision)
        durable = self.store.load()
        with self.assertRaises(OSError):
            self.store.save_changes({}, {"accounts": {}})
        self.assertEqual(self.reopen().load(), durable)


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class ScopedMatchProtocolTests(protocol.ProtocolFixture):
    player_count = 2
    options = protocol.PostgresMatchTests.options
    flush = protocol.PostgresMatchTests.flush
    send = protocol.PostgresMatchTests.send
    play = protocol.PostgresMatchTests.play
    choose = protocol.PostgresMatchTests.choose
    admit = protocol.PostgresMatchTests.admit

    async def test_encrypted_match_never_calls_the_full_save_path(self):
        with (
            patch.object(self.server, "persist", side_effect=AssertionError("full match save")),
            patch.object(self.store, "save", side_effect=AssertionError("full snapshot write")),
        ):
            await protocol.PostgresMatchTests.test_complete_match_prizes_stakes_ratings_and_restart(
                self
            )
