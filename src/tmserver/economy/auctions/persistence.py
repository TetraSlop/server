"""Strict auction save shapes; account and escrow invariants stay in the ledger."""

import json
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .models import Bid, Listing, Pending, SavedCard


def _require(field):
    def check(value):
        if isinstance(value, dict) and field not in value:
            raise ValueError(f"missing saved auction field {field}")
        return value

    return BeforeValidator(check)


class ExternalReturn(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str
    key: str
    cards: list[SavedCard] = Field(max_length=30)


class AuctionSnapshot(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    version: Annotated[int, Field(ge=1, le=1)]
    next_id: Annotated[int, Field(ge=1, le=0x100000000)]
    next_pending: Annotated[int, Field(ge=1, le=0x100000000)]
    listings: list[Annotated[Listing, _require("bidder")]]
    history: list[Bid]
    pending: list[Annotated[Pending, _require("card")]]
    external_returns: list[ExternalReturn] = Field(default_factory=list)

    @classmethod
    def read(cls, document):
        # JSON mode allows persisted arrays to populate immutable tuple fields.
        # Strict numeric validation still rejects booleans, floats and strings.
        return cls.model_validate_json(json.dumps(document))
