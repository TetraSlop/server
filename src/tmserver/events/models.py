"""Event configuration, member records and admission values."""

import hashlib
import re
import struct
from dataclasses import dataclass

from tmserver.economy.errors import EconomyError


@dataclass(frozen=True)
class EventListing:
    key_region: bytes
    key_event: bytes
    description: bytes
    scalar: int = 0
    flags: tuple = (0, 0, 0, 0)
    start: int = 0
    end: int = 0


@dataclass(frozen=True)
class PrizeDisplay:
    cards: tuple = ()
    money: int = 0
    description: bytes = b""


@dataclass(frozen=True)
class EventMember:
    identity: int
    name: bytes
    steps: int = 0
    completed_missions: int = 0
    eligibility: int = 0


class EventError(EconomyError):
    """Malformed or unauthorized event input; no success should be sent."""


@dataclass(frozen=True)
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
        from .packets import _data_checked, _id, event_list, integer, text

        _id(self.id)
        event_list((self.listing,))
        _data_checked(self.data)
        integer(self.start, signed=True)
        integer(self.end, signed=True)
        if self.start < 0 or self.end <= self.start:
            raise EventError("event needs a bounded positive time window")
        ids = (self.room_id, self.init_id, self.card_id)
        if any(type(i) is not int or not 0 < i < 1 << 64 for i in ids) or len(set(ids)) != 3:
            raise EventError("event services need distinct nonzero game identities")
        for channel in (self.init_channel, self.card_channel):
            text(channel, 15, empty=False)
            if not re.fullmatch(rb"#[A-Za-z0-9_-]+", channel):
                raise EventError("invalid event service channel")
        if self.init_channel == self.card_channel:
            raise EventError("event channels must differ")
        if type(self.entry_answer) is not int or self.entry_answer not in (1, 2):
            raise EventError("unmapped event entry answer")
        if type(self.match_enabled) is not bool or type(self.card_service_enabled) is not bool:
            raise EventError("service enable flags must be boolean")

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
            row = bytearray(0x68)
            struct.pack_into("<Q", row, 0, identity)
            row[0x18 : 0x18 + len(channel)] = channel
            row[0x28:0x59] = prefix + b"A" * 48
            # E discovery accepts DWORD+0C<8. Zero is a local fixture value.
            rows.append(bytes(row))
        return tuple(rows)


@dataclass(frozen=True)
class EventAdmission:
    key: str
    event_id: str
    kind: str
    mode: int | None
    member: EventMember
