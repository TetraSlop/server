"""Compare and benchmark the optional original-code backend without starting a game."""

import argparse
import json
import platform
import random
import statistics
import struct
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

from tmserver.game import ai, rules
from tmserver.game.original.image import IMAGE_SHA256


class Draws:
    def __init__(self, values):
        self.values = iter(values)
        self.used = []

    def __call__(self):
        value = next(self.values)
        self.used.append(value)
        return value


def battle_cases(count=1024):
    """Selector ties, signed limits, multiplier flags, zero fallback and wide RNG."""
    rng = random.Random(271828)
    for kind in range(4):
        for modifier in (-128, 0, 127):
            for properties in ((0, 0), (1, 2), (0xF1, 0xF2), (2, 1)):
                for value in (0, 32767, 0x7FFFFFFF):
                    card = rules.Card(0, 0, kind, 0, 0, 1, 255)
                    yield (
                        (card, card),
                        dict(
                            attacker_modifier=modifier,
                            defender_modifier=modifier,
                            attacker_property=properties[0],
                            defender_property=properties[1],
                        ),
                        (value,) * 8,
                    )
    for _ in range(count):
        cards = tuple(
            rules.Card(
                rng.randrange(250),
                rng.randrange(256),
                rng.randrange(4),
                rng.randrange(256),
                rng.randrange(256),
                1,
                rng.randrange(256),
            )
            for _ in range(2)
        )
        options = dict(
            attacker_modifier=rng.randrange(-128, 128),
            defender_modifier=rng.randrange(-128, 128),
            attacker_property=rng.randrange(256),
            defender_property=rng.randrange(256),
        )
        yield cards, options, tuple(rng.randrange(0x80000000) for _ in range(8))


def move_cases(count=256):
    """Deterministic ordinary boards, including no-move and one-move boundaries."""
    rng = random.Random(314159)
    for index in range(count):
        hand = tuple(
            rules.Card(rng.randrange(250), 50, 0, 40, 30, 1, rng.randrange(256))
            for _ in range(index % 6)
        )
        board = []
        for field in range(16):
            roll = rng.randrange(10)
            board.append(
                None
                if roll < 4
                else ai.BLOCKED
                if roll == 4
                else ai.Cell(
                    rules.Card(rng.randrange(250), 50, 0, 40, 30, 1, rng.randrange(256)),
                    rng.randrange(2),
                )
            )
        if index % 16 == 0:
            board = [ai.BLOCKED] * 16
        elif index % 16 == 1:
            board = [ai.BLOCKED] * 15 + [None]
        elif index % 16 == 2:
            board = [None] * 16
        personality = ai.Personality(
            (0, 49, 100, 255)[index % 4],
            (0, 1, 2, 3, 4, 5, 255)[index % 7],
            (0, 50, 100, 255)[index // 4 % 4],
        )
        yield (
            (hand, tuple(board), index % 2),
            {"personality": personality},
            tuple(rng.randrange(32768) for _ in range(6)),
        )


def compare(reference, original, cases, backend):
    count = 0
    for index, (args, options, values) in enumerate(cases):
        expected_draws, actual_draws = Draws(values), Draws(values)
        expected = reference(*args, expected_draws, **options)
        actual = original(*args, actual_draws, **options)
        if actual != expected or actual_draws.used != expected_draws.used:
            raise AssertionError(
                f"{reference.__name__} differs at case {index}: {actual} != {expected}"
            )
        if backend.last_draws != tuple(expected_draws.used):
            raise AssertionError(f"Draw accounting differs at {reference.__name__} case {index}")
        count += 1
    return count


def timing(function, case, repetitions):
    args, options, values = case
    for _ in range(10):
        function(*args, Draws(values), **options)
    samples = []
    for _ in range(repetitions):
        start = time.perf_counter_ns()
        function(*args, Draws(values), **options)
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    samples.sort()
    return {
        "median_ms": round(statistics.median(samples), 4),
        "p95_ms": round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 4),
    }


def probe(image, *, repetitions=200, bridge=None):
    """Supplying an x86 bridge selects native execution without importing Unicorn."""
    start = time.perf_counter()
    with ExitStack() as cleanup:
        if bridge is None:
            from tmserver.game.original.backend import OriginalRules

            backend = OriginalRules(image)
        else:
            from tmserver.game.original.native import NativeRules

            backend = cleanup.enter_context(NativeRules(image, bridge))
        initialization_ms = (time.perf_counter() - start) * 1000
        report = measure(backend, repetitions)
        report.update(
            backend="original-x86-native" if bridge is not None else "original-x86-unicorn",
            initialization_ms=round(initialization_ms, 3),
        )
        return report


def measure(backend, repetitions):
    battles, moves = list(battle_cases()), list(move_cases())
    matched = {
        "battles": compare(rules.calculate_battle, backend.calculate_battle, battles, backend),
        "moves": compare(ai.choose_move, backend.choose_move, moves, backend),
    }
    # Run a populated-board scoring case, not an empty-hand early return.
    move_case = next(
        case
        for case in moves
        if len(case[0][0]) == 5 and case[1]["personality"].score_chance >= 100
    )
    measurements = {}
    for name, reference, original, case in (
        ("battle", rules.calculate_battle, backend.calculate_battle, battles[-1]),
        ("move", ai.choose_move, backend.choose_move, move_case),
    ):
        measurements[name] = {
            "python": timing(reference, case, repetitions),
            "original": timing(original, case, repetitions),
        }
    card = rules.Card(1, 50, 0, 40, 30, 1, 255)
    sample = backend.calculate_battle(card, card, rules.CRTLCG(11))
    return {
        "image_sha256": IMAGE_SHA256,
        "python": platform.python_version(),
        "python_bits": struct.calcsize("P") * 8,
        "platform": platform.platform(),
        "matched": matched,
        "exact_random_draws": True,
        "timing_includes_memory_reset": True,
        "repetitions": repetitions,
        "timings": measurements,
        "sample_battle": asdict(sample),
        "scope": "Battle calculation and ordinary two-player AI only; live server unchanged",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", type=Path, required=True, help="unpacked TM_emulated_mapped.dll memory image"
    )
    parser.add_argument("--output", type=Path, help="optional JSON report")
    parser.add_argument(
        "--bridge",
        type=Path,
        help="run natively using this bridge DLL (requires 32-bit Windows Python)",
    )
    parser.add_argument("--repetitions", type=int, default=200)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    report = (
        json.dumps(probe(args.image, repetitions=args.repetitions, bridge=args.bridge), indent=2)
        + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
