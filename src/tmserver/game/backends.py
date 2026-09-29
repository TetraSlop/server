"""Borrowed calculation dependency; match state and randomness stay in Python."""

from typing import Callable, Protocol

from tmserver.game.ai import Move, Personality
from tmserver.game.rules import BattleResult, GrowthBaseline, GrowthResult


class RulesBackend(Protocol):
    """Required operations; GrowthBackend and TargetBackend are optional capabilities."""

    def calculate_battle(
        self,
        attacker,
        defender,
        draw: Callable[[], int],
        *,
        attacker_modifier: int = 0,
        defender_modifier: int = 0,
        attacker_property: int = 0,
        defender_property: int = 0,
    ) -> BattleResult: ...

    def choose_move(
        self,
        hand,
        board,
        owner: int,
        raw_rng: Callable[[], int],
        personality: Personality = Personality(),
    ) -> Move | None: ...


class GrowthBackend(Protocol):
    def grow_card(
        self,
        card,
        baseline: GrowthBaseline,
        mode: int,
        random_value: int,
        *,
        x87_precision: int = 64,
    ) -> GrowthResult: ...


class TargetBackend(Protocol):
    def choose_target(
        self, candidates, raw_rng: Callable[[], int], *, board_size: int = 16
    ) -> int | None: ...
