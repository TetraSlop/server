"""Strict named envelopes for the economy and their field validation helpers."""

import re
from functools import partial

from tmserver.checks import decimal, exact_keys, hex_bytes
from tmserver.economy.errors import EconomyError
from tmserver.protocol.envelope import encode, parse

# Economy fields accept a signed spelling (-0 included) and bound it afterwards.
_int = partial(decimal, signed=True, error=EconomyError, what="decimal field")
_hex = partial(hex_bytes, error=EconomyError)
_keys = partial(exact_keys, error=EconomyError)


parse_named = partial(parse, error=EconomyError)
named = partial(encode, error=EconomyError)


def check_entry(sections, section, account):
    """A ShReq or CvReq entry: exactly NN, HID, Dm, Vol and CN, naming the account."""
    if set(sections) != {section}:
        raise EconomyError(f"expected only {section}")
    fields = sections[section]
    _keys(fields, ("NN", "HID", "Dm", "Vol", "CN"))
    if (
        not re.fullmatch(r"[0-9A-Fa-f]{16}", fields["NN"])
        or int(fields["NN"], 16) != account.nn
        or _hex(fields["CN"], 16) != account.name
    ):
        raise EconomyError(f"{section} identity differs from the authenticated account")
    for key, maximum in (("HID", 0xFFFFFFFF), ("Dm", 255), ("Vol", 65535)):
        decimal(fields[key], maximum, error=EconomyError, what=f"{section} {key}")


def card_pages(head, cards, *, limit):
    """Split cards into @Card bodies of reduced records within the 447-byte envelope.

    head is the text between "@Card=" and "/E", such as "/P=0". Each page holds
    as many cards as fit, at most limit. Callers send their own empty page.
    """

    def body(start, count):
        records = "".join(
            f"@N{i}=/D=" + "|".join(map(str, card.reduced()))
            for i, card in enumerate(cards[start : start + count])
        )
        return f"@Card={head}/E={len(cards)}/S={start}/C={count}" + records

    pages, start = [], 0
    while start < len(cards):
        count = min(limit, len(cards) - start)
        while count and len(body(start, count)) > 447:
            count -= 1
        if not count:
            raise EconomyError("card cannot fit native page")
        pages.append(body(start, count))
        start += count
    return pages
