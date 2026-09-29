"""Compare the Win32 bridge with the independent existing Unicorn oracle.

Run with the research Python containing Unicorn. TM_ORIGINAL_IMAGE and
TM_NATIVE_BRIDGE are required; TM_ORIGINAL_RESEARCH defaults to the image's
directory and TM_NATIVE_PYTHON to build/python-x86/python.exe. The child uses
only the native bridge, and no game or UI is launched.
"""

import json
import os
import subprocess
import sys
import unittest
from dataclasses import asdict
from pathlib import Path

from research.native_primitive_cases import growth_cases, target_cases
from tmserver.game import rules
from tmserver.game.original.image import read_image
from tools.probe_original_rules import Draws

ROOT = Path(__file__).resolve().parents[1]


def collect_native():
    from tmserver.game.original.native import NativeRules

    with NativeRules(os.environ["TM_ORIGINAL_IMAGE"], os.environ["TM_NATIVE_BRIDGE"]) as backend:
        growth = [
            asdict(backend.grow_card(card, baseline, mode, value, x87_precision=precision))
            for card, baseline, mode, value, precision in growth_cases()
        ]
        targets = []
        for candidates, size, values in target_cases():
            draw = Draws(values)
            field = backend.choose_target(candidates, draw, board_size=size)
            targets.append(dict(field=field, draws=draw.used))
    print(json.dumps(dict(growth=growth, targets=targets)))


class NativePrimitiveOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image = Path(os.environ["TM_ORIGINAL_IMAGE"])
        research = Path(os.environ.get("TM_ORIGINAL_RESEARCH", image.parent))
        read_image(image)
        read_image(research / "TM_emulated_mapped.dll")
        sys.path.insert(0, str(research / "protocol_map"))
        child = subprocess.run(
            [
                os.environ.get("TM_NATIVE_PYTHON", str(ROOT / "build/python-x86/python.exe")),
                "-B",
                "-m",
                "research.native_primitive_oracle_tests",
                "--collect-native",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        cls.native = json.loads(child.stdout)

    def test_growth_matches_original_instructions_with_actual_catalogue(self):
        from result_reward_vectors import VM
        from unicorn.x86_const import UC_X86_REG_FPCW

        oracle = VM()
        address = 0x20130000
        count = 0
        for args, actual in zip(growth_cases(), self.native["growth"], strict=True):
            card, _baseline, mode, value, precision = args
            oracle.u.reg_write(UC_X86_REG_FPCW, 0x27F if precision == 53 else 0x37F)
            oracle.u.mem_write(address, bytes(0x9C))
            oracle.u.mem_write(address + 8, bytes(card.full()[1:7]))
            oracle.w16(address + 14, card.model)
            oracle.w8(address + 0x16, card.deck)
            oracle.call(0x100B3630, [mode, value, oracle.game], this=address)
            after = rules.Card(
                oracle.r16(address + 14),
                *(oracle.r8(address + offset) for offset in range(8, 14)),
                oracle.r8(address + 0x16),
            )
            changed = next(
                (
                    field
                    for field in ("attack", "kind", "physical", "magical")
                    if getattr(after, field) != getattr(card, field)
                ),
                None,
            )
            expected = rules.GrowthResult(after, oracle.r32(address + 0x10), changed)
            self.assertEqual(actual, asdict(expected), args)
            count += 1
        self.assertEqual(count, 6248)

    def test_target_fragment_matches_each_original_seat_and_random_stream(self):
        from match_timeout_vectors import TimerVM

        oracle = TimerVM()
        comparisons = 0
        for (targets, size, values), actual in zip(
            target_cases(), self.native["targets"], strict=True
        ):
            if len(targets) <= 1:
                self.assertEqual(actual, dict(field=targets[0] if targets else None, draws=[]))
                continue
            for owner in range(2 if size == 16 else 3):
                oracle.random, oracle.rng = list(values), []
                oracle.w32(0x102FF568, 1)
                oracle.w8(oracle.player + 0xE5, 2 if size == 16 else 3)
                oracle.w8(oracle.player + 0x19A + owner * 0x30, 1)
                oracle.w8(oracle.game + 0x171, 1)
                oracle.w8(oracle.game + 0x17D, len(targets))
                oracle.w8(oracle.game + 0x17A, 255)
                oracle.u.mem_write(oracle.game + 0x1DC, bytes(targets))
                oracle.branch(
                    0x100CEB2B, [0x100CEB96], {"EAX": owner, "EBP": oracle.game, "ESI": 0}
                )
                self.assertEqual(
                    actual,
                    dict(field=oracle.r8(oracle.game + 0x17A), draws=oracle.rng),
                    (targets, size, values, owner),
                )
                comparisons += 1
        self.assertEqual(comparisons, 700)

    def test_original_classifier_bypasses_randomness_for_one_target_on_both_boards(self):
        from match_timeout_vectors import TimerVM

        for size in (16, 25):
            oracle = TimerVM()
            oracle.random, oracle.rng = [], []
            oracle.w8(0x20300000, int(size == 25))
            oracle.w8(oracle.player + 0xE5, 2 if size == 16 else 3)
            oracle.w8(oracle.player + 0xE6, int(size == 25))
            for index in range(size):
                oracle.card(index, id=0xFFFF, owner=8, arrows=0)
            oracle.card(size - 2, owner=0, arrows=4)
            oracle.card(size - 1, owner=1, arrows=64)
            oracle.call(0x100D3760, [size - 2], this=oracle.game)
            oracle.step(2)
            self.assertEqual(oracle.r8(oracle.game + 0x17C), 6)
            self.assertEqual(oracle.r8(oracle.game + 0x17A), size - 1)
            self.assertEqual(oracle.rng, [])


if __name__ == "__main__":
    if sys.argv[1:] == ["--collect-native"]:
        collect_native()
    else:
        unittest.main()
