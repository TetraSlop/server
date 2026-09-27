"""Offline administrator commands against disposable PostgreSQL databases."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.test_event_configuration import DOCUMENT
from tests.test_postgres_store import TEST_URL, DatabaseFixture
from tmserver import admin
from tmserver.claim_transactions import prize_transaction
from tmserver.economy_service import EconomyService, named
from tmserver.event_configuration import parse_event_setup
from tmserver.event_service import EventHost
from tmserver.postgres_store import PostgresStateStore
from tmserver.rating_policy import RatingPolicy


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class AdminPostgresTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.events_path = Path(directory.name) / "events.json"
        self.economy = EconomyService(seed=37)
        for nn, key in enumerate(("alice", "bob"), 1):
            self.economy.provision(key, nn=nn, player_id=nn, name=key.encode())
        self.store.save(self.economy.snapshot())
        self.store.close()  # Each command owns its own real writer connection.

    def argv(self, *command):
        args = ["--database-url", self.url.render_as_string(hide_password=False)]
        if self.events_path.exists():
            args += ["--events", str(self.events_path)]
        return args + list(command)

    def execute(self, *command):
        output = io.StringIO()
        with redirect_stdout(output):
            admin.cli(self.argv(*command))
        return json.loads(output.getvalue())

    def saved(self):
        with PostgresStateStore(self.url) as store:
            return store.load()

    def prize(self, amount=500):
        return ("prize-award", "--account-key", "alice", "--id", "award-1", "--top30", str(amount))

    def test_prize_award_reload_and_replay_after_original_redemption(self):
        self.assertEqual(self.execute(*self.prize())["status"], "queued")
        before = self.saved()
        self.assertEqual(before["accounts"], self.economy.snapshot()["accounts"])
        self.assertEqual(self.execute(*self.prize(900))["status"], "already-recorded")
        self.assertEqual(self.saved(), before)
        with PostgresStateStore(self.url) as store:
            state = admin.AdminState(store, store.load(), ())
            view = state.economy.account_view("alice")
            request = named(
                0x70, 0, 0, f"@CvReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}"
            )
            with prize_transaction(state, "alice") as prize:
                prize.handle("alice", request)
        accepted = self.saved()
        self.assertEqual(EconomyService.restore(accepted).prize_points("alice"), 500)
        self.assertEqual(self.execute(*self.prize())["status"], "already-recorded")
        self.assertEqual(self.saved(), accepted)

    def test_event_grant_eligibility_and_configuration_preserve_other_journals(self):
        configs = parse_event_setup(DOCUMENT)[0]
        events = EventHost(self.economy, configs, clock=lambda: 200)
        events.admit_room("alice", "festival")
        ratings = RatingPolicy(
            self.economy, mode="local-history", weekly=True, clock=lambda: 1700000000
        )
        ratings.advance_week()
        baseline = self.economy.snapshot()
        baseline.update(events=events.snapshot(), ratings=ratings.snapshot())
        with PostgresStateStore(self.url) as store:
            store.save(baseline)
        self.events_path.write_text(json.dumps(DOCUMENT), encoding="utf8")
        card = self.economy.inventory("alice")[0]
        grant = (
            "event-grant",
            "--account-key",
            "alice",
            "--event",
            "festival",
            "--id",
            "grant-1",
            "--money",
            "75",
            "--card",
            ",".join(map(str, card.reduced())),
        )
        self.assertEqual(self.execute(*grant)["status"], "queued")
        self.assertEqual(self.execute(*grant)["status"], "already-recorded")
        queued = self.saved()
        self.assertEqual(queued["event_cards"]["grants"][0]["cards"], [list(card.full())])
        with self.assertRaises(SystemExit):
            self.execute(*grant, "--money", "76")
        self.assertEqual(self.saved(), queued)
        eligibility = (
            "event-eligibility",
            "--account-key",
            "alice",
            "--event",
            "festival",
            "--value",
        )
        for value in ("-1", "0"):
            self.assertEqual(self.execute(*eligibility, value)["status"], "updated")
            self.assertEqual(
                self.saved()["events"]["events"][0]["members"][0]["eligibility"], int(value)
            )
        after = self.saved()
        self.assertEqual(after["accounts"], baseline["accounts"])
        self.assertEqual(after["events"], baseline["events"])
        self.assertEqual(after["ratings"], baseline["ratings"])
        self.assertNotIn("event_results", after)
        invalid = copy.deepcopy(DOCUMENT)
        invalid["events"][0]["end"] += 1
        self.events_path.write_text(json.dumps(invalid), encoding="utf8")
        with self.assertRaises(SystemExit):
            self.execute(*eligibility, "-1")
        self.assertEqual(self.saved(), after)

    def test_active_writer_rejects_command_without_success_then_releases(self):
        with PostgresStateStore(self.url) as owner:
            before = owner.load()
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit):
                admin.cli(self.argv(*self.prize()))
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(owner.load(), before)
        self.assertEqual(self.execute(*self.prize())["status"], "queued")
