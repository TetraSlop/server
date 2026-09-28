"""Shared auction records and reservation capabilities."""

from dataclasses import dataclass

from tmserver.cards.model import Card
from tmserver.economy.errors import EconomyError


class AuctionError(EconomyError):
    def __init__(self, message, code=-8616):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Actor:
    key: str
    nn: int
    player_id: int
    name: bytes
    guild: int
    rating: int
    points: int


@dataclass(frozen=True)
class Listing:
    id: int
    seller: Actor
    card: Card
    opening: int
    increment: int
    hours: int
    relists: int
    created: int
    expires: int
    item_metadata: tuple
    reoffers: int = 0
    revision: int = 0
    bidder: Actor | None = None
    amount: int = 0
    bid_at: int = 0
    status: str = "active"
    closed_at: int = 0

    @property
    def minimum_bid(self):
        return self.amount + self.increment if self.bidder is not None else self.opening


@dataclass(frozen=True)
class Bid:
    auction_id: int
    bidder: Actor
    amount: int
    timestamp: int
    revision: int


@dataclass(frozen=True)
class Pending:
    id: int
    key: str
    kind: str
    amount: int = 0
    card: Card | None = None


@dataclass(frozen=True, eq=False)
class CollectionReservation:
    key: str
    account: object
    payments: int
    returned: tuple
    won: tuple
    refunds: int
