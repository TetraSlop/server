"""Opt-in Windows x86 bridge checks; no game launch or emulator dependency.

Set TM_ORIGINAL_IMAGE and TM_NATIVE_BRIDGE, then run this module with a
32-bit Windows Python interpreter. Missing inputs are errors, not skips.
"""

import ctypes
import os
import random
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from unittest.mock import Mock, patch

from research import original_backend_tests as shared_checks
from research.native_primitive_cases import growth_cases, target_cases
from tmserver.game import ai, rules
from tmserver.game.original.image import OriginalCodeError
from tmserver.game.original.native import NativeRules
from tmserver.game.original import native as native_api
from tools.probe_original_rules import Draws, battle_cases, compare, move_cases

CARD = rules.Card(1, 50, 0, 40, 30, 1, 255)
HAND = (CARD,) * 5
BOARD = (None,) * 16
BASELINE = rules.GrowthBaseline(100, 80, 60, 20, 100)


class NativeBackendTests(unittest.TestCase):
    def setUp(self):
        self.image = os.environ["TM_ORIGINAL_IMAGE"]
        self.bridge = os.environ["TM_NATIVE_BRIDGE"]
        self.backend = NativeRules(self.image, self.bridge)
        self.addCleanup(self.backend.close)

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

    def test_growth_matches_all_original_oracle_cases(self):
        comparisons = 0
        for card, baseline, mode, value, precision in growth_cases():
            actual = self.backend.grow_card(card, baseline, mode, value, x87_precision=precision)
            self.assertEqual(
                actual,
                rules.grow_card(card, baseline, mode, value, x87_precision=precision),
                (card, baseline, mode, value, precision),
            )
            self.assertEqual(self.backend.last_draws, ())
            comparisons += 1
        self.assertEqual(comparisons, 6248)

    def test_growth_uses_supplied_baseline_and_preserves_full_model_domain(self):
        rng = random.Random(99031)
        for model in (0, 249, 250, 32767, 32768, 65534):
            for precision in (53, 64):
                for price in (0, 1, 0x7FFFFFFF):
                    card = rules.Card(
                        model, 255, rng.randrange(4), 0, 254, 255, 129, rng.randrange(256)
                    )
                    baseline = rules.GrowthBaseline(1, 255, 13, 255, price)
                    for mode, value in ((0, 0), (1, 1), (257, -1), (-256, 1 << 80)):
                        with self.subTest(card=card, baseline=baseline, mode=mode, value=value):
                            self.assertEqual(
                                self.backend.grow_card(
                                    card, baseline, mode, value, x87_precision=precision
                                ),
                                rules.grow_card(
                                    card, baseline, mode, value, x87_precision=precision
                                ),
                            )

    def test_targets_preserve_bounds_order_and_consumed_draws(self):
        count = 0
        for targets, size, values in target_cases():
            actual_draw, expected_draw = Draws(values), Draws(values)
            self.assertEqual(
                self.backend.choose_target(targets, actual_draw, board_size=size),
                ai.choose_target(targets, expected_draw, board_size=size),
            )
            self.assertEqual(actual_draw.used, expected_draw.used)
            self.assertEqual(self.backend.last_draws, tuple(expected_draw.used))
            count += 1
        self.assertEqual(count, 360)

    def test_growth_and_target_do_not_execute_python_algorithms(self):
        growth = rules.grow_card(CARD, BASELINE, 1, 0)
        with (
            patch.object(rules, "grow_card", side_effect=AssertionError("Python growth")),
            patch.object(rules, "_sale_value", side_effect=AssertionError("Python sale")),
            patch.object(ai, "choose_target", side_effect=AssertionError("Python target")),
            patch.object(ai, "poll_target", side_effect=AssertionError("Python target")),
        ):
            self.assertEqual(self.backend.grow_card(CARD, BASELINE, 1, 0), growth)
            self.assertEqual(self.backend.choose_target((14, 1, 6), iter((0, 1)).__next__), 1)

    def test_growth_and_target_reject_invalid_static_inputs(self):
        draw = Mock(side_effect=AssertionError("Randomness before validation"))
        for targets, size in (
            ((1, 1), 16),
            ((-1,), 16),
            ((16,), 16),
            ((25,), 25),
            (tuple(range(9)), 25),
            ((True,), 16),
            ((1,), True),
            ((1,), 24),
        ):
            with self.subTest(targets=targets, size=size), self.assertRaises(ai.AIError):
                self.backend.choose_target(targets, draw, board_size=size)
        for options in ({"x87_precision": 24}, {"x87_precision": True}):
            with self.assertRaises(rules.RulesError):
                self.backend.grow_card(CARD, BASELINE, 0, 0, **options)
        for mode, value in ((True, 0), (0, True), (0, 0.0), ("1", 1)):
            with self.assertRaises(rules.RulesError):
                self.backend.grow_card(CARD, BASELINE, mode, value)
        draw.assert_not_called()

    def test_c_abi_validates_new_records_before_execution(self):
        growth = native_api._GrowthRequest(
            (native_api._U32 * 8)(*CARD.full()),
            (native_api._U32 * 5)(0, 80, 60, 20, 100),
            1,
            0,
            64,
        )
        output = native_api._GrowthResult()
        self.assertEqual(
            self.backend._bridge.tm_rules_growth(ctypes.byref(growth), ctypes.byref(output)), 2
        )
        draws = []

        @native_api._Random
        def callback(*_):
            draws.append(1)
            return 1

        target = native_api._TargetRequest(2, 25, (native_api._U32 * 8)(24, 24))
        selected = native_api._TargetResult()
        self.assertEqual(
            self.backend._bridge.tm_rules_target(
                ctypes.byref(target), callback, None, ctypes.byref(selected)
            ),
            2,
        )
        self.assertEqual(draws, [])
        self.assertEqual(
            self.backend.choose_target((24,), lambda: self.fail("Sole draw"), board_size=25), 24
        )

    def test_target_callback_errors_and_partial_draws_recover(self):
        for value in (-1, 32768, True, 1.5):
            with self.assertRaises(ai.AIError):
                self.backend.choose_target((0, 1), iter((123, value)).__next__)
            self.assertEqual(self.backend.last_draws, (123,))
        failure = RuntimeError("target RNG stopped")
        draw = Mock(side_effect=(123, failure))
        with self.assertRaises(RuntimeError) as caught:
            self.backend.choose_target((0, 1), draw)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.backend.last_draws, (123,))
        self.assertEqual(self.backend.choose_target((0, 1), iter((0, 1)).__next__), 1)
        self.assertEqual(
            self.backend.grow_card(CARD, BASELINE, 0, 0), rules.grow_card(CARD, BASELINE, 0, 0)
        )

    def test_growth_restores_x87_state_even_after_native_access_violation(self):
        crt = ctypes.CDLL("msvcrt")
        crt._control87.argtypes = [ctypes.c_uint, ctypes.c_uint]
        crt._control87.restype = ctypes.c_uint
        saved = crt._control87(0, 0)
        expected = rules.grow_card(CARD, BASELINE, 1, 0)
        request = native_api._GrowthRequest(
            (native_api._U32 * 8)(*CARD.full()),
            (native_api._U32 * 5)(100, 80, 60, 20, 100),
            1,
            0,
            64,
        )
        try:
            # Deliberately choose host precision/rounding different from the call.
            host = crt._control87(0x10200, 0x30300)
            self.assertEqual(self.backend.grow_card(CARD, BASELINE, 1, 0), expected)
            self.assertEqual(crt._control87(0, 0), host)
            # The guarded ABI must restore the host state even when output copying faults.
            invalid_output = ctypes.cast(1, ctypes.POINTER(native_api._GrowthResult))
            self.assertEqual(
                self.backend._bridge.tm_rules_growth(ctypes.byref(request), invalid_output),
                0xC0000005,
            )
            self.assertEqual(crt._control87(0, 0), host)
            self.assertEqual(self.backend.grow_card(CARD, BASELINE, 1, 0), expected)
        finally:
            crt._control87(saved, 0xFFFFFFFF)

    def test_no_python_rule_execution(self):
        shared_checks.OriginalBackendTests.test_no_python_rule_execution(self)

    def test_no_move_does_not_consume_randomness(self):
        shared_checks.OriginalBackendTests.test_no_move_does_not_consume_randomness(self)

    def test_invalid_inputs_fail_before_randomness(self):
        shared_checks.OriginalBackendTests.test_invalid_inputs_fail_before_randomness(self)

    def test_rng_errors_propagate_and_context_recovers(self):
        shared_checks.OriginalBackendTests.test_rng_errors_propagate_and_context_recovers(self)

    def test_rng_exception_identity_and_partial_draws(self):
        failure = RuntimeError("RNG callback interrupted")
        values = iter((123,))

        def draw():
            try:
                return next(values)
            except StopIteration:
                raise failure

        with self.assertRaises(RuntimeError) as caught:
            self.backend.calculate_battle(CARD, CARD, draw)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.backend.last_draws, (123,))

        with self.assertRaises(KeyboardInterrupt):
            self.backend.calculate_battle(
                CARD, CARD, lambda: (_ for _ in ()).throw(KeyboardInterrupt)
            )
        self.assertEqual(self.backend.last_draws, ())

    def test_reentrant_calls_and_close_preserve_outer_operation(self):
        with NativeRules(self.image, self.bridge) as other:
            rng = rules.CRTLCG(19)

            def draw():
                for operation in (
                    lambda: self.backend.choose_move(HAND, BOARD, 0, rng),
                    lambda: other.choose_move(HAND, BOARD, 0, rng),
                    lambda: self.backend.grow_card(CARD, BASELINE, 1, 0),
                    lambda: other.grow_card(CARD, BASELINE, 1, 0),
                    lambda: self.backend.choose_target((0, 1), rng),
                    lambda: other.choose_target((0, 1), rng),
                    self.backend.close,
                    other.close,
                ):
                    with self.assertRaisesRegex(OriginalCodeError, "already executing"):
                        operation()
                return rng()

            self.assertEqual(
                self.backend.calculate_battle(CARD, CARD, draw),
                rules.calculate_battle(CARD, CARD, rules.CRTLCG(19)),
            )
            self.assertEqual(
                other.choose_move(HAND, BOARD, 0, rules.CRTLCG(19)),
                ai.choose_move(HAND, BOARD, 0, rules.CRTLCG(19)),
            )

    def test_concurrent_context_is_busy_and_recovers(self):
        entered, release = Event(), Event()
        with NativeRules(self.image, self.bridge) as other:
            rng = rules.CRTLCG(21)

            def draw():
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Native test did not release RNG callback")
                return rng()

            with ThreadPoolExecutor(max_workers=1) as pool:
                job = pool.submit(self.backend.calculate_battle, CARD, CARD, draw)
                try:
                    self.assertTrue(entered.wait(5))
                    with self.assertRaisesRegex(OriginalCodeError, "already executing"):
                        other.calculate_battle(CARD, CARD, rules.CRTLCG(21))
                finally:
                    release.set()
                self.assertEqual(
                    job.result(timeout=5),
                    rules.calculate_battle(CARD, CARD, rules.CRTLCG(21)),
                )
            self.assertEqual(
                other.calculate_battle(CARD, CARD, rules.CRTLCG(21)),
                rules.calculate_battle(CARD, CARD, rules.CRTLCG(21)),
            )

    def test_context_reference_counts_and_reopening(self):
        with NativeRules(self.image, self.bridge) as other:
            self.backend.close()
            self.backend.close()
            with self.assertRaisesRegex(OriginalCodeError, "closed"):
                self.backend.calculate_battle(CARD, CARD, rules.CRTLCG(9))
            with self.assertRaisesRegex(OriginalCodeError, "closed"):
                self.backend.__enter__()
            self.assertEqual(
                other.calculate_battle(CARD, CARD, rules.CRTLCG(9)),
                rules.calculate_battle(CARD, CARD, rules.CRTLCG(9)),
            )
        with NativeRules(self.image, self.bridge) as reopened:
            self.assertEqual(
                reopened.choose_move(HAND, BOARD, 0, rules.CRTLCG(9)),
                ai.choose_move(HAND, BOARD, 0, rules.CRTLCG(9)),
            )

    def test_interleaved_contexts_reset_global_state(self):
        with NativeRules(self.image, self.bridge) as other:
            for index, (args, options, values) in enumerate(move_cases(100)):
                expected = ai.choose_move(*args, Draws(values), **options)
                self.backend.calculate_battle(CARD, CARD, rules.CRTLCG(index))
                card = replace(CARD, model=65534, kind=index % 4)
                growth = rules.grow_card(card, BASELINE, index, index)
                self.assertEqual(other.grow_card(card, BASELINE, index, index), growth)
                self.assertEqual(
                    self.backend.choose_target((24, 3), iter((1, index)).__next__, board_size=25),
                    (24, 3)[index % 2],
                )
                self.assertEqual(
                    self.backend.choose_move(*args, Draws(values), **options), expected
                )
                other.choose_move(HAND, BOARD, 1, rules.CRTLCG(index + 999))
                self.assertEqual(other.choose_move(*args, Draws(values), **options), expected)

    def test_abi_version_is_checked_before_image_open(self):
        with patch("tmserver.game.original.native.ctypes.CDLL") as load:
            load.return_value.tm_rules_abi.return_value = 1
            with self.assertRaisesRegex(OriginalCodeError, "ABI 1; expected 2"):
                NativeRules(self.image, self.bridge)
            load.return_value.tm_rules_open.assert_not_called()
            self.assertNotIn("tm_rules_growth", load.return_value._mock_children)

    def test_unsupported_host_fails_before_loading_bridge(self):
        with patch("tmserver.game.original.native.sys.platform", "linux"):
            with self.assertRaisesRegex(OriginalCodeError, "32-bit Windows Python"):
                NativeRules(self.image, self.bridge)
        with patch("tmserver.game.original.native.ctypes.sizeof", return_value=8):
            with self.assertRaisesRegex(OriginalCodeError, "32-bit Windows Python"):
                NativeRules(self.image, self.bridge)

    def test_adapter_imports_no_emulator(self):
        script = """
import ctypes, sys
from tmserver.game.original.native import NativeRules
with NativeRules(sys.argv[1], sys.argv[2]):
    assert ctypes.sizeof(ctypes.c_void_p) == 4
    assert not any(name == 'unicorn' or name.startswith('unicorn.') for name in sys.modules)
    assert 'tmserver.game.original.machine' not in sys.modules
    assert 'tmserver.game.original.backend' not in sys.modules
"""
        child = subprocess.run(
            [sys.executable, "-B", "-c", script, self.image, self.bridge],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_complete_com_rounds_emit_identical_packets_and_results(self):
        with (
            patch.object(self.backend, "grow_card", wraps=self.backend.grow_card) as growth,
            patch.object(self.backend, "choose_target", wraps=self.backend.choose_target) as target,
        ):
            shared_checks.OriginalBackendTests.test_complete_com_rounds_emit_identical_packets_and_results(
                self
            )
        self.assertGreaterEqual(growth.call_count, 30)
        self.assertGreater(target.call_count, 0)

    def test_hybrid_special_cbc_and_three_player_rounds_preserve_packets_and_all_draws(self):
        """Special/three-player placement AI stays Python; other supported rules are native."""
        from tests.game.test_match_service import select
        from tests.game.test_three_player_computer import choose, finish, menu
        from tmserver.game.models import Card

        cards = tuple(Card.from_card(CARD) for _ in range(5))
        for npcs, tile in (((1, 30), 0), ((1,), 6), ((1,), 18)):
            with self.subTest(npcs=npcs, tile=tile):
                fields = None if not tile else (0, tile) + (0,) * 14
                menu_rules = f"0|0|{int(bool(tile))}|0|2|0|0|1"

                def play(backend=None):
                    rng = rules.CRTLCG(57)
                    draws = []

                    def draw():
                        value = rng()
                        draws.append(value)
                        return value

                    match = menu(
                        rules_backend=backend, draw=draw, start_fields=fields, allow_cbc=tile == 18
                    )
                    output = match.handle("alice", choose(npcs, rules=menu_rules))
                    output += match.handle("alice", select(cards))
                    output += finish(match)
                    return output, match.snapshot(), match.final_cards("alice"), draws

                expected = play()
                backend = Mock(wraps=self.backend)
                self.assertEqual(play(backend), expected)
                self.assertGreater(backend.calculate_battle.call_count, 0)
                backend.choose_move.assert_not_called()
                self.assertGreaterEqual(backend.grow_card.call_count, 5 * (len(npcs) + 1))
                self.assertGreater(backend.choose_target.call_count, 0)
                if tile == 18:
                    self.assertTrue(any(b"/CBC=" in item.payload for item in expected[0]))

    def test_hybrid_event_rounds_preserve_borrowed_hands_and_results(self):
        from tests.events.test_event_matches import finish
        from tmserver.events.matches import EventMatchRound, EventParticipant
        from tmserver.game.models import Card

        cards = tuple(Card.from_card(CARD) for _ in range(5))
        for count, cpu in ((2, False), (3, False), (2, True)):
            with self.subTest(count=count, cpu=cpu):

                def play(backend=None):
                    match = EventMatchRound(
                        tuple(
                            EventParticipant(i, i + 1, f"Player{i}".encode(), i + 1, cards)
                            for i in range(1 if cpu else count)
                        ),
                        cpu_cards=cards if cpu else None,
                        seed=17,
                        rules_backend=backend,
                    )
                    draws = Draws(iter(match._combat.rng, None))
                    match._combat.draw = draws
                    output = match.begin() + finish(match)
                    return (
                        output,
                        match.snapshot(),
                        match.outcome,
                        draws.used,
                        match._combat.rng.state,
                    )

                expected = play()
                backend = Mock(wraps=self.backend)
                self.assertEqual(play(backend), expected)
                self.assertGreater(backend.calculate_battle.call_count, 0)
                self.assertEqual(backend.choose_move.call_count, 5 if cpu else 0)
                # Borrowed event hands receive combat promotion, never ordinary result growth.
                self.assertGreater(backend.grow_card.call_count, 0)
                self.assertGreater(backend.choose_target.call_count, 0)


if __name__ == "__main__":
    unittest.main()
