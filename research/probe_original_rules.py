"""Verify and benchmark native original-code rules without launching the game."""

import argparse
import hashlib
import json
import platform
import random
import statistics
import struct
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from research.native_primitive_cases import growth_cases, target_cases
from tmserver.game import ai, rules, special_rules
from tmserver.game.original.image import IMAGE_SHA256
from tmserver.game.original.native import NativeRules

VECTORS = json.loads(Path(__file__).with_name("native_vectors.json").read_text(encoding="utf-8"))
SPECIAL_VECTORS = json.loads(
    Path(__file__).with_name("native_special_vectors.json").read_text(encoding="utf-8")
)["cases"]


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


def snapshot(value):
    """Canonical JSON representation, including tuple-valued native records."""
    return json.loads(json.dumps(asdict(value) if is_dataclass(value) else value))


def vector_digest(records):
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return {"count": len(records), "sha256": hashlib.sha256(encoded).hexdigest()}


def verify_vectors(backend):
    """Check frozen original-code outputs and draw order; no Python rules oracle."""
    actual = {}
    for name, function, cases in (
        ("battles", backend.calculate_battle, battle_cases()),
        ("moves", backend.choose_move, move_cases()),
    ):
        records = []
        for args, options, values in cases:
            draws = Draws(values)
            result = function(*args, draws, **options)
            if backend.last_draws != tuple(draws.used):
                raise AssertionError(f"Draw accounting differs in {name} case {len(records)}")
            records.append({"result": snapshot(result), "draws": list(backend.last_draws)})
        actual[name] = vector_digest(records)
    records = []
    for card, baseline, mode, value, precision in growth_cases():
        result = backend.grow_card(card, baseline, mode, value, x87_precision=precision)
        records.append({"result": snapshot(result), "draws": list(backend.last_draws)})
    actual["growth"] = vector_digest(records)
    records = []
    for targets, size, values in target_cases():
        draws = Draws(values)
        result = backend.choose_target(targets, draws, board_size=size)
        if backend.last_draws != tuple(draws.used):
            raise AssertionError(f"Draw accounting differs in targets case {len(records)}")
        records.append({"result": result, "draws": list(backend.last_draws)})
    actual["targets"] = vector_digest(records)
    for name, expected in VECTORS["groups"].items():
        if actual[name] != expected:
            raise AssertionError(
                f"Frozen native {name} vectors changed: {actual[name]} != {expected}"
            )
    for index, row in enumerate(SPECIAL_VECTORS):
        board = tuple(
            special_rules.Cell(
                model=model, arrows=arrows, owner=owner, property=prop, direction=direction
            )
            for model, arrows, owner, prop, direction in row["board"]
        )
        hand = tuple(rules.Card(model, 10, 0, 10, 10, 1, arrows) for model, arrows in row["hand"])
        draws = Draws(row["draws"])
        result = backend.choose_special_move(
            hand, board, row["active"], draws, ai.Personality(*row["personality"])
        )
        if snapshot(result) != row["expected"] or draws.used != row["used"]:
            raise AssertionError(f"Frozen special-board native vector changed at case {index}")
        if backend.last_draws != tuple(draws.used):
            raise AssertionError(f"Draw accounting differs in special-board case {index}")
    return {
        **{name: record["count"] for name, record in actual.items()},
        "special_moves": len(SPECIAL_VECTORS),
    }


def probe(image, *, bridge, repetitions=200):
    start = time.perf_counter()
    with NativeRules(image, bridge) as backend:
        initialization_ms = (time.perf_counter() - start) * 1000
        verified = verify_vectors(backend)
        battle_case = next(iter(battle_cases()))
        move_case = next(
            case
            for case in move_cases()
            if len(case[0][0]) == 5 and case[1]["personality"].score_chance >= 100
        )
        return {
            "backend": "original-x86-native",
            "image_sha256": IMAGE_SHA256,
            "python": platform.python_version(),
            "python_bits": struct.calcsize("P") * 8,
            "platform": platform.platform(),
            "initialization_ms": round(initialization_ms, 3),
            "verified": verified,
            "exact_random_draws": True,
            "repetitions": repetitions,
            "timings": {
                "battle": timing(backend.calculate_battle, battle_case, repetitions),
                "move": timing(backend.choose_move, move_case, repetitions),
            },
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", type=Path, required=True, help="TM_emulated_mapped.dll memory image"
    )
    parser.add_argument(
        "--bridge",
        type=Path,
        required=True,
        help="native bridge DLL; requires 32-bit Windows Python",
    )
    parser.add_argument("--output", type=Path, help="optional JSON report")
    parser.add_argument("--repetitions", type=int, default=200)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    report = (
        json.dumps(probe(args.image, bridge=args.bridge, repetitions=args.repetitions), indent=2)
        + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
