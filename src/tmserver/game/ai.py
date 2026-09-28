"""Pure port of the original PC client's ordinary 4x4 local computer choices.

Hand:100C42FD..100C4333; placement:100D68D0/100D69C0/100D6AA0;
battle target:100CEB4A..100CEB7A. See NATIVE_AI_NOTES.md for exact scope.
This is client-local AI, not a claim about the historical online server.

raw_rng() supplies one CRT rand result (0..32767). poll_* preserve the original
rate gate and may return None. choose_move/choose_target use denominator=1:
always-ready server scheduling while retaining every original gate draw.
Inputs are read-only: card-like objects need model/arrows; board occupants need
card/owner, None is empty, BLOCKED is an unavailable ordinary wall. Special
tiles and three-player placement scoring are outside this port. The independent
target chooser accepts explicit16/25-cell bounds and preserves candidate order.
"""

from dataclasses import dataclass


class AIError(ValueError):
    """Input outside the demonstrated ordinary-board AI contract."""


@dataclass(frozen=True)
class Personality:
    # Native player bytes +184, +185, +186. Default is local server policy,
    # not a recovered named NPC's personality.
    score_chance: int = 100
    preference: int = 0
    preference_chance: int = 100

    def __post_init__(self):
        for value in (self.score_chance, self.preference, self.preference_chance):
            if type(value) is not int or not 0 <= value <= 255:
                raise AIError("personality values must be original unsigned bytes")


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
DIRECTIONS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))
# CardPrm.BIN byte5, independently decoded in economy_catalog.json. The original
# evaluator stores this price tier in a four-bit marker, so tiers16..24 wrap.
PRICE_TIERS = (
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    3,
    3,
    11,
    2,
    6,
    2,
    3,
    2,
    3,
    3,
    3,
    4,
    3,
    3,
    4,
    3,
    4,
    3,
    4,
    6,
    3,
    4,
    3,
    3,
    4,
    4,
    3,
    5,
    5,
    5,
    6,
    6,
    9,
    22,
    9,
    7,
    6,
    7,
    6,
    7,
    5,
    6,
    7,
    6,
    7,
    11,
    5,
    6,
    3,
    11,
    8,
    5,
    6,
    6,
    4,
    3,
    7,
    2,
    3,
    5,
    11,
    8,
    3,
    6,
    5,
    4,
    7,
    3,
    6,
    5,
    3,
    12,
    11,
    7,
    5,
    6,
    11,
    11,
    21,
    6,
    6,
    6,
    6,
    8,
    6,
    6,
    9,
    6,
    9,
    7,
    7,
    7,
    9,
    9,
    10,
    9,
    8,
    10,
    10,
    11,
    7,
    7,
    7,
    5,
    5,
    6,
    7,
    11,
    2,
    21,
    8,
    7,
    11,
    7,
    7,
    4,
    6,
    7,
    8,
    4,
    5,
    4,
    4,
    5,
    8,
    6,
    7,
    0,
    23,
    19,
    18,
    24,
    18,
    18,
    18,
    19,
    18,
    24,
    19,
    20,
    21,
    16,
    13,
    23,
    24,
    13,
    13,
    14,
    16,
    18,
    17,
    14,
    18,
    18,
    15,
    15,
    14,
    13,
    14,
    14,
    15,
    16,
    17,
    20,
    21,
    16,
    16,
    16,
    18,
    18,
    18,
    14,
    19,
    18,
    18,
    17,
    13,
    13,
    15,
    17,
    16,
    15,
    15,
    17,
    17,
    16,
    16,
    16,
    21,
    21,
    21,
    22,
    23,
    24,
    23,
    14,
    14,
    17,
    18,
    18,
    16,
    20,
    16,
    21,
    13,
    14,
    17,
    13,
    13,
    13,
    13,
    16,
    16,
    15,
    15,
    17,
    16,
    13,
    16,
    15,
    16,
    14,
    16,
    16,
    13,
    14,
    15,
    16,
    24,
)


def _card(card):
    try:
        model, arrows = card.model, card.arrows
    except AttributeError as exc:
        raise AIError("card must expose model and arrows") from exc
    if type(model) is not int or not 0 <= model < 250:
        raise AIError("only ordinary catalogue cards are supported")
    if type(arrows) is not int or not 0 <= arrows <= 255:
        raise AIError("arrows must be an unsigned byte")
    return model, arrows


def _board(board, owner):
    if type(owner) is not int or owner not in (0, 1):
        raise AIError("ordinary two-player owner must be 0 or 1")
    if len(board) != 16:
        raise AIError("ordinary board must contain sixteen cells")
    result = []
    for cell in board:
        if cell is None or cell is BLOCKED:
            result.append(cell)
        else:
            if type(cell.owner) is not int or cell.owner not in (0, 1):
                raise AIError("board occupant owner must be 0 or 1")
            result.append((_card(cell.card), cell.owner))
    return tuple(result)


def _draw(raw_rng):
    value = raw_rng()
    if type(value) is not int or not 0 <= value <= 32767:
        raise AIError("raw_rng must return CRT rand values 0..32767")
    return value


def _denominator(value):
    if type(value) is not int or not 1 <= value <= 0x7FFFFFFF:
        raise AIError("pacing denominator must be a positive signed DWORD")
    return value


def _neighbors(field, arrows):
    x, y = field % 4, field // 4
    for direction, (dx, dy) in enumerate(DIRECTIONS):
        nx, ny = x + dx, y + dy
        if arrows & (1 << direction) and 0 <= nx < 4 and 0 <= ny < 4:
            yield direction, ny * 4 + nx


def _score(card, board, owner, field, preference):
    # Original G+227 markers: low1 direct, low2 reciprocal battle, low4 one-hop
    # combo; high nibble CardPrm price tier. OR preserves overlaps exactly.
    markers = [0] * 16

    def mark(index, bit):
        model = board[index][0][0]
        markers[index] |= bit | ((PRICE_TIERS[model] << 4) & 255)

    for direction, target in _neighbors(field, card[1]):
        other = board[target]
        if other is None or other is BLOCKED or other[1] == owner:
            continue
        if other[0][1] & (1 << ((direction + 4) & 7)):
            # Mode2 recursion marks the target's same-owner neighbors only.
            # Their arrows need not point back; no further recursion occurs.
            for _, combo in _neighbors(target, other[0][1]):
                occupant = board[combo]
                if occupant is not None and occupant is not BLOCKED and occupant[1] == other[1]:
                    mark(combo, 4)
            mark(target, 2)
        else:
            mark(target, 1)

    affected = sum(bool(marker & 15) for marker in markers)
    if preference == 1:
        largest_tier_nibble = max(marker >> 4 for marker in markers)
        return 16 + largest_tier_nibble if largest_tier_nibble > 1 else affected
    if preference == 2:
        battles = sum(bool(marker & 2) for marker in markers)
        return 16 + battles if battles else affected
    if preference == 3:
        # The original counts even unmarked/empty cells in this mode.
        return sum(2 + bool(marker & 1) for marker in markers if not marker & 2)
    if preference == 4:
        combos = sum(bool(marker & 4) for marker in markers)
        return 16 + combos if combos else affected
    return affected


def placement_scores(card, board, owner, preference=0):
    """Ascending (field, score) pairs from the original ordinary-board evaluator."""
    if type(preference) is not int or not 0 <= preference <= 255:
        raise AIError("preference must be an unsigned byte")
    card, board = _card(card), _board(board, owner)
    return tuple(
        (field, _score(card, board, owner, field, preference))
        for field, occupant in enumerate(board)
        if occupant is None
    )


def poll_hand(hand, raw_rng, denominator=60):
    """Original simulated-player hand poll; None means no decision this poll."""
    denominator = _denominator(denominator)
    if len(hand) > 5:
        raise AIError("native hand has at most five cards")
    for card in hand:
        _card(card)
    if not hand:
        return None  # Defensive server boundary; original assumes a nonempty hand.
    if _draw(raw_rng) % denominator >= 2:
        return None
    return _draw(raw_rng) % len(hand)


def poll_placement(card, board, owner, raw_rng, personality=Personality(), denominator=60):
    """Original placement poll, including conditional draws and max-score ties."""
    denominator = _denominator(denominator)
    if not isinstance(personality, Personality):
        raise AIError("personality must be a Personality")
    card, board = _card(card), _board(board, owner)
    candidates = [field for field, occupant in enumerate(board) if occupant is None]
    if not candidates:
        return None  # Original downstream division has no zero-candidate fallback.
    if _draw(raw_rng) % denominator >= 2:
        return None
    if _draw(raw_rng) % 100 < personality.score_chance:
        preference = (
            personality.preference if _draw(raw_rng) % 100 < personality.preference_chance else 0
        )
        scores = [_score(card, board, owner, field, preference) for field in candidates]
        best = max(scores)
        candidates = [field for field, score in zip(candidates, scores) if score == best]
    return candidates[_draw(raw_rng) % len(candidates)]


def _targets(candidates, board_size=16):
    if type(board_size) is not int or board_size not in (16, 25):
        raise AIError("target board must contain sixteen or twenty-five cells")
    candidates = tuple(candidates)
    if any(type(field) is not int or not 0 <= field < board_size for field in candidates):
        raise AIError("battle target outside the board")
    if len(candidates) > 8 or len(set(candidates)) != len(candidates):
        raise AIError("battle candidates must be at most eight distinct fields")
    return candidates


def poll_target(candidates, raw_rng, denominator=60, *, board_size=16):
    """Original battle-target poll; preserve the supplied native candidate order."""
    denominator = _denominator(denominator)
    candidates = _targets(candidates, board_size)
    if not candidates:
        return None
    if _draw(raw_rng) % denominator >= 2:
        return None
    return candidates[_draw(raw_rng) % len(candidates)]


def choose_move(hand, board, owner, raw_rng, personality=Personality()):
    """Immediate original decision policy, with a deliberately always-ready clock."""
    # Validate before consuming RNG, including for empty/full no-move boundaries.
    hand = tuple(hand)
    snapshot = _board(board, owner)
    if len(hand) > 5:
        raise AIError("native hand has at most five cards")
    for card in hand:
        _card(card)
    if not isinstance(personality, Personality):
        raise AIError("personality must be a Personality")
    if not hand or all(cell is not None for cell in snapshot):
        return None
    index = poll_hand(hand, raw_rng, denominator=1)
    field = poll_placement(hand[index], board, owner, raw_rng, personality, denominator=1)
    return Move(index, field)


def choose_target(candidates, raw_rng, *, board_size=16):
    """Immediate target decision; caller supplies the current legal candidate list."""
    candidates = _targets(candidates, board_size)
    if len(candidates) == 1:
        # Original combat classifier skips the selection branch entirely for
        # a sole target (100CE481..100CE553); no AI random draws occur.
        return candidates[0]
    return poll_target(candidates, raw_rng, denominator=1, board_size=board_size)
