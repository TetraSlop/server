"""Unknown save fields must survive a rejected startup, including recovery."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tests.support.memory_store import MemoryStore
from tmserver.profiles.characters import CharacterProfiles
from tmserver.economy.service import EconomyService
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.network.server import Server
from tmserver.storage.validation import SERVICE_NAMES


class StartupSnapshotValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.economy = EconomyService(seed=1)
        for number, key in enumerate(("a", "b"), 1):
            self.economy.provision(
                key, nn=number, player_id=number, name=key.encode(), balance=1000
            )

    def interrupted(self):
        matches = MatchEconomy(self.economy)
        tokens = {seat: self.economy.reserve_match(key) for seat, key in enumerate(("a", "b"))}
        matches.begin("interrupted", tokens, 100)
        return dict(self.economy.snapshot(), match_economy=matches.snapshot())

    def seed(self, snapshot):
        self.store.save(snapshot)
        return self.store.load()

    async def test_unknown_journal_rejects_before_recovery_without_rewriting_save(self):
        saved = dict(self.interrupted(), future_journal={"keep": 123})
        original = self.seed(saved)
        with self.assertRaisesRegex(ValueError, "unknown snapshot sections"):
            game = Server(store=self.store)
            self.addAsyncCleanup(game.stop)
        self.assertEqual(self.store.load(), original)
        self.assertEqual(self.store.load()["accounts"]["a"]["balance"], 900)

    async def test_unsafe_profile_float_rejects_before_recovery_without_rewriting_save(self):
        profiles = CharacterProfiles(self.economy)
        profiles.handle("a", b"GTM0P<CR>\x070x0000000000000001\x061\x07")
        saved = dict(self.interrupted(), character_profiles=profiles.snapshot())
        for value in ("0" * 128, "1" + "0" * 39):
            saved["character_profiles"]["records"]["a"][12] = value
            original = self.seed(saved)
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "profile float"):
                    game = Server(store=self.store)
                    self.addAsyncCleanup(game.stop)
                self.assertEqual(self.store.load(), original)
                self.assertEqual(self.store.load()["accounts"]["a"]["balance"], 900)

    async def test_unknown_account_field_rejects_even_without_pending_recovery(self):
        for recovering in (False, True):
            saved = self.interrupted() if recovering else self.economy.snapshot()
            saved["accounts"]["a"]["future_field"] = {"keep": 456}
            original = self.seed(saved)
            with self.subTest(recovering=recovering):
                with self.assertRaisesRegex(ValueError, "unsupported account fields"):
                    game = Server(store=self.store)
                    self.addAsyncCleanup(game.stop)
                self.assertEqual(self.store.load(), original)

    async def test_injected_store_obeys_same_boundary_before_any_save(self):
        saved = dict(self.interrupted(), future_journal={"keep": 123})
        store = Mock()
        store.load.return_value = copy.deepcopy(saved)
        with self.assertRaisesRegex(ValueError, "unknown snapshot sections"):
            game = Server(store=store)
            self.addAsyncCleanup(game.stop)
        store.save.assert_not_called()
        store.save_changes.assert_not_called()
        self.assertEqual(store.load.return_value, saved)

    async def test_older_absent_or_null_journals_remain_accepted_without_rewrite(self):
        for nulls in (False, True):
            saved = self.economy.snapshot()
            if nulls:
                saved.update(dict.fromkeys(SERVICE_NAMES))
            original = self.seed(saved)
            with self.subTest(nulls=nulls):
                game = Server(store=self.store)
                await game.stop()
                self.assertEqual(self.store.load(), original)
