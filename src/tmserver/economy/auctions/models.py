"""Shared auction records and reservation capabilities."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BeforeValidator, ConfigDict, Field, PlainSerializer, TypeAdapter

from tmserver.cards.model import Card
from tmserver.economy.errors import EconomyError
from tmserver.economy.models import MAX_MONEY

_SAVED = ConfigDict(strict=True, extra="forbid")
_UINT = Annotated[int, Field(ge=0, le=0xFFFFFFFF)]
_MONEY = Annotated[int, Field(ge=0, le=MAX_MONEY)]
_CARD_FIELDS = TypeAdapter(Annotated[list[int], Field(min_length=7, max_length=8)])


def _saved_name(value, info):
    return bytes.fromhex(value) if info.mode == "json" else value


def _saved_card(value, info):
    if info.mode == "json":
        return Card(*_CARD_FIELDS.validate_python(value, strict=True))
    return value


SavedCard = Annotated[
    Card,
    BeforeValidator(_saved_card),
    PlainSerializer(lambda card: list(card.full()), return_type=list[int], when_used="json"),
]


class AuctionError(EconomyError):
    def __init__(self, message, code=-8616):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Actor:
    __pydantic_config__ = _SAVED
    key: str
    nn: int
    player_id: int
    name: Annotated[
        bytes, BeforeValidator(_saved_name), PlainSerializer(bytes.hex, when_used="json")
    ]
    guild: Annotated[int, Field(ge=0, le=3)]
    rating: _UINT
    points: Annotated[int, Field(ge=0, le=65535)]


@dataclass(frozen=True)
class Listing:
    __pydantic_config__ = _SAVED
    id: Annotated[_UINT, Field(ge=1)]
    seller: Actor
    card: SavedCard
    opening: Annotated[_MONEY, Field(ge=1)]
    increment: int
    hours: Annotated[int, Field(ge=1, le=168)]
    relists: Annotated[int, Field(ge=0, le=3)]
    created: _UINT
    expires: _UINT
    item_metadata: Annotated[
        tuple[int, ...],
        BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
    ]
    reoffers: _UINT = 0
    revision: _UINT = 0
    bidder: Actor | None = None
    amount: _MONEY = 0
    bid_at: _UINT = 0
    status: Literal["active", "sold", "returned", "cancelled"] = "active"
    closed_at: _UINT = 0

    @property
    def minimum_bid(self):
        return self.amount + self.increment if self.bidder is not None else self.opening


@dataclass(frozen=True)
class Bid:
    __pydantic_config__ = _SAVED
    auction_id: Annotated[_UINT, Field(ge=1)]
    bidder: Actor
    amount: Annotated[_MONEY, Field(ge=1)]
    timestamp: _UINT
    revision: Annotated[_UINT, Field(ge=1)]


@dataclass(frozen=True)
class Pending:
    __pydantic_config__ = _SAVED
    id: Annotated[_UINT, Field(ge=1)]
    key: str
    kind: Literal["payment", "returned", "won", "refund"]
    amount: _MONEY = 0
    card: SavedCard | None = None


@dataclass(frozen=True, eq=False)
class CollectionReservation:
    key: str
    account: object
    payments: int
    returned: tuple
    won: tuple
    refunds: int
