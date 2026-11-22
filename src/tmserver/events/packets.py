"""Event file fields, wire validation and packet constructors.

Layout: TetraPackets file/event-files.md.
"""

import re
import struct
from functools import partial

from tmserver.checks import decimal, exact_keys, stable_id
from tmserver.game.packets import packet

from .models import EventError, PrizeDisplay

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


def cstring(out, offset, size, value):
    out[offset : offset + size] = text(value, size - 1).ljust(size, b"\0")


def event_list(records=()):
    records = tuple(records)
    if len(records) > 32:
        raise ValueError("event directory capacity is 32")
    out = bytearray(0x2C08)
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
):
    out = bytearray(0x1308)
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


def event_members(records=()):
    records = tuple(records)
    if len(records) > 256:
        raise ValueError("event member capacity is 256")
    out = bytearray(0x2808)
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


_id = partial(stable_id, error=EventError, what="stable event/update ID")
_keys = partial(exact_keys, error=EventError, what="event fields")
_number = partial(decimal, error=EventError, what="event decimal")


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{16}", value):
        raise EventError("event identity must be sixteen hex digits")
    return int(value, 16)
