"""Match state, immutable deliveries and completed-round values."""

from dataclasses import dataclass, field
from typing import Hashable

from tmserver.cards.model import Card as CardValue
from tmserver.cards.model import card_values
from tmserver.game import special_rules as special
from tmserver.game.ai import Personality
from tmserver.game.errors import MatchError
from tmserver.game.packets import numbers
from tmserver.game.prizes import PrizeSettlement
from tmserver.rankings.stats import PracticeStats
from tmserver.rooms.rules import RoomRules


@dataclass(frozen=True)
class Card(CardValue):
    def __post_init__(self):
        if not 0 <= self.model < 250 or not 0 <= self.kind < 4:
            raise MatchError("card model/type outside local catalogue domain")
        if any(
            not 0 <= x <= 255
            for x in (self.attack, self.physical, self.magical, self.points, self.arrows, self.deck)
        ):
            raise MatchError("card field outside byte range")

    def full(self):
        return "|".join(map(str, card_values(self)))

    def reduced(self):
        return "|".join(map(str, super().reduced()))

    @classmethod
    def parse(cls, text):
        values = numbers(text, 8)
        return cls(*values)


@dataclass(frozen=True)
class Delivery:
    peer: Hashable
    payload: bytes


@dataclass(frozen=True)
class FinalCards:
    """Immutable completed-round replacement, indexed by original selection slot."""

    round_id: int
    original: tuple[Card, ...]
    updated: tuple[Card, ...]
    stats: PracticeStats
    extras: tuple[Card, ...] = ()


@dataclass(frozen=True)
class FinalPrizes:
    """Completed ownership settlement; root must commit before confirmation."""

    round_id: int
    settlement: PrizeSettlement


@dataclass
class Player:
    peer: Hashable | None
    character_id: int
    name: bytes
    nickname_id: int
    rating: int = 300
    balance: int = 10000
    points: int = 1
    selected: tuple[Card, ...] = ()
    hand: list[Card] = field(default_factory=list)
    hand_slots: list[int] = field(default_factory=list)
    updated: list[Card] = field(default_factory=list)
    requested: bool = False
    accepted: bool = False
    identity_confirmed: bool = False
    result_stats: PracticeStats = field(default_factory=PracticeStats)
    collection_size: int = 0
    departed: bool = False
    substitute: int = 0
    npc_id: int = 0
    ai_personality: Personality | None = None


@dataclass
class BoardCard:
    card: Card | None
    owner: int
    original_owner: int
    original_slot: int | None = None
    property: int = 0
    modifier: int = 0
    tile_id: int | None = None
    direction: int = 0
    tile_arrows: int = 0
    base_owner: int | None = None


@dataclass(frozen=True)
class ObserverPlayer:
    peer: Hashable | None
    name: bytes
    nickname_id: int
    character_id: int
    rating: int
    points: int
    hand: tuple[Card, ...]
    hand_slots: tuple[int, ...]
    selected: tuple[Card, ...]
    departed: bool = False
    substitute: int = 0
    npc_id: int = 0

    @property
    def remaining(self):
        return len(self.hand)


@dataclass(frozen=True)
class ObserverSnapshot:
    phase: str
    round_id: int
    sequence: int
    active: int
    players: tuple[ObserverPlayer, ...]
    game_rules: tuple[tuple[str, int], ...]
    start_fields: tuple[int, ...]
    board: tuple[special.Cell, ...]
    scores: tuple[int, ...]
    turn_packet: bytes | None
    observation: int = 0


DEFAULT_GAME_RULES = RoomRules().as_dict()

DEFAULT_TABLE_RULES = {"in": 1, "lu": 99999, "ll": 0, "au": 300, "al": 100, "co": 4, "pa": 0}
