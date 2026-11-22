"""The client's battle and growth arithmetic, its result models and its CRT rand.

Ported from TM.dll. Inputs are range-checked before any draw.
"""

from dataclasses import astuple, dataclass
from fractions import Fraction

from tmserver.cards.model import Card, card_values


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



class RulesError(ValueError):
    """An input outside the client's domain, or an out-of-range random draw."""


def _int(value, low=0, high=255):
    return type(value) is int and low <= value <= high


def calculate_battle(
    attacker,
    defender,
    draw,
    *,
    attacker_modifier=0,
    defender_modifier=0,
    attacker_property=0,
    defender_property=0,
):
    """TM.dll +D31D0. Four draws, one more per active multiplier, and two more
    only when both finals are zero. Ties are returned, not rerolled."""
    sides = (
        (attacker, attacker_modifier, attacker_property),
        (defender, defender_modifier, defender_property),
    )
    if not all(
        _int(c.attack) and _int(c.physical) and _int(c.magical) and _int(c.kind, 0, 3)
        and _int(modifier, -128, 127) and _int(prop)
        for c, modifier, prop in sides
    ):
        raise RulesError("battle input outside the client's domain")
    aa, ap, am = attacker.attack, attacker.physical, attacker.magical
    da, dp, dm = defender.attack, defender.physical, defender.magical
    # Strict comparisons: ties prefer magical, then physical, then attack.
    if attacker.kind == 3:
        a, sa = (aa, 1) if aa > ap and aa > am else (ap, 4) if ap > am else (am, 8)
        d, sd = (da, 1) if da < dp and da < dm else (dp, 4) if dp < dm else (dm, 8)
    else:
        a, sa = aa, 1
        d, sd = ((dp, 4), (dm, 8), (dp, 4) if dp < dm else (dm, 8))[attacker.kind]
    a = max(a + attacker_modifier, 1)
    d = max(d + defender_modifier, 1)
    used = []

    def raw():
        value = draw()
        if not _int(value, 0, 0x7FFFFFFF):
            raise RulesError("Random draw must be an integer in 0..2147483647")
        used.append(value)
        return value

    ma = raw() % 4 + 2 if attacker_property & 15 == 1 else 1
    md = raw() % 4 + 2 if defender_property & 15 == 2 else 1
    r0, r1, r2, r3 = raw(), raw(), raw(), raw()
    av = ((r0 & 65535) % (a * ma + 1) + (r1 & 65535) % (a * ma + 1)) // 2
    dv = ((r2 & 65535) % (d * md + 1) + (r3 & 65535) % (d * md + 1)) // 2
    if av + dv == 0:
        av, dv = raw() & 1, raw() & 1
    return BattleResult(BattleSide(a, av, sa, ma), BattleSide(d, dv, sd, md), tuple(used))


def _x87(value, precision):
    """Round a rational to a precision-bit significand, to nearest even, as the x87 does."""
    if not value:
        return value
    n, d = abs(value.numerator), value.denominator
    exponent = n.bit_length() - d.bit_length()
    if (n < d << exponent) if exponent >= 0 else (n << -exponent < d):
        exponent -= 1
    shift = precision - 1 - exponent
    if shift >= 0:
        n <<= shift
    else:
        d <<= -shift
    q, r = divmod(n, d)
    if 2 * r > d or (2 * r == d and q & 1):
        q += 1
    q = Fraction(q, 1 << shift) if shift >= 0 else Fraction(q << -shift)
    return q if value > 0 else -q


def grow_card(card, baseline, mode, random_value, *, x87_precision=24):
    """TM.dll +B3630 without its growth animation.

    A mode low byte of 0 promotes the type; any other grows one stat, picked by
    the random low byte. Points and the sale value are recomputed even when
    nothing grows. The sale value is x87 arithmetic, so it depends on the
    precision: the client runs at 24 bits, which Direct3D sets on CreateDevice.
    """
    values, base = card_values(card), astuple(baseline)
    if not (
        _int(values[0], 0, 65534) and _int(values[2], 0, 3) and all(map(_int, values[1:]))
        and all(_int(v, 1) for v in base[:3]) and _int(base[3]) and _int(base[4], 0, 0x7FFFFFFF)
        and type(mode) is int and type(random_value) is int and x87_precision in (24, 53, 64)
    ):
        raise RulesError("growth input outside the client's domain")
    grown = dict(zip(Card.__dataclass_fields__, values))
    r, changed = random_value & 255, None
    if mode & 255 == 0:
        kind = grown["kind"]
        if kind < 2 and r < 4 or kind == 2 and r < 1:
            grown["kind"], changed = max(kind + 1, 2), "kind"
    else:
        field = ("attack", "physical", "magical")[r % 3]
        if grown[field] < getattr(baseline, field):
            grown[field] += 1
            changed = field
    grown["points"] = (
        (grown["attack"] + grown["physical"] + grown["magical"]) * baseline.points
        // (baseline.attack + baseline.physical + baseline.magical)
    ) & 255
    grown = Card(**grown)

    def x87(value):
        return _x87(value, x87_precision)

    # +B386B..+B38DB: attack, physical, magical, then type / 2, * price, / 4, truncated.
    total = 0
    for stat, stat_base in zip((grown.attack, grown.physical, grown.magical), base):
        total = x87(total + x87(x87(x87(Fraction(2 * stat - stat_base, stat_base)) + 1) / 2))
    total = x87(x87(x87(total + Fraction(max(grown.kind, 1), 2)) * baseline.price) / 4)
    return GrowthResult(grown, int(total) & 0xFFFFFFFF, changed)
