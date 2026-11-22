"""Monotonic application deadlines against actual MatchService state.

Synthetic battle and result scenes isolate the deadline policy; the client's own
timer arithmetic is not exercised here.
"""

import unittest

from tmserver.game.models import Card
from tmserver.game.match import MatchService
from tmserver.game.packets import packet
from tmserver.rooms.watchdog import TURN_SECONDS, MatchWatchdog

CARDS = tuple(Card(i, 50, 0, 40, 30, 1, 0) for i in range(5))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def select(seat=0):
    return packet(
        0x43,
        7,
        seat,
        "@CardSelect=/C=5" + "".join(f"@N{i}=/D={card.full()}" for i, card in enumerate(CARDS)),
    )


def ack(seat=0, sequence=0):
    return packet(0x43, 9, sequence, f"@TurnData=/Ans={seat}")


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.watch = MatchWatchdog(clock=self.clock)

    def test_start_handshake_has_one_deadline_despite_partial_progress_and_retries(self):
        match = MatchService(mode="versus")
        for i, peer in enumerate(("alice", "bob")):
            match.register(peer, i + 1, nickname_id=i + 1)
            match.handle(peer, packet(65, 1, body="@GameEN="))
        self.assertIsNone(self.watch.observe(match, match))
        self.clock.now = 1000
        self.assertIsNone(self.watch.expired(match, match))
        match.handle("alice", packet(65, 10, body="@GameReady="))
        wait = self.watch.observe(match, match)
        self.assertEqual(
            (wait.reason, wait.deadline, wait.missing_peers),
            ("start_handshake", 1120, ("alice", "bob")),
        )
        self.clock.now = 1110
        match.handle("alice", packet(1, body="@Req="))
        match.handle("alice", packet(65, 8, body="@GameOK="))
        wait = self.watch.observe(match, match)
        self.assertEqual((wait.deadline, wait.missing_peers), (1120, ("bob",)))
        for name in ("GameReady", "GameEN"):
            match.handle(
                "alice", packet(65, 10 if name == "GameReady" else 1, body="@" + name + "=")
            )
            self.assertEqual(self.watch.observe(match, match).deadline, 1120)
        self.clock.now = 1120
        self.assertEqual(self.watch.expired(match, match).reason, "start_handshake")
        match.handle("bob", packet(1, body="@Req="))
        match.handle("bob", packet(65, 8, body="@GameOK="))
        wait = self.watch.observe(match, match)
        self.assertEqual((wait.reason, wait.deadline), ("card_selection", 1240))

    def test_request_without_offer_cannot_hold_an_incomplete_table_forever(self):
        match = MatchService(mode="versus")
        match.register("alice", 1)
        match.handle("alice", packet(1, body="@Req="))
        match.handle("alice", packet(65, 8, body="@GameOK="))
        wait = self.watch.observe(match, match)
        self.assertEqual(
            (wait.reason, wait.missing_peers, wait.deadline), ("start_handshake", (), 120)
        )
        self.clock.now = 120
        self.assertEqual(self.watch.expired(match, match).reason, "start_handshake")

    def test_original_computer_configuration_has_fixed_admitted_deadline(self):
        match = MatchService(computer_menu=True)
        match.register("alice", 1)
        match.handle("alice", packet(0x41, 0x12, body="@GameENC="))
        match.handle("alice", packet(1, body="@Req="))
        wait = self.watch.observe(match, match)
        self.assertEqual((wait.reason, wait.deadline), ("computer_configuration", 900))
        self.clock.now = 899
        match.handle("alice", packet(0x43, 6, 0, "@ComGame=/Rule=0|0|0|0|2|0|0"))
        self.assertEqual(self.watch.observe(match, match).deadline, 900)
        self.clock.now = 900
        self.assertEqual(self.watch.expired(match, match).reason, "computer_configuration")

    def match(self, *, versus=False, tl=0, selected=True):
        match = MatchService(mode="versus" if versus else "computer", ai_cards=CARDS)
        match.register("alice", 1)
        if versus:
            match.register("bob", 2)
        match.game_rules["tl"] = tl
        match.begin()
        if selected:
            match.handle("alice", select())
            if versus:
                match.handle("bob", select(1))
        self.watch.observe(match, match)
        return match

    def test_all_six_selection_limits_include_configured_grace(self):
        for tl, seconds in enumerate(TURN_SECONDS):
            with self.subTest(tl=tl):
                self.clock.now += 1000
                match = self.match(tl=tl, selected=False)
                start = self.clock.now
                self.clock.now = start + seconds + 60 - 0.001
                self.assertIsNone(self.watch.expired(match, match))
                self.clock.now += 0.001
                failure = self.watch.expired(match, match)
                self.assertEqual(failure.reason, "card_selection")
                self.assertEqual(failure.missing_peers, ("alice",))

    def test_ack_wait_has_own_deadline_and_ignores_repeated_observation(self):
        match = self.match(tl=0)
        for now in (15, 60, 119.999):
            self.clock.now = now
            self.watch.observe(match, match)
            self.assertIsNone(self.watch.expired(match, match))
        self.clock.now = 120
        failure = self.watch.expired(match, match)
        self.assertEqual((failure.reason, failure.missing_peers), ("turn_ack", ("alice",)))
        self.assertEqual(failure.started, 0)

    def test_partial_ack_progress_does_not_extend_remaining_peer(self):
        match = self.match(versus=True)
        self.clock.now = 110
        match.handle("alice", ack())
        self.watch.observe(match, match)
        self.clock.now = 120
        failure = self.watch.expired(match, match)
        self.assertEqual(failure.missing_peers, ("bob",))
        self.assertEqual(failure.deadline, 120)

    def test_last_ack_starts_human_budget_and_duplicate_ack_does_not_extend(self):
        match = self.match(tl=0)
        self.clock.now = 119
        match.handle("alice", ack())
        self.watch.observe(match, match)
        self.clock.now = 190
        match.handle("alice", ack())
        self.watch.observe(match, match)
        self.assertIsNone(self.watch.expired(match, match))
        self.clock.now = 194
        failure = self.watch.expired(match, match)
        self.assertEqual(
            (failure.reason, failure.started, failure.deadline), ("placement", 119, 194)
        )

    def test_early_move_does_not_replace_missing_ack_watchdog(self):
        match = self.match()
        self.clock.now = 119
        match.handle("alice", packet(0x43, 10, 0, "@PutData=/P=0/H=0/F=0"))
        self.watch.observe(match, match)
        self.clock.now = 120
        self.assertEqual(self.watch.expired(match, match).reason, "turn_ack")
        self.assertEqual(match.sequence, 0)
        self.assertIsNotNone(match.pending_move)

    def test_battle_choices_preserve_same_turn_budget(self):
        match = self.match()
        match.handle("alice", ack())
        self.watch.observe(match, match)
        self.clock.now = 70
        match.phase = "battle"
        match.targets = [1, 2]
        wait = self.watch.observe(match, match)
        self.assertEqual((wait.reason, wait.deadline), ("battle_selection", 75))
        # A changed eligible target list is not a fresh client turn.
        self.clock.now = 74
        match.targets = [2, 3]
        self.watch.observe(match, match)
        self.clock.now = 75
        self.assertEqual(self.watch.expired(match, match).reason, "battle_selection")

    def test_pending_ties_have_processing_limit_without_per_packet_refresh(self):
        match = self.match()
        match.handle("alice", ack())
        self.watch.observe(match, match)
        self.clock.now = 50
        match.phase = "battle"
        match._pending_battle = 3
        self.watch.observe(match, match)
        for now in (60, 75, 79.999):
            self.clock.now = now
            self.watch.observe(match, match)
            self.assertIsNone(self.watch.expired(match, match))
        self.clock.now = 80
        failure = self.watch.expired(match, match)
        self.assertEqual((failure.reason, failure.deadline), ("server_processing", 80))
        self.assertTrue(match.pending_work)  # Watchdog is read-only.

    def test_different_processing_target_is_real_progress(self):
        match = self.match()
        match.handle("alice", ack())
        match.phase, match._pending_battle = "battle", 3
        self.watch.observe(match, match)
        self.clock.now = 29
        match._pending_battle = 4
        self.watch.observe(match, match)
        self.clock.now = 30
        self.assertIsNone(self.watch.expired(match, match))
        self.clock.now = 59
        self.assertEqual(self.watch.expired(match, match).reason, "server_processing")

    def test_return_from_processing_does_not_grant_new_human_budget(self):
        match = self.match()
        match.handle("alice", ack())
        self.watch.observe(match, match)
        self.clock.now = 65
        match.phase, match._pending_battle = "battle", 2
        self.watch.observe(match, match)
        self.clock.now = 74
        match.cancel_pending()
        self.watch.observe(match, match)
        self.clock.now = 75
        self.assertEqual(self.watch.expired(match, match).reason, "battle_selection")

    def test_new_turn_rearms_ack_and_uses_current_sequence(self):
        match = self.match()
        match.handle("alice", ack())
        self.watch.observe(match, match)
        self.clock.now = 70
        match.handle("alice", packet(0x43, 10, 0, "@PutData=/P=0/H=0/F=0"))
        self.watch.observe(match, match)
        self.assertEqual(match.sequence, 1)
        self.clock.now = 189.999
        self.assertIsNone(self.watch.expired(match, match))
        self.clock.now = 190
        failure = self.watch.expired(match, match)
        self.assertEqual((failure.reason, failure.sequence), ("turn_ack", 1))

    def test_cpu_stall_is_server_processing_not_human_decision(self):
        match = self.match()
        match.active = 1
        match.acks.add("alice")
        wait = self.watch.observe(match, match)
        self.assertEqual(wait.reason, "server_processing")
        self.clock.now = 30
        self.assertEqual(self.watch.expired(match, match).reason, "server_processing")

    def test_result_ready_and_continue_have_separate_idle_stages(self):
        match = self.match()
        match.phase, match.completed_matches = "result", 1
        self.watch.observe(match, match)
        self.clock.now = 899
        match.ready.add("alice")
        wait = self.watch.observe(match, match)
        self.assertEqual((wait.reason, wait.deadline), ("result_continue", 1799))
        self.clock.now = 1700
        match.ready.add("alice")
        self.watch.observe(match, match)
        self.clock.now = 1799
        self.assertEqual(self.watch.expired(match, match).reason, "result_continue")

    def test_rematch_is_new_round_and_new_selection_deadline(self):
        match = self.match()
        match.phase, match.completed_matches = "result", 1
        self.watch.observe(match, match)
        self.clock.now = 800
        match.handle("alice", packet(0x43, 0x11, 0, "@Continue=/Ans=1/Conf=0"))
        wait = self.watch.observe(match, match)
        self.assertEqual((wait.reason, wait.round_id, wait.deadline), ("card_selection", 1, 875))

    def test_new_match_or_epoch_prevents_old_deadline_from_firing(self):
        old = self.match()
        self.clock.now = 100
        current = self.match()
        self.clock.now = 120
        self.assertIsNone(self.watch.expired(old, old))
        self.assertIsNone(self.watch.expired(current, current))
        token = object()
        self.watch.observe(current, token)
        self.assertIsNone(self.watch.expired(current, current))
        self.clock.now = 240
        self.assertIs(self.watch.expired(current, token).epoch, token)

    def test_lobby_finished_and_empty_match_have_no_automatic_disconnect(self):
        match = self.match()
        for phase in ("lobby", "finished"):
            match.phase = phase
            self.watch.observe(match, match)
            self.clock.now += 100000
            self.assertIsNone(self.watch.expired(match, match))
        match.players.clear()
        match.phase = "turn"
        self.watch.observe(match, match)
        self.assertIsNone(self.watch.expired(match, match))


if __name__ == "__main__":
    unittest.main()
