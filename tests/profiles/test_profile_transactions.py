"""Profile and account-creation publication follows successful persistence."""

import unittest
from unittest.mock import patch

from tests.profiles.test_character_profiles import request as profile_request
from tests.profiles.test_options_service import request as options_request
from tests.profiles.test_table_preferences import save as preferences_request
from tmserver.economy.service import named
from tmserver.profiles.options import save_options
from tmserver.profiles.transactions import character_profile_request
from tmserver.network.server import Server
from tmserver.profiles.preferences import parse_save
from tmserver.protocol.commands import Command, parse


class ProfileTransactionTests(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.nicks = tuple(self.server.accounts)
        self.key = self.server.ensure_account(self.nicks[0])

    def fails_unchanged(self, operation):
        before = self.server.snapshot()
        with (
            patch.object(self.server, "persist_changes", side_effect=OSError("profile write")),
            self.assertRaises(OSError),
        ):
            operation()
        self.assertEqual(self.server.snapshot(), before)

    def test_failed_account_creation_keeps_identity_cache_and_random_state(self):
        rng = self.server.economy.rng.getstate()
        self.fails_unchanged(lambda: self.server.ensure_account(self.nicks[1]))
        self.assertNotIn(self.nicks[1].decode(), self.server.account_keys)
        self.assertEqual(self.server.economy.rng.getstate(), rng)
        self.server.ensure_account(self.nicks[1])
        self.assertEqual(len(self.server.economy.inventory(self.nicks[1].decode())), 10)

    def test_character_profile_failure_preserves_opaque_fields_and_previous_record(self):
        view = self.server.economy.account_view(self.key)
        payload = profile_request(b"CR", view.nn, extras=(Command(b"AN", b"First"),))
        character_profile_request(self.server, self.key, payload)
        record = self.server.character_profiles.records[self.key]
        payload = profile_request(b"GR", view.nn, extras=(Command(b"AN", b"Changed"),))
        self.fails_unchanged(lambda: character_profile_request(self.server, self.key, payload))
        self.assertIs(self.server.character_profiles.records[self.key], record)

    def test_character_profile_read_still_finds_another_players_default_profile(self):
        other = self.server.ensure_account(self.nicks[1])
        view = self.server.economy.account_view(self.key)
        reply = character_profile_request(self.server, other, profile_request(b"PG", view.nn))[0]
        self.assertEqual(parse(reply[5:])[0].token, b"PO")
        self.assertIn(view.name, reply)

    def test_profile_reads_and_rejected_edits_do_not_write_to_storage(self):
        other = self.server.ensure_account(self.nicks[1])
        view = self.server.economy.account_view(self.key)
        before = self.server.snapshot()
        requests = (
            (profile_request(b"PG", view.nn), b"PO"),
            (profile_request(b"PG", 999999), b"PM"),
            (profile_request(b"CR", view.nn, extras=(Command(b"AN", b"Other"),)), b"NS"),
            (profile_request(b"GR", view.nn, extras=(Command(b"AN", b"Other"),)), b"GG"),
        )
        with patch.object(self.server, "persist_changes") as save:
            for request, token in requests:
                with self.subTest(token=token):
                    reply = character_profile_request(self.server, other, request)[0]
                    self.assertEqual(parse(reply[5:])[0].token, token)
                    save.assert_not_called()
        self.assertEqual(self.server.snapshot(), before)

    def test_identical_profile_retry_acknowledges_without_another_write(self):
        view = self.server.economy.account_view(self.key)
        request = profile_request(b"CR", view.nn, extras=(Command(b"AN", b"Alias"),))
        with patch.object(self.server, "persist_changes") as save:
            first = character_profile_request(self.server, self.key, request)
            save.assert_called_once()
            before = self.server.snapshot()
            self.assertEqual(character_profile_request(self.server, self.key, request), first)
            save.assert_called_once()
        self.assertEqual(self.server.snapshot(), before)

    def test_failed_options_save_keeps_bytes_and_current_reservation(self):
        token = self.server.economy.reserve_match(self.key)
        view = token.account
        before = self.server.economy.profile_bytes(self.key)
        with (
            patch.object(self.server.commerce, "save_accounts", side_effect=OSError("options")),
            self.assertRaises(OSError),
            self.server.commerce.edit_account(self.key) as staged,
        ):
            save_options(staged, self.key, options_request(nn=view.nn, name=view.name))
        self.assertEqual(self.server.economy.profile_bytes(self.key), before)
        self.assertIs(self.server.economy._reservations[self.key], token)

    def test_failed_guild_initialization_keeps_unregistered_profile(self):
        self.server.economy._accounts[self.key].guild = 0
        view = self.server.economy.account_view(self.key)
        payload = named(
            0x80,
            1,
            0,
            f"@Init=/NN={view.nn:016X}/HID=0/Dm=0/Vol=0/CN={view.name.hex()}/GID={view.player_id:016X}/L=0/GLD=2",
        )
        with (
            patch.object(self.server.commerce, "save_accounts", side_effect=OSError("guild")),
            self.assertRaises(OSError),
            self.server.commerce.edit_account(self.key) as staged,
        ):
            staged.initialize_profile(self.key, payload)
        self.assertEqual(self.server.economy.account_view(self.key).guild, 0)

    def test_failed_saved_table_settings_preserve_the_original_profile(self):
        before = self.server.economy.profile_bytes(self.key)
        with (
            patch.object(self.server.commerce, "save_accounts", side_effect=OSError("settings")),
            self.assertRaises(OSError),
            self.server.commerce.edit_account(self.key) as staged,
        ):
            staged.save_table_preferences(self.key, **parse_save(preferences_request()))
        self.assertEqual(self.server.economy.profile_bytes(self.key), before)
