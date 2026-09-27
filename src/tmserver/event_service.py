"""Exact consumed event file fields and bounded event-control constructors.

This supplies an empty event directory by default. It does not invent active
tournaments, mission award predicates or opaque metadata. Configured data are
explicit local fixtures; original files/layouts remain unmodified.
"""

import hashlib
import re
import struct
import time
from copy import copy
from dataclasses import dataclass

from tmserver.economy_service import EconomyError, named, parse_named
from tmserver.match_service import packet

SIZES = {
    "b/g/TM0EventList": 0x2C08,
    "b/g/TM0EventDataList": 0x1308,
    "b/g/TM0EventMemberList": 0x2808,
}


def integer(value, bits=32, *, signed=False):
    low = -(1 << (bits - 1)) if signed else 0
    high = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
    if type(value) is not int or not low <= value <= high:
        raise ValueError("event integer outside field width")
    return value


def text(value, maximum, *, empty=True):
    if (
        not isinstance(value, bytes)
        or len(value) > maximum
        or b"\0" in value
        or (not empty and not value)
    ):
        raise ValueError("event text exceeds its terminated field")
    return value


def buffer(size, template):
    if template is None:
        return bytearray(size)
    if not isinstance(template, bytes) or len(template) != size:
        raise ValueError("incorrect event template extent")
    return bytearray(template)


def cstring(out, offset, size, value):
    out[offset : offset + size] = text(value, size - 1).ljust(size, b"\0")


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


def event_list(records=(), *, template=None):
    records = tuple(records)
    if len(records) > 32:
        raise ValueError("event directory capacity is32")
    out = buffer(0x2C08, template)
    struct.pack_into("<I", out, 4, len(records))
    keys = set()
    for index, record in enumerate(records):
        text(record.key_region, 31, empty=False)
        text(record.key_event, 31, empty=False)
        key = (record.key_region, record.key_event)
        if key in keys:
            raise ValueError("duplicate event key pair")
        keys.add(key)
        if len(record.flags) != 4:
            raise ValueError("event listing has four flag bytes")
        at = 8 + index * 0x160
        struct.pack_into(
            "<I4Bii",
            out,
            at,
            integer(record.scalar),
            *(integer(value, 8) for value in record.flags),
            integer(record.start, signed=True),
            integer(record.end, signed=True),
        )
        cstring(out, at + 0x10, 32, record.key_region)
        cstring(out, at + 0x30, 32, record.key_event)
        cstring(out, at + 0x50, 0x110, record.description)
    return bytes(out)


def event_data(
    *,
    start=(0, 0, 0, 0, 0),
    end=(0, 0, 0, 0, 0),
    classes=(),
    steps_per_class=1,
    prizes=(),
    missions=None,
    template=None,
):
    out = buffer(0x1308, template)
    if len(start) != 5 or len(end) != 5:
        raise ValueError("schedule requires five raw components")
    classes = tuple(classes)
    prizes = tuple(prizes)
    missions = dict(missions or {})
    if len(classes) not in (0, 2, 3, 4):
        raise ValueError("unsupported event class layout")
    bounds = {0: (1, 1), 2: (4, 8), 3: (1, 5), 4: (1, 4)}
    lo, hi = bounds[len(classes)]
    if type(steps_per_class) is not int or not lo <= steps_per_class <= hi:
        raise ValueError("invalid original board selector")
    if len(prizes) > 9:
        raise ValueError("only nine prize display rows exist")
    if len(missions) > 3 or any(type(k) is not int or not 0 <= k <= 7 for k in missions):
        raise ValueError("only three low-byte missions can be presented")
    struct.pack_into("<5I", out, 0x20, *(integer(x) for x in start))
    struct.pack_into("<5I", out, 0x38, *(integer(x) for x in end))
    struct.pack_into("<I", out, 0x54, len(classes))
    for index, name in enumerate(classes):
        cstring(out, 0x60 + index * 0x28, 32, text(name, 31, empty=False))
    struct.pack_into("<I", out, 0x80, steps_per_class)
    for index in range(9):
        prize = prizes[index] if index < len(prizes) else PrizeDisplay()
        if len(prize.cards) > 3:
            raise ValueError("prize display has three card slots")
        if any(type(card) is not int or not 0 <= card < 250 for card in prize.cards):
            raise ValueError("unknown prize card")
        integer(prize.money)
        text(prize.description, 39)
        at = 0x1100 + index * 0x38
        out[at] = (
            int(bool(prize.cards)) | int(bool(prize.money)) * 2 | int(bool(prize.description)) * 4
        )
        struct.pack_into(
            "<3H", out, at + 2, *(tuple(prize.cards) + (0xFFFF,) * (3 - len(prize.cards)))
        )
        struct.pack_into("<I", out, at + 8, prize.money)
        cstring(out, at + 0x10, 40, prize.description)
    struct.pack_into("<I", out, 0x12FC, sum(1 << k for k in missions))
    out[0x1300:0x1308] = bytes(integer(missions.get(k, 0), 8) for k in range(8))
    if any(value == 0 for value in missions.values()):
        raise ValueError("configured mission targets must be positive")
    return bytes(out)


def event_members(records=(), *, template=None):
    records = tuple(records)
    if len(records) > 256:
        raise ValueError("event member capacity is256")
    out = buffer(0x2808, template)
    struct.pack_into("<I", out, 4, len(records))
    seen = set()
    for index, member in enumerate(records):
        integer(member.identity, 64)
        if member.identity == 0 or member.identity in seen:
            raise ValueError("event identities must be nonzero and unique")
        seen.add(member.identity)
        at = 8 + index * 0x28
        struct.pack_into("<Q", out, at, member.identity)
        cstring(out, at + 8, 16, text(member.name, 15, empty=False))
        integer(member.steps)
        if member.steps > 0x7FFFFFFF:
            raise ValueError("steps must remain nonnegative in original signed sorting")
        struct.pack_into(
            "<IIi",
            out,
            at + 0x1C,
            member.steps,
            integer(member.completed_missions),
            integer(member.eligibility, signed=True),
        )
    return bytes(out)


def time_reply(start, end, now):
    for value in (start, end, now):
        integer(value, signed=True)
    return packet(0xD1, 5, body=f"@EventTimeReqCheck=/Start={start}/End={end}/Now={now}")


def announcement(identifier, contents, loops=1):
    integer(identifier, signed=True)
    if identifier < 0:
        raise ValueError("new event announcement ID must exceed initial-1")
    integer(loops, signed=True)
    if loops < 1:
        raise ValueError("announcement loop count must be positive")
    text(contents, 1990, empty=False)
    # Reserve the longest possible Pg field (9|10); original buffers hold199
    # terminated text bytes, while the named body has its own447-byte ceiling.
    prefix = f"@ETelop=/ID={identifier}/Lp={loops}/Pg=9|10/St="
    chunk = min(199, (447 - len(prefix)) // 2)
    pieces = [contents[i : i + chunk] for i in range(0, len(contents), chunk)]
    if len(pieces) > 10:
        raise ValueError("announcement exceeds original ten-page array")
    return tuple(
        packet(
            0xD7,
            body=f"@ETelop=/ID={identifier}/Lp={loops}/Pg={i}|{len(pieces)}/St="
            + part.hex().upper(),
        )
        for i, part in enumerate(pieces)
    )


class EventDirectory:
    """Empty, compatible browse files until a tournament is explicitly hosted."""

    def __init__(self):
        self.files = {
            "b/g/TM0EventList": event_list(),
            "b/g/TM0EventDataList": event_data(),
            "b/g/TM0EventMemberList": event_members(),
        }

    def file(self, path):
        return self.files.get(path)


class EventError(EconomyError):
    """Malformed or unauthorized event input; no success should be sent."""


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise EventError("invalid stable event/update ID")
    return value


def _keys(values, expected):
    if set(values) != set(expected):
        raise EventError("unexpected event fields")


def _number(value, maximum):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{1,10}", value)
        or int(value) > maximum
    ):
        raise EventError("invalid event decimal")
    return int(value)


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{16}", value):
        raise EventError("event identity must be sixteen hex digits")
    return int(value, 16)


def _data_checked(data):
    """Validate consumed array extents without assigning opaque bytes meanings."""
    if not isinstance(data, bytes) or len(data) != SIZES["b/g/TM0EventDataList"]:
        raise EventError("incorrect event data extent")
    count = struct.unpack_from("<I", data, 0x54)[0]
    steps = struct.unpack_from("<I", data, 0x80)[0]
    bounds = {0: (1, 1), 2: (4, 8), 3: (1, 5), 4: (1, 4)}
    if count not in bounds or not bounds[count][0] <= steps <= bounds[count][1]:
        raise EventError("unsupported event class layout")
    for index in range(count):
        if b"\0" not in data[0x60 + index * 0x28 : 0x80 + index * 0x28]:
            raise EventError("unterminated event class")
    mask = struct.unpack_from("<I", data, 0x12FC)[0]
    if (
        mask > 255
        or mask.bit_count() > 3
        or any(not data[0x1300 + i] for i in range(8) if mask & (1 << i))
    ):
        raise EventError("invalid event mission display mask/target")
    for index in range(9):
        at = 0x1100 + index * 0x38
        flags = data[at]
        if flags & 1 and any(
            card not in range(250) and card != 65535
            for card in struct.unpack_from("<3H", data, at + 2)
        ):
            raise EventError("unknown event prize display model")
        if flags & 4 and b"\0" not in data[at + 0x10 : at + 0x38]:
            raise EventError("unterminated event prize text")
    return data


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


class EventHost:
    """Authenticated event control and durable member/progress storage.

    Network adapters persist snapshot jointly with accounts before replies.
    DE membership validation/routing and match reservation remain adapter work.
    This object never accepts progress facts from the wire or grants a prize.
    """

    def __init__(self, economy, configs=(), *, clock=time.time):
        self.economy = economy
        self.clock = clock
        configs = tuple(configs)
        self.configs = {config.id: config for config in configs}
        if len(self.configs) != len(configs):
            raise EventError("duplicate event ID")
        event_list(tuple(config.listing for config in configs))
        ids = [i for c in configs for i in (c.room_id, c.init_id, c.card_id)]
        channels = [v for c in configs for v in (c.init_channel, c.card_channel)]
        if len(set(ids)) != len(ids) or len(set(channels)) != len(channels):
            raise EventError("event service routes overlap")
        self._members = {c.id: {} for c in configs}
        self._updates = {c.id: {} for c in configs}
        self._present = {}
        self._admissions = {}

    def stage_state(self, economy):
        """Membership, progress facts and admissions are immutable map values."""
        staged = copy(self)
        staged.economy = economy
        staged._members = {event: dict(rows) for event, rows in self._members.items()}
        staged._updates = {event: dict(rows) for event, rows in self._updates.items()}
        staged._present = dict(self._present)
        staged._admissions = dict(self._admissions)
        return staged

    def publish_state(self, staged):
        self._members = staged._members
        self._updates = staged._updates
        self._present = staged._present
        self._admissions = staged._admissions

    def config(self, event_id):
        try:
            return self.configs[event_id]
        except KeyError as exc:
            raise EventError("unknown hosted event") from exc

    def _now(self):
        return integer(int(self.clock()), signed=True)

    def event_for_service(self, identity):
        for c in self.configs.values():
            for route, value in (("room", c.room_id), ("init", c.init_id), ("card", c.card_id)):
                if value == identity:
                    return c.id, route
        return None

    def file(self, path, event_id=None):
        if path == "b/g/TM0EventList":
            return event_list(tuple(c.listing for c in self.configs.values()))
        if path not in SIZES:
            return None
        if event_id is None:
            if not self.configs:
                return EventDirectory().file(path)
            if len(self.configs) != 1:
                raise EventError("event file owner is ambiguous")
            event_id = next(iter(self.configs))
        config = self.config(event_id)
        if path == "b/g/TM0EventDataList":
            return config.data
        return event_members(tuple(self._members[event_id].values()))

    def member(self, key, event_id):
        self.config(event_id)
        self.economy.account_view(key)
        return self._members[event_id].get(key)

    def admit_room(self, key, event_id):
        """Call only after authenticated DE succeeds, then send D0 from room."""
        config = self.config(event_id)
        view = self.economy.account_view(key)
        old = self._members[event_id].get(key)
        # Existing members can return after end to collect durable grants.
        denied = (
            old is None and self._now() >= config.end or old is not None and old.eligibility < 0
        )
        denied = denied or old is None and len(self._members[event_id]) >= 256
        if denied:
            return [named(0xD0, 0, 0, "@EventEn=/Ans=-32870")]
        text(view.name, 16, empty=False)
        member = EventMember(
            view.nn,
            view.name[:15],  # The member list has a terminated 16-byte display field.
            0 if old is None else old.steps,
            0 if old is None else old.completed_missions,
            0 if old is None else old.eligibility,
        )
        # An identity cannot appear twice under different authenticated keys.
        if any(
            k != key and m.identity == member.identity for k, m in self._members[event_id].items()
        ):
            raise EventError("event member identity already belongs to another account")
        self._members[event_id][key] = member
        self._present[key] = event_id
        self._admissions.pop(key, None)
        return [named(0xD0, 0, 0, "@EventEn=/Ans=1")]

    def disconnect(self, key):
        self._present.pop(key, None)
        self._admissions.pop(key, None)

    def leave_room(self, key, event_id):
        if self._present.get(key) == event_id:
            self.disconnect(key)

    def admission(self, key):
        return self._admissions.get(key)

    def complete_admission(self, key):
        """Adapter consumes a pending admission after reserving/registering match."""
        return self._admissions.pop(key, None)

    def _auth(self, key, f, with_name=False, with_gid=False):
        view = self.economy.account_view(key)
        if _identity(f["NN"]) != view.nn:
            raise EventError("event NN differs from authenticated account")
        if with_name:
            value = f["CN"]
            if (
                not re.fullmatch(r"[0-9A-Fa-f]{0,32}", value)
                or len(value) % 2
                or bytes.fromhex(value) != view.name
            ):
                raise EventError("event CN differs from authenticated account")
            _number(f["HID"], 0xFFFFFFFF)
            _number(f["Dm"], 255)
            _number(f["Vol"], 65535)
        if with_gid and _identity(f["GID"]) not in (view.player_id, view.nn):
            raise EventError("event GID is not an owned local character alias")
        return view

    def authenticate_entry(self, key, payload, *, route):
        """Identity-only validation for a delivery service's entry replay."""
        family, subtype, sequence, sections = parse_named(payload)
        if family not in (0x90, 0x98):
            raise EventError("not an event entry request")
        kind = "card" if family == 0x90 else "init"
        token = "CardReq" if family == 0x90 else "EInitReq"
        if route != kind or subtype != 0:
            raise EventError("event entry on wrong service/subtype")
        _keys(sections, (token,))
        fields = sections[token]
        _keys(fields, ("NN", "HID", "Dm", "Vol", "CN") + (("GID",) if family == 0x98 else ()))
        return sequence, self._auth(key, fields, True, family == 0x98)

    def handle(self, key, payload, *, event_id, route):
        if payload[:7].upper() not in (b"GTM0G90", b"GTM0G98", b"GTM0GD1"):
            return None
        config = self.config(event_id)
        family, subtype, sequence, sections = parse_named(payload)
        if family == 0xD1:
            if subtype != 4 or route != "room":
                raise EventError("EventTimeReq belongs to the event room route")
            _keys(sections, ("EventTimeReq",))
            _keys(sections["EventTimeReq"], ("NN",))
            self._auth(key, sections["EventTimeReq"])
            return [time_reply(config.start, config.end, self._now())]
        kind = "card" if family == 0x90 else "init"
        sequence, view = self.authenticate_entry(key, payload, route=route)
        member = self._members[event_id].get(key)
        now = self._now()
        answer = 1 if family == 0x90 else config.entry_answer
        enabled = config.card_service_enabled if family == 0x90 else config.match_enabled
        if (
            member is None
            or not enabled
            or family == 0x98
            and (self._present.get(key) != event_id or now < config.start)
        ):
            answer = -32870
        elif member.eligibility < 0:
            answer = -32872 if family == 0x98 else -32870
        elif family == 0x98 and now >= config.end:
            answer = -32968
        elif self.economy.is_busy(key):
            answer = -32870
        if answer > 0 and (family == 0x90 or answer == 2 or len(view.cards) >= 5):
            self._admissions[key] = EventAdmission(
                key, event_id, kind, None if family == 0x90 else 4 if answer == 1 else 8, member
            )
        else:
            self._admissions.pop(key, None)
        # Mode4/Card0 opens shortage when <5 cards. Mode8 skips owned selection.
        body = f"@CardEnt=/Ans={answer}" if family == 0x90 else f"@EInitEnt=/Card=0/Ans={answer}"
        return [named(0x91 if family == 0x90 else 0x99, 0, sequence, body)]

    def update_progress(self, key, event_id, update_id, *, steps_delta=0, completed_missions=0):
        """Trusted engine/policy facts only; replay-safe and never grants assets.

        completed_missions contains newly proven bits, not opaque client claims.
        The caller defines the local policy; this module knows no threshold rule.
        """
        config = self.config(event_id)
        _id(update_id)
        integer(steps_delta)
        integer(completed_missions)
        if steps_delta > 0x7FFFFFFF or completed_missions & ~config.mission_mask:
            raise EventError("progress exceeds configured display domain")
        member = self.member(key, event_id)
        if member is None:
            raise EventError("progress requires a durable member")
        identity = (key, update_id)
        facts = (steps_delta, completed_missions)
        old = self._updates[event_id].get(identity)
        if old is not None:
            if old != facts:
                raise EventError("progress ID replay has different facts")
            return False
        if member.eligibility < 0:
            raise EventError("excluded event member cannot progress")
        value = member.steps + steps_delta
        if value > 0x7FFFFFFF:
            raise EventError("event steps overflow native signed sorter")
        self._members[event_id][key] = EventMember(
            member.identity,
            member.name,
            value,
            member.completed_missions | completed_missions,
            member.eligibility,
        )
        self._updates[event_id][identity] = facts
        return True

    def set_eligibility(self, key, event_id, value):
        """Trusted administration; negative values exclude/reject this member."""
        integer(value, signed=True)
        member = self.member(key, event_id)
        if member is None:
            raise EventError("eligibility requires a durable member")
        self._members[event_id][key] = EventMember(
            member.identity, member.name, member.steps, member.completed_missions, value
        )
        if value < 0:
            self._admissions.pop(key, None)

    def time_update(self, event_id):
        c = self.config(event_id)
        return named(0xD1, 3, 0, f"@EventTime=/Start={c.start}/End={c.end}/Now={self._now()}")

    def time_notice(self, event_id, subtype, *, close=None, ecm=None):
        c = self.config(event_id)
        if subtype == 1:
            body = f"/Start={c.start}"
        elif subtype == 2:
            body = f"/End={c.end}"
        elif subtype == 8:
            integer(close, signed=True)
            body = f"/Close={close}"
            if ecm is not None:
                integer(ecm, signed=True)
                body += f"/ECM={ecm}"
        else:
            raise EventError("unsupported event time notice")
        return named(0xD1, subtype, 0, "@EventTime=" + body)

    def snapshot(self):
        return {
            "version": 1,
            "events": [
                {
                    "id": eid,
                    "configuration": self.configs[eid].fingerprint(),
                    "members": [
                        {
                            "key": key,
                            "identity": m.identity,
                            "name": m.name.hex(),
                            "steps": m.steps,
                            "completed_missions": m.completed_missions,
                            "eligibility": m.eligibility,
                        }
                        for key, m in sorted(rows.items())
                    ],
                    "updates": [
                        {
                            "key": key,
                            "id": uid,
                            "steps_delta": facts[0],
                            "completed_missions": facts[1],
                        }
                        for (key, uid), facts in sorted(self._updates[eid].items())
                    ],
                }
                for eid, rows in sorted(self._members.items())
            ],
        }

    @classmethod
    def restore(cls, economy, configs=(), snapshot=None, *, clock=time.time):
        result = cls(economy, configs, clock=clock)
        if snapshot is None:
            return result
        try:
            _keys(snapshot, ("version", "events"))
            if snapshot["version"] != 1:
                raise EventError("unsupported event snapshot version")
            seen = set()
            for row in snapshot["events"]:
                _keys(row, ("id", "configuration", "members", "updates"))
                eid = row["id"]
                config = result.config(eid)
                if eid in seen or row["configuration"] != config.fingerprint():
                    raise EventError("saved event configuration differs")
                seen.add(eid)
                for record in row["members"]:
                    _keys(
                        record,
                        ("key", "identity", "name", "steps", "completed_missions", "eligibility"),
                    )
                    key = record["key"]
                    view = economy.account_view(key)
                    if key in result._members[eid] or record["identity"] != view.nn:
                        raise EventError("saved event member owner differs")
                    if not isinstance(record["name"], str) or not re.fullmatch(
                        r"[0-9a-fA-F]{2,30}", record["name"]
                    ):
                        raise EventError("invalid saved member name")
                    member = EventMember(
                        record["identity"],
                        bytes.fromhex(record["name"]),
                        record["steps"],
                        record["completed_missions"],
                        record["eligibility"],
                    )
                    if member.completed_missions & ~config.mission_mask:
                        raise EventError("saved member has disabled mission")
                    result._members[eid][key] = member
                event_members(tuple(result._members[eid].values()))
                totals = {key: [0, 0] for key in result._members[eid]}
                for update in row["updates"]:
                    _keys(update, ("key", "id", "steps_delta", "completed_missions"))
                    key = update["key"]
                    identity = (key, _id(update["id"]))
                    if key not in totals or identity in result._updates[eid]:
                        raise EventError("invalid progress update owner/ID")
                    facts = (integer(update["steps_delta"]), integer(update["completed_missions"]))
                    if facts[0] > 0x7FFFFFFF or facts[1] & ~config.mission_mask:
                        raise EventError("invalid saved progress facts")
                    totals[key][0] += facts[0]
                    totals[key][1] |= facts[1]
                    result._updates[eid][identity] = facts
                for key, m in result._members[eid].items():
                    if totals[key] != [m.steps, m.completed_missions]:
                        raise EventError("saved progress differs from update history")
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            if isinstance(exc, EventError):
                raise
            raise EventError("invalid event snapshot") from exc
        return result
