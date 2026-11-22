"""Event configuration, member records and admission values."""

import hashlib
import re
import struct
from dataclasses import dataclass
from typing import Annotated

from pydantic import BeforeValidator, ConfigDict
from pydantic.dataclasses import dataclass as validated_dataclass

from tmserver.economy.errors import EconomyError
from tmserver.game import special_rules as special
from tmserver.game.models import Card
from tmserver.rooms.records import build_table_record


_CONFIG = ConfigDict(strict=True, extra="forbid")


def _json_text(value, info):
    """Configuration JSON carries latin1 text as strings; Python callers pass bytes."""
    if info.mode != "json":
        return value
    if not isinstance(value, str):
        raise ValueError("event text must be a string")
    try:
        return value.encode("latin1")
    except UnicodeEncodeError as exc:
        raise ValueError("event text must fit the configured PC client encoding") from exc


Text = Annotated[bytes, BeforeValidator(_json_text)]


@validated_dataclass(frozen=True, config=_CONFIG)
class EventListing:
    key_region: Text
    key_event: Text
    description: Text
    scalar: int = 0
    flags: tuple[int, ...] = (0, 0, 0, 0)
    start: int = 0
    end: int = 0


@validated_dataclass(frozen=True, config=_CONFIG)
class PrizeDisplay:
    cards: tuple[int, ...] = ()
    money: int = 0
    description: Text = b""


@dataclass(frozen=True)
class EventMember:
    identity: int
    name: bytes
    steps: int = 0
    completed_missions: int = 0
    eligibility: int = 0


@validated_dataclass(frozen=True, config=_CONFIG)
class EventConfig:
    """Trusted local configuration, not a reconstructed historical tournament.

    IDs are game-domain qwords. entry_answer1/2 selects original mode4/8.
    Mode4 E entry uses the account's own cards (99/Card=0); mode8 bypasses
    selection and enters the tournament observer room. match_enabled gates E
    service discovery/admission; only mode4 needs a playable table. The distinct F CardReq
    route enters the B2 card collection UI, gated by card_service_enabled.
    """

    id: str
    listing: EventListing
    data: bytes
    start: int
    end: int
    room_id: int
    init_id: int
    card_id: int
    init_channel: bytes = b"#LOCALEVENTE"
    card_channel: bytes = b"#LOCALEVENTF"
    entry_answer: int = 1
    match_enabled: bool = False
    card_service_enabled: bool = False

    def __post_init__(self):
        from .packets import _id, event_list, integer, text

        _id(self.id)
        event_list((self.listing,))
        integer(self.start, signed=True)
        integer(self.end, signed=True)
        if self.start < 0 or self.end <= self.start:
            raise EconomyError("event needs a bounded positive time window")
        ids = (self.room_id, self.init_id, self.card_id)
        if any(not 0 < i < 1 << 64 for i in ids) or len(set(ids)) != 3:
            raise EconomyError("event services need distinct nonzero game identities")
        for channel in (self.init_channel, self.card_channel):
            text(channel, 15, empty=False)
            if not re.fullmatch(rb"#[A-Za-z0-9_-]+", channel):
                raise EconomyError("invalid event service channel")
        if self.init_channel == self.card_channel:
            raise EconomyError("event channels must differ")
        if self.entry_answer not in (1, 2):
            raise EconomyError("unmapped event entry answer")

    @property
    def mission_mask(self):
        return struct.unpack_from("<I", self.data, 0x12FC)[0]

    def fingerprint(self):
        from .packets import event_list

        # Match enablement is a runtime capability gate, not event identity.
        data = (
            event_list((self.listing,))
            + self.data
            + struct.pack(
                "<iiQQQB",
                self.start,
                self.end,
                self.room_id,
                self.init_id,
                self.card_id,
                self.entry_answer,
            )
            + self.init_channel
            + b"\0"
            + self.card_channel
        )
        return hashlib.sha256(data).hexdigest()

    def service_records(self):
        """Append to this event room's raw PTL list; update its count separately."""
        rows = []
        for identity, channel, prefix, enabled in (
            (self.init_id, self.init_channel, b"E", self.match_enabled),
            (self.card_id, self.card_channel, b"F", self.card_service_enabled),
        ):
            if not enabled:
                continue
            rows.append(
                build_table_record(identity=identity, channel=channel, metadata=prefix + b"A" * 48)
            )
        return tuple(rows)


@dataclass(frozen=True)
class EventAdmission:
    key: str
    event_id: str
    kind: str
    mode: int | None
    member: EventMember


@dataclass(frozen=True)
class EventDelivery:
    peer: object
    source: int
    payload: bytes


@validated_dataclass(frozen=True, config=_CONFIG)
class EventTableConfig:
    event_id: str
    table_id: int
    channel: bytes
    player_count: int = 2
    cpu_cards: tuple | list | None = None
    turn_limit: int = 2
    departure_rule: int = 1
    seed: int = 1
    start_fields: tuple | list | None = None

    def __post_init__(self):
        if self.player_count not in (2, 3):
            raise EconomyError("event table needs two/three seats")
        if self.cpu_cards is not None and (self.player_count != 2 or len(self.cpu_cards) != 5):
            raise EconomyError("synthetic event CPU needs one human and five CPU cards")
        if self.cpu_cards is not None:
            checked = []
            for card in self.cpu_cards:
                try:
                    values = tuple(getattr(card, k) for k in Card.__dataclass_fields__)
                except AttributeError as exc:
                    raise EconomyError("invalid event CPU card") from exc
                if any(type(v) is not int for v in values):
                    raise EconomyError("event CPU card fields must be integers")
                checked.append(Card(*values))
            object.__setattr__(self, "cpu_cards", tuple(checked))
        if not 0 <= self.turn_limit <= 5:
            raise EconomyError("unsupported event turn limit")
        if not 0 <= self.departure_rule <= 2:
            raise EconomyError("unsupported event departure rule")
        if not 0 <= self.seed <= 0xFFFFFFFF:
            raise EconomyError("event seed must be an unsigned32-bit integer")
        if self.start_fields is not None:
            board = special.start_board(self.start_fields)
            size = (self.player_count + 2) ** 2
            if len(board.fields) != size:
                raise EconomyError("event board has wrong dimensions")
            if 18 in board.fields:
                raise EconomyError("event boards require original non-CBC tile values")
            if board.blocks > size - 5 * self.player_count:
                raise EconomyError("event board must leave five placement cells per player")
            object.__setattr__(self, "start_fields", board.fields)
