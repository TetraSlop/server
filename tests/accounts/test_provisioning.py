"""Self-registration stores a username and scrypt hash with its account in one commit."""

import json
import re
import unittest
from unittest.mock import patch

from tests.support.memory_store import MemoryStore
from tmserver.accounts.credentials import (
    CredentialRegistry, Limiter, hash_password, valid_password, valid_username, verify_password,
)
from tmserver.accounts.provisioning import MAX_REGISTERED_RAW, MIN_REGISTERED_RAW, provision_account
from tmserver.network.server import Server
from tmserver.protocol.identities import player_id
from tmserver.protocol.wire import polid_from_string, polid_scramble
from tmserver.storage.validation import validate_snapshot

USERNAME = b"Alice"
ADDRESS = "203.0.113.5"
GRANTED = re.compile(rb"^:srv 900 (\w{8}) ([0-9a-f]{14}) :(created|signed_in)$")


def granted(reply):
    """POL ID, account key, game password and outcome from a 900 reply."""
    match = GRANTED.match(reply)
    assert match, reply
    pol_id = match.group(1)
    key = polid_scramble(polid_from_string(pol_id)).decode("ascii")
    return pol_id, key, match.group(2), match.group(3)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()
        self.host = self.restart()

    def restart(self):
        self.host = Server(store=self.store)
        self.addAsyncCleanup(self.host.stop)
        return self.host

    async def login(self, password=b"secret pass", username=USERNAME, address=ADDRESS):
        return await self.host.login(address, username, password)

    async def test_first_login_registers_and_later_logins_must_match_the_hash(self):
        pol_id, key, game_password, outcome = granted(await self.login())
        self.assertEqual(outcome, b"created")
        raw = polid_from_string(pol_id)
        self.assertTrue(MIN_REGISTERED_RAW <= raw <= MAX_REGISTERED_RAW)
        saved = self.store.load()
        account = saved["accounts"][key]
        self.assertEqual((len(account["cards"]), account["balance"]), (10, 10000))
        self.assertEqual(bytes.fromhex(account["name_hex"]), USERNAME)
        # The client sends the raw POL ID as GID and derives NN from it, like local accounts.
        self.assertEqual(account["player_id"], raw)
        self.assertEqual(account["nn"], player_id(key.encode()))
        credential = saved["identities"]["credentials"][key]
        self.assertEqual((saved["identities"]["version"], credential["username"]), (2, "Alice"))
        self.assertFalse(credential["disabled"])
        self.assertNotIn("secret pass", json.dumps(saved))
        self.assertNotIn(game_password.decode(), json.dumps(saved))
        validate_snapshot(saved)
        self.assertEqual(self.host.accounts[key.encode()], game_password)
        self.assertEqual(self.host.account_specs[key][:2], (pol_id, game_password))

        self.restart()
        self.assertNotIn(key.encode(), self.host.accounts)  # Game passwords are never saved.
        again_pol_id, again_key, again, outcome = granted(await self.login(username=b"ALICE"))
        self.assertEqual((again_pol_id, again_key, outcome), (pol_id, key, b"signed_in"))
        self.assertNotEqual(again, game_password)
        self.assertEqual(await self.login(b"wrong"), b":srv 901 invalid_credentials :login refused")
        self.assertEqual(self.store.load()["accounts"], saved["accounts"])

    async def test_formats_follow_the_client_limits(self):
        for username in (b"", b"x" * 16, b"two words", b"tab\t", "café".encode(), b"a:b", b"*"):
            with self.subTest(username=username):
                self.assertEqual(await self.login(username=username),
                                 b":srv 901 invalid_request :login refused")
        for password in (b"", b"x" * 16, b"tab\there", b"del\x7f", "café".encode()):
            with self.subTest(password=password):
                self.assertEqual(await self.login(password),
                                 b":srv 901 invalid_request :login refused")
        self.assertTrue(valid_password(b" " * 15) and valid_username(b"A.b-c_9" + b"x" * 8))
        self.assertIsNone(self.store.load())

    async def test_assigned_pol_ids_skip_taken_keys(self):
        taken = polid_scramble(MIN_REGISTERED_RAW).decode("ascii")
        self.host.account_keys.add(taken)
        draws = iter((0, 0, 7))
        with patch("tmserver.accounts.provisioning.secrets.randbelow", lambda _: next(draws)):
            pol_id = granted(await self.login())[0]
        self.assertEqual(polid_from_string(pol_id), MIN_REGISTERED_RAW + 7)

    async def test_failed_logins_and_registrations_are_limited(self):
        granted(await self.login())
        for _ in range(5):
            self.assertIn(b"invalid_credentials", await self.login(b"wrong", address="198.51.100.1"))
        # The username is locked out from every address, even with its password.
        self.assertIn(b"rate_limited", await self.login(username=b"alice", address="198.51.100.2"))
        for _ in range(20):
            self.host.address_failures.hit("192.0.2.9")
        self.assertIn(b"rate_limited", await self.login(username=b"bob", address="192.0.2.9"))
        for index in range(1, 5):  # The first login above was this address's first account.
            granted(await self.login(username=b"user%d" % index))
        self.assertIn(b"rate_limited", await self.login(username=b"user5"))
        granted(await self.login(username=b"user5", address="192.0.2.1"))

    async def test_disabled_and_connected_accounts_are_refused(self):
        key = granted(await self.login())[1]
        self.host.nicks[key.encode()] = object()
        self.assertIn(b"account_connected", await self.login())
        del self.host.nicks[key.encode()]
        disabled = self.host.credentials.get(key).model_copy(update={"disabled": True})
        staged = self.host.credentials.stage_copy()
        staged.put(key, disabled)
        self.host.credentials.publish_staged(staged)
        self.assertIn(b"account_locked", await self.login())

    async def test_storage_failure_creates_neither_account_nor_credential(self):
        with patch.object(self.store, "save_changes", side_effect=OSError("injected")):
            self.assertEqual(await self.login(), b":srv 901 unavailable :login refused")
        self.assertIsNone(self.host.credentials.account("alice"))
        self.assertEqual(self.host.account_keys, set())

    async def test_retired_identity_links_restore_and_are_replaced_on_the_next_write(self):
        old = polid_scramble((1 << 40) + 129).decode("ascii")
        with self.host.economy.edit_accounts(()) as accounts:
            provision_account(accounts, old, 129, b"Discord")
            self.host.persist_changes(accounts.snapshot()["accounts"], {"identities": {
                "version": 1, "links": [{"issuer": "https://example.supabase.co/auth/v1",
                                         "subject": "00000000-0000-4000-8000-000000000001",
                                         "account_key": old}]}})
        self.restart()
        self.assertEqual(self.host.credentials.snapshot(), {"version": 2, "credentials": {}})
        key = granted(await self.login())[1]
        saved = self.store.load()
        self.assertEqual(list(saved["identities"]["credentials"]), [key])
        self.assertIn(old, saved["accounts"])  # The account is kept; it cannot sign in.


class CredentialTests(unittest.TestCase):
    def test_hash_checks_in_constant_time_and_rejects_tampering(self):
        credential = hash_password("alice", b"secret", salt=bytes(16))
        self.assertTrue(verify_password(credential, b"secret"))
        self.assertFalse(verify_password(credential, b"Secret"))
        with patch("hmac.compare_digest", return_value=False) as compare:
            self.assertFalse(verify_password(credential, b"secret"))
        compare.assert_called_once()
        restored = CredentialRegistry.restore(None, {"version": 2, "credentials": {}})
        self.assertEqual(restored.snapshot(), {"version": 2, "credentials": {}})
        with self.assertRaises(ValueError):
            CredentialRegistry.restore(None, {"version": 2, "credentials": {
                "UGITW5T4U": {"username": "a", "salt": "00", "hash": "00"}}})
        with self.assertRaisesRegex(ValueError, "duplicate username"):
            CredentialRegistry({"A": credential, "B": hash_password("ALICE", b"x", bytes(16))})

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
