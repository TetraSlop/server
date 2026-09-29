"""Growth cases from the original oracle and immediate target boundary cases."""

import random
from itertools import product

from tmserver.data.resources import read_json
from tmserver.game import rules


def growth_cases():
    catalog = read_json("economy_catalog.json")
    baselines = tuple(
        rules.GrowthBaseline.from_catalog(row, catalog["price_tiers"]) for row in catalog["cards"]
    )
    for kind, mode, value in product(range(4), range(2), range(256)):
        yield rules.Card(5, 10, kind, 10, 10, 250, 129, 7), baselines[5], mode, value, 64
    for model, row in enumerate(catalog["cards"]):
        base = (row["attack"], row["physical_defense"], row["magical_defense"])
        for stats, value in product(
            ((0, 0, 0), tuple(x // 2 for x in base), base, tuple(min(255, x + 1) for x in base)),
            range(3),
        ):
            yield (
                rules.Card(model, stats[0], row["type"], stats[1], stats[2], 255, 255, 49),
                baselines[model],
                1,
                value,
                64,
            )
    rng = random.Random(731992)
    for _ in range(600):
        card = rules.Card(
            rng.randrange(250),
            rng.randrange(256),
            rng.randrange(4),
            *(rng.randrange(256) for _ in range(5)),
        )
        yield (
            card,
            baselines[card.model],
            rng.choice((0, 1, 2, 255, 256, 257, -1)),
            rng.randrange(-0x80000000, 0x100000000),
            64,
        )
    rng = random.Random(532026)
    for _ in range(600):
        card = rules.Card(
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
    for size in (16, 25):
        for count in range(9):
            # Descending order also proves the adapter does not sort candidates.
            targets = tuple(range(size - 1, size - count - 1, -1))
            for trigger, choice in product((0, 1, 2, 32767), (0, 1, 7, 8, 32767)):
                yield targets, size, (trigger, choice)
