"""Injected calculations preserve default hooks, live board dispatch and RNG ownership."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.game.test_match_service import CARDS, ack, finish_arrowless_round, select
from tests.game.test_three_player_computer import choose, finish, menu
from tmserver.game import ai, rules, special_rules
from tmserver.game.errors import RulesExecutionError
from tmserver.game.match import MatchService
from tmserver.game.models import BoardCard
from tmserver.game.packets import packet


def recording_backend():
    return SimpleNamespace(
        calculate_battle=Mock(wraps=rules.calculate_battle),
        choose_move=Mock(wraps=ai.choose_move),
        close=Mock(),
    )


def extended_backend():
    backend = recording_backend()
    backend.grow_card = Mock(wraps=rules.grow_card)
    backend.choose_target = Mock(wraps=ai.choose_target)
    return backend


class RulesBackendTests(unittest.TestCase):
    def computer(self, backend=None, **kwargs):
        match = MatchService(seed=11, ai_cards=CARDS, rules_backend=backend, **kwargs)
        match.register("alice")
        match.begin()
        match.handle("alice", select())
        return match

    def test_default_looks_up_existing_python_hooks_after_construction(self):
        match = self.computer()
        with (
            patch("tmserver.game.match.choose_move", wraps=ai.choose_move) as move,
            patch("tmserver.game.match.calculate_battle", wraps=rules.calculate_battle) as battle,
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
        with patch("tmserver.game.match.choose_move", side_effect=AssertionError("fallback")):
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
        with patch("tmserver.game.match.calculate_battle", side_effect=AssertionError("fallback")):
            actual = match.battle_values(CARDS[0], CARDS[1], **effects)
        self.assertEqual(actual, expected.battle_values(CARDS[0], CARDS[1], **effects))
        backend.calculate_battle.assert_called_once_with(CARDS[0], CARDS[1], match.draw, **effects)
        self.assertEqual(backend.choose_move.call_count, 5)
        self.assertTrue(
            all(call.args[3] is match.draw for call in backend.choose_move.call_args_list)
        )

    def test_optional_operations_keep_default_hooks_for_legacy_backends(self):
        for backend in (None, recording_backend()):
            with self.subTest(backend=backend):
                match = self.computer(backend)
                with (
                    patch("tmserver.game.match.grow_card", wraps=rules.grow_card) as growth,
                    patch("tmserver.game.match.choose_target", wraps=ai.choose_target) as target,
                ):
                    finish_arrowless_round(match)
                    match.targets = [14, 1]
                    self.assertIn(match._choose_target(), match.targets)
                self.assertEqual(growth.call_count, 10)
                target.assert_called_once_with(match.targets, match.draw, board_size=16)

    def test_optional_growth_handles_combat_and_result_without_python_fallback(self):
        backend = extended_backend()
        match, expected = self.computer(backend), self.computer()
        board_card = BoardCard(CARDS[0], 0, 0, 0)
        expected_card = BoardCard(CARDS[0], 0, 0, 0)
        expected._grow_board(expected_card, 0, 0)
        expected_output = finish_arrowless_round(expected)
        with patch("tmserver.game.match.grow_card", side_effect=AssertionError("fallback")):
            match._grow_board(board_card, 0, 0)
            self.assertEqual(board_card, expected_card)
            self.assertEqual(finish_arrowless_round(match), expected_output)
        self.assertEqual(backend.grow_card.call_count, 11)
        self.assertEqual(match.final_cards("alice"), expected.final_cards("alice"))
        self.assertEqual(match.rng.state, expected.rng.state)

    def test_optional_target_uses_live_board_size_after_computer_menu_changes(self):
        backend = extended_backend()
        match = menu(rules_backend=backend)
        match.handle("alice", choose((1, 30)))
        match.handle("alice", select())
        match.targets = [24, 17, 1]
        with patch("tmserver.game.match.choose_target", side_effect=AssertionError("fallback")):
            self.assertIn(match._choose_target(), match.targets)
        backend.choose_target.assert_called_once_with(match.targets, match.draw, board_size=25)

    def test_optional_target_failure_consumes_draw_once_and_aborts(self):
        backend = extended_backend()
        match = self.computer(backend)
        match.targets = [3, 7]
        failure = RuntimeError("target failed after a draw")

        def fail(candidates, draw, **options):
            draw()
            raise failure

        backend.choose_target.side_effect = fail
        expected = rules.CRTLCG(match.rng.state)
        expected()
        with patch("tmserver.game.match.choose_target", side_effect=AssertionError("fallback")):
            for _ in range(2):
                with self.assertRaises(RulesExecutionError) as caught:
                    match._choose_target()
                self.assertIs(caught.exception.__cause__, failure)
        self.assertEqual(backend.choose_target.call_count, 1)
        self.assertEqual(match.rng.state, expected.state)
        self.assertEqual(match.phase, "finished")
        self.assertFalse(match.pending_work)

    def test_optional_growth_failure_aborts_before_board_mutation_and_cannot_retry(self):
        backend = extended_backend()
        match = self.computer(backend)
        board_card = BoardCard(CARDS[0], 0, 0, 0)
        failure = RuntimeError("growth bridge failed")
        backend.grow_card.side_effect = failure
        rng_before = match.rng.state
        with patch("tmserver.game.match.grow_card", side_effect=AssertionError("fallback")):
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

    def test_optional_result_growth_failure_prevents_completed_card_snapshot(self):
        backend = extended_backend()
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
                backend = extended_backend()
                match = self.computer(backend)
                calls = 0

                def interrupted_growth(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == failed_call:
                        raise RuntimeError("result growth failed after the first player")
                    return rules.grow_card(*args, **kwargs)

                backend.grow_card.side_effect = interrupted_growth
                with self.assertRaises(RulesExecutionError):
                    finish_arrowless_round(match)
                self.assertEqual(calls, failed_call)
                self.assertEqual(match.phase, "finished")
                self.assertIsNone(match.final_cards("alice"))
                self.assertFalse(match._final_snapshots)
                self.assertFalse(match._final_order)

    def test_special_board_retains_python_special_ai(self):
        backend = recording_backend()
        fields = (1,) + (0,) * 15
        match = self.computer(backend, start_fields=fields)
        with patch(
            "tmserver.game.match.special.choose_special_move",
            wraps=special_rules.choose_special_move,
        ) as special:
            finish(match)
        self.assertEqual(special.call_count, 5)
        backend.choose_move.assert_not_called()

    def test_computer_menu_reconfiguration_uses_live_player_count(self):
        backend = recording_backend()
        match = menu(rules_backend=backend)
        for npcs in ((1, 30), (3,)):
            match.handle("alice", choose(npcs))
            match.handle("alice", select())
            backend.choose_move.reset_mock()
            with patch(
                "tmserver.game.match.special.choose_special_move",
                wraps=special_rules.choose_special_move,
            ) as special:
                finish(match)
            self.assertEqual(backend.choose_move.call_count, 0 if len(npcs) == 2 else 5)
            self.assertEqual(special.call_count, 10 if len(npcs) == 2 else 0)
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
        with patch("tmserver.game.match.calculate_battle", side_effect=AssertionError("fallback")):
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

    def test_failed_injected_move_aborts_without_python_retry(self):
        backend = recording_backend()
        match = self.computer(backend)
        failure = RuntimeError("move bridge failure")
        backend.choose_move.side_effect = failure
        match.active = 1
        with self.assertRaises(RulesExecutionError) as caught:
            match.handle("alice", ack(0, match.sequence))
        self.assertIs(caught.exception.__cause__, failure)
        self.assertEqual(match.phase, "finished")
        backend.choose_move.assert_called_once()

    def test_backend_requires_both_operations(self):
        with self.assertRaisesRegex(ValueError, "rules backend"):
            MatchService(rules_backend=object())


if __name__ == "__main__":
    unittest.main()
