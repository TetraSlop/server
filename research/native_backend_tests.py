"""Native x86 acceptance checks against frozen original-code results.

Set TM_ORIGINAL_IMAGE and TM_NATIVE_BRIDGE and run with 32-bit Windows Python.
Missing inputs are errors, not successful skips. No game or UI is launched.
"""

import ctypes
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

from tmserver.game import ai, rules
from tmserver.game.original.image import OriginalCodeError
from tmserver.game.original.native import NativeRules
from tmserver.game.original import native as native_api
from research.probe_original_rules import Draws, SPECIAL_VECTORS, VECTORS, snapshot, verify_vectors

CARD = rules.Card(1, 50, 0, 40, 30, 1, 255)
HAND = (CARD,) * 5
BOARD = (None,) * 16
BASELINE = rules.GrowthBaseline(100, 80, 60, 20, 100)


def battle(seed):
    value = VECTORS["samples"][str(seed)]["battle"]
    return rules.BattleResult(
        rules.BattleSide(**value["attacker"]),
        rules.BattleSide(**value["defender"]),
        tuple(value["raw_draws"]),
    )


def move(seed):
    return ai.Move(**VECTORS["samples"][str(seed)]["move"])


def growth(mode):
    value = VECTORS["growth"][str(mode)]
    return rules.GrowthResult(
        rules.Card(**value["card"]), value["sale_value"], value["changed_field"]
    )


class NativeBackendTests(unittest.TestCase):
    def setUp(self):
        self.image = os.environ["TM_ORIGINAL_IMAGE"]
        self.bridge = os.environ["TM_NATIVE_BRIDGE"]
        self.backend = NativeRules(self.image, self.bridge)
        self.addCleanup(self.backend.close)

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
        self.assertEqual(self.backend.grow_card(CARD, BASELINE, 0, 0), growth(0))

    def test_growth_restores_x87_state_even_after_native_access_violation(self):
        crt = ctypes.CDLL("msvcrt")
        crt._control87.argtypes = [ctypes.c_uint, ctypes.c_uint]
        crt._control87.restype = ctypes.c_uint
        saved = crt._control87(0, 0)
        expected = growth(1)
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
            battle(9),
        )

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
                battle(19),
            )
            self.assertEqual(
                other.choose_move(HAND, BOARD, 0, rules.CRTLCG(19)),
                move(19),
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
                    battle(21),
                )
            self.assertEqual(
                other.calculate_battle(CARD, CARD, rules.CRTLCG(21)),
                battle(21),
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
                battle(9),
            )
        with NativeRules(self.image, self.bridge) as reopened:
            self.assertEqual(
                reopened.choose_move(HAND, BOARD, 0, rules.CRTLCG(9)),
                move(9),
            )

    def test_abi_version_is_checked_before_image_open(self):
        with patch("tmserver.game.original.native.ctypes.CDLL") as load:
            load.return_value.tm_rules_abi.return_value = 1
            with self.assertRaisesRegex(OriginalCodeError, "ABI 1; expected 3"):
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

    def test_frozen_original_results_and_draw_order(self):
        self.assertEqual(
            verify_vectors(self.backend),
            {
                **{name: record["count"] for name, record in VECTORS["groups"].items()},
                "special_moves": len(SPECIAL_VECTORS),
            },
        )

    def test_move_abi_rejects_malformed_records_before_randomness(self):
        draws = []

        @native_api._Random
        def callback(*_):
            draws.append(1)
            return 1

        invalid = (
            ("board_size", 0),
            ("board_size", 24),
            ("board_size", 0xFFFFFFFF),
            ("owner", 3),
            ("count", 6),
            ("model", 250),
            ("model", 0x800C),
            ("cell_owner", 3),
            ("arrows", 256),
            ("property", 256),
            ("direction", 8),
        )
        for field, value in invalid:
            request = native_api._MoveRequest()
            request.count, request.owner, request.board_size = 1, 0, 25
            request.hand[0] = native_api._HandCard(1, 255)
            for index in range(25):
                request.board[index] = native_api._BoardCell(0xFFFF, 0, 8, 0, 0)
            if field in ("board_size", "owner", "count"):
                setattr(request, field, value)
            else:
                setattr(request.board[24], "owner" if field == "cell_owner" else field, value)
            result = native_api._MoveResult(91, 92)
            with self.subTest(field=field, value=value):
                self.assertEqual(
                    self.backend._bridge.tm_rules_move(
                        ctypes.byref(request), callback, None, ctypes.byref(result)
                    ),
                    2,
                )
                self.assertEqual((result.hand, result.field), (91, 92))
        self.assertEqual(draws, [])

    def test_known_native_results(self):
        self.assertEqual(self.backend.calculate_battle(CARD, CARD, rules.CRTLCG(7)), battle(7))
        self.assertEqual(self.backend.choose_move(HAND, BOARD, 0, rules.CRTLCG(7)), move(7))
        self.assertEqual(self.backend.grow_card(CARD, BASELINE, 1, 0), growth(1))
        self.assertEqual(self.backend.choose_target((14, 1, 6), iter((0, 1)).__next__), 1)

    def test_interleaved_calls_reset_global_state(self):
        for seed in (7, 9, 19, 21):
            self.backend.choose_target((24, 3), iter((1, seed)).__next__, board_size=25)
            self.backend.grow_card(CARD, BASELINE, 1, seed)
            self.assertEqual(
                self.backend.calculate_battle(CARD, CARD, rules.CRTLCG(seed)), battle(seed)
            )
            self.assertEqual(
                self.backend.choose_move(HAND, BOARD, 0, rules.CRTLCG(seed)), move(seed)
            )
            self.assertEqual(
                snapshot(self.backend.grow_card(CARD, BASELINE, 0, 0)), VECTORS["growth"]["0"]
            )


if __name__ == "__main__":
    unittest.main()
