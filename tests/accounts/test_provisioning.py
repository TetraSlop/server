"""Self-registration stores a scrypt hash with its account in one commit."""

import json
import re
import unittest
from unittest.mock import patch

from tests.support.memory_store import MemoryStore
from tmserver.accounts.credentials import (
    CredentialRegistry, Limiter, hash_password, valid_password, valid_pol_id, verify_password,
)
from tmserver.accounts.provisioning import provision_account
from tmserver.network.server import Server
from tmserver.protocol.identities import player_id
from tmserver.protocol.wire import polid_from_string, polid_scramble, polid_to_string
from tmserver.storage.validation import validate_snapshot

POL_ID = b"EEKAOYMJ"
KEY = polid_scramble(polid_from_string(POL_ID)).decode("ascii")
ADDRESS = "203.0.113.5"
GRANTED = re.compile(rb"^:srv 900 (\w{8}) ([0-9a-f]{14}) :(created|signed_in)$")


def nth(index):
    return polid_to_string(polid_from_string(POL_ID) + index).encode()


def granted(reply):
    match = GRANTED.match(reply)
    assert match, reply
    return match.group(2), match.group(3)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.host = self.restart()

    def restart(self):
        self.host = Server(store=self.store)
        self.addAsyncCleanup(self.host.stop)
        return self.host

    async def login(self, password=b"secret pass", pol_id=POL_ID, address=ADDRESS):
        return await self.host.login(address, pol_id, password)

    async def test_first_login_registers_and_later_logins_must_match_the_hash(self):
        game_password, outcome = granted(await self.login())
        self.assertEqual(outcome, b"created")
        saved = self.store.load()
        account = saved["accounts"][KEY]
        self.assertEqual((len(account["cards"]), account["balance"]), (10, 10000))
        self.assertEqual(bytes.fromhex(account["name_hex"]), POL_ID)
        # The client sends the raw POL ID as GID and derives NN from it, like local accounts.
        self.assertEqual(account["player_id"], polid_from_string(POL_ID))
        self.assertEqual(account["nn"], player_id(KEY.encode()))
        credential = saved["identities"]["credentials"][KEY]
        self.assertEqual(saved["identities"]["version"], 2)
        self.assertFalse(credential["disabled"])
        self.assertNotIn("secret pass", json.dumps(saved))
        self.assertNotIn(game_password.decode(), json.dumps(saved))
        validate_snapshot(saved)
        self.assertEqual(self.host.accounts[KEY.encode()], game_password)
        self.assertEqual(self.host.account_specs[KEY][:2], (POL_ID, game_password))

        self.restart()
        self.assertNotIn(KEY.encode(), self.host.accounts)  # Game passwords are never saved.
        again, outcome = granted(await self.login())
        self.assertEqual(outcome, b"signed_in")
        self.assertNotEqual(again, game_password)
        self.assertEqual(await self.login(b"wrong"), b":srv 901 invalid_credentials :login refused")
        self.assertEqual(self.store.load()["accounts"], saved["accounts"])

    async def test_formats_follow_the_client_and_low_pol_ids_stay_reserved(self):
        for pol_id in (b"EKAOYMJ", b"EKAOYMJVV", b"ekaoymjv", b"EKAOYMJ!", b"55555555"):
            with self.subTest(pol_id=pol_id):
                self.assertEqual(await self.login(pol_id=pol_id),
                                 b":srv 901 invalid_request :login refused")
        for password in (b"", b"x" * 16, b"tab\there", b"del\x7f", "café".encode()):
            with self.subTest(password=password):
                self.assertEqual(await self.login(password),
                                 b":srv 901 invalid_request :login refused")
        self.assertTrue(valid_password(b" " * 15) and valid_pol_id(POL_ID))
        # The built-in EEEEEEEF account, and GIDs past the save's 32-bit player ID.
        for raw in (1, (1 << 16) - 1, 1 << 32, 0x1B015335048):
            with self.subTest(raw=raw):
                self.assertEqual(await self.login(pol_id=polid_to_string(raw).encode()),
                                 b":srv 901 pol_id_unavailable :login refused")
        granted(await self.login(pol_id=polid_to_string(0xFFFFFFFF).encode()))

    async def test_failed_logins_and_registrations_are_limited(self):
        granted(await self.login())
        for _ in range(5):
            self.assertIn(b"invalid_credentials", await self.login(b"wrong", address="198.51.100.1"))
        # The POL ID is locked out from every address, even with its password.
        self.assertIn(b"rate_limited", await self.login(address="198.51.100.2"))
        for _ in range(20):
            self.host.address_failures.hit("192.0.2.9")
        self.assertIn(b"rate_limited", await self.login(pol_id=nth(1), address="192.0.2.9"))
        for index in range(1, 5):  # The first login above was this address's first account.
            granted(await self.login(pol_id=nth(index)))
        self.assertIn(b"rate_limited", await self.login(pol_id=nth(5)))
        granted(await self.login(pol_id=nth(5), address="192.0.2.1"))

    async def test_disabled_connected_and_passwordless_accounts_are_refused(self):
        granted(await self.login())
        self.host.nicks[KEY.encode()] = object()
        self.assertIn(b"account_connected", await self.login())
        del self.host.nicks[KEY.encode()]
        disabled = self.host.credentials.get(KEY).model_copy(update={"disabled": True})
        self.host.credentials.publish_state(self.host.credentials.staged(KEY, disabled))
        self.assertIn(b"account_locked", await self.login())

    async def test_storage_failure_creates_neither_account_nor_credential(self):
        with patch.object(self.store, "save_changes", side_effect=OSError("injected")):
            self.assertEqual(await self.login(), b":srv 901 unavailable :login refused")
        self.assertIsNone(self.host.credentials.get(KEY))
        self.assertNotIn(KEY, self.host.account_keys)

    async def test_retired_identity_links_restore_and_their_accounts_need_a_password(self):
        old = polid_scramble((1 << 40) + 129).decode("ascii")
        with self.host.economy.edit_accounts(()) as accounts:
            provision_account(accounts, old, 129, b"Discord")
            self.host.persist_changes(accounts.snapshot()["accounts"], {"identities": {
                "version": 1, "links": [{"issuer": "https://example.supabase.co/auth/v1",
                                         "subject": "00000000-0000-4000-8000-000000000001",
                                         "account_key": old}]}})
        self.restart()
        self.assertEqual(self.host.credentials.snapshot(), {"version": 2, "credentials": {}})
        pol_id = polid_to_string((1 << 40) + 129).encode()
        self.assertEqual(await self.login(pol_id=pol_id),
                         b":srv 901 pol_id_unavailable :login refused")
        # A saved registrable account without a password waits for an operator.
        locked = polid_scramble(polid_from_string(nth(9))).decode("ascii")
        with self.host.economy.edit_accounts(()) as accounts:
            provision_account(accounts, locked, polid_from_string(nth(9)), b"Locked")
            self.host.persist_changes(accounts.snapshot()["accounts"], {})
        self.host.account_keys.add(locked)
        self.assertEqual(await self.login(pol_id=nth(9)), b":srv 901 account_locked :login refused")
        granted(await self.login())  # The next credential write replaces the old links.
        self.assertEqual(list(self.store.load()["identities"]["credentials"]), [KEY])


class CredentialTests(unittest.TestCase):
    def test_hash_checks_in_constant_time_and_rejects_tampering(self):
        credential = hash_password(b"secret", salt=bytes(16))
        self.assertTrue(verify_password(credential, b"secret"))
        self.assertFalse(verify_password(credential, b"Secret"))
        with patch("hmac.compare_digest", return_value=False) as compare:
            self.assertFalse(verify_password(credential, b"secret"))
        compare.assert_called_once()
        restored = CredentialRegistry.restore(None, {"version": 2, "credentials": {}})
        self.assertEqual(restored.snapshot(), {"version": 2, "credentials": {}})
        with self.assertRaises(ValueError):
            CredentialRegistry.restore(None, {"version": 2, "credentials": {
                KEY: {"salt": "00", "hash": "00"}}})

    def test_limiter_window_expires_and_key_count_is_bounded(self):
        now = [0.0]
        limiter = Limiter(2, 10, clock=lambda: now[0], max_keys=3)
        limiter.hit("a")
        limiter.hit("a")
        self.assertTrue(limiter.blocked("a"))
        now[0] = 10
        self.assertFalse(limiter.blocked("a"))
        for key in "bcde":
            limiter.hit(key)
        self.assertLessEqual(len(limiter.events), 3)


if __name__ == "__main__":
    unittest.main()
