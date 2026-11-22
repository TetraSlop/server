"""GTM0G named envelopes: four hex header bytes, then one ASCII body of at most 447 bytes.

The header is family, secondary, subtype and session. The body is
``@Section=/Key=value/...@Next=...``. Two dialects share this codec:

- strict (economy, profiles and event services): secondary 00, a non-empty
  printable body, and alphanumeric section and field names;
- lenient (match traffic): the secondary byte is ignored, the body may be
  empty, and names are any text before ``=``.
"""

import re
from typing import NamedTuple

MAX_BODY = 447
_HEADER = re.compile(rb"GTM0G([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})")
_SECTION = re.compile(r"[A-Za-z][A-Za-z0-9]*|[0-9]+")
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


class Envelope(NamedTuple):
    family: int
    subtype: int
    session: int
    sections: dict


def encode(family, subtype=0, session=0, body="", *, printable=True, error=ValueError):
    """Build one envelope. Strict bodies are printable 32..126; lenient ones exclude NUL, CR, LF."""
    if any(not 0 <= value <= 255 for value in (family, subtype, session)):
        raise error("envelope header byte out of range")
    raw = body.encode("ascii")
    if len(raw) > MAX_BODY or any(
        not 32 <= c <= 126 if printable else c in (0, 10, 13) for c in raw
    ):
        raise error("envelope body exceeds the single-body contract")
    return f"GTM0G{family:02X}00{subtype:02X}{session:02X}".encode() + raw


def parse(payload, *, strict=True, error=ValueError):
    header = _HEADER.match(payload)
    if not header or len(payload) > 13 + MAX_BODY:
        raise error("invalid or oversized named envelope")
    family, secondary, subtype, session = (int(x, 16) for x in header.groups())
    if strict and secondary:
        raise error("unsupported secondary envelope byte")
    try:
        body = payload[13:].decode("ascii")
    except UnicodeDecodeError as exc:
        raise error("envelope body must be ASCII") from exc
    if strict and (not body or any(not " " <= c <= "~" for c in body)):
        raise error("invalid named body")
    sections = {}
    if body:
        if not body.startswith("@"):
            raise error("section header required")
        for part in body[1:].split("@"):
            head, *items = part.split("/")
            name, equal, value = head.partition("=")
            if not equal or value or name in sections or strict and not _SECTION.fullmatch(name):
                raise error("invalid or duplicate section")
            fields = {}
            for item in items:
                key, equal, value = item.partition("=")
                if not equal or not key or key in fields or strict and not _FIELD.fullmatch(key):
                    raise error("invalid or duplicate field")
                fields[key] = value
            sections[name] = fields
    return Envelope(family, subtype, session, sections)
