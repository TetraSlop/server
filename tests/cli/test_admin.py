"""Offline administration runs on a store stand-in and the existing claim validators."""

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.events.test_event_configuration import DOCUMENT
from tests.support.memory_store import DATABASE, MemoryStore, opened_by
from tmserver.cli import admin
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.models import MAX_MONEY
from tmserver.economy.service import EconomyService
from tmserver.economy.packets import named, parse_named
from tmserver.events.cards import EventCardService
from tmserver.events.config import parse_event_setup
from tmserver.events.service import EventHost
from tmserver.rankings.policy import RatingPolicy
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.storage.validation import SERVICE_NAMES


class AdminTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = MemoryStore()
        self.enterContext(opened_by("tmserver.cli.admin", self.store))
        self.directory = Path(directory.name)
        self.events_path = Path(directory.name) / "events.json"
        self.economy = EconomyService(seed=37)
        for nn, key in enumerate(("alice", "bob"), 1):
            self.economy.provision(key, nn=nn, player_id=nn, name=b"Same Name")
        self.seed(self.economy.snapshot())

    def seed(self, snapshot):
        self.store.save(snapshot)

    def load(self):
        return self.store.load()

    def argv(self, *command):
        args = list(DATABASE)
        if self.events_path.exists():
            args += ["--events", str(self.events_path)]
        return args + list(command)

    def execute(self, *command):
        return admin.execute(admin.arguments(self.argv(*command)))

    def enable_events(self):
        self.events_path.write_text(json.dumps(DOCUMENT), encoding="utf8")
        configs = parse_event_setup(DOCUMENT)[0]
        host = EventHost(self.economy, configs, clock=lambda: 200)
        host.admit_room("alice", "festival")
        snapshot = self.load()
        snapshot["events"] = host.snapshot()
        self.seed(snapshot)
        return configs

    def audited(self, command, request_id, *options):
        return (
            command, *options, "--request-id", request_id,
            "--operator", "fixture-operator", "--reason", "Fixture correction",
        )

    def prize(self, key="alice", award_id="award-1", amount=500):
        return self.audited(
            "prize-award", "prize-request", "--account-key", key, "--id", award_id,
            "--top30", str(amount),
        )

    def grant(self, key="alice", amount=75):
        return self.audited(
            "event-grant", "grant-request", "--account-key", key, "--event", "festival",
            "--id", "grant-1", "--money", str(amount),
        )

    def eligibility(self, value=-1, key="alice", request_id="eligibility-request"):
        return self.audited(
            "event-eligibility", request_id, "--account-key", key, "--event", "festival",
            "--value", str(value),
        )

    def test_operator_sets_disables_and_enables_a_login_password(self):
        from tmserver.accounts.credentials import Credential, hash_password, verify_password
        from tmserver.protocol.identities import player_id
        from tmserver.protocol.wire import polid_scramble

        KEY = polid_scramble(1 << 20).decode("ascii")
        self.economy.provision(KEY, nn=player_id(KEY.encode()), player_id=1 << 20, name=b"Alice")
        snapshot = self.economy.snapshot()
        snapshot["identities"] = {"version": 2, "credentials": {
            KEY: hash_password("Alice", b"old", bytes(16)).model_dump()}}
        self.seed(snapshot)
        with self.assertRaisesRegex(ValueError, "no login"):
            self.execute("account-disable", "--account-key", "alice")
        for password in ("", "x" * 16):
            with patch("sys.stdin", io.StringIO(password + "\n")), self.assertRaises(ValueError):
                self.execute("account-password", "--account-key", KEY)
        with patch("sys.stdin", io.StringIO("new pass\n")):
            result = self.execute("account-password", "--account-key", KEY)
        self.assertEqual(result, dict(account_key=KEY, username="Alice", disabled=False))
        saved = self.load()["identities"]["credentials"][KEY]
        self.assertTrue(verify_password(Credential(**saved), b"new pass"))
        self.assertFalse(verify_password(Credential(**saved), b"old"))
        listed = {row["account_key"]: row for row in self.execute("accounts")}
        self.assertEqual((listed[KEY]["username"], listed["alice"]["username"]), ("Alice", None))
        self.assertTrue(self.execute("account-disable", "--account-key", KEY)["disabled"])
        self.assertTrue(self.load()["identities"]["credentials"][KEY]["disabled"])
        self.assertFalse(self.execute("account-enable", "--account-key", KEY)["disabled"])
        self.assertEqual(self.load()["identities"]["credentials"][KEY], saved)

    def test_target_is_explicit_and_unknown_accounts_are_never_created(self):
        with patch.dict(os.environ, {}, clear=True):
            for argv in (["accounts"], ["--state", "save.json", *DATABASE, "accounts"]):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    admin.arguments(argv)
        before = self.load()
        for key in ("Same Name", "ALICE", "missing"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.execute(*self.prize(key=key))
            self.assertEqual(self.load(), before)
        missing = MemoryStore()
        with opened_by("tmserver.cli.admin", missing), self.assertRaisesRegex(ValueError, "existing saved"):
            self.execute("accounts")
        self.assertIsNone(missing.load())

    def test_accounts_are_read_only_and_names_are_safely_escaped(self):
        snapshot = self.load()
        snapshot["accounts"]["alice"]["name_hex"] = b"A\n\x1b[31m".hex()
        self.seed(snapshot)
        before = self.load()
        output = io.StringIO()
        with (
            redirect_stdout(output),
            patch.object(self.store, "save_changes", side_effect=AssertionError("read wrote")),
        ):
            admin.cli(self.argv("accounts"))
        rows = json.loads(output.getvalue())
        self.assertEqual([row["account_key"] for row in rows], ["alice", "bob"])
        self.assertEqual(rows[0]["name"], "A\n\x1b[31m")
        self.assertNotIn("\x1b", output.getvalue())
        self.assertEqual(self.load(), before)

    def test_account_summary_is_exact_read_only_and_preserves_null_journals(self):
        snapshot = self.load()
        snapshot.update({name: None for name in SERVICE_NAMES})
        self.seed(snapshot)
        before = self.load()
        with patch.object(self.store, "save_changes", side_effect=AssertionError("read wrote")):
            result = self.execute("account", "--account-key", "alice")
            self.assertEqual(result["account_key"], "alice")
            self.assertEqual(result["balance"], self.economy.balance("alice"))
            self.assertEqual(result["card_count"], 10)
            self.assertEqual(result["prize_points"], 0)
            self.assertIsNone(result["pending_prize"])
            self.assertEqual(result["events"], [])
            self.assertEqual(result["event_claims"], [])
            self.assertEqual(result["auction_claims"]["receipts"], [])
            self.assertIsNone(result["pending_result"])
            self.assertEqual(result["match_rounds"], [])
            for key in ("Same Name", "ALICE", "missing"):
                with self.assertRaises(ValueError):
                    self.execute("account", "--account-key", key)
        self.assertEqual(self.load(), before)

    def test_account_summary_reports_remaining_claims_after_partial_collection(self):
        configs = self.enable_events()
        snapshot = self.load()
        snapshot["accounts"]["alice"]["balance"] = MAX_MONEY - 25
        economy = EconomyService.restore(snapshot)
        host = EventHost.restore(economy, configs, snapshot["events"], clock=lambda: 200)
        cards = EventCardService(economy, host)
        cards.grant(
            "alice", "festival", "batch", cards=economy.inventory("alice")[:1] * 20, money=75
        )
        view = economy.account_view("alice")
        replies = cards.handle(
            "alice",
            named(0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"),
            event_id="festival",
        )
        number = int(parse_named(replies[1])[3]["EcmInit"]["N"])
        cards.handle("alice", named(0xB2, 0x15, number, f"@Get=/No={number}"))
        cards.disconnect("alice")
        pending = cards.pending("alice", "festival")
        self.assertEqual(pending["money"], 50)
        self.assertTrue(0 < pending["cards"] < 20)
        host.update_progress("alice", "festival", "round", steps_delta=2)
        host.set_eligibility("alice", "festival", -1)
        snapshot.update(economy.snapshot(), events=host.snapshot(), event_cards=cards.snapshot())
        self.seed(snapshot)
        self.execute(*self.prize())
        before = self.load()
        with patch.object(self.store, "save_changes", side_effect=AssertionError("read wrote")):
            result = self.execute("account", "--account-key", "alice")
            self.assertEqual(result["balance"], MAX_MONEY)
            self.assertEqual(result["card_count"], 30 - pending["cards"])
            self.assertEqual(result["pending_prize"]["id"], "award-1")
            self.assertEqual(result["pending_prize"]["top30"], 500)
            self.assertEqual(result["prize_points"], 0)
            self.assertEqual(result["event_claims"], [
                dict(event_id="festival", id="batch", **pending)
            ])
            self.assertEqual(
                result["events"],
                [
                    dict(
                        event_id="festival",
                        eligibility=-1,
                        steps=2,
                        completed_missions=0,
                        pending=pending,
                    )
                ],
            )
            other = self.execute("account", "--account-key", "bob")
            self.assertIsNone(other["pending_prize"])
            self.assertEqual(other["events"], [])
            self.assertEqual(other["event_claims"], [])
        self.assertEqual(self.load(), before)

    def test_reused_admin_saves_each_change_and_preserves_unrelated_journals(self):
        configs = self.enable_events()
        original = self.load()
        original.update(prize=None, event_results=None)
        self.seed(original)
        store = self.store
        state = admin.AdminState(store, store.load(), configs)
        for number, value in enumerate((-1, 0, -1)):
            with self.subTest(value=value):
                args = admin.arguments(self.argv(*self.eligibility(value, request_id=str(number))))
                self.assertEqual(state.apply(args)["after"]["eligibility"], value)
                saved = self.load()
                self.assertEqual(saved["events"]["events"][0]["members"][0]["eligibility"], value)
                self.assertEqual(len(saved["admin_audit"]["receipts"]), number + 1)
                for name in set(original) - {"events", "admin_audit"}:
                    self.assertEqual(saved[name], original[name])
                self.assertEqual(state.events.member("alice", "festival").eligibility, value)

    def test_reused_admin_failed_save_does_not_advance_comparison_or_publish(self):
        configs = self.enable_events()
        store = self.store
        state = admin.AdminState(store, store.load(), configs)
        state.apply(admin.arguments(self.argv(*self.eligibility(-1, request_id="exclude"))))
        included = admin.arguments(self.argv(*self.eligibility(0, request_id="include")))
        saved = self.load()
        # Reject before entering the backend; it has not failed or committed.
        with patch.object(store, "save_changes", side_effect=OSError("rejected before save")):
            with self.assertRaisesRegex(OSError, "rejected before save"):
                state.apply(included)
        self.assertEqual(self.load(), saved)
        self.assertEqual(state.events.member("alice", "festival").eligibility, -1)
        self.assertEqual(state.apply(included)["after"]["eligibility"], 0)
        self.assertEqual(self.load()["events"], state.events.snapshot())

    def test_event_configuration_must_match_saved_ids_and_fingerprints(self):
        self.enable_events()
        before = self.load()
        with self.assertRaises(ValueError):
            admin.execute(admin.arguments([*DATABASE, "accounts"]))
        changed = copy.deepcopy(DOCUMENT)
        changed["events"][0]["end"] += 1
        added = copy.deepcopy(DOCUMENT)
        extra = copy.deepcopy(added["events"][0])
        extra.update(id="other", room_raw=8192, init_raw=8193, card_raw=8194)
        added["events"].append(extra)
        for document in (changed, added):
            self.events_path.write_text(json.dumps(document), encoding="utf8")
            with self.assertRaises(ValueError):
                self.execute(*self.grant())
            self.assertEqual(self.load(), before)

    def test_pending_recovery_and_old_weekly_rankings_do_not_advance(self):
        auction = AuctionEconomy(self.economy)
        rewards = ResultRewardService(self.economy, auction)
        token = self.economy.reserve_match("bob")
        rewards.stage("bob", token, "unclaimed-result", 0, self.economy.inventory("bob")[:1])
        policy = RatingPolicy(
            self.economy, mode="local-history", weekly=True, clock=lambda: 1700000000
        )
        policy.advance_week()
        snapshot = self.load()
        snapshot.update(
            auction=auction.snapshot(), result_rewards=rewards.snapshot(), ratings=policy.snapshot()
        )
        self.seed(snapshot)
        self.execute("account", "--account-key", "bob")
        self.assertEqual(self.load(), snapshot)
        self.execute(*self.prize())
        after = self.load()
        self.assertEqual(
            {k: v for k, v in after.items() if k not in {"prize", "admin_audit"}}, snapshot
        )
        self.assertTrue(after["result_rewards"]["pending"])

    def test_save_failures_produce_no_success_or_partial_state(self):
        self.enable_events()
        before = self.load()
        for command in (self.prize(), self.grant(), self.eligibility()):
            output = io.StringIO()
            with (
                redirect_stdout(output),
                patch.object(self.store, "save_changes", side_effect=OSError("injected save failure")),
                self.assertRaises(SystemExit),
            ):
                admin.cli(self.argv(*command))
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(self.load(), before)
        self.assertEqual(self.execute(*self.prize())["status"], "queued")

    def test_command_owns_store_context_and_uses_same_atomic_changes(self):
        calls = []

        @contextmanager
        def store(url):
            calls.append(("open", url))
            try:
                yield self.store
            finally:
                calls.append(("close", url))

        args = admin.arguments(["--database-url", "postgresql://fixture", *self.prize()])
        with patch.object(admin, "open_store", store):
            self.assertEqual(admin.execute(args)["status"], "queued")
        self.assertEqual(
            calls, [("open", "postgresql://fixture"), ("close", "postgresql://fixture")]
        )
        self.assertIn("prize", self.load())

    def test_help_runs_without_database_or_server_import(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        code = (
            "import sys\n"
            "from tmserver.cli.admin import cli\n"
            "try:\n    cli()\n"
            "finally:\n"
            "    assert not any(k in sys.modules for k in "
            "('sqlalchemy', 'alembic', 'psycopg', 'tmserver.network.server'))"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, "--help"],
            env=environment,
            cwd=self.directory,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("--database-url", result.stdout)

    def test_null_optional_journals_remain_unchanged_until_that_service_is_edited(self):
        snapshot = self.load()
        snapshot.update({name: None for name in SERVICE_NAMES})
        self.seed(snapshot)
        before = self.load()
        self.assertEqual(len(self.execute("accounts")), 2)
        self.assertEqual(self.load(), before)
        self.execute(*self.prize())
        saved = self.load()
        self.assertEqual(saved["accounts"], snapshot["accounts"])
        self.assertEqual(set(saved), set(snapshot))
        self.assertTrue(
            all(saved[name] is None for name in SERVICE_NAMES - {"prize", "admin_audit"})
        )
        self.assertEqual(saved["prize"]["accounts"]["alice"]["known_awards"], ["award-1"])

    def test_malformed_snapshot_shapes_fail_cleanly_without_writes(self):
        valid = self.load()
        invalid = [
            None,
            [],
            {"version": 1},
            dict(valid, accounts=[]),
            dict(valid, accounts={"alice": []}),
        ]
        invalid += [dict(valid, **{name: []}) for name in SERVICE_NAMES]
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                self.store.save(snapshot)
                before = self.load()
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit):
                    admin.cli(self.argv("accounts"))
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(self.load(), before)
