"""Identity and account state commit together, without taking another player's save."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.support.memory_store import MemoryStore
from tmserver.accounts.identities import IdentityRegistry
from tmserver.accounts.provisioning import HOSTED_RAW_BASE, import_bindings, provision_identity
from tmserver.network.server import Server
from tmserver.protocol.wire import polid_from_string, polid_unscramble
from tmserver.storage.validation import validate_snapshot


ISSUER = "https://fixture.supabase.co/auth/v1"
USER = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"


class IdentityProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = MemoryStore()
        self.host = Server(store=self.store)

    def create(self, subject=USER, **options):
        return provision_identity(self.host, ISSUER, subject, **options)

    def test_first_account_has_native_credentials_and_exactly_one_starter_collection(self):
        before = self.host.snapshot()
        key = self.create(name="Discord Name")
        account = self.host.economy.account_view(key)
        self.assertEqual(account.name, b"Discord Name")
        self.assertEqual((account.balance, len(account.cards), account.player_id), (10000, 10, 129))
        login, password, character = self.host.account_specs[key]
        self.assertEqual(polid_from_string(login.decode()), HOSTED_RAW_BASE + character)
        self.assertEqual(polid_unscramble(key.encode()), HOSTED_RAW_BASE + 129)
        self.assertEqual(len(password), 16)
        after, disk = self.host.snapshot(), self.store.load()
        for name in set(before) - {"accounts", "identities"}:
            self.assertEqual(after[name], before[name])
        with patch.object(self.host.store, "save_changes", side_effect=AssertionError("repeat wrote")):
            self.assertEqual(self.create(name="Updated Name"), key)
        self.assertEqual(self.store.load(), disk)
        self.host = Server(store=self.store)
        self.assertEqual(self.create(), key)
        self.assertEqual(self.host.economy.account_view(key), account)
        self.assertNotIn(password.decode(), self.store.load())

    def test_existing_bindings_migrate_without_reset_and_can_be_removed_from_config(self):
        key = self.host.ensure_account(next(iter(self.host.accounts)), name=b"Existing")
        before = deepcopy(self.host.snapshot()["accounts"])
        import_bindings(self.host, ISSUER, {USER: key})
        self.assertEqual(self.host.snapshot()["accounts"], before)
        self.host = Server(store=self.store)
        import_bindings(self.host, ISSUER, {})
        self.assertEqual(self.create(), key)
        self.assertEqual(self.host.snapshot()["accounts"], before)
        self.assertTrue(self.host.identities.owns(key))

    def test_identity_and_native_ids_do_not_depend_on_display_name_or_local_pool_size(self):
        self.host = Server(store=self.store, account_count=128)
        one, two = self.create(name="Same Name"), self.create(SECOND, name="Same Name")
        self.assertNotEqual(one, two)
        self.assertEqual(len(self.host.accounts), 130)
        self.assertEqual(self.host.economy.account_view(two).player_id, 130)
        other = provision_identity(self.host, "https://other.supabase.co/auth/v1", USER)
        self.assertNotEqual(one, other)

    def test_changed_configuration_cannot_rebind_an_identity_or_take_an_owned_account(self):
        key = self.create()
        local = self.host.ensure_account(next(iter(self.host.accounts)))
        before, disk = self.host.snapshot(), self.store.load()
        for issuer, bindings in ((ISSUER, {USER: local}), (ISSUER, {SECOND: key}),
                                 ("https://other.supabase.co/auth/v1", {USER: key})):
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                import_bindings(self.host, issuer, bindings)
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.store.load(), disk)

    def test_allocation_skips_configured_service_identity(self):
        from tmserver.protocol.wire import polid_scramble

        reserved = polid_scramble(HOSTED_RAW_BASE + 129)
        self.host.event_routes[reserved] = ("fixture", "room")
        key = self.create()
        self.assertNotEqual(key.encode(), reserved)
        self.assertEqual(self.host.economy.account_view(key).player_id, 130)

    def test_import_refuses_connected_legacy_account_without_changing_ownership(self):
        nick = next(iter(self.host.accounts))
        key = self.host.ensure_account(nick)
        before, disk = self.host.snapshot(), self.store.load()
        self.host.nicks[nick] = object()
        with self.assertRaisesRegex(ValueError, "disconnect"):
            import_bindings(self.host, ISSUER, {USER: key})
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.store.load(), disk)

    def test_precommit_failure_publishes_neither_account_nor_owner(self):
        before = self.host.snapshot()
        specs = dict(self.host.account_specs)
        with patch.object(self.host.store, "save_changes", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(self.host.snapshot(), before)
        self.assertEqual(self.host.account_specs, specs)
        self.assertIsNone(self.store.load())
        self.assertIsNotNone(self.host.storage_failed)

    def test_lost_commit_ack_reloads_one_account_before_retry(self):
        save = self.host.store.save_changes
        before = self.host.snapshot()

        def lost_ack(accounts, services):
            save(accounts, services)
            raise OSError("commit acknowledgement lost")

        with patch.object(self.host.store, "save_changes", side_effect=lost_ack), self.assertRaises(OSError):
            self.create()
        self.assertEqual(self.host.snapshot(), before)
        self.host = Server(store=self.store)
        key = self.host.identities.account(ISSUER, USER)
        with patch.object(self.host.store, "save_changes", side_effect=AssertionError("retry wrote")):
            self.assertEqual(self.create(), key)
        self.assertEqual(len(self.host.account_keys), 1)
        self.assertEqual(len(self.host.economy.inventory(key)), 10)

    def test_restore_rejects_orphans_duplicate_owners_and_noncanonical_identity(self):
        key = self.create()
        saved = self.host.snapshot()
        for change in (
            dict(account_key="missing"), dict(subject="not-a-uuid"), dict(issuer="http://fixture/auth/v1"),
            dict(issuer="https://fixture.supabase.co/auth/v1?x=y"),
        ):
            altered = deepcopy(saved)
            altered["identities"]["links"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_snapshot(altered)
        for subject in (USER, SECOND):
            altered = deepcopy(saved)
            altered["identities"]["links"].append(dict(altered["identities"]["links"][0], subject=subject))
            with self.assertRaises(ValueError):
                validate_snapshot(altered)
        altered = deepcopy(saved)
        altered["accounts"][key]["nn"] += 1
        with self.assertRaisesRegex(ValueError, "native player"):
            validate_snapshot(altered)
        self.assertEqual(IdentityRegistry.restore(self.host.economy, None).snapshot(),
                         dict(version=1, links=[]))
