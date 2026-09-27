"""Native B2 selection traffic must obey the coordinator's admission expiry."""

import unittest
from unittest.mock import patch

from tests import test_event_matchmaking as match_tests
from tmserver.economy_service import Catalog, EconomyError
from tmserver.event_service import EventError
from tmserver.match_service import packet


class EventSelectionExpiryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def fixture(self):
        fixture = match_tests.CoordinatorTests()
        fixture.catalog = self.catalog
        fixture.setUp()
        fixture.c.initial_updates()
        return fixture

    def selection_packet(self, fixture, key="1", session=None):
        cards = fixture.economy.account_view(key).cards[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(cards)
        )
        return packet(0xB2, 7, fixture.c.session(key) if session is None else session, body)

    def deck_packet(self, session):
        return packet(0xB2, 0x22, session, "@Decks=/C=0@DN1=/S=45787069726564")

    def expire(self, fixture, clock, deadline):
        if clock == "schedule":
            fixture.time = fixture.cfg.end
        else:
            fixture.mono = deadline

    def assert_released(self, fixture, before, peers, output):
        self.assertEqual(fixture.economy.snapshot(), before)
        self.assertEqual(set(fixture.c.take_closures()), set(peers))
        for peer in peers:
            self.assertIsNone(fixture.c.session(peer))
            self.assertFalse(fixture.economy.is_reserved(peer))
        self.assertFalse(any(b"@Select=/A=1" in d.payload for d in output))
        self.assertFalse(any(b"@EQuit=" in d.payload for d in output))
        self.assertEqual(fixture.c.results(), ())
        self.assertEqual(fixture.c.poll(), [])
        self.assertEqual(fixture.c.take_closures(), ())

    def test_expired_decks_never_reach_the_persistence_transaction(self):
        for clock in ("schedule", "admission"):
            with self.subTest(clock=clock):
                fixture = self.fixture()
                fixture.admit("1", select=False)
                request = self.deck_packet(fixture.c.session("1"))
                before = fixture.economy.snapshot()
                self.expire(fixture, clock, fixture.mono + fixture.c.timeout)
                with patch.object(
                    fixture.c.transactions,
                    "apply_match_deck_changes",
                    side_effect=OSError("save unavailable"),
                ) as save:
                    output = fixture.send("1", request, fixture.cfg.init_id)
                save.assert_not_called()
                self.assert_released(fixture, before, ("1",), output)
                with self.assertRaisesRegex(EventError, "no current event selection session"):
                    fixture.send("1", request, fixture.cfg.init_id)

    def test_new_and_duplicate_selection_cannot_succeed_after_either_boundary(self):
        for clock in ("schedule", "admission"):
            for selected in (False, True):
                with self.subTest(clock=clock, selected=selected):
                    fixture = self.fixture()
                    fixture.admit("1", select=selected)
                    request = self.selection_packet(fixture)
                    before = fixture.economy.snapshot()
                    self.expire(fixture, clock, fixture.mono + fixture.c.timeout)
                    output = fixture.send("1", request, fixture.cfg.init_id)
                    self.assert_released(fixture, before, ("1",), output)

    def test_paired_selection_uses_the_oldest_deadline_in_all_waiting_phases(self):
        for phase in ("proposal", "backoff", "admission"):
            for command in ("Decks", "CardSelect"):
                with self.subTest(phase=phase, command=command):
                    fixture = self.fixture()
                    fixture.admit("1")
                    oldest_deadline = fixture.mono + fixture.c.timeout
                    fixture.mono += 100
                    fixture.admit("2")
                    fixture.c.poll()
                    fixture.consent("1")
                    if phase != "proposal":
                        fixture.consent("2", answer=int(phase == "admission"))
                    request = (
                        self.deck_packet(fixture.c.session("2"))
                        if command == "Decks"
                        else self.selection_packet(fixture, "2")
                    )
                    before = fixture.economy.snapshot()
                    fixture.mono = oldest_deadline
                    output = fixture.send("2", request, fixture.cfg.init_id)
                    self.assertIsNone(fixture.c.snapshot("festival"))
                    self.assertEqual(fixture.rooms.rooms[0].room.table_count, 0)
                    self.assert_released(fixture, before, ("1", "2"), output)
                    if phase == "proposal":
                        self.assertEqual(
                            sum(b"@MuchMake=/Start=-1" in d.payload for d in output), 1
                        )
                    elif phase == "admission":
                        self.assertEqual(sum(b"@GameWa=/Error=-1" in d.payload for d in output), 2)

    def test_unpaired_expiry_does_not_cancel_a_newer_waiting_entry(self):
        fixture = self.fixture()
        fixture.admit("1", select=False)
        deadline = fixture.mono + fixture.c.timeout
        request = self.selection_packet(fixture)
        fixture.mono += 50
        fixture.admit("2", select=False)
        newer_session = fixture.c.session("2")
        before = fixture.economy.snapshot()
        fixture.mono = deadline
        output = fixture.send("1", request, fixture.cfg.init_id)
        self.assert_released(fixture, before, ("1",), output)
        self.assertEqual(fixture.c.session("2"), newer_session)
        self.assertTrue(fixture.economy.is_reserved("2"))
        self.assertTrue(any(b"@Select=/A=1" in d.payload for d in fixture.select("2")))

    def test_wrong_account_or_session_cannot_expire_someone_elses_entry(self):
        fixture = self.fixture()
        fixture.admit("1", select=False)
        session = fixture.c.session("1")
        fixture.time = fixture.cfg.end
        before = fixture.economy.snapshot()
        for key, request in (
            ("2", self.deck_packet(session)),
            ("1", self.deck_packet(session + 1)),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(EventError, "session mismatch"):
                fixture.c.handle("1", key, fixture.cfg.init_id, request)
        self.assertEqual(fixture.economy.snapshot(), before)
        self.assertEqual(fixture.c.session("1"), session)
        self.assertTrue(fixture.economy.is_reserved("1"))
        self.assertEqual(fixture.c.take_closures(), ())

    def test_expired_quit_cannot_create_a_clean_exit_editor_receipt(self):
        fixture = self.fixture()
        fixture.admit("1", select=False)
        session = fixture.c.session("1")
        before = fixture.economy.snapshot()
        fixture.time = fixture.cfg.end
        output = fixture.send(
            "1", packet(0xB2, 0x17, session, f"@Quit=/No={session}"), fixture.cfg.init_id
        )
        self.assert_released(fixture, before, ("1",), output)
        with self.assertRaisesRegex(EventError, "no current event selection session"):
            fixture.send("1", self.deck_packet(session), fixture.cfg.init_id)
        self.assertEqual(fixture.economy.snapshot(), before)

    def test_before_expiry_and_clean_exit_delayed_editor_keep_existing_contract(self):
        fixture = self.fixture()
        fixture.admit("1", select=False)
        session = fixture.c.session("1")
        fixture.mono += fixture.c.timeout - 0.01
        fixture.time = fixture.cfg.end - 1
        request = self.deck_packet(session)
        self.assertEqual(fixture.send("1", request, fixture.cfg.init_id), [])
        self.assertEqual(dict(fixture.economy.account_view("1").deck_names)[1], b"Expired")
        self.assertTrue(any(b"@Select=/A=1" in d.payload for d in fixture.select("1")))
        fixture.send("1", packet(0xB2, 0x17, session, f"@Quit=/No={session}"), fixture.cfg.init_id)
        fixture.time = fixture.cfg.end
        late = packet(0xB2, 0x22, session, "@Decks=/C=0@DN1=/S=4C617465")
        self.assertEqual(fixture.send("1", late, fixture.cfg.init_id), [])
        self.assertEqual(dict(fixture.economy.account_view("1").deck_names)[1], b"Late")
        token = fixture.economy.reserve_match("1")
        before = fixture.economy.snapshot()
        with self.assertRaises(EconomyError):
            fixture.send("1", request, fixture.cfg.init_id)
        self.assertEqual(fixture.economy.snapshot(), before)
        fixture.economy.release_match(token)
        self.assertEqual(fixture.c.take_closures(), ())

    def test_playing_round_selection_replay_does_not_expire_at_event_end(self):
        fixture = self.fixture()
        fixture.start()
        request = self.selection_packet(fixture)
        expected = fixture.send("1", request, fixture.cfg.init_id)
        before = fixture.c.snapshot("festival"), fixture.economy.snapshot()
        fixture.time = fixture.cfg.end
        self.assertEqual(fixture.send("1", request, fixture.cfg.init_id), expected)
        self.assertEqual((fixture.c.snapshot("festival"), fixture.economy.snapshot()), before)
        self.assertEqual(fixture.c.take_closures(), ())
        self.assertTrue(all(fixture.economy.is_reserved(key) for key in ("1", "2")))


if __name__ == "__main__":
    unittest.main()
