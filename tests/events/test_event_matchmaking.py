"""Headless event coordinator, account locks and original client gate checks."""

import struct
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests import FIXTURES
from tests.events.test_event_matches import finish
from tests.events.test_event_rooms import config, presence
from tmserver.cards.catalog import Catalog
from tmserver.economy.errors import EconomyError
from tmserver.economy.service import EconomyService
from tmserver.events import matchmaking
from tmserver.events.matchmaking import EventMatchmaking
from tmserver.events.models import EventTableConfig
from tmserver.events.rooms import EventRooms
from tmserver.events.models import EventError
from tmserver.events.service import EventHost
from tmserver.protocol.identities import game_id
from tmserver.game.errors import MatchError
from tmserver.game.models import Card
from tmserver.game.packets import decode, packet
from tmserver.protocol.commands import parse

P = FIXTURES / "protocol"

CARDS = tuple(Card(i, 50, 0, 40, 30, 1, 0) for i in range(5))


class CoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.time = 200.0
        self.mono = 10.0
        self.economy = EconomyService(catalog=self.catalog, seed=11)
        for i, name in enumerate((b"Alice", b"Bob", b"Carol"), 1):
            self.economy.provision(str(i), nn=game_id(i), player_id=i, name=name)
        self.cfg = config(match_enabled=True)
        self.host = EventHost(self.economy, [self.cfg], clock=lambda: self.time)
        self.rooms = EventRooms(self.host)
        self.table = EventTableConfig("festival", game_id(0x1003), b"#EVTABLE001")
        self.c = EventMatchmaking(
            self.host,
            self.rooms,
            [self.table],
            clock=lambda: self.mono,
        )

    def _make(self, table):
        self.table = table
        self.rooms = EventRooms(self.host)
        self.c = EventMatchmaking(
            self.host,
            self.rooms,
            [table],
            clock=lambda: self.mono,
        )

    def admit(self, key, select=True):
        binding = self.rooms.rooms[0]
        v = self.economy.account_view(key)
        if key in binding.room.members:
            binding.room.leave(key)
        binding.room.handle(
            key,
            presence(binding.room_id, v.nn, v.player_id, v.name),
            authenticated_identity=(v.nn, v.player_id),
        )
        self.host.admit_room(key, "festival")
        out = self.c.handle(key, key, self.cfg.init_id, self.entry_packet(key))
        if select:
            self.select(key)
        return out

    def select(self, key, cards=None):
        cards = self.economy.account_view(key).cards[:5] if cards is None else cards
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D="
            + ("|".join(map(str, c.full())) if isinstance(c.full(), tuple) else c.full())
            for i, c in enumerate(cards)
        )
        return self.send(key, packet(0xB2, 7, self.c.session(key), body), self.cfg.init_id)

    def entry_packet(self, key):
        v = self.economy.account_view(key)
        return packet(
            0x98,
            body=f"@EInitReq=/NN={v.nn:016X}/HID={v.player_id}/Dm=1/Vol=1/CN={v.name.hex()}/GID={v.player_id:016X}",
        )

    def send(self, key, payload, target=None, channels=None):
        return self.c.handle(
            key,
            key,
            self.table.table_id if target is None else target,
            payload,
            joined_channels=(self.table.channel,) if channels is None else channels,
        )

    def consent(self, key, answer=1):
        return self.send(
            key,
            packet(
                0xD3, body=f"@MuchMakeAns=/Ans={answer}/TblNo=1/TblId={self.table.table_id:016X}"
            ),
            self.cfg.room_id,
        )

    def identity(self, key, name):
        v = self.economy.account_view(key)
        from tests.protocol.test_admission_identity import native_identity

        return native_identity(v, name)

    def start(self, n=2):
        if n == 3:
            self._make(replace(self.table, player_count=3))
        for key in map(str, range(1, n + 1)):
            self.admit(key)
        out = self.c.poll()
        for key in map(str, range(1, n + 1)):
            out += self.consent(key)
        for key in map(str, range(1, n + 1)):
            out += self.send(key, packet(0x41, 1, body=self.identity(key, "GameEN")))
            out += self.send(key, packet(1, body=self.identity(key, "Req")))
            out += self.send(key, packet(0x41, 8, body="@GameOK="))
        return out

    def play(self):
        out = []
        for _ in range(300):
            r = self.c._rounds[self.table.table_id]
            s = r.engine
            if s.phase == "result":
                return out
            if s.pending_work:
                out += self.c.advance("festival", r.epoch, 1)
                continue
            if s.phase == "turn":
                sequence = s.sequence
                for seat, p in enumerate(s.players):
                    if p.peer is not None:
                        out += self.send(
                            p.peer, packet(0x43, 9, sequence, f"@TurnData=/Ans={seat}")
                        )
                if s.phase == "turn" and s.players[s.active].peer is not None:
                    field = next(i for i, c in enumerate(s.snapshot()["board"]) if c is None)
                    out += self.send(
                        s.players[s.active].peer,
                        packet(0x43, 10, s.sequence, f"@PutData=/P={s.active}/H=0/F={field}"),
                    )
            elif s.phase == "battle":
                out += self.send(
                    s.players[s.active].peer,
                    packet(
                        0x43,
                        11,
                        s.sequence,
                        "@BattleSelect=/B=" + str(s.snapshot()["battle_targets"][0]),
                    ),
                )
            else:
                raise AssertionError(s.phase)
        raise AssertionError("round did not finish")

    def test_nonpresence_nonparticipant_wrong_identity_cannot_reserve(self):
        with self.assertRaises(EventError):
            self.send("1", self.entry_packet("1"), self.cfg.init_id)
        self.assertFalse(self.economy.is_busy("1"))
        for msg in (
            packet(0x41, 1, body="@GameEN="),
            packet(0x41, 8, body="@GameOK="),
            packet(0x43, 9, body="@TurnData=/Ans=0"),
        ):
            with self.assertRaises(EventError):
                self.send("1", msg)
        self.assertFalse(self.economy.is_busy("1"))
        self.assertIsNone(self.c.snapshot("festival"))

    def test_table_publication_precedes_proposal_and_round_rules_agree(self):
        self.admit("1")
        self.admit("2")
        out = self.c.poll()
        first_proposal = next(i for i, d in enumerate(out) if d.payload.startswith(b"GTM0GD2"))
        self.assertGreater(first_proposal, 0)
        self.assertTrue(all(d.payload.startswith(b"GTM0L") for d in out[:first_proposal]))
        binding = self.rooms.rooms[0]
        self.assertEqual(struct.unpack_from("<I", binding.room.snapshot(), 0x48)[0], 2)
        self.assertEqual(binding.room.snapshot()[0x5878], ord("A"))
        self.assertIs(self.rooms.room_for_service(self.table.table_id), binding)
        self.assertIs(self.rooms.room_for_channel(self.table.channel.lower()), binding)
        self.assertEqual(self.c.poll(), [])

    def test_all_consent_before_start_and_channel_before_req(self):
        self.admit("1")
        self.admit("2")
        self.c.poll()
        with self.assertRaises(EventError):
            self.send("1", packet(1, body=self.identity("1", "Req")))
        self.assertEqual(self.consent("1"), [])
        start = self.consent("2")
        self.assertEqual(len(start), 2)
        self.assertTrue(all(d.payload == packet(0xD2, 1, body="@MuchMake=/Start=1") for d in start))
        self.assertEqual(self.consent("1"), [])
        with self.assertRaises(EventError):
            self.send("1", packet(0x41, 8, body="@GameOK="))
        self.send("1", packet(0x41, 1, body=self.identity("1", "GameEN")))
        with self.assertRaises(EventError):
            self.send("1", packet(1, body=self.identity("1", "Req")), channels=())
        ack = self.send("1", packet(1, body=self.identity("1", "Req")))
        self.assertEqual(ack, self.send("1", packet(1, body=self.identity("1", "Req"))))
        self.assertEqual(self.c.snapshot("festival")["requested"], 1)

    def test_wrong_proposal_and_packet_identity_rejected_without_change(self):
        self.admit("1")
        self.admit("2")
        self.c.poll()
        before = self.c.snapshot("festival")
        with self.assertRaises(EventError):
            self.send(
                "1",
                packet(0xD3, body=f"@MuchMakeAns=/Ans=1/TblNo=2/TblId={self.table.table_id:016X}"),
                self.cfg.room_id,
            )
        self.assertEqual(before, self.c.snapshot("festival"))
        self.consent("1")
        self.consent("2")
        before = self.c.snapshot("festival")
        with self.assertRaises(EventError):
            self.send("1", packet(0x41, 1, body=self.identity("2", "GameEN")))
        self.assertEqual(before, self.c.snapshot("festival"))

    def test_two_and_three_human_rounds_defer_release_until_durable_finish(self):
        for n in [2, 3]:
            if n == 3:
                self.setUp()
            before = {k: self.economy.profile_bytes(k) for k in ("1", "2", "3")}
            out = self.start(n)
            state = self.c.snapshot("festival")
            epoch = state["epoch"]
            self.assertEqual(state["phase"], "playing")
            self.assertEqual(self.rooms.rooms[0].room.table_state, 2)
            for key in map(str, range(1, n + 1)):
                own = [
                    decode(d.payload)
                    for d in out
                    if d.peer == key and d.payload.startswith(b"GTM0G")
                ]
                indices = [i for i, m in enumerate(own) if m.primary == 0x41 and m.subtype == 9]
                event_index = next(i for i, m in enumerate(own) if "EventGameInit" in m.sections)
                self.assertLess(indices[0], event_index)
            r = self.c._rounds[self.table.table_id]
            self.play()
            for seat in range(n):
                self.send(str(seat + 1), packet(0x43, 14, 5 * n, f"@Ready=/Ans={seat}"))
            completed = self.c.completed()
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0][:2], ("festival", epoch))
            self.assertTrue(all(self.economy.is_busy(str(i)) for i in range(1, n + 1)))
            self.c.finish("festival", epoch)
            self.assertTrue(all(not self.economy.is_busy(str(i)) for i in range(1, n + 1)))
            self.assertEqual(self.c.completed(), ())
            self.assertEqual(self.c.poll(), [])
            self.assertEqual(before, {k: self.economy.profile_bytes(k) for k in before})
            self.assertEqual(self.c.finish("festival", epoch), [])

    def test_synthetic_cpu_complete(self):
        self._make(replace(self.table, cpu_cards=CARDS))
        self.admit("1")
        self.c.poll()
        self.consent("1")
        self.send("1", packet(0x41, 1, body=self.identity("1", "GameEN")))
        self.send("1", packet(1, body=self.identity("1", "Req")))
        self.send("1", packet(0x41, 8, body="@GameOK="))
        r = self.c._rounds[self.table.table_id]
        self.play()
        self.send("1", packet(0x43, 14, 10, "@Ready=/Ans=0"))
        self.assertEqual(len(self.c.completed()), 1)
        self.c.finish("festival", r.epoch)
        self.assertFalse(self.economy.is_busy("1"))

    def test_rejected_proposal_timeout_and_disconnect_release_all(self):
        self.admit("1")
        self.admit("2")
        self.c.poll()
        self.consent("1")
        out = self.consent("2", 0)
        self.assertIn(packet(0xD2, 1, body="@MuchMake=/Start=-1"), [d.payload for d in out])
        self.assertEqual(self.c.take_closures(), ())
        self.assertTrue(self.economy.is_busy("1"))
        self.assertTrue(self.economy.is_busy("2"))
        self.assertEqual(self.c.snapshot("festival")["phase"], "backoff")
        before = self.c.snapshot("festival")
        self.assertEqual(self.consent("1"), [])
        self.assertEqual(before, self.c.snapshot("festival"))
        self.mono += 2
        retry = self.c.poll()
        self.assertEqual(sum(d.payload.startswith(b"GTM0GD20000") for d in retry), 2)
        self.mono += 121
        self.c.poll()
        self.assertIsNone(self.c.snapshot("festival"))
        self.assertEqual(set(self.c.take_closures()), {"1", "2"})
        self.admit("1")
        self.assertTrue(self.economy.is_busy("1"))
        self.c.disconnect("1")
        self.assertFalse(self.economy.is_busy("1"))

    def test_old_continuation_cannot_mutate_new_round_and_active_abort_closes_peers(self):
        self.start()
        old = self.c.snapshot("festival")["epoch"]
        self.c.disconnect("1")
        self.assertEqual(set(self.c.take_closures()), {"1", "2"})
        self.assertTrue(all(not self.economy.is_busy(k) for k in ("1", "2")))
        self.admit("1")
        self.admit("2")
        self.c.poll()
        before = self.c.snapshot("festival")
        self.assertEqual(self.c.advance("festival", old), [])
        self.assertEqual(self.c.cancel("festival", old), [])
        self.assertEqual(self.c.finish("festival", old), [])
        self.assertEqual(before, self.c.snapshot("festival"))

    def test_configuration_collision_and_mode8_rejected_atomically(self):
        rooms = EventRooms(self.host)
        before = rooms.profile_file(0x1000, "b/g/PTL")
        with self.assertRaises(EventError):
            EventMatchmaking(
                self.host,
                rooms,
                [replace(self.table, table_id=self.cfg.init_id)],
            )
        self.assertEqual(before, rooms.profile_file(0x1000, "b/g/PTL"))
        host = EventHost(self.economy, [replace(self.cfg, entry_answer=2)], clock=lambda: self.time)
        with self.assertRaises(EventError):
            EventMatchmaking(host, EventRooms(host), [self.table])

    def test_result_facts_precede_ready_and_survive_disconnect(self):
        self.start()
        out = self.play()
        self.assertTrue(any(b"@EventResult=" in d.payload for d in out))
        facts = self.c.results()
        self.assertEqual(len(facts), 1)
        event_id, epoch, outcome, keys = facts[0]
        self.assertEqual(keys, ("1", "2"))
        self.assertFalse(outcome.aborted)
        self.assertEqual(sum(outcome.scores), 10)
        self.assertEqual(self.c.completed(), ())
        self.c.disconnect("1")
        self.assertEqual(self.c.results(), facts)
        self.c.acknowledge_result(event_id, epoch)
        self.assertEqual(self.c.results(), ())

    def test_watchdog_ack_decision_and_result_expire_without_clock_extension(self):
        for phase in ("ack", "decision", "result"):
            with self.subTest(phase=phase):
                self.setUp()
                self.start()
                if phase == "ack":
                    self.send("1", packet(0x43, 9, 0, "@TurnData=/Ans=0"))
                    self.mono += 119
                    self.send("1", packet(0x43, 9, 0, "@TurnData=/Ans=0"))
                    self.mono += 1
                elif phase == "decision":
                    self.send("1", packet(0x43, 9, 0, "@TurnData=/Ans=0"))
                    self.send("2", packet(0x43, 9, 0, "@TurnData=/Ans=1"))
                    self.mono += 121
                else:
                    self.play()
                    self.mono += 901
                self.c.poll()
                self.assertIsNone(self.c.snapshot("festival"))
                self.assertEqual(set(self.c.take_closures()), {"1", "2"})
                self.assertFalse(self.economy.is_busy("1"))
                self.assertEqual(bool(self.c.results()), phase == "result")

    def test_waiting_entry_timeout_and_disabled_e_have_no_dangling_reservations(self):
        self.admit("1")
        self.mono += 121
        self.c.poll()
        self.assertFalse(self.economy.is_busy("1"))
        self.assertEqual(self.c.take_closures(), ("1",))
        host = EventHost(
            self.economy, [replace(self.cfg, match_enabled=False)], clock=lambda: self.time
        )
        rooms = EventRooms(host)
        c = EventMatchmaking(host, rooms, [self.table])
        v = self.economy.account_view("1")
        b = rooms.rooms[0]
        b.room.handle(
            "1",
            presence(b.room_id, v.nn, v.player_id, v.name),
            authenticated_identity=(v.nn, v.player_id),
        )
        host.admit_room("1", "festival")
        out = c.handle("1", "1", self.cfg.init_id, self.entry_packet("1"))
        self.assertEqual(len(out), 1)
        self.assertIn(b"/Ans=-32870", out[0].payload)
        self.assertFalse(self.economy.is_busy("1"))
        self.assertEqual(c.poll(), [])

    def test_invalid_cpu_card_model_is_rejected_by_table_configuration(self):
        bad = SimpleNamespace(**{k: getattr(CARDS[0], k) for k in Card.__dataclass_fields__})
        for model in (250, -1, 1.2, True):
            bad.model = model
            with self.assertRaises(ValueError):
                replace(self.table, cpu_cards=(bad,) * 5)

    def test_failed_round_setup_releases_its_entries_without_table_changes(self):
        self.admit("1")
        self.admit("2")
        before = self.rooms.rooms[0].room.snapshot()
        with patch.object(matchmaking, "EventMatchRound", side_effect=MatchError("bad setup")):
            self.c.poll()
        self.assertIsNone(self.c.snapshot("festival"))
        self.assertEqual(before, self.rooms.rooms[0].room.snapshot())
        self.assertEqual(set(self.c.take_closures()), {"1", "2"})
        self.assertTrue(all(not self.economy.is_busy(k) for k in ("1", "2")))
        self.assertEqual(self.c.take_errors()[0][0], "festival")
        self.assertEqual(self.c.take_errors(), ())

    def test_native_second_ready_after_durable_finish_is_idempotent(self):
        self.start()
        self.play()
        epoch = self.c.snapshot("festival")["epoch"]
        ready = packet(0x43, 14, 10, "@Ready=/Ans=0")
        self.send("1", ready)
        self.send("2", packet(0x43, 14, 10, "@Ready=/Ans=1"))
        self.c.finish("festival", epoch)
        self.assertEqual(self.send("1", ready), [])
        self.assertFalse(self.economy.is_busy("1"))
        self.assertEqual(self.c.results(), ())
        with self.assertRaises(EventError):
            self.send("1", packet(0x43, 14, 9, "@Ready=/Ans=0"))
        with self.assertRaises(EventError):
            self.send("2", ready)
        self.admit("1")
        with self.assertRaises(EventError):
            self.send("1", ready)

    def test_one_bad_event_policy_does_not_stop_a_second_event(self):
        cfg2 = config("second", 0x1100, name=b"Second event", match_enabled=True)
        self.host = EventHost(self.economy, [self.cfg, cfg2], clock=lambda: self.time)
        self.rooms = EventRooms(self.host)
        t1 = replace(self.table, cpu_cards=CARDS)
        t2 = replace(t1, event_id="second", table_id=game_id(0x1103), channel=b"#EVTABLE002")
        self.c = EventMatchmaking(
            self.host,
            self.rooms,
            [t1, t2],
            clock=lambda: self.mono,
        )
        real_round = matchmaking.EventMatchRound

        def failing_first_event(participants, **options):
            if participants[0].peer == "1":
                raise MatchError("bad event policy")
            return real_round(participants, **options)

        self.admit("1")
        binding = self.rooms.room_for_service(cfg2.room_id)
        v = self.economy.account_view("2")
        binding.room.handle(
            "2",
            presence(binding.room_id, v.nn, v.player_id, v.name),
            authenticated_identity=(v.nn, v.player_id),
        )
        self.host.admit_room("2", "second")
        self.c.handle("2", "2", cfg2.init_id, self.entry_packet("2"))
        cards = v.cards[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
        )
        self.c.handle("2", "2", cfg2.init_id, packet(0xB2, 7, self.c.session("2"), body))
        with patch.object(matchmaking, "EventMatchRound", side_effect=failing_first_event):
            out = self.c.poll()
        self.assertIsNone(self.c.snapshot("festival"))
        self.assertEqual(self.c.snapshot("second")["phase"], "proposal")
        self.assertEqual(self.c.take_closures(), ("1",))
        self.assertFalse(self.economy.is_busy("1"))
        self.assertTrue(self.economy.is_busy("2"))
        self.assertTrue(any(d.peer == "2" and b"@MuchMake=" in d.payload for d in out))

    def test_timeout_configuration_and_pairing_do_not_extend_entry_deadline(self):
        for key, value in [
            ("admission_timeout", True),
            ("proposal_backoff", True),
            ("admission_timeout", float("nan")),
            ("proposal_backoff", float("inf")),
        ]:
            with self.assertRaises(EventError):
                EventMatchmaking(
                    self.host,
                    EventRooms(self.host),
                    [self.table],
                    **{key: value},
                )
        self.admit("1")
        self.mono += 100
        self.admit("2")
        self.c.poll()
        self.assertEqual(self.c.snapshot("festival")["deadline"], 130.0)
        self.mono = 130.0
        self.c.poll()
        self.assertIsNone(self.c.snapshot("festival"))
        self.assertFalse(self.economy.is_busy("1"))
        self.assertFalse(self.economy.is_busy("2"))

    def test_original_selection_session_is_required_and_uses_authenticated_five(self):
        self.admit("1", False)
        self.admit("2", False)
        self.assertFalse(any(b"@MuchMake=" in d.payload for d in self.c.poll()))
        self.assertIsNone(self.c.snapshot("festival"))
        selected = self.economy.account_view("1").cards[3:8]
        response = self.select("1", selected)
        self.assertEqual([decode(d.payload).subtype for d in response], [7, 0x17])
        self.assertIn(b"@Select=/A=1", response[0].payload)
        self.assertIn(b"@EQuit=/D=0/Stat=0", response[1].payload)
        self.assertEqual(self.select("1", selected), response)
        self.select("2")
        self.c.poll()
        r = self.c._rounds[self.table.table_id]
        self.assertEqual(
            [c.reduced() for c in r.engine._hands["1"]],
            ["|".join(map(str, c.reduced())) for c in selected],
        )

    def test_selection_forgery_wrong_session_and_cancel_are_atomic(self):
        self.admit("1", False)
        session = self.c.session("1")
        before = self.economy.profile_bytes("1")
        with self.assertRaises(EventError):
            self.select("1", CARDS)
        wrong = packet(0xB2, 0x17, session + 1, f"@Quit=/No={session}")
        with self.assertRaises(EventError):
            self.send("1", wrong, self.cfg.init_id)
        self.assertEqual(before, self.economy.profile_bytes("1"))
        self.assertTrue(self.economy.is_busy("1"))
        quit = packet(0xB2, 0x17, session, f"@Quit=/No={session}")
        response = self.send("1", quit, self.cfg.init_id)
        self.assertEqual(len(response), 1)
        self.assertIn(b"@EQuit=", response[0].payload)
        self.assertFalse(self.economy.is_busy("1"))
        self.assertEqual(self.c.take_closures(), ())
        self.assertEqual(self.send("1", quit, self.cfg.init_id), response)
        self.assertFalse(any(b"@MuchMake=" in d.payload for d in self.c.poll()))

    def test_event_deck_edits_preserve_reservation_refresh_selected_and_persist(self):
        self.admit("1", False)
        old = self.c._entries["1"].reservation
        card = old.account.cards[0]
        session = self.c.session("1")

        def edit(slot, header=session, model=None):
            vals = list(card.reduced()) + [slot]
            if model is not None:
                vals[0] = model
            return packet(
                0xB2,
                0x22,
                header,
                "@Decks=/C=1@0=/D=" + "|".join(map(str, vals)) + "@DN1=/S=4576656E74",
            )

        before = self.economy.profile_bytes("1")
        for bad in (edit(0, session + 1), edit(0, model=249), edit(50)):
            with self.assertRaises(ValueError):
                self.send("1", bad, self.cfg.init_id)
            self.assertEqual(before, self.economy.profile_bytes("1"))
        self.assertEqual(self.send("1", edit(0), self.cfg.init_id), [])
        with self.assertRaises(EconomyError):
            self.economy.match_account(old)
        self.assertTrue(self.economy.is_busy("1"))
        self.assertEqual(self.economy.account_view("1").cards[0].deck, 0)
        self.assertEqual(dict(self.economy.account_view("1").deck_names)[1], b"Event")
        self.select("1")
        self.send("1", edit(1), self.cfg.init_id)
        self.assertEqual(self.c._entries["1"].selected[0].deck, 1)
        self.send("1", packet(0xB2, 0x17, session, f"@Quit=/No={session}"), self.cfg.init_id)
        self.assertFalse(self.economy.is_busy("1"))
        self.send("1", edit(2), self.cfg.init_id)
        self.assertEqual(self.economy.account_view("1").cards[0].deck, 2)

    def test_active_event_quit_ack_and_break_cleanup_are_authenticated(self):
        self.start()
        before = self.c.snapshot("festival")
        for bad in (packet(0x43, 5, 10, "@Quit=/No=1"), packet(0x43, 0x27, 3, "@Break=/ID=2")):
            with self.assertRaises(EventError):
                self.send("1", bad)
            self.assertEqual(before, self.c.snapshot("festival"))
        quit = packet(0x43, 5, 10, "@Quit=/No=0")
        out = self.send("1", quit)
        self.assertEqual(out[0].payload, packet(0x43, 5, 10, "@Quit=/No=0/B=0"))
        self.assertEqual(self.c.take_closures(), ("2",))
        self.assertIsNone(self.c.snapshot("festival"))
        self.assertFalse(self.economy.is_busy("1"))
        self.assertFalse(self.economy.is_busy("2"))
        self.assertEqual(self.send("1", quit), out[:1])
        self.setUp()
        self.start()
        out = self.send("1", packet(0x43, 0x27, 3, "@Break=/ID=1"))
        self.assertFalse(any(b"@Quit=" in d.payload or b"@Break=" in d.payload for d in out))
        self.assertEqual(set(self.c.take_closures()), {"1", "2"})
        self.assertFalse(self.economy.is_busy("1"))


if __name__ == "__main__":
    unittest.main()
