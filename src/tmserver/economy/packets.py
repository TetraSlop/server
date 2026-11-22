"""Strict named envelopes for the economy and their field validation helpers."""

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
