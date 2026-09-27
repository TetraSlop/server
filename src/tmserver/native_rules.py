"""Pure ports of original TM100D31D0,100B3630 and101E2E44.

This recovers client-local arithmetic, not historical hosted seeds/draw order.
No sockets, assets, file I/O or presentation code. See NATIVE_RULES_NOTES.md.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Callable

from tmserver.cards import Card as CardValue


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

    def wire(self, board_index):
        """Five decimal-array values for BattleData/A or /D; no framing."""
        return (
            _integer(board_index, "board index", 0, 24),
            self.start,
            self.final,
            self.selector,
            self.multiplier,
        )


@dataclass(frozen=True)
class BattleResult:
    attacker: BattleSide
    defender: BattleSide
    raw_draws: tuple[int, ...]

    @property
    def comparison(self):
        """1 attacker wins, -1 defender wins, 0 tie requiring another battle."""
        return (self.attacker.final > self.defender.final) - (
            self.attacker.final < self.defender.final
        )

    @property
    def tied(self):
        return self.comparison == 0

    @property
    def calculator_return(self):
        """Original100D31D0 returns2 on ties too; not the presentation outcome."""
        return 1 if self.comparison > 0 else 2


def _battle_stats(card):
    return tuple(
        _integer(getattr(card, name), name, 0, 255) for name in ("attack", "physical", "magical")
    )


def calculate_battle(
    attacker,
    defender,
    draw: Callable[[], int],
    *,
    attacker_modifier=0,
    defender_modifier=0,
    attacker_property=0,
    defender_property=0,
):
    """Exact native battle calculation for valid cards/nonnegative raw draws.

    Card-like inputs need attack/kind/physical/magical attributes. Modifiers are
    signed bytes; properties are full bytes (only low nibble is used here).
    Four draws normally; one extra per active multiplier, two more only when
    both initial finals are zero. No tie reroll or ownership mutation occurs.
    """
    aa, ap, am = _battle_stats(attacker)
    da, dp, dm = _battle_stats(defender)
    kind = _integer(attacker.kind, "attacker kind", 0, 3)
    _integer(defender.kind, "defender kind", 0, 3)
    a_mod = _integer(attacker_modifier, "attacker modifier", -128, 127)
    d_mod = _integer(defender_modifier, "defender modifier", -128, 127)
    a_prop = _integer(attacker_property, "attacker property", 0, 255) & 15
    d_prop = _integer(defender_property, "defender property", 0, 255) & 15
    # Native ties prefer magical, then physical, then attack. Strict branch
    # comparisons at100D3263..100D3340 also govern the displayed selector.
    if kind == 3:
        a, sa = (aa, 1) if aa > ap and aa > am else ((ap, 4) if ap > am else (am, 8))
        d, sd = (da, 1) if da < dp and da < dm else ((dp, 4) if dp < dm else (dm, 8))
    else:
        a, sa = aa, 1
        if kind == 0:
            d, sd = dp, 4
        elif kind == 1:
            d, sd = dm, 8
        else:
            d, sd = (dp, 4) if dp < dm else (dm, 8)
    a = max(a + a_mod, 1)
    d = max(d + d_mod, 1)
    consumed = []

    def raw():
        value = _integer(draw(), "raw random draw", 0, 0x7FFFFFFF)
        consumed.append(value)
        return value

    ma = raw() % 4 + 2 if a_prop == 1 else 1
    md = raw() % 4 + 2 if d_prop == 2 else 1
    r0, r1, r2, r3 = raw(), raw(), raw(), raw()
    av = ((r0 & 65535) % (a * ma + 1) + (r1 & 65535) % (a * ma + 1)) // 2
    dv = ((r2 & 65535) % (d * md + 1) + (r3 & 65535) % (d * md + 1)) // 2
    if av + dv == 0:
        av, dv = raw() & 1, raw() & 1
    return BattleResult(BattleSide(a, av, sa, ma), BattleSide(d, dv, sd, md), tuple(consumed))


@dataclass(frozen=True)
class GrowthBaseline:
    attack: int
    physical: int
    magical: int
    points: int
    price: int

    def __post_init__(self):
        # All250 recovered catalogue baselines have positive three stats.
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


def _extended(value, precision):
    """Round a finite rational to an explicit x87 significand, nearest/even.

    All supported growth inputs stay normal and finite. Fraction arithmetic
    keeps the original instruction order/rounding independent of host floats.
    """
    value = Fraction(value)
    if not value:
        return value
    sign = -1 if value < 0 else 1
    n, d = abs(value.numerator), value.denominator
    exponent = n.bit_length() - d.bit_length()
    below = (n < d << exponent) if exponent >= 0 else (n << -exponent < d)
    if below:
        exponent -= 1
    shift = precision - 1 - exponent
    if shift >= 0:
        n <<= shift
    else:
        d <<= -shift
    q, r = divmod(n, d)
    if 2 * r > d or (2 * r == d and q & 1):
        q += 1
    return sign * (Fraction(q, 1 << shift) if shift >= 0 else Fraction(q << -shift))


def _sale_value(card, baseline, precision):
    # 100B386B..100B38DB: normalize attack, then physical, then magical; add
    # type/2, multiply price then1/4, and truncate. Preserve x87 rounding.
    norms = []
    rounded = lambda value: _extended(value, precision)
    for value, base in (
        (card.attack, baseline.attack),
        (card.physical, baseline.physical),
        (card.magical, baseline.magical),
    ):
        normalized = rounded(Fraction(2 * value - base, base))
        normalized = rounded(normalized + 1)
        norms.append(rounded(normalized / 2))
    total = rounded(norms[0] + norms[1])
    total = rounded(total + norms[2])
    total = rounded(total + Fraction(max(card.kind, 1), 2))
    total = rounded(total * baseline.price)
    total = rounded(total / 4)
    return int(total) & 0xFFFFFFFF


def grow_card(card, baseline: GrowthBaseline, mode, random_value, *, x87_precision=64):
    """Port the card-state part of100B3630; omit its visual growth indicator.

    mode low byte0 promotes type; any other low byte grows one stat. Random
    input also narrows to its low byte. Recomputes points and sale even when
    capped/no promotion. Does not decide eligibility, ownership or persistence.
    x87_precision53/64 controls only derived sale arithmetic; native function
    inherits that control-word setting. Default64 matches the existing oracle.
    """
    if _integer(x87_precision, "x87 precision") not in (53, 64):
        raise RulesError("supported x87 significands are53 or64 bits")
    old = Card.from_card(card)
    mode = _integer(mode, "growth mode") & 255
    r = _integer(random_value, "growth random") & 255
    values = {name: getattr(old, name) for name in Card.__dataclass_fields__}
    changed = None
    if mode == 0:
        if old.kind < 2 and r < 4:
            values["kind"] = 2
            changed = "kind"
        elif old.kind == 2 and r < 1:
            values["kind"] = 3
            changed = "kind"
    else:
        field = ("attack", "physical", "magical")[r % 3]
        if values[field] < getattr(baseline, field):
            values[field] += 1
            changed = field
    values["points"] = (
        (values["attack"] + values["physical"] + values["magical"])
        * baseline.points
        // (baseline.attack + baseline.physical + baseline.magical)
    ) & 255
    result = Card(**values)
    return GrowthResult(result, _sale_value(result, baseline, x87_precision), changed)
