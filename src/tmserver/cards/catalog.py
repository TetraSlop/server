"""Decoded card catalogue, generation and collection arithmetic."""

import struct

from tmserver.cards.model import Card
from tmserver.economy.errors import EconomyError
from tmserver.data.resources import read_json

_ARROW_COUNTS = tuple(
    n for n, count in enumerate((1, 4, 16, 31, 27, 10, 6, 3, 2)) for _ in range(count)
)


class Catalog:
    def __init__(self):
        data = read_json("economy_catalog.json")
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
        """The value the client's shop and auction screens compute.

        The client runs its x87 at 24-bit precision once Direct3D is up, so
        every step rounds like IEEE single precision. Physical and magical are
        summed before attack; that order changes some results.
        """
        b = self.cards[card.model]

        def term(stat, base):
            return _single(_single((2 * stat - base) / base) + 1) * 0.5

        total = _single(
            _single(
                term(card.physical, b["physical_defense"])
                + term(card.magical, b["magical_defense"])
            )
            + term(card.attack, b["attack"])
        )
        total = _single(total + max(card.kind, 1) * 0.5)
        return int(_single(total * self.prices[b["price_tier"]]) * 0.25)

    def select(self, pack_id, guild, draw):
        p = self.packs[pack_id]
        excluded = ((), (2, 4, 6), (2, 3), (3, 4, 6))[guild]
        for _ in range(256):
            roll = draw() & 255
            bins = [g for g in p["generation_bins"] if g["threshold"] and roll <= g["threshold"]]
            if not bins:
                for e in p["explicit_models"]:
                    if e["threshold"] and roll <= e["threshold"] and e["model"] < len(self.cards):
                        return e["model"]
                continue
            group = bins[0]
            # 255 price tier and zero categories match any card.
            choices = [
                i
                for i in range(p["range_first"], p["range_end"])
                if self.cards[i]["category_7"] not in excluded
                and group["price_tier"] in (255, self.cards[i]["price_tier"])
                and group["category_6"] in (0, self.cards[i]["category_6"])
                and group["category_7"] in (0, self.cards[i]["category_7"])
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


def _single(value):
    # Double has more than 2 * 24 + 2 bits, so a double +, -, * or / of single
    # operands rounded to single equals the correctly rounded single result.
    return struct.unpack("<f", struct.pack("<f", value))[0]


def collection_points(cards):
    best = {}
    for c in cards:
        best[c.model] = max(best.get(c.model, 0), c.points)
    return (
        sum(best.values())
        + sum(max(c.kind - 1, 0) for c in cards)
        + 5 * len({c.arrows for c in cards})
    ) & 65535
