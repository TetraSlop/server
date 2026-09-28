"""Incoming event admission cannot outrun the shared expiry poll."""

import unittest

from tests.events import test_event_cards as card_tests
from tests.events import test_event_matchmaking as match_tests
from tmserver.cards.catalog import Catalog
from tmserver.economy.packets import parse_named
from tmserver.events.models import EventError
from tmserver.game.packets import packet


class EventAdmissionExpiryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def fixture(self):
        fixture = match_tests.CoordinatorTests()
        fixture.catalog = self.catalog
        fixture.setUp()
        return fixture

    def prepare(self, stage):
        fixture = self.fixture()
        for key in ("1", "2"):
            fixture.admit(key)
        fixture.c.poll()
        fixture.consent("1")
        if stage == "proposal":
            return fixture
        fixture.consent("2", answer=int(stage != "backoff"))
        if stage in ("backoff", "GameEN"):
            return fixture
        for key in ("1", "2"):
            fixture.send(key, packet(0x41, 1, body=fixture.identity(key, "GameEN")))
        if stage == "Req":
            return fixture
        for key in ("1", "2"):
            fixture.send(key, packet(1, body=fixture.identity(key, "Req")))
        fixture.send("1", packet(0x41, 8, body="@GameOK="))
        return fixture

    def expire(self, fixture, clock):
        if clock == "schedule":
            fixture.time = fixture.cfg.end
        else:
            fixture.mono = fixture.c.snapshot("festival")["deadline"]

    def assert_cancelled(self, fixture, before, output):
        self.assertIsNone(fixture.c.snapshot("festival"))
        self.assertEqual(set(fixture.c.take_closures()), {"1", "2"})
        self.assertFalse(any(fixture.economy.is_reserved(key) for key in ("1", "2")))
        self.assertEqual(fixture.rooms.rooms[0].room.table_count, 0)
        self.assertEqual(fixture.economy.snapshot(), before)
        self.assertEqual(fixture.c.results(), ())
        self.assertFalse(any(b"@EventGameInit=" in delivery.payload for delivery in output))
        self.assertEqual(fixture.c.poll(), [])
        self.assertEqual(fixture.c.take_closures(), ())

    def test_final_game_ok_cannot_start_at_either_expiry_boundary(self):
        for clock in ("schedule", "admission"):
            with self.subTest(clock=clock):
                fixture = self.prepare("GameOK")
                before = fixture.economy.snapshot()
                self.expire(fixture, clock)
                output = fixture.send("2", packet(0x41, 8, body="@GameOK="))
                self.assert_cancelled(fixture, before, output)
                self.assertEqual(sum(b"@GameWa=/Error=-1" in d.payload for d in output), 2)

    def test_late_proposal_retry_and_table_admission_use_the_same_expiry(self):
        for clock in ("schedule", "admission"):
            for stage in ("proposal", "backoff", "GameEN", "Req"):
                with self.subTest(clock=clock, stage=stage):
                    fixture = self.prepare(stage)
                    before = fixture.economy.snapshot()
                    self.expire(fixture, clock)
                    if stage in ("proposal", "backoff"):
                        output = fixture.consent("2")
                    else:
                        primary, subtype = (0x41, 1) if stage == "GameEN" else (1, 0)
                        output = fixture.send(
                            "2", packet(primary, subtype, body=fixture.identity("2", stage))
                        )
                    self.assert_cancelled(fixture, before, output)
                    self.assertFalse(any(b"@MuchMake=/Start=1" in d.payload for d in output))

    def test_unrelated_or_unadmitted_request_cannot_cancel_waiting_round(self):
        fixture = self.prepare("GameOK")
        self.expire(fixture, "schedule")
        before = fixture.c.snapshot("festival"), fixture.economy.snapshot()
        self.assertIsNone(fixture.send("2", packet(0x41, 8, body="@GameOK="), target=123))
        with self.assertRaises(EventError):
            fixture.send("3", packet(0x41, 8, body="@GameOK="))
        self.assertEqual((fixture.c.snapshot("festival"), fixture.economy.snapshot()), before)
        self.assertEqual(fixture.c.take_closures(), ())

    def test_before_boundary_starts_and_playing_round_can_finish_after_event_end(self):
        fixture = self.prepare("GameOK")
        fixture.time = fixture.cfg.end - 1
        fixture.mono = fixture.c.snapshot("festival")["deadline"] - 0.01
        output = fixture.send("2", packet(0x41, 8, body="@GameOK="))
        self.assertTrue(any(b"@EventGameInit=" in d.payload for d in output))
        fixture.time = fixture.cfg.end
        before = fixture.c.snapshot("festival")
        self.assertEqual(fixture.c.poll(), [])
        self.assertEqual(fixture.c.snapshot("festival"), before)
        self.assertIsNotNone(fixture.c.observer_context(fixture.table.table_id))
        fixture.play()
        self.assertEqual(len(fixture.c.results()), 1)
        self.assertFalse(fixture.c.results()[0][2].aborted)
        self.assertEqual(fixture.c.take_closures(), ())

    def test_post_event_claim_reconnect_preserves_unclaimed_cards_without_recredit(self):
        fixture = card_tests.EventCardTests()
        fixture.catalog = self.catalog
        fixture.setUp()
        fixture.s.grant("a", "event", "expiry-test", cards=[fixture.card], money=25)
        fixture.now = fixture.config.end
        fixture.restart()
        first = fixture.entry()
        self.assertEqual(parse_named(first[1])[3]["EcmInit"]["PM"], "25")
        fixture.s.disconnect("a")
        fixture.restart()
        second = fixture.entry()
        self.assertEqual(parse_named(second[1])[3]["EcmInit"]["PM"], "0")
        fixture.get()
        fixture.quit()
        fixture.restart()
        self.assertEqual(fixture.e.balance("a"), 1025)
        self.assertEqual(len(fixture.e.inventory("a")), 11)
        self.assertEqual(fixture.s.pending("a", "event"), {"cards": 0, "money": 0})


if __name__ == "__main__":
    unittest.main()
