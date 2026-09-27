"""Unknown save fields must survive a rejected startup, including recovery."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tmserver import local_play
from tmserver.character_profiles import CharacterProfiles
from tmserver.economy_service import EconomyService
from tmserver.match_economy import MatchEconomy
from tmserver.server import Server
from tmserver.state_store import SERVICE_NAMES, JsonStateStore


class StartupSnapshotValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "state.json"
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
        self.path.write_text(json.dumps(snapshot), encoding="utf8")
        return self.path.read_bytes()

    def play_args(self):
        args = local_play.arguments(
            ["--project-dir", str(self.root), "--state", str(self.path), "--check"]
        )
        args.loader_dir = self.root / "loader"
        args.loader = args.loader_dir / "tmloader.exe"
        args.config = args.loader_dir / "tmloader.ini"
        for path in (args.loader, args.config, self.root / "examples/auto_rooms.example.json"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test fixture", encoding="utf8")
        return args

    async def test_unknown_journal_rejects_before_recovery_without_rewriting_save(self):
        saved = dict(self.interrupted(), future_journal={"keep": 123})
        original = self.seed(saved)
        with self.assertRaisesRegex(ValueError, "unknown snapshot sections"):
            game = Server(state_path=self.path)
            self.addAsyncCleanup(game.stop)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(JsonStateStore(self.path).load()["accounts"]["a"]["balance"], 900)

    async def test_unsafe_profile_float_rejects_before_recovery_without_rewriting_save(self):
        profiles = CharacterProfiles(self.economy)
        profiles.handle("a", b"GTM0P<CR>\x070x0000000000000001\x061\x07")
        saved = dict(self.interrupted(), character_profiles=profiles.snapshot())
        for value in ("0" * 128, "1" + "0" * 39):
            saved["character_profiles"]["records"]["a"][12] = value
            original = self.seed(saved)
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "profile float"):
                    game = Server(state_path=self.path)
                    self.addAsyncCleanup(game.stop)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(JsonStateStore(self.path).load()["accounts"]["a"]["balance"], 900)

    async def test_unknown_account_field_rejects_even_without_pending_recovery(self):
        for recovering in (False, True):
            saved = self.interrupted() if recovering else self.economy.snapshot()
            saved["accounts"]["a"]["future_field"] = {"keep": 456}
            original = self.seed(saved)
            with self.subTest(recovering=recovering):
                with self.assertRaisesRegex(ValueError, "unsupported account fields"):
                    game = Server(state_path=self.path)
                    self.addAsyncCleanup(game.stop)
                self.assertEqual(self.path.read_bytes(), original)

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

    async def test_setup_rejects_invalid_journals_before_invoking_loader(self):
        args = self.play_args()
        child = AsyncMock(returncode=0)
        child.communicate.return_value = (b"loader check passed", None)
        for extra in (
            {"prize": {"version": 999}},
            {"ratings": {"version": 999}},
            {"result_rewards": []},
            {"future_journal": {"keep": 123}},
        ):
            original = self.seed(dict(self.economy.snapshot(), **extra))
            with (
                self.subTest(extra=extra),
                patch.object(
                    local_play.asyncio, "create_subprocess_exec", AsyncMock(return_value=child)
                ) as spawn,
                self.assertRaises(ValueError),
            ):
                await local_play.preflight(args)
            spawn.assert_not_awaited()
            self.assertEqual(self.path.read_bytes(), original)

    async def test_setup_preserves_interrupted_rewards_and_creates_no_listener(self):
        original = self.seed(self.interrupted())
        args = self.play_args()
        child = AsyncMock(returncode=0)
        child.communicate.return_value = (b"loader check passed", None)
        with (
            patch.object(
                local_play.asyncio, "create_subprocess_exec", AsyncMock(return_value=child)
            ) as spawn,
            patch.object(local_play, "running_services") as start,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(await local_play.run(args), 0)
        start.assert_not_called()
        spawn.assert_awaited_once()
        self.assertIn("--check", spawn.call_args.args)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.root / "var").exists())

    async def test_older_absent_or_null_journals_remain_accepted_without_rewrite(self):
        for nulls in (False, True):
            saved = self.economy.snapshot()
            if nulls:
                saved.update(dict.fromkeys(SERVICE_NAMES))
            original = self.seed(saved)
            with self.subTest(nulls=nulls):
                game = Server(state_path=self.path)
                await game.stop()
                self.assertEqual(self.path.read_bytes(), original)
