"""The client's computer-player hand, placement and battle-target choices.

Ported from TM.dll: hand +C42DE, placement +D68D0 with its scorer +D6AA0, and
target +CEB2B. The server's clock is always ready, so each poll's rate gate
still draws once but always passes. Draws are CRT rand values (0..32767).
"""

from dataclasses import dataclass

from tmserver.data.resources import read_json
from tmserver.game.rules import RulesError
from tmserver.game.special_rules import DIRECTIONS, effective_arrows, neighbors

# CardPrm byte 5. The scorer keeps it in a marker's high nibble, so 16..24 wrap.
_TIERS = tuple(row["price_tier"] & 15 for row in read_json("economy_catalog.json")["cards"])


@dataclass(frozen=True)
class Personality:
    # Native player bytes +184, +185, +186. Default is local server policy,
    # not a recovered named NPC's personality.
    score_chance: int = 100
    preference: int = 0
    preference_chance: int = 100


@dataclass(frozen=True)
class Move:
    hand_index: int
    field: int


@dataclass(frozen=True)
class Cell:
    card: object
    owner: int


@dataclass(frozen=True)
class _Blocked:
    pass


BLOCKED = _Blocked()


@dataclass(frozen=True)
class _Square:
    model: int
    arrows: int
    owner: int
    property: int = 0
    direction: int = 0


def _byte(value, top=255):
    return type(value) is int and 0 <= value <= top


def _draw(raw_rng):
    value = raw_rng()
    if not _byte(value, 32767):
        raise RulesError("Random draw must be an integer in 0..32767")
    return value


def _players(size):
    if size not in (16, 25):
        raise RulesError("board size outside the client's domain")
    return size // 8


def _score(card, board, active, field, preference):
    players = len(board) // 8
    width = 5 if len(board) == 25 else 4
    markers = [0] * len(board)

    def mark(index, bit, overwrite=False):
        value = bit | _TIERS[board[index].model] << 4
        markers[index] = value if overwrite else markers[index] | value

    arrows = 255 if board[field].property & 15 == 3 else card.arrows
    for direction, target in neighbors(board, field):
        if not arrows & 1 << direction:
            continue
        other = board[target]
        if other.owner < players:
            if other.owner == active:
                continue
            if effective_arrows(other) & 1 << (direction + 4 & 7):
                # A reciprocal battle also marks the target's same-owner neighbours.
                for bit, index in neighbors(board, target):
                    if effective_arrows(other) & 1 << bit and board[index].owner == other.owner:
                        mark(index, 4)
                mark(target, 2)
            else:
                mark(target, 1)
        elif other.model == 0x8009:
            # Spinner prediction: player-count steps, no chain, last mark wins.
            dx, dy = DIRECTIONS[other.direction - 4 & 7]
            x, y = target % width, target // width
            for step in range(1, players + 1):
                index = (y + dy * step) % width * width + (x + dx * step) % width
                if board[index].owner < players and board[index].owner != active:
                    mark(index, 1, overwrite=True)
    affected = sum(bool(m & 15) for m in markers)
    if preference == 1:
        tier = max(m >> 4 for m in markers)
        return len(board) + tier if tier > 1 else affected
    if preference in (2, 4):
        count = sum(bool(m & preference) for m in markers)
        return len(board) + count if count else affected
    if preference == 3:
        return sum(2 + (m & 1) for m in markers if not m & 2)
    return affected


def choose_special_move(hand, board, active, raw_rng, personality=None):
    """Hand and placement for 4x4 and 5x5 boards, with tile properties and spinners."""
    personality = personality or Personality()
    hand = tuple(hand)
    players = _players(len(board))
    if (
        len(hand) > 5
        or not _byte(active, players - 1)
        or not all(map(_byte, (personality.score_chance, personality.preference,
                               personality.preference_chance)))
        or not all(_byte(c.model, 249) and _byte(c.arrows) for c in hand)
        or not all(
            _byte(c.arrows) and _byte(c.property) and _byte(c.direction, 7)
            and (
                (_byte(c.model, 249) and _byte(c.owner, players - 1))
                or (c.model == 0xFFFF and c.owner == 8)
                or (0x8000 <= c.model <= 0x800B and c.owner in (4, 8, 9))
            )
            for c in board
        )
    ):
        raise RulesError("computer move input outside the client's domain")
    fields = [i for i, c in enumerate(board) if c.owner == 8]
    if not hand or not fields:
        return None
    _draw(raw_rng)  # hand poll's rate gate
    index = _draw(raw_rng) % len(hand)
    _draw(raw_rng)  # placement poll's rate gate
    if _draw(raw_rng) % 100 < personality.score_chance:
        preference = (
            personality.preference if _draw(raw_rng) % 100 < personality.preference_chance else 0
        )
        scores = [_score(hand[index], board, active, f, preference) for f in fields]
        fields = [f for f, s in zip(fields, scores) if s == max(scores)]
    return Move(index, fields[_draw(raw_rng) % len(fields)])


def choose_move(hand, board, owner, raw_rng, personality=Personality()):
    """The same choice on a board of None, BLOCKED and Cell(card, owner)."""
    squares = tuple(
        _Square(0xFFFF, 0, 8)
        if cell is None
        else _Square(0x8000, 0, 9)
        if cell is BLOCKED
        else _Square(cell.card.model, cell.card.arrows, cell.owner)
        for cell in board
    )
    return choose_special_move(hand, squares, owner, raw_rng, personality)


def choose_target(candidates, raw_rng, *, board_size=16):
    """Pick the next battle; candidates keep the client's order."""
    candidates = tuple(candidates)
    _players(board_size)
    if (
        len(candidates) > 8
        or len(set(candidates)) != len(candidates)
        or not all(_byte(f, board_size - 1) for f in candidates)
    ):
        raise RulesError("battle candidates outside the client's domain")
    if len(candidates) < 2:
        # The client skips the choice for a sole target and draws nothing.
        return candidates[0] if candidates else None
    _draw(raw_rng)  # rate gate
    return candidates[_draw(raw_rng) % len(candidates)]
