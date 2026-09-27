"""Decoded card catalogue, generation and collection arithmetic."""

import json
from fractions import Fraction
from pathlib import Path

from tmserver.cards import Card
from tmserver.economy_errors import EconomyError
from tmserver.resources import read_json

_ARROW_COUNTS = tuple(
    n for n, count in enumerate((1, 4, 16, 31, 27, 10, 6, 3, 2)) for _ in range(count)
)


class Catalog:
    def __init__(self, path=None):
        data = (
            read_json("economy_catalog.json")
            if path is None
            else json.loads(Path(path).read_text(encoding="utf8"))
        )
        self.cards = tuple(data["cards"])
        self.packs = tuple(data["packs"])
        self.prices = tuple(data["price_tiers"])
        if len(self.cards) != 250 or len(self.packs) != 78:
            raise EconomyError("unexpected decoded catalogue")

    def points(self, model, attack, physical, magical):
        b = self.cards[model]
        return (
            (attack + physical + magical)
            * b["base_card_points"]
            // (b["attack"] + b["physical_defense"] + b["magical_defense"])
        ) & 255

    def validate(self, card):
        fields = card.full()
        if any(type(x) is not int for x in fields):
            raise EconomyError("card fields must be integers")
        if not 0 <= card.model < len(self.cards) or not 0 <= card.kind <= 3:
            raise EconomyError("unknown card or type")
        if any(not 0 <= x <= 255 for x in fields[1:]):
            raise EconomyError("card byte out of range")
        if card.attack + card.physical + card.magical == 0:
            raise EconomyError("empty card stats")
        if card.deck not in range(50) and card.deck != 255:
            raise EconomyError("unknown deck slot")
        if card.points != self.points(card.model, card.attack, card.physical, card.magical):
            raise EconomyError("card points disagree with stats")
        return card

    def sale_value(self, card):
        b = self.cards[card.model]
        # Rational form matches all positive-baseline original sale fixtures.
        scale = (
            Fraction(card.attack, b["attack"])
            + Fraction(card.physical, b["physical_defense"])
            + Fraction(card.magical, b["magical_defense"])
            + Fraction(max(card.kind, 1), 2)
        )
        return int(scale * self.prices[b["price_tier"]] / 4)

    def select(self, pack_id, guild, draw):
        p = self.packs[pack_id]
        excluded = ((), (2, 4, 6), (2, 3), (3, 4, 6))[guild]
        for _ in range(256):
            roll = draw() & 255
            group = next(
                (
                    g
                    for g in p["generation_bins"]
                    if g["inclusive_threshold"] and roll <= g["inclusive_threshold"]
                ),
                None,
            )
            if group is None:
                entry = next(
                    (
                        e
                        for e in p["explicit_models"]
                        if e["inclusive_threshold"]
                        and roll <= e["inclusive_threshold"]
                        and e["model_id"] < len(self.cards)
                    ),
                    None,
                )
                if entry is not None:
                    return entry["model_id"]
                continue
            lo, hi = p["model_range_inclusive_exclusive"]
            choices = [
                i
                for i in range(lo, hi)
                if self.cards[i]["filter_category_7"] not in excluded
                and (
                    group["price_tier_filter"] == 255
                    or self.cards[i]["price_tier"] == group["price_tier_filter"]
                )
                and (
                    not group["category_6_filter"]
                    or self.cards[i]["filter_category_6"] == group["category_6_filter"]
                )
                and (
                    not group["category_7_filter"]
                    or self.cards[i]["filter_category_7"] == group["category_7_filter"]
                )
            ]
            if choices:
                return choices[draw() % len(choices)]
        return 148

    def generate(self, model, draw):
        b = self.cards[model]
        attack = b["attack"] // 2 + (draw() % b["attack"]) // 2
        kind = 2 if draw() % 256 < 2 else b["type"]
        physical = b["physical_defense"] // 2 + (draw() % b["physical_defense"]) // 2
        magical = b["magical_defense"] // 2 + (draw() % b["magical_defense"]) // 2
        if model == 148:
            arrows = 1 << (draw() % 8)
        else:
            count = _ARROW_COUNTS[draw() % 100]
            pool, arrows = list(range(8)), 0
            for _ in range(count):
                arrows |= 1 << pool.pop(draw() % len(pool))
        return self.validate(
            Card(
                model,
                attack,
                kind,
                physical,
                magical,
                self.points(model, attack, physical, magical),
                arrows,
            )
        )


def collection_points(cards):
    best = {}
    for c in cards:
        best[c.model] = max(best.get(c.model, 0), c.points)
    return (
        sum(best.values())
        + sum(max(c.kind - 1, 0) for c in cards)
        + 5 * len({c.arrows for c in cards})
    ) & 65535
