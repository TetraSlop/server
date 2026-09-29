"""Opt-in executable-dependent checks, intentionally outside the portable suite.

Set TM_ORIGINAL_IMAGE and run python -m unittest research.original_backend_tests.
Missing dependencies/image are errors, never successful skipped tests.
"""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock, patch

from tmserver.game import ai, rules
from tmserver.game.original.backend import OriginalRules
from tmserver.game.original.image import OriginalCodeError
from tmserver.game.match import MatchService
from tmserver.game.packets import packet
from tools.probe_original_rules import Draws, battle_cases, compare, move_cases

CARD = rules.Card(1, 50, 0, 40, 30, 1, 255)
HAND = (CARD,) * 5
BOARD = (None,) * 16


class OriginalBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = os.environ["TM_ORIGINAL_IMAGE"]
        cls.backend = OriginalRules(cls.image)

    def test_battle_results_and_draw_order(self):
        self.assertEqual(
            compare(
                rules.calculate_battle, self.backend.calculate_battle, battle_cases(), self.backend
            ),
            1168,
        )

    def test_ai_results_and_draw_order(self):
        self.assertEqual(
            compare(ai.choose_move, self.backend.choose_move, move_cases(), self.backend), 256
        )

    def test_no_python_rule_execution(self):
        expected_battle = rules.calculate_battle(CARD, CARD, rules.CRTLCG(7))
        expected_move = ai.choose_move(HAND, BOARD, 0, rules.CRTLCG(7))
        with (
            patch.object(rules, "calculate_battle", side_effect=AssertionError("Python battle")),
            patch.object(ai, "choose_move", side_effect=AssertionError("Python move")),
            patch.object(ai, "_score", side_effect=AssertionError("Python scoring")),
        ):
            self.assertEqual(
                self.backend.calculate_battle(CARD, CARD, rules.CRTLCG(7)), expected_battle
            )
            self.assertEqual(
                self.backend.choose_move(HAND, BOARD, 0, rules.CRTLCG(7)), expected_move
            )

    def test_no_move_does_not_consume_randomness(self):
        for hand, board in (
            ((), BOARD),
            (HAND, (ai.BLOCKED,) * 16),
            (HAND, (ai.Cell(CARD, 1),) * 16),
        ):
            self.assertIsNone(self.backend.choose_move(hand, board, 0, Draws(())))
            self.assertEqual(self.backend.last_draws, ())

    def test_invalid_inputs_fail_before_randomness(self):
        draw = Draws(())
        for kwargs in ({"attacker_modifier": 128}, {"defender_property": -1}):
            with self.assertRaises(rules.RulesError):
                self.backend.calculate_battle(CARD, CARD, draw, **kwargs)
        for hand, board, owner, personality in (
            (HAND * 2, BOARD, 0, ai.Personality()),
            ((), BOARD, 2, ai.Personality()),
            ((), BOARD[:15], 0, ai.Personality()),
            ((rules.Card(250, 1, 0, 1, 1, 1, 0),), BOARD, 0, ai.Personality()),
            ((), BOARD, 0, None),
        ):
            with self.assertRaises(ai.AIError):
                self.backend.choose_move(hand, board, owner, draw, personality)
        self.assertEqual(draw.used, [])

    def test_rng_errors_propagate_and_context_recovers(self):
        for value in (-1, True, 32768):
            with self.assertRaises(ai.AIError):
                self.backend.choose_move(HAND, BOARD, 0, lambda: value)
        for value in (-1, True, 0x80000000):
            with self.assertRaises(rules.RulesError):
                self.backend.calculate_battle(CARD, CARD, lambda: value)
        with self.assertRaises(StopIteration):
            self.backend.calculate_battle(CARD, CARD, Draws(()))
        self.assertEqual(
            self.backend.calculate_battle(CARD, CARD, rules.CRTLCG(9)),
            rules.calculate_battle(CARD, CARD, rules.CRTLCG(9)),
        )

    def test_instruction_budget_and_recovery(self):
        backend = OriginalRules(self.image, instruction_limit=1)
        with self.assertRaisesRegex(OriginalCodeError, "budget exhausted"):
            backend.calculate_battle(CARD, CARD, rules.CRTLCG(9))
        backend._machine.instruction_limit = 200_000
        self.assertEqual(
            backend.calculate_battle(CARD, CARD, rules.CRTLCG(9)),
            rules.calculate_battle(CARD, CARD, rules.CRTLCG(9)),
        )

    def test_reentrant_calls_are_rejected_without_corrupting_outer_call(self):
        draw = rules.CRTLCG(19)
        calls = []

        def reentrant_draw():
            with self.assertRaisesRegex(OriginalCodeError, "already executing"):
                self.backend.choose_move(HAND, BOARD, 0, draw)
            calls.append(1)
            return draw()

        self.assertEqual(
            self.backend.calculate_battle(CARD, CARD, reentrant_draw),
            rules.calculate_battle(CARD, CARD, rules.CRTLCG(19)),
        )
        self.assertGreater(len(calls), 0)

    def test_interleaved_battle_and_move_calls_reset_global_state(self):
        other = OriginalRules(self.image)
        for index, (args, options, values) in enumerate(move_cases(100)):
            expected = ai.choose_move(*args, Draws(values), **options)
            self.backend.calculate_battle(CARD, CARD, rules.CRTLCG(index))
            self.assertEqual(self.backend.choose_move(*args, Draws(values), **options), expected)
            other.choose_move(HAND, BOARD, 1, rules.CRTLCG(index + 999))
            self.assertEqual(other.choose_move(*args, Draws(values), **options), expected)

    def test_two_contexts_execute_concurrently(self):
        engines = [OriginalRules(self.image), OriginalRules(self.image)]
        barrier = Barrier(2, timeout=5)

        def play(index):
            draw = rules.CRTLCG(index + 11)
            first = True

            def synchronized_draw():
                nonlocal first
                if first:
                    first = False
                    barrier.wait()
                return draw()

            return engines[index].choose_move(HAND, BOARD, index, synchronized_draw)

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(play, index) for index in (0, 1)]
            actual = [job.result(timeout=10) for job in jobs]
        self.assertEqual(
            actual,
            [ai.choose_move(HAND, BOARD, index, rules.CRTLCG(index + 11)) for index in (0, 1)],
        )

    def test_complete_com_rounds_emit_identical_packets_and_results(self):
        """Hybrid MatchService injection preserves packets, cards and caller RNG."""

        def play(seed, npc, backend=None):
            rng = rules.CRTLCG(seed)
            draws = Draws(iter(rng, None))
            match = MatchService(computer_menu=True, seed=seed, draw=draws, rules_backend=backend)
            match.register("human")
            output = []

            def send(message):
                output.extend(match.handle("human", message))

            send(packet(0x41, 0x12, body="@GameENC="))
            send(packet(1, body="@Req="))
            send(packet(0x43, 6, 2, f"@ComGame=/Rule=0|0|0|0|2|0|0|1/Com={npc}"))
            send(
                packet(
                    0x43,
                    7,
                    0,
                    "@CardSelect=/C=5"
                    + "".join(
                        f"@N{i}=/D={'|'.join(map(str, card.full()))}" for i, card in enumerate(HAND)
                    ),
                )
            )
            for _ in range(1000):
                if match.phase == "result":
                    return (
                        output,
                        match.snapshot(),
                        match.final_cards("human"),
                        draws.used,
                        rng.state,
                    )
                if match.pending_work:
                    output.extend(match.advance())
                elif match.phase == "battle":
                    send(packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}"))
                else:
                    self.assertEqual(match.phase, "turn")
                    send(packet(0x43, 9, match.sequence, "@TurnData=/Ans=0"))
                    if match.phase == "turn" and match.active == 0 and match._unlocked():
                        send(
                            packet(
                                0x43,
                                10,
                                match.sequence,
                                f"@PutData=/P=0/H=0/F={match.legal_fields()[0]}",
                            )
                        )
            self.fail("COM round did not finish")

        for npc in (1, 3, 30):
            expected = play(npc + 20, npc)
            backend = Mock(wraps=self.backend)
            self.assertEqual(play(npc + 20, npc, backend), expected)
            self.assertGreater(backend.calculate_battle.call_count, 0)
            self.assertEqual(backend.choose_move.call_count, 5)


if __name__ == "__main__":
    unittest.main()
