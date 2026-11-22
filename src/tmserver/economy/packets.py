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
