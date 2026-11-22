"""The Python rules against results frozen from the client's own code.

research/native_vectors.json holds digests of 8,032 battle, move, growth and
target results with the draws each consumed, captured by running TM.dll's
routines through the former x86 bridge. research/native_special_vectors.json
holds 35 special-board and three-player moves. Neither file changes.
"""

import hashlib
import json
import random
import unittest
from dataclasses import asdict, is_dataclass
from itertools import product

from tests import ROOT
from tmserver.data.resources import read_json
from tmserver.game import ai, rules, special_rules
from tmserver.game.rules import CRTLCG, Card, GrowthBaseline

VECTORS = json.loads((ROOT / "research/native_vectors.json").read_text(encoding="utf-8"))
SPECIAL = json.loads(
    (ROOT / "research/native_special_vectors.json").read_text(encoding="utf-8")
)["cases"]
# All 6,248 growth cases at 24 bits, from the bridge rebuilt with its x87 at
# 24-bit precision on 2026-11-22. Two differ from the recorded 53/64-bit vectors.
GROWTH_24 = "254e7e89512ac10b6093fc1b5ccaa8bcee912e9939362751214164f70eb81827"


class Draws:
    def __init__(self, values):
        self.values = iter(values)
        self.used = []

    def __call__(self):
        value = next(self.values)
        self.used.append(value)
        return value


def snapshot(value):
    return json.loads(json.dumps(asdict(value) if is_dataclass(value) else value))


def digest(records):
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return {"count": len(records), "sha256": hashlib.sha256(encoded).hexdigest()}


def battle_cases():
    """Selector ties, signed limits, multiplier flags, zero fallback and wide draws."""
    for kind, modifier, properties, value in product(
        range(4), (-128, 0, 127), ((0, 0), (1, 2), (0xF1, 0xF2), (2, 1)), (0, 32767, 0x7FFFFFFF)
    ):
        card = Card(0, 0, kind, 0, 0, 1, 255)
        effects = dict(
            attacker_modifier=modifier,
            defender_modifier=modifier,
            attacker_property=properties[0],
            defender_property=properties[1],
        )
        yield (card, card), effects, (value,) * 8
    rng = random.Random(271828)
    for _ in range(1024):
        cards = tuple(
            Card(
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
        effects = dict(
            attacker_modifier=rng.randrange(-128, 128),
            defender_modifier=rng.randrange(-128, 128),
            attacker_property=rng.randrange(256),
            defender_property=rng.randrange(256),
        )
        yield cards, effects, tuple(rng.randrange(0x80000000) for _ in range(8))


def move_cases():
    """Ordinary boards, including no-move and one-move boundaries."""
    rng = random.Random(314159)
    for index in range(256):
        hand = tuple(
            Card(rng.randrange(250), 50, 0, 40, 30, 1, rng.randrange(256))
            for _ in range(index % 6)
        )
        board = []
        for _ in range(16):
            roll = rng.randrange(10)
            board.append(
                None
                if roll < 4
                else ai.BLOCKED
                if roll == 4
                else ai.Cell(
                    Card(rng.randrange(250), 50, 0, 40, 30, 1, rng.randrange(256)),
                    rng.randrange(2),
                )
            )
        board = {0: [ai.BLOCKED] * 16, 1: [ai.BLOCKED] * 15 + [None], 2: [None] * 16}.get(
            index % 16, board
        )
        personality = ai.Personality(
            (0, 49, 100, 255)[index % 4],
            (0, 1, 2, 3, 4, 5, 255)[index % 7],
            (0, 50, 100, 255)[index // 4 % 4],
        )
        draws = tuple(rng.randrange(32768) for _ in range(6))
        yield (hand, tuple(board), index % 2), {"personality": personality}, draws


def growth_cases():
    """(card, baseline, mode, random value, x87 precision the vector was recorded at)."""
    catalog = read_json("economy_catalog.json")
    rows = catalog["cards"]
    baselines = tuple(GrowthBaseline.from_catalog(row, catalog["price_tiers"]) for row in rows)
    for kind, mode, value in product(range(4), range(2), range(256)):
        yield Card(5, 10, kind, 10, 10, 250, 129, 7), baselines[5], mode, value, 64
    for model, row in enumerate(rows):
        base = (row["attack"], row["physical_defense"], row["magical_defense"])
        for stats, value in product(
            ((0, 0, 0), tuple(x // 2 for x in base), base, tuple(min(255, x + 1) for x in base)),
            range(3),
        ):
            card = Card(model, stats[0], row["type"], stats[1], stats[2], 255, 255, 49)
            yield card, baselines[model], 1, value, 64
    rng = random.Random(731992)
    for _ in range(600):
        card = Card(
            rng.randrange(250),
            rng.randrange(256),
            rng.randrange(4),
            *(rng.randrange(256) for _ in range(5)),
        )
        mode = rng.choice((0, 1, 2, 255, 256, 257, -1))
        yield card, baselines[card.model], mode, rng.randrange(-0x80000000, 0x100000000), 64
    rng = random.Random(532026)
    for _ in range(600):
        card = Card(
            rng.randrange(250),
            rng.randrange(256),
            rng.randrange(4),
            rng.randrange(256),
            rng.randrange(256),
            0,
            0,
        )
        yield card, baselines[card.model], rng.randrange(2), rng.randrange(256), 53


def target_cases():
    for size, count in product((16, 25), range(9)):
        # Descending, so a sorted candidate list would show.
        targets = tuple(range(size - 1, size - count - 1, -1))
        for trigger, choice in product((0, 1, 2, 32767), (0, 1, 7, 8, 32767)):
            yield (targets,), {"board_size": size}, (trigger, choice)


def records(function, cases):
    out = []
    for args, options, values in cases:
        draws = Draws(values)
        out.append({"result": snapshot(function(*args, draws, **options)), "draws": draws.used})
    return out


def grown(recorded):
    """Every growth case, at its recorded precision or at the default."""
    return [
        {
            "result": snapshot(
                rules.grow_card(*case[:4], x87_precision=case[4])
                if recorded
                else rules.grow_card(*case[:4])
            ),
            "draws": [],
        }
        for case in growth_cases()
    ]


class FrozenVectorTests(unittest.TestCase):
    def assertGroup(self, name, records):
        self.assertEqual(digest(records), VECTORS["groups"][name])

    def test_battles(self):
        self.assertGroup("battles", records(rules.calculate_battle, battle_cases()))

    def test_moves(self):
        self.assertGroup("moves", records(ai.choose_move, move_cases()))

    def test_targets(self):
        self.assertGroup("targets", records(ai.choose_target, target_cases()))

    def test_growth_at_the_recorded_precision(self):
        self.assertGroup("growth", grown(recorded=True))

    def test_growth_defaults_to_the_clients_24_bits(self):
        client = grown(recorded=False)
        self.assertEqual(digest(client), {"count": 6248, "sha256": GROWTH_24})
        cases = list(growth_cases())
        for index, precision, recorded, at_24 in ((5313, 64, 2299, 2300), (6147, 53, 565, 564)):
            card, baseline, mode, value, _ = cases[index]
            result = rules.grow_card(card, baseline, mode, value, x87_precision=precision)
            self.assertEqual((result.sale_value, client[index]["result"]["sale_value"]),
                             (recorded, at_24))

    def test_special_and_three_player_moves(self):
        self.assertEqual(len(SPECIAL), 35)
        for index, row in enumerate(SPECIAL):
            board = tuple(
                special_rules.Cell(model=m, arrows=a, owner=o, property=p, direction=d)
                for m, a, o, p, d in row["board"]
            )
            hand = tuple(Card(model, 10, 0, 10, 10, 1, arrows) for model, arrows in row["hand"])
            draws = Draws(row["draws"])
            move = ai.choose_special_move(
                hand, board, row["active"], draws, ai.Personality(*row["personality"])
            )
            with self.subTest(index=index):
                self.assertEqual((snapshot(move), draws.used), (row["expected"], row["used"]))

    def test_samples(self):
        card = Card(1, 50, 0, 40, 30, 1, 255)
        for seed, sample in VECTORS["samples"].items():
            battle = rules.calculate_battle(card, card, CRTLCG(int(seed)))
            move = ai.choose_move((card,) * 5, (None,) * 16, 0, CRTLCG(int(seed)))
            self.assertEqual((snapshot(battle), snapshot(move)), (sample["battle"], sample["move"]))
        baseline = GrowthBaseline(100, 80, 60, 20, 100)
        for mode, expected in VECTORS["growth"].items():
            result = rules.grow_card(card, baseline, int(mode), 0, x87_precision=64)
            self.assertEqual(snapshot(result), expected)


if __name__ == "__main__":
    unittest.main()
