"""Match packet codec, preserving the original match-specific dialect."""

import re
from dataclasses import dataclass

from tmserver.game.errors import MatchError


@dataclass(frozen=True)
class Packet:
    primary: int
    subtype: int
    argument: int
    sections: dict


def numbers(text, count=None):
    if not isinstance(text, str) or not re.fullmatch(r"-?[0-9]+(?:\|-?[0-9]+)*", text):
        raise MatchError("expected decimal field")
    parts = text.split("|")
    if count is not None and len(parts) != count:
        raise MatchError("incorrect numeric element count")
    if any(len(p) > 11 for p in parts):
        raise MatchError("oversized integer")
    return [int(p) for p in parts]


def packet(primary, subtype=0, argument=0, body=""):
    if any(not 0 <= v <= 255 for v in (primary, subtype, argument)):
        raise MatchError("header byte out of range")
    raw = body.encode("ascii")
    if len(raw) > 447 or any(c in raw for c in (0, 10, 13)):
        raise MatchError("packet exceeds demonstrated single-body contract")
    return f"GTM0G{primary:02X}00{subtype:02X}{argument:02X}".encode() + raw


def decode(payload):
    m = re.fullmatch(
        rb"GTM0G([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([^\x00\r\n]*)",
        payload,
    )
    if m is None:
        return None
    if len(m[5]) > 447:
        raise MatchError("fragmented/oversized match request unsupported")
    try:
        text = m[5].decode("ascii")
    except UnicodeDecodeError as e:
        raise MatchError("match fields must be ASCII") from e
    sections = {}
    if text:
        if not text.startswith("@"):
            raise MatchError("section header required")
        for item in text[1:].split("@"):
            chunks = item.split("/")
            name, equal, value = chunks[0].partition("=")
            if not equal or value or name in sections:
                raise MatchError("invalid/duplicate section")
            fields = {}
            for item in chunks[1:]:
                key, equal, value = item.partition("=")
                if not equal or not key or key in fields:
                    raise MatchError("invalid/duplicate field")
                fields[key] = value
            sections[name] = fields
    return Packet(int(m[1], 16), int(m[3], 16), int(m[4], 16), sections)


RULES = "@Tet=/bm=0/du=0/st=0/cb=0/ca=0/gs=1/tl=2@Tab=/in=1/lu=99999/ll=0/au=300/al=100/co=4/pa=0"
