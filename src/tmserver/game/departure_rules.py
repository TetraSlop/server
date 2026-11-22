"""Client departure and ranking rules for ordinary matches.

gs0 Substitute, gs1 Invalid, gs2 Loss. Seat indices are always in server order.
Details: TetraPackets flows/departures.md.
"""

from dataclasses import dataclass

from tmserver.game.errors import MatchError


@dataclass(frozen=True)
class TurnBoundary:
    sequence: int
    active: int
    complete: bool


def next_turn(player_count, rule, departed, sequence, active, substitutes=()):
    """100C76A2..7762: each skipped inactive seat also consumes a sequence."""
    skipped = set(departed) - set(substitutes)
    if len(skipped) == player_count:
        raise MatchError("original loop has no remaining active player")
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


def result_classes(scores, rule=0, departed=()):
    """Result classes from 100CBD80, including the departure-only branches.

    'scores' is the pre-ranking board tally. Departure rank presets originate
    in 100C0AB0, so chronology is part of this contract. gs1 removes departures
    from comparisons; gs2 counts them as losses irrespective of their tally.
    """
    scores = tuple(scores)
    n = len(scores)
    departed = tuple(departed)
    if not rule or not departed:
        return tuple(2 + sum(o > s for o in scores) - sum(o < s for o in scores) for s in scores)
    live = [i for i in range(n) if i not in departed]
    classes = [0] * n
    for ordinal, seat in enumerate(departed, 1):
        classes[seat] = n - 2 * ordinal + 3
    for seat in live:
        equal = sum(scores[i] == scores[seat] for i in live)
        higher = sum(scores[i] > scores[seat] for i in live)
        classes[seat] = (equal + 2 * higher + (0 if rule == 1 else 2 - n)) & 255
    return tuple(classes)


def result_slots(remaining, board_slots, *, departed=False, rule=0):
    """100C87E2 appends board cards to surviving compacted hands, maximum5.

    Departed gs1 hands are excluded; gs2 loses the unplayed hand representation
    but keeps board-origin cards. This describes native presentation only; an
    authoritative inventory must retain selected-five provenance separately.
    """
    remaining = tuple(remaining)
    board_slots = tuple(board_slots)
    if departed and rule == 1:
        return ()
    return (() if departed and rule == 2 else remaining) + board_slots
