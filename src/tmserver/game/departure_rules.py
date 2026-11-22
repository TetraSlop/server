"""Client departure and ranking rules for ordinary matches.

gs0 Substitute, gs1 Invalid, gs2 Loss. Seat indices are always in server order.
Details: TetraPackets flows/departures.md.
"""

from dataclasses import dataclass


class DepartureError(ValueError):
    pass


def _inputs(player_count, rule, departed):
    if type(player_count) is not int or player_count not in (2, 3):
        raise DepartureError("two or three players required")
    if type(rule) is not int or rule not in (0, 1, 2):
        raise DepartureError("departure rule must be 0..2")
    departed = tuple(departed)
    if len(set(departed)) != len(departed) or any(
        type(s) is not int or not 0 <= s < player_count for s in departed
    ):
        raise DepartureError("departures must be distinct chronological seats")
    return departed


@dataclass(frozen=True)
class TurnBoundary:
    sequence: int
    active: int
    complete: bool


def next_turn(player_count, rule, departed, sequence, active, substitutes=()):
    """100C76A2..7762: each skipped inactive seat also consumes a sequence."""
    departed = _inputs(player_count, rule, departed)
    substitutes = tuple(substitutes)
    if any(s not in departed for s in substitutes):
        raise DepartureError("replacement must be departed")
    if (
        type(sequence) is not int
        or not 0 <= sequence <= 255
        or type(active) is not int
        or not 0 <= active < player_count
    ):
        raise DepartureError("invalid turn state")
    skipped = set(departed) - set(substitutes)
    if len(skipped) == player_count:
        raise DepartureError("original loop has no remaining active player")
    while True:
        sequence = (sequence + 1) & 255
        active = (active + 1) % player_count
        if active not in skipped:
            break
    return TurnBoundary(
        sequence,
        active,
        sequence >= 5 * player_count or (rule == 2 and player_count - len(departed) == 1),
    )


@dataclass(frozen=True)
class Ranking:
    scores: tuple[int, ...]
    classes: tuple[int, ...]
    lower: tuple[int, ...]
    multipliers: tuple[int, ...]
    order: tuple[int, ...]


def result_ranking(scores, rule=0, departed=()):
    """Exact100CBD80 ranking stores including the departure-only branches.

    'scores' is the pre-ranking board tally. Departure rank presets originate
    in 100C0AB0, so chronology is part of this contract. gs1 removes departures
    from comparisons; gs2 counts them as losses irrespective of their tally.
    """
    scores = tuple(scores)
    n = len(scores)
    departed = _inputs(n, rule, departed)
    if any(type(s) is not int or not 0 <= s <= 255 for s in scores):
        raise DepartureError("byte scores required")
    if not rule or not departed:
        lower = tuple(sum(o < s for o in scores) for s in scores)
        classes = tuple(2 + sum(o > s for o in scores) - lower[i] for i, s in enumerate(scores))
        return Ranking(
            scores,
            classes,
            lower,
            tuple(3 if s == sum(scores) else 1 for s in scores),
            tuple(sorted(range(n), key=lambda i: (-lower[i], i))),
        )
    total = sum(scores)
    live = [i for i in range(n) if i not in departed]
    classes = [0] * n
    lower = [0] * n
    mult = [1] * n
    ranks = [0] * n
    for ordinal, seat in enumerate(departed, 1):
        classes[seat] = n - 2 * ordinal + 3
        if rule == 1:
            ranks[seat] = n - ordinal
    for seat in live:
        equal = sum(scores[i] == scores[seat] for i in live)
        higher = sum(scores[i] > scores[seat] for i in live)
        classes[seat] = (equal + 2 * higher + (0 if rule == 1 else 2 - n)) & 255
        lower[seat] = (2 if rule == 1 else n) - equal - higher
        mult[seat] = 3 if scores[seat] == total and (rule == 1 or total > 0) else 1
    eligible = live if rule == 1 else range(n)
    for rank, seat in enumerate(sorted(eligible, key=lambda i: (-lower[i], i))):
        ranks[seat] = rank
    # 100CC4C2..535 compares positions1/2, but its stores write1 and 2 to
    # those SAME records. Preserve the resulting order, not an assumed swap.
    return Ranking(
        tuple(0 if i in departed else s for i, s in enumerate(scores)),
        tuple(classes),
        tuple(lower),
        tuple(mult),
        tuple(sorted(range(n), key=ranks.__getitem__)),
    )


def result_slots(remaining, board_slots, *, departed=False, rule=0):
    """100C87E2 appends board cards to surviving compacted hands, maximum5.

    Departed gs1 hands are excluded; gs2 loses the unplayed hand representation
    but keeps board-origin cards. This describes native presentation only; an
    authoritative inventory must retain selected-five provenance separately.
    """
    remaining = tuple(remaining)
    board_slots = tuple(board_slots)
    if rule not in (0, 1, 2) or any(
        type(i) is not int or not 0 <= i < 5 for i in remaining + board_slots
    ):
        raise DepartureError("invalid selected-card slot")
    if len(set(remaining + board_slots)) != len(remaining + board_slots):
        raise DepartureError("duplicate card slot")
    if departed and rule == 1:
        return ()
    return (() if departed and rule == 2 else remaining) + board_slots
