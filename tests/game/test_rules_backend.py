"""Native calculations preserve live board dispatch, RNG ownership and failure isolation."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.game.test_match_service import CARDS, ack, finish_arrowless_round, select
from tests.game.test_three_player_computer import choose, finish, menu
from tmserver.game import rules
from tmserver.game.original.runtime import NativeRuntime, default_rules
from tmserver.game.errors import RulesExecutionError
from tmserver.game.match import MatchService
from tmserver.game.models import BoardCard
from tmserver.game.packets import packet


def recording_backend():
    native = default_rules()
    return SimpleNamespace(
        calculate_battle=Mock(wraps=native.calculate_battle),
        choose_special_move=Mock(wraps=native.choose_special_move),
        grow_card=Mock(wraps=native.grow_card),
        choose_target=Mock(wraps=native.choose_target),
        close=Mock(),
    )


class RulesBackendTests(unittest.TestCase):
    def computer(self, backend=None, **kwargs):
        match = MatchService(seed=11, ai_cards=CARDS, rules_backend=backend, **kwargs)
        match.register("alice")
        match.begin()
        match.handle("alice", select())
        return match

    def test_default_dispatches_to_native_engine(self):
        match = self.computer()
        self.assertIsInstance(match.rules_backend, NativeRuntime)
        native = default_rules()
        with (
            patch.object(native, "choose_special_move", wraps=native.choose_special_move) as move,
            patch.object(native, "calculate_battle", wraps=native.calculate_battle) as battle,
        ):
            finish_arrowless_round(match)
            match.battle_values(CARDS[0], CARDS[1])
        self.assertEqual(move.call_count, 5)
        self.assertEqual(battle.call_count, 1)

    def test_injected_ordinary_ai_and_battle_share_the_match_rng(self):
        backend = recording_backend()
        match = self.computer(backend)
        expected = self.computer()
        expected_output = finish_arrowless_round(expected)
        self.assertEqual(finish_arrowless_round(match), expected_output)
        self.assertEqual(
            (match.snapshot(), match.rng.state), (expected.snapshot(), expected.rng.state)
        )
        effects = dict(
            attacker_modifier=-30,
            defender_modifier=15,
            attacker_property=0x42,
            defender_property=0x51,
        )
        actual = match.battle_values(CARDS[0], CARDS[1], **effects)
        self.assertEqual(actual, expected.battle_values(CARDS[0], CARDS[1], **effects))
        backend.calculate_battle.assert_called_once_with(CARDS[0], CARDS[1], match.draw, **effects)
        self.assertEqual(backend.choose_special_move.call_count, 5)
        self.assertTrue(
            all(call.args[3] is match.draw for call in backend.choose_special_move.call_args_list)
        )

    def test_native_growth_handles_combat_and_result_without_python_fallback(self):
        backend = recording_backend()
        match, expected = self.computer(backend), self.computer()
        board_card = BoardCard(CARDS[0], 0, 0, 0)
        expected_card = BoardCard(CARDS[0], 0, 0, 0)
        expected._grow_board(expected_card, 0, 0)
        expected_output = finish_arrowless_round(expected)
        match._grow_board(board_card, 0, 0)
        self.assertEqual(board_card, expected_card)
        self.assertEqual(finish_arrowless_round(match), expected_output)
        self.assertEqual(backend.grow_card.call_count, 11)
        self.assertEqual(match.final_cards("alice"), expected.final_cards("alice"))
        self.assertEqual(match.rng.state, expected.rng.state)

    def test_native_target_uses_live_board_size_after_computer_menu_changes(self):
        backend = recording_backend()
        match = menu(rules_backend=backend)
        match.handle("alice", choose((1, 30)))
        match.handle("alice", select())
        match.targets = [24, 17, 1]
        self.assertIn(match._choose_target(), match.targets)
        backend.choose_target.assert_called_once_with(match.targets, match.draw, board_size=25)

    def test_native_target_failure_consumes_draw_once_and_aborts(self):
        backend = recording_backend()
        match = self.computer(backend)
        match.targets = [3, 7]
        failure = RuntimeError("target failed after a draw")

        def fail(candidates, draw, **options):
            draw()
            raise failure

        backend.choose_target.side_effect = fail
        expected = rules.CRTLCG(match.rng.state)
        expected()
        for _ in range(2):
            with self.assertRaises(RulesExecutionError) as caught:
                match._choose_target()
            self.assertIs(caught.exception.__cause__, failure)
        self.assertEqual(backend.choose_target.call_count, 1)
        self.assertEqual(match.rng.state, expected.state)
        self.assertEqual(match.phase, "finished")
        self.assertFalse(match.pending_work)

    def test_native_growth_failure_aborts_before_board_mutation_and_cannot_retry(self):
        backend = recording_backend()
        match = self.computer(backend)
        board_card = BoardCard(CARDS[0], 0, 0, 0)
        failure = RuntimeError("growth bridge failed")
        backend.grow_card.side_effect = failure
        rng_before = match.rng.state
        for _ in range(2):
            with self.assertRaises(RulesExecutionError) as caught:
                match._grow_board(board_card, 0, 0)
            self.assertIs(caught.exception.__cause__, failure)
        self.assertEqual(backend.grow_card.call_count, 1)
        self.assertEqual(board_card.card, CARDS[0])
        self.assertEqual(match.players[0].updated[0], CARDS[0])
        self.assertEqual(match.rng.state, rng_before)
        self.assertEqual(match.phase, "finished")
        self.assertFalse(match.pending_work)

    def test_native_result_growth_failure_prevents_completed_card_snapshot(self):
        backend = recording_backend()
        match = self.computer(backend)
        backend.grow_card.side_effect = RuntimeError("result growth failed")
        with self.assertRaises(RulesExecutionError):
            finish_arrowless_round(match)
        self.assertEqual(match.phase, "finished")
        self.assertFalse(match._final_snapshots)
        backend.grow_card.assert_called_once()

    def test_late_result_growth_failure_never_exposes_one_players_completed_cards(self):
        for failed_call in (6, 10):
            with self.subTest(failed_call=failed_call):
                backend = recording_backend()
                match = self.computer(backend)
                calls = 0

                def interrupted_growth(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == failed_call:
                        raise RuntimeError("result growth failed after the first player")
                    return default_rules().grow_card(*args, **kwargs)

                backend.grow_card.side_effect = interrupted_growth
                with self.assertRaises(RulesExecutionError):
                    finish_arrowless_round(match)
                self.assertEqual(calls, failed_call)
                self.assertEqual(match.phase, "finished")
                self.assertIsNone(match.final_cards("alice"))
                self.assertFalse(match._final_snapshots)
                self.assertFalse(match._final_order)

    def test_special_board_dispatches_to_native_special_ai(self):
        backend = recording_backend()
        fields = (1,) + (0,) * 15
        match = self.computer(backend, start_fields=fields)
        finish(match)
        self.assertEqual(backend.choose_special_move.call_count, 5)

    def test_computer_menu_reconfiguration_uses_live_player_count(self):
        backend = recording_backend()
        match = menu(rules_backend=backend)
        for npcs in ((1, 30), (3,)):
            match.handle("alice", choose(npcs))
            match.handle("alice", select())
            backend.choose_special_move.reset_mock()
            finish(match)
            self.assertEqual(backend.choose_special_move.call_count, 10 if len(npcs) == 2 else 5)
            match.handle("alice", packet(0x43, 17, 0, "@Continue=/Ans=1/Conf=1"))

    def test_failed_injected_battle_consumes_draw_once_and_cannot_retry(self):
        backend = recording_backend()
        match = self.computer(backend, allow_departures=True)
        failure = RuntimeError("bridge stopped after a draw")

        def fail(attacker, defender, draw, **effects):
            draw()
            raise failure

        backend.calculate_battle.side_effect = fail
        expected_rng = rules.CRTLCG(match.rng.state)
        expected_rng()
        match._pending_battle = 0
        match.pending_move = (0, 0)
        for _ in range(2):
            with self.assertRaises(RulesExecutionError) as caught:
                match.battle_values(CARDS[0], CARDS[1])
            self.assertIs(caught.exception.__cause__, failure)
        self.assertEqual(backend.calculate_battle.call_count, 1)
        self.assertEqual(match.rng.state, expected_rng.state)
        self.assertEqual(match.phase, "finished")
        self.assertFalse(match.pending_work)
        self.assertIsNone(match.pending_move)
        self.assertFalse(match.departure_supported("alice"))
        self.assertEqual(match.advance(), [])

    def test_failed_injected_move_aborts_without_retry_on_ordinary_and_special_boards(self):
        for fields in ((0,) * 16, (1,) + (0,) * 15):
            with self.subTest(fields=fields):
                backend = recording_backend()
                match = self.computer(backend, start_fields=fields)
                failure = RuntimeError("move bridge failure")
                calculate = backend.choose_special_move
                calculate.side_effect = failure
                match.active = 1
                with self.assertRaises(RulesExecutionError) as caught:
                    match.handle("alice", ack(0, match.sequence))
                self.assertIs(caught.exception.__cause__, failure)
                self.assertEqual(match.phase, "finished")
                calculate.assert_called_once()
                self.assertEqual(match.advance(), [])
                calculate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
