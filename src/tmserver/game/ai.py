"""Computer-player inputs and the native decision calls."""

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
    players = {16: 2, 25: 3}.get(len(board))
    if players is None:
        raise AIError("board must contain sixteen or twenty-five cells")
    if type(owner) is not int or not 0 <= owner < players:
        raise AIError("owner outside board mode")
    result = []
    for cell in board:
        if cell is None or cell is BLOCKED:
            result.append(cell)
        else:
            if type(cell.owner) is not int or not 0 <= cell.owner < players:
                raise AIError("board occupant owner outside board mode")
            result.append((_card(cell.card), cell.owner))
    return tuple(result)


def _targets(candidates, board_size=16):
    if type(board_size) is not int or board_size not in (16, 25):
        raise AIError("target board must contain sixteen or twenty-five cells")
    candidates = tuple(candidates)
    if any(type(field) is not int or not 0 <= field < board_size for field in candidates):
        raise AIError("battle target outside the board")
    if len(candidates) > 8 or len(set(candidates)) != len(candidates):
        raise AIError("battle candidates must be at most eight distinct fields")
    return candidates


def choose_move(hand, board, owner, raw_rng, personality=Personality()):
    from tmserver.game.original.runtime import default_rules

    return default_rules().choose_move(hand, board, owner, raw_rng, personality)


def choose_target(candidates, raw_rng, *, board_size=16):
    from tmserver.game.original.runtime import default_rules

    return default_rules().choose_target(candidates, raw_rng, board_size=board_size)
