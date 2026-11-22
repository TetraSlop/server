"""Bounded named economy envelopes and their field validation dialect."""

import re
from functools import partial

from tmserver.checks import decimal, exact_keys, hex_bytes
from tmserver.economy.errors import EconomyError

_HEADER = re.compile(rb"GTM0G([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})")

_SECTION = re.compile(r"([A-Za-z][A-Za-z0-9]*|[0-9]+)=(.*)\Z")

_FIELD = re.compile(r"([A-Za-z][A-Za-z0-9]*)=([^/@]*)\Z")


# Economy fields accept a signed spelling (-0 included) and bound it afterwards.
_int = partial(decimal, signed=True, error=EconomyError, what="decimal field")
_hex = partial(hex_bytes, error=EconomyError)
_keys = partial(exact_keys, error=EconomyError)


def parse_named(payload):
    """Strict, bounded named envelope parser, separate from the BEL protocol."""
    match = _HEADER.match(payload)
    if not match or len(payload) > 13 + 447:
        raise EconomyError("invalid named envelope")
    family, secondary, subtype, session = (int(x, 16) for x in match.groups())
    if secondary != 0:
        raise EconomyError("unsupported secondary envelope byte")
    try:
        body = payload[13:].decode("ascii")
    except UnicodeDecodeError as exc:
        raise EconomyError("non-ASCII named body") from exc
    if not body.startswith("@") or any(ord(c) < 32 or ord(c) > 126 for c in body):
        raise EconomyError("invalid named body")
    sections = {}
    for part in body[1:].split("@"):
        parsed = _SECTION.fullmatch(part)
        if not parsed or parsed[1] in sections:
            raise EconomyError("invalid or repeated section")
        name, tail = parsed.groups()
        fields = {}
        if tail:
            if not tail.startswith("/"):
                raise EconomyError("invalid section body")
            for item in tail[1:].split("/"):
                field_match = _FIELD.fullmatch(item)
                if not field_match or field_match[1] in fields:
                    raise EconomyError("invalid or repeated field")
                fields[field_match[1]] = field_match[2]
        sections[name] = fields
    return family, subtype, session, sections


def named(family, subtype, session, body):
    encoded = body.encode("ascii")
    if len(encoded) > 447 or any(x < 32 or x > 126 for x in encoded):
        raise EconomyError("response exceeds native envelope capacity")
    if any(not 0 <= x <= 255 for x in (family, subtype, session)):
        raise EconomyError("invalid response header")
    return f"GTM0G{family:02X}00{subtype:02X}{session:02X}".encode() + encoded
