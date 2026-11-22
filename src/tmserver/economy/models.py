"""Account snapshots, pending shop state and reservation capabilities."""

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Annotated

from pydantic import ConfigDict, Field

from tmserver.checks import STABLE_ID
from tmserver.economy.errors import EconomyError

MAX_MONEY = 99_999_999
MAX_CARDS = 1000
PROFILE_SIZE = 0x3028

# Saved journal shapes: exact fields, no coercion, so a bool is never an int.
SAVED = ConfigDict(strict=True, extra="forbid")
Version1 = Annotated[int, Field(ge=1, le=1)]
StableId = Annotated[str, Field(pattern=rf"\A{STABLE_ID}\z")]
Digest = Annotated[str, Field(pattern=r"\A[0-9a-f]{64}\z")]
SavedCard = Annotated[list[int], Field(min_length=7, max_length=8)]  # Card.full() order.


@dataclass
class _State:
    balance: int
    cards: list
    names: dict = dataclass_field(default_factory=lambda: {1: b"Deck One", 2: b"Deck Two"})


@dataclass
class _Pending:
    pack: int
    reply: bytes
    state: _State


@dataclass
class _Account:
    nn: int
    player_id: int
    name: bytes
    guild: int
    template: bytes
    committed: _State
    session: int | None = None
    last_session: int = 0
    pending: _Pending | None = None


@dataclass(frozen=True)
class AccountView:
    key: str
    nn: int
    player_id: int
    name: bytes
    guild: int
    rating: int
    balance: int
    cards: tuple
    deck_names: tuple


@dataclass(frozen=True, eq=False)
class ExchangeReservation:
    """In-process capability; only the service that issued it can commit it."""

    accounts: tuple[AccountView, AccountView]


@dataclass(frozen=True, eq=False)
class MatchReservation:
    """Capability for one table member; refreshed after each committed round."""

    account: AccountView


@dataclass(frozen=True, eq=False)
class AuctionReservation:
    """Exclusive capability for a short escrow operation or collection session."""

    account: AccountView


@dataclass(frozen=True, eq=False)
class PrizeReservation:
    """Exclusive Prize Center capability, including its independent points."""

    account: AccountView
    points: int


def check_inventory(cards):
    """Enforce the persisted capacity and unique occupied deck slots."""
    if len(cards) > MAX_CARDS:
        raise EconomyError("inventory exceeds persisted capacity")
    slots = [card.deck for card in cards if card.deck != 255]
    if len(slots) != len(set(slots)):
        raise EconomyError("two cards occupy one deck slot")
