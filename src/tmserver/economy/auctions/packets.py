"""Auction field parsing, native file records and reply encoding."""

import re
import struct

from tmserver.economy.errors import EconomyError
from tmserver.protocol.commands import Command, encode_service

EXHIBIT_PATH = "U/g/TM0_EXHIBITLIST"
BID_PATH = "U/g/TM0_BIDLIST"
METADATA_PATH = "b/g/TM0AucData"
PENDING_PATHS = ("u/g/TM0_PM", "u/g/TM0_BI", "u/g/TM0_DI", "u/g/TM0_RM")
ACK = b"\x06"


def _number(value, bits=32):
    if isinstance(value, str):
        value = value.encode("ascii")
    if not isinstance(value, bytes) or re.fullmatch(rb"-?[0-9]{1,10}", value) is None:
        raise EconomyError("invalid auction decimal")
    n = int(value)
    # Native DWORD serializers use signed %d. Smaller fields are unsigned.
    if not (-(1 << 31) if bits == 32 else 0) <= n < (1 << bits):
        raise EconomyError("auction decimal exceeds field width")
    return n & ((1 << bits) - 1)


def _decimal(value):
    return str(value if value < 0x80000000 else value - 0x100000000).encode()


def _name(value, limit=16):
    if (
        not isinstance(value, bytes)
        or len(value) > limit
        or any(c < 32 or c > 255 or c in (127,) for c in value)
    ):
        raise EconomyError("invalid auction name")
    return value


def actor_record(actor):
    """UI identity/name fields; unknown actor metadata remains explicitly zero."""
    out = bytearray(0x38)
    if actor is not None:
        struct.pack_into("<QI", out, 0, actor.nn, actor.player_id)
        # Display copy is NUL-terminated even for a 16-byte account name.
        out[0x18:0x28] = actor.name[:15].ljust(16, b"\0")
    return bytes(out)


def actor_wire(actor):
    if actor is None:
        values = [b"0x0000000000000000", b"0", b"0", b"0", b"0", b"", *([b"0"] * 6)]
    else:
        values = [
            f"0x{actor.nn:016X}".encode(),
            _decimal(actor.player_id),
            b"0",
            b"0",
            b"0",
            actor.name[:15],
            *([b"0"] * 6),
        ]
    return ACK.join(values)


def descriptor(item):
    if item.item_metadata:
        return item.item_metadata
    c, a = item.card, item.seller
    return (
        c.model,
        0,
        a.rating,
        0,
        0,
        0,
        a.points & 65535,
        0,
        c.attack,
        c.physical,
        c.kind,
        c.magical,
        c.points,
        c.arrows,
        0,
        0,
    )


def listing_record(item):
    out = bytearray(0xC0)
    out[:0x38] = actor_record(item.seller)
    # NC is used for remaining duration. ED/LC/CD use this server's Unix clock;
    # the complete historical meanings of ED/LC are not established.
    struct.pack_into(
        "<6I3B",
        out,
        0x38,
        item.id,
        item.opening,
        item.increment,
        item.created,
        item.expires,
        item.bid_at or item.created,
        item.hours,
        item.relists,
        item.reoffers,
    )
    out[0x58:0x90] = actor_record(item.bidder)
    struct.pack_into(
        "<3I", out, 0x90, item.amount if item.bidder else item.opening, item.bid_at, item.revision
    )
    struct.pack_into("<4I4H8B", out, 0xA0, *descriptor(item))
    return bytes(out)


def bid_record(bid):
    return actor_record(bid.bidder) + struct.pack(
        "<4I", bid.auction_id, bid.amount, bid.timestamp, 0
    )


def listing_reply(token, item):
    scalar = [
        ("AI", item.id),
        ("SP", item.opening),
        ("BI", item.increment),
        ("ED", item.created),
        ("NC", item.expires),
        ("LC", item.bid_at or item.created),
        ("ET", item.hours),
        ("AM", item.relists),
        ("AC", item.reoffers),
        ("CM", item.amount if item.bidder else item.opening),
        ("CD", item.bid_at),
        ("BC", item.revision),
    ]
    commands = [Command(token, b""), Command(b"EI", actor_wire(item.seller))]
    commands += [Command(k.encode(), _decimal(v)) for k, v in scalar]
    commands += [
        Command(b"CB", actor_wire(item.bidder)),
        Command(b"II", ACK.join(_decimal(v) for v in descriptor(item))),
    ]
    return encode_service(b"A", commands)
