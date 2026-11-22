"""Bounds and shape checks shared by the services.

Each check raises the caller's exception class, so a service keeps its own
error type while sharing the rule.
"""

import re

STABLE_ID = r"[A-Za-z0-9_.:-]{1,128}"


def uint(value, maximum=0xFFFFFFFF, minimum=0, *, error=ValueError, what="integer"):
    """An int (never a bool) within minimum..maximum."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise error(f"{what} outside {minimum}..{maximum}")
    return value


def decimal(text, maximum=0xFFFFFFFF, minimum=0, *, signed=False, error=ValueError,
            what="decimal"):
    """Up to ten ASCII digits, with an optional leading minus when signed."""
    if isinstance(text, bytes):
        text = text.decode("latin1")
    pattern = r"-?[0-9]{1,10}" if signed else r"[0-9]{1,10}"
    if not isinstance(text, str) or re.fullmatch(pattern, text) is None:
        raise error(f"invalid {what}")
    number = int(text)
    if not minimum <= number <= maximum:
        raise error(f"{what} outside {minimum}..{maximum}")
    return number


def stable_id(value, *, error=ValueError, what="ID"):
    """A durable identifier: 1..128 of A-Z, a-z, 0-9 and _.:-"""
    if not isinstance(value, str) or re.fullmatch(STABLE_ID, value) is None:
        raise error(f"invalid {what}")
    return value


def exact_keys(mapping, names, *, error=ValueError, what="fields"):
    """The mapping has exactly these keys."""
    if set(mapping) != set(names):
        raise error(f"unexpected or missing {what}")


def hex_bytes(text, maximum, *, error=ValueError, what="string"):
    """Even-length hex of at most `maximum` bytes, with no embedded NUL."""
    if (
        not isinstance(text, str)
        or len(text) % 2
        or len(text) > maximum * 2
        or re.fullmatch(r"[0-9A-Fa-f]*", text) is None
    ):
        raise error(f"invalid hexadecimal {what}")
    value = bytes.fromhex(text)
    if b"\0" in value:
        raise error(f"embedded terminator in {what}")
    return value
