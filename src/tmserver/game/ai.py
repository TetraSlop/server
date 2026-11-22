"""Computer-player inputs for the native decision calls; the bridge checks their ranges."""

from dataclasses import dataclass


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
