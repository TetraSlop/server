"""Native calculation result models and server-owned random state.

Inputs are range-checked once, by the bridge's valid() before any draw.
"""

from dataclasses import dataclass

from tmserver.cards.model import Card


class CRTLCG:
    """Original CRT rand: callable, with an explicit restorable uint32 state."""

    def __init__(self, seed=1):
        self.state = seed & 0xFFFFFFFF

    def __call__(self):
        self.state = (self.state * 214013 + 2531011) & 0xFFFFFFFF
        return (self.state >> 16) & 32767


@dataclass(frozen=True)
class BattleSide:
    start: int
    final: int
    selector: int
    multiplier: int


@dataclass(frozen=True)
class BattleResult:
    attacker: BattleSide
    defender: BattleSide
    raw_draws: tuple[int, ...]


@dataclass(frozen=True)
class GrowthBaseline:
    attack: int
    physical: int
    magical: int
    points: int
    price: int

    @classmethod
    def from_catalog(cls, row, price_tiers):
        return cls(
            row["attack"],
            row["physical_defense"],
            row["magical_defense"],
            row["base_card_points"],
            price_tiers[row["price_tier"]],
        )


@dataclass(frozen=True)
class GrowthResult:
    card: Card
    sale_value: int
    changed_field: str | None

