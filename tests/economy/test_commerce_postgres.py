"""Scoped writes and original encrypted commerce flows against PostgreSQL."""

import unittest
from copy import deepcopy
from unittest.mock import patch

from tests.storage import test_postgres_protocol as protocol
from tests.network import test_services_integration as shops
from tests.economy import test_trade_integration as trades
from tests.storage.test_postgres_store import TEST_URL, DatabaseFixture, sa
from tmserver.economy.packets import named
from tmserver.protocol.identities import SHOP_RAW, TRADE_RAW, service_nick
from tmserver.network.server import Server


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class ScopedAccountStoreTests(DatabaseFixture, unittest.TestCase):
    def sample(self):
        game = Server()
        for nick in game.accounts:
            game.ensure_account(nick)
        return game.snapshot()

    def test_account_write_never_compares_or_copies_other_accounts_or_services(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        one, two = sorted(snapshot["accounts"])
        changed = deepcopy(snapshot["accounts"][one])
        changed["balance"] -= 100

        class Untouched:
            def __eq__(self, other):
                raise AssertionError("unrelated state was compared")

            def __deepcopy__(self, memo):
                raise AssertionError("unrelated state was copied")

        self.store.snapshot["accounts"][two] = Untouched()
        self.store.snapshot["events"] = Untouched()
        with patch.object(self.store, "save", side_effect=AssertionError("full save")):
            self.store.save_accounts({one: changed})
        restored = self.reopen().load()
        snapshot["accounts"][one] = changed
        self.assertEqual(restored, snapshot)

    def test_scoped_two_account_failure_rolls_back_both_and_keeps_all_journals(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        updates = deepcopy(snapshot["accounts"])
        one, two = sorted(updates)
        updates[one]["cards"].pop()
        updates[two]["balance"] = -1
        with self.assertRaises(OSError):
            self.store.save_accounts(updates)
        self.assertEqual(self.reopen().load(), snapshot)

    def test_scoped_commit_updates_baseline_for_later_full_save(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        key = next(iter(snapshot["accounts"]))
        snapshot["accounts"][key]["balance"] += 50
        self.store.save_accounts({key: snapshot["accounts"][key]})
        revision = self.store.revision
        self.store.save(snapshot)
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(self.store.load(), snapshot)

    def test_scoped_noop_detects_connection_loss_and_never_reconnects(self):
        snapshot = self.sample()
        self.store.save(snapshot)
        with self.store.connection.begin():
            pid = self.store.connection.scalar(sa.text("SELECT pg_backend_pid()"))
        with self.admin.connect() as connection:
            connection.execute(sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with self.assertRaises(OSError):
            self.store.save_accounts(snapshot["accounts"])
        with self.assertRaises(OSError):
            self.store.save_accounts(snapshot["accounts"])
        self.assertEqual(self.reopen().load(), snapshot)

    def test_scoped_lost_commit_acknowledgement_is_reloaded_without_reapplying(self):
        import psycopg

        snapshot = self.sample()
        self.store.save(snapshot)
        key = next(iter(snapshot["accounts"]))
        snapshot["accounts"][key]["balance"] += 50
        original = psycopg.Connection.commit

        def lose_ack(connection):
            original(connection)
            raise psycopg.OperationalError("injected lost acknowledgement")

        with patch.object(psycopg.Connection, "commit", lose_ack), self.assertRaises(OSError):
            self.store.save_accounts({key: snapshot["accounts"][key]})
        self.assertEqual(self.reopen().load(), snapshot)


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class ScopedShopProtocolTests(protocol.ProtocolFixture):
    request = shops.ServicesIntegrationTests.request
    enter_shop = shops.ServicesIntegrationTests.enter_shop

    async def test_purchase_never_takes_a_world_snapshot(self):
        with (
            patch.object(self.server, "snapshot", side_effect=AssertionError("world snapshot")),
            patch.object(self.store, "save", side_effect=AssertionError("full save")),
        ):
            session = await self.enter_shop()
            await self.request(SHOP_RAW, named(0xB2, 0x14, session, "@Buy=/No=1"))
            await self.request(SHOP_RAW, named(0xB2, 0x15, session, f"@Get=/No={session}"))
            await self.request(SHOP_RAW, named(0xB2, 0x17, session, f"@Quit=/No={session}"))
        self.assertEqual(self.server.economy.balance(self.key), 9000)
        await self.assert_restart()

    async def test_failed_get_disconnects_with_original_inventory_still_in_memory(self):
        session = await self.enter_shop()
        await self.request(SHOP_RAW, named(0xB2, 0x14, session, "@Buy=/No=1"))
        before = self.server.economy.snapshot()
        with self.store.connection.begin():
            pid = self.store.connection.scalar(sa.text("SELECT pg_backend_pid()"))
        with self.admin.connect() as connection:
            connection.execute(sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        await self.client.send(
            b"NOTICE "
            + service_nick(SHOP_RAW)
            + b" :"
            + named(0xB2, 0x15, session, f"@Get=/No={session}")
        )
        self.assertEqual(await self.client.read(), b"")
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.server.economy.snapshot(), before)
        await self.disconnect()
        self.assertEqual(self.reopen().load()["accounts"], before["accounts"])

    async def test_get_with_lost_commit_ack_reloads_the_purchase_once(self):
        import psycopg

        session = await self.enter_shop()
        await self.request(SHOP_RAW, named(0xB2, 0x14, session, "@Buy=/No=1"))
        before = self.server.economy.snapshot()
        commit = psycopg.Connection.commit

        def lose_ack(connection):
            commit(connection)
            raise psycopg.OperationalError("injected lost purchase COMMIT acknowledgement")

        get = named(0xB2, 0x15, session, f"@Get=/No={session}")
        with patch.object(psycopg.Connection, "commit", lose_ack):
            await self.client.send(b"NOTICE " + service_nick(SHOP_RAW) + b" :" + get)
            self.assertEqual(await self.client.read(), b"")
        self.assertEqual(self.server.economy.snapshot(), before)
        await self.disconnect()
        self.server = Server(store=self.reopen())
        self.assertEqual(self.server.economy.balance(self.key), 9000)
        self.assertEqual(len(self.server.economy.inventory(self.key)), 20)
        with self.assertRaises(ValueError):
            self.server.commerce.shop(self.key, get)
        self.assertEqual(self.server.economy.balance(self.key), 9000)


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class ScopedTradeProtocolTests(protocol.ProtocolFixture):
    flush = trades.TradeIntegrationTests.flush
    send = trades.TradeIntegrationTests.send
    control = trades.TradeIntegrationTests.control
    metadata = trades.TradeIntegrationTests.metadata
    enter = trades.TradeIntegrationTests.enter
    operation = trades.TradeIntegrationTests.operation
    settle = trades.TradeIntegrationTests.settle

    async def test_negotiation_and_final_get_never_take_a_world_snapshot(self):
        with (
            patch.object(self.server, "snapshot", side_effect=AssertionError("world snapshot")),
            patch.object(self.store, "save", side_effect=AssertionError("full save")),
        ):
            await self.settle()
            await self.operation(0, 0x1D, "@Get=/P=0")
            await self.operation(1, 0x1D, "@Get=/P=1")
            await self.operation(0, 0x1D, "@Get=/P=0")
        await self.assert_restart()

    async def test_failed_final_get_never_publishes_either_inventory_or_quit(self):
        await self.settle()
        await self.operation(0, 0x1D, "@Get=/P=0")
        before = self.server.economy.snapshot()
        write_account = self.store._save_account
        written = []

        def fail_after_both_updates(key, account, old):
            write_account(key, account, old)
            written.append(key)
            if len(written) == 2:
                self.store.connection.exec_driver_sql("SELECT 1 / 0")

        with patch.object(self.store, "_save_account", fail_after_both_updates):
            await self.clients[1].send(
                b"NOTICE "
                + service_nick(TRADE_RAW)
                + b" :"
                + named(0xA2, 0x1D, self.session, "@Get=/P=1")
            )
            for client in self.clients:
                self.assertEqual(await client.read(), b"")
        self.assertEqual(len(written), 2)
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.server.economy.snapshot(), before)
        self.assertFalse(self.server.trade._closed)
        await self.disconnect()
        self.assertEqual(self.reopen().load()["accounts"], before["accounts"])


if __name__ == "__main__":
    unittest.main()
