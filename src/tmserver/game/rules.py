"""Native calculation result models, validation, and server-owned random state."""

from dataclasses import dataclass

from tmserver.cards.model import Card as CardValue


class RulesError(ValueError):
    """Input is outside the supported native card/rule domain."""


def _integer(value, name, minimum=None, maximum=None):
    if (
        type(value) is not int
        or (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
    ):
        raise RulesError(f"{name} outside supported integer domain")
    return value


class CRTLCG:
    """Original CRT rand: callable, with an explicit restorable uint32 state."""

    def __init__(self, seed=1):
        self.state = seed

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = _integer(value, "random state") & 0xFFFFFFFF

    def __call__(self):
        self._state = (self._state * 214013 + 2531011) & 0xFFFFFFFF
        return (self._state >> 16) & 32767


@dataclass(frozen=True)
class Card(CardValue):
    def __post_init__(self):
        _integer(self.model, "model", 0, 65534)
        _integer(self.kind, "kind", 0, 3)
        for name in ("attack", "physical", "magical", "points", "arrows", "deck"):
            _integer(getattr(self, name), name, 0, 255)


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


def _battle_stats(card):
    return tuple(
        _integer(getattr(card, name), name, 0, 255) for name in ("attack", "physical", "magical")
    )


@dataclass(frozen=True)
class GrowthBaseline:
    attack: int
    physical: int
    magical: int
    points: int
    price: int

    def __post_init__(self):
        # All 250 recovered catalogue baselines have positive three stats.
        # Reject malformed zero denominators instead of emulating x87 faults.
        for name in ("attack", "physical", "magical"):
            _integer(getattr(self, name), f"baseline {name}", 1, 255)
        _integer(self.points, "baseline points", 0, 255)
        _integer(self.price, "price tier", 0, 0x7FFFFFFF)

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

