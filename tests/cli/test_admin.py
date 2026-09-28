"""Offline administration uses real disposable saves and existing claim validators."""

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
from tmserver.cli import admin
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.models import MAX_MONEY
from tmserver.economy.service import EconomyService
from tmserver.economy.packets import named, parse_named
from tmserver.events.cards import EventCardService
from tmserver.events.config import parse_event_setup
from tmserver.events.service import EventHost
from tmserver.economy.prize_center import PrizeService
from tmserver.rankings.policy import RatingPolicy
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.storage.validation import validate_snapshot
from tmserver.storage.base import SERVICE_NAMES
from tmserver.storage.json import JsonStateStore, writer_lease


class AdminTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.json"
        self.events_path = Path(directory.name) / "events.json"
        self.economy = EconomyService(seed=37)
        for nn, key in enumerate(("alice", "bob"), 1):
            self.economy.provision(key, nn=nn, player_id=nn, name=b"Same Name")
        self.seed(self.economy.snapshot())

    def seed(self, snapshot):
        JsonStateStore(self.path).save(snapshot)

    def load(self):
        return JsonStateStore(self.path).load()

    def argv(self, *command):
        args = ["--state", str(self.path)]
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

    def prize(self, key="alice", award_id="award-1", amount=500):
        return ("prize-award", "--account-key", key, "--id", award_id, "--top30", str(amount))

    def grant(self, key="alice", amount=75, card=None):
        args = [
            "event-grant",
            "--account-key",
            key,
            "--event",
            "festival",
            "--id",
            "grant-1",
            "--money",
            str(amount),
        ]
        if card is not None:
            args += ["--card", card]
        return args

    def eligibility(self, value=-1, key="alice"):
        return (
            "event-eligibility",
            "--account-key",
            key,
            "--event",
            "festival",
            "--value",
            str(value),
        )

    def test_target_is_explicit_and_unknown_accounts_are_never_created(self):
        with patch.dict(os.environ, {"TM_DATABASE_URL": "postgresql://unused"}):
            for argv in (
                ["accounts"],
                ["--state", str(self.path), "--database-url", "x", "accounts"],
            ):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    admin.arguments(argv)
        before = self.path.read_bytes()
        for key in ("Same Name", "ALICE", "missing"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.execute(*self.prize(key=key))
            self.assertEqual(self.path.read_bytes(), before)
        missing = self.path.with_name("missing.json")
        with self.assertRaisesRegex(ValueError, "existing saved"):
            admin.execute(admin.arguments(["--state", str(missing), "accounts"]))
        self.assertFalse(missing.exists())

    def test_accounts_are_read_only_and_names_are_safely_escaped(self):
        snapshot = self.load()
        snapshot["accounts"]["alice"]["name_hex"] = b"A\n\x1b[31m".hex()
        self.seed(snapshot)
        before = self.path.read_bytes()
        output = io.StringIO()
        with (
            redirect_stdout(output),
            patch.object(JsonStateStore, "save_changes", side_effect=AssertionError("read wrote")),
        ):
            admin.cli(self.argv("accounts"))
        rows = json.loads(output.getvalue())
        self.assertEqual([row["account_key"] for row in rows], ["alice", "bob"])
        self.assertEqual(rows[0]["name"], "A\n\x1b[31m")
        self.assertNotIn("\x1b", output.getvalue())
        self.assertEqual(self.path.read_bytes(), before)

    def test_prize_queues_once_then_replay_after_redemption_keeps_points(self):
        result = self.execute(*self.prize())
        self.assertEqual(result, dict(account_key="alice", id="award-1", status="queued"))
        snapshot = self.load()
        self.assertEqual(set(snapshot), {"version", "accounts", "prize"})
        self.assertEqual(snapshot["accounts"], self.economy.snapshot()["accounts"])
        before = self.path.read_bytes()
        # Existing Prize Center receipts retain IDs, not historical award contents.
        self.assertEqual(self.execute(*self.prize(amount=900))["status"], "already-recorded")
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "pending award"):
            self.execute(*self.prize(award_id="another"))
        economy = validate_snapshot(snapshot)
        prize = PrizeService.restore(economy, snapshot["prize"])
        view = economy.account_view("alice")
        prize.handle(
            "alice",
            named(0x70, 0, 0, f"@CvReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"),
        )
        snapshot.update(economy.snapshot(), prize=prize.snapshot())
        self.seed(snapshot)
        before = self.path.read_bytes()
        self.assertEqual(self.execute(*self.prize())["status"], "already-recorded")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(validate_snapshot(self.load()).prize_points("alice"), 500)

    def test_account_summary_is_exact_read_only_and_preserves_null_journals(self):
        snapshot = self.load()
        snapshot.update({name: None for name in SERVICE_NAMES})
        self.seed(snapshot)
        before = self.path.read_bytes()
        with patch.object(JsonStateStore, "save_changes", side_effect=AssertionError("read wrote")):
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
        self.assertEqual(self.path.read_bytes(), before)

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
        before = self.path.read_bytes()
        with patch.object(JsonStateStore, "save_changes", side_effect=AssertionError("read wrote")):
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
        self.assertEqual(self.path.read_bytes(), before)

    def test_prize_bounds_use_domain_validation_before_any_save(self):
        before = self.path.read_bytes()
        for flags in (
            ("--top30", "65536"),
            ("--lucky", "-1"),
            ("--rookies", "65536"),
            ("--lucky-cards", "1,2,250"),
            ("--id", "bad/id"),
        ):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.execute(*self.prize(), *flags)
            self.assertEqual(self.path.read_bytes(), before)
        result = self.execute(
            *self.prize(amount=65535),
            "--lucky",
            "65535",
            "--rookies",
            "65535",
            "--lucky-cards",
            "0,249,65535",
        )
        self.assertEqual(result["status"], "queued")

    def test_event_grants_require_exact_membership_and_replay_exact_contents(self):
        configs = self.enable_events()
        card = self.economy.inventory("alice")[0]
        card_arg = ",".join(map(str, card.reduced()))
        before = self.load()
        with self.assertRaisesRegex(ValueError, "member"):
            self.execute(*self.grant(key="bob", card=card_arg))
        self.assertEqual(self.load(), before)
        self.assertEqual(self.execute(*self.grant(card=card_arg))["status"], "queued")
        after = self.load()
        self.assertEqual(set(after), set(before) | {"event_cards"})
        self.assertEqual(after["events"], before["events"])
        self.assertEqual(after["accounts"], before["accounts"])
        grant = after["event_cards"]["grants"][0]
        self.assertEqual(grant["cards"], [list(card.full())])
        self.assertEqual((grant["money"], grant["card_offset"], grant["money_claimed"]), (75, 0, 0))
        self.assertEqual(self.execute(*self.grant(card=card_arg))["status"], "already-recorded")
        with self.assertRaisesRegex(ValueError, "replay differs"):
            self.execute(*self.grant(amount=76, card=card_arg))
        self.assertEqual(self.load(), after)
        validate_snapshot(after, event_configs=configs)

    def test_invalid_last_card_or_excessive_batch_and_money_are_atomic(self):
        self.enable_events()
        card = ",".join(map(str, self.economy.inventory("alice")[0].reduced()))
        before = self.path.read_bytes()
        cases = [
            self.grant(card=card) + ["--card", invalid]
            for invalid in (
                "250,1,0,1,1,1",
                "0,256,0,1,1,1",
                "0,1,4,1,1,1",
                "0,1,0,1,1,256",
                "0,0,0,0,0,1",
            )
        ]
        cases += [
            self.grant() + [item for _ in range(31) for item in ("--card", card)],
            self.grant(amount=-1),
            self.grant(amount=100000000),
        ]
        for argv in cases:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                self.execute(*argv)
            self.assertEqual(self.path.read_bytes(), before)
        self.execute(
            *self.grant(amount=99999999), *[item for _ in range(30) for item in ("--card", card)]
        )
        self.assertEqual(len(self.load()["event_cards"]["grants"][0]["cards"]), 30)

    def test_eligibility_changes_only_existing_member_status(self):
        self.enable_events()
        before = self.load()
        for value, status in ((-1, "updated"), (-1, "unchanged"), (0, "updated")):
            self.assertEqual(self.execute(*self.eligibility(value))["status"], status)
            after = self.load()
            expected = copy.deepcopy(before)
            expected["events"]["events"][0]["members"][0]["eligibility"] = value
            self.assertEqual(after, expected)
        for argv in (
            self.eligibility(key="bob"),
            self.eligibility(1 << 31),
            self.eligibility(-(1 << 31) - 1),
        ):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                self.execute(*argv)
            self.assertEqual(self.load(), before)

    def test_reused_admin_persists_reversions_and_preserves_unrelated_journals(self):
        configs = self.enable_events()
        original = self.load()
        original.update(prize=None, event_results=None)
        self.seed(original)
        store = JsonStateStore(self.path)
        state = admin.AdminState(store, store.load(), configs)
        previous = 0
        for value in (-1, -1, 0, 0, -1):
            with self.subTest(value=value, previous=previous):
                args = admin.arguments(self.argv(*self.eligibility(value)))
                result = state.apply(args)
                self.assertEqual(result["status"], "unchanged" if value == previous else "updated")
                expected = copy.deepcopy(original)
                expected["events"]["events"][0]["members"][0]["eligibility"] = value
                self.assertEqual(self.load(), expected)
                self.assertEqual(state.events.member("alice", "festival").eligibility, value)
            previous = value

    def test_reused_admin_failed_save_does_not_advance_comparison_or_publish(self):
        configs = self.enable_events()
        store = JsonStateStore(self.path)
        state = admin.AdminState(store, store.load(), configs)
        excluded = admin.arguments(self.argv(*self.eligibility(-1)))
        included = admin.arguments(self.argv(*self.eligibility(0)))
        state.apply(excluded)
        saved = self.path.read_bytes()
        # Reject before entering the backend; it has not failed or committed.
        with patch.object(store, "save_changes", side_effect=OSError("rejected before save")):
            with self.assertRaisesRegex(OSError, "rejected before save"):
                state.apply(included)
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(state.events.member("alice", "festival").eligibility, -1)
        self.assertEqual(state.apply(included)["status"], "updated")
        self.assertEqual(self.load()["events"], state.events.snapshot())
        self.assertEqual(state.events.member("alice", "festival").eligibility, 0)

    def test_explicit_progress_replays_exactly_and_preserves_rewards_and_accounts(self):
        self.enable_events()
        self.execute(*self.prize())
        self.execute(*self.grant())
        before = self.load()
        command = (
            "event-progress",
            "--account-key",
            "alice",
            "--event",
            "festival",
            "--id",
            "manual-1",
            "--steps",
            "2",
            "--completed-missions",
            "1",
        )
        self.assertEqual(self.execute(*command)["status"], "recorded")
        saved = self.load()
        self.assertEqual(
            {k: v for k, v in saved.items() if k != "events"},
            {k: v for k, v in before.items() if k != "events"},
        )
        summary = self.execute("account", "--account-key", "alice")["events"][0]
        self.assertEqual((summary["steps"], summary["completed_missions"]), (2, 1))
        self.assertEqual(summary["pending"], dict(cards=0, money=75))
        unchanged = self.path.read_bytes()
        self.assertEqual(self.execute(*command)["status"], "already-recorded")
        with self.assertRaisesRegex(ValueError, "different facts"):
            self.execute(*command, "--steps", "3")
        self.assertEqual(self.path.read_bytes(), unchanged)
        self.execute(*command, "--id", "manual-2", "--steps", "1", "--completed-missions", "0")
        member = self.execute("account", "--account-key", "alice")["events"][0]
        self.assertEqual((member["steps"], member["completed_missions"]), (3, 1))
        before_failure = self.path.read_bytes()
        with patch.object(JsonStateStore, "save_changes", side_effect=OSError("save failed")):
            with self.assertRaises(OSError):
                self.execute(*command, "--id", "manual-3", "--steps", "1")
        self.assertEqual(self.path.read_bytes(), before_failure)
        self.assertEqual(
            self.execute(*command, "--id", "manual-3", "--steps", "1")["status"], "recorded"
        )

    def test_progress_rejects_invalid_facts_missing_members_and_excluded_members(self):
        self.enable_events()
        command = ["event-progress", "--account-key", "alice", "--event", "festival", "--id", "one"]
        before = self.path.read_bytes()
        for flags in (
            ("--steps", "-1"),
            ("--steps", str(1 << 31)),
            ("--completed-missions", "4"),
            ("--completed-missions", "-1"),
            ("--account-key", "bob"),
            ("--event", "missing"),
            ("--id", "invalid/id"),
        ):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.execute(*command, *flags)
            self.assertEqual(self.path.read_bytes(), before)
        self.execute(*self.eligibility(-1))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "excluded"):
            self.execute(*command, "--steps", "1")
        self.assertEqual(self.path.read_bytes(), before)

    def test_event_configuration_must_match_saved_ids_and_fingerprints(self):
        self.enable_events()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            admin.execute(admin.arguments(["--state", str(self.path), "accounts"]))
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
            self.assertEqual(self.path.read_bytes(), before)

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
        self.assertEqual({k: v for k, v in after.items() if k != "prize"}, snapshot)
        self.assertTrue(after["result_rewards"]["pending"])

    def test_save_failures_produce_no_success_or_partial_state(self):
        self.enable_events()
        before = self.path.read_bytes()
        for command in (self.prize(), self.grant(), self.eligibility()):
            output = io.StringIO()
            with (
                redirect_stdout(output),
                patch.object(Path, "replace", side_effect=OSError("injected save failure")),
                self.assertRaises(SystemExit),
            ):
                admin.cli(self.argv(*command))
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.execute(*self.prize())["status"], "queued")

    def test_json_busy_lease_blocks_before_load_and_releases_after_failure(self):
        before = self.path.read_bytes()
        with (
            writer_lease(self.path),
            patch.object(JsonStateStore, "load", side_effect=AssertionError("busy read")),
            self.assertRaises(OSError),
        ):
            self.execute(*self.prize())
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.execute(*self.prize(key="missing"))
        self.assertEqual(self.execute(*self.prize())["status"], "queued")

    def test_postgres_selection_owns_store_context_and_uses_same_atomic_changes(self):
        calls = []

        @contextmanager
        def store(url):
            calls.append(("open", url))
            try:
                yield JsonStateStore(self.path)
            finally:
                calls.append(("close", url))

        args = admin.arguments(["--database-url", "postgresql://fixture", *self.prize()])
        with (
            patch.object(admin, "postgres_store", store),
            patch.object(admin, "writer_lease", side_effect=AssertionError("wrong backend")),
        ):
            self.assertEqual(admin.execute(args)["status"], "queued")
        self.assertEqual(
            calls, [("open", "postgresql://fixture"), ("close", "postgresql://fixture")]
        )
        self.assertIn("prize", self.load())

    def test_json_command_and_help_run_without_site_packages_or_server_import(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        code = (
            "from tmserver.cli.admin import cli; cli(); import sys; "
            "assert not any(k in sys.modules for k in "
            "('sqlalchemy', 'alembic', 'psycopg', 'tmserver.network.server'))"
        )
        for args in (
            ["--help"],
            self.argv("account", "--account-key", "alice"),
            self.argv("recovery"),
            self.argv(*self.prize()),
        ):
            result = subprocess.run(
                [sys.executable, "-S", "-B", "-c", code, *args],
                env=environment,
                cwd=self.path.parent,
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["status"], "queued")

    def test_snapshot_validation_public_imports_remain_compatible(self):
        try:
            from tmserver.storage import database
            from tmserver.storage import postgres as postgres_store
        except ImportError:
            self.skipTest("optional database dependencies absent")
        from tmserver.storage.validation import ACCOUNT_FIELDS

        self.assertIs(database.validate_snapshot, validate_snapshot)
        self.assertIs(postgres_store.ACCOUNT_FIELDS, ACCOUNT_FIELDS)

    def test_null_optional_journals_remain_unchanged_until_that_service_is_edited(self):
        snapshot = self.load()
        snapshot.update({name: None for name in SERVICE_NAMES})
        self.seed(snapshot)
        before = self.path.read_bytes()
        self.assertEqual(len(self.execute("accounts")), 2)
        self.assertEqual(self.path.read_bytes(), before)
        self.execute(*self.prize())
        saved = self.load()
        self.assertEqual(saved["accounts"], snapshot["accounts"])
        self.assertEqual(set(saved), set(snapshot))
        self.assertTrue(all(saved[name] is None for name in SERVICE_NAMES - {"prize"}))
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
                self.path.write_text(json.dumps(snapshot), encoding="utf8")
                before = self.path.read_bytes()
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit):
                    admin.cli(self.argv("accounts"))
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(self.path.read_bytes(), before)
