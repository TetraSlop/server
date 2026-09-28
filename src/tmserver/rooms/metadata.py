"""Encode the 49-byte PTL/TD table-info field consumed by TM 10066D10.

Input mappings use the same integer fields as MatchService.game_rules and
table_rules. Enabled passwords use the @Tab/pw hex string representation.
This is format validation, not permission to enable a gameplay feature.

Format limits differ from the editor: bm fits 16 bits, lu/ll fit 20 bits,
and only eight password bytes fit (the editor can send twelve). There are no
au/al fields: the decoder substitutes 300/100. Unrepresentable settings raise
TableMetadataError; values are never clamped or silently omitted. A trailing
run of '*' password bytes is the client's padding, not part of the password.

The decoder reads these rules for an occupied state-1 record. Empty/state-3
records zero their table rules; nonzero room context also clears game rules
in those states. Record state, service identity and channel belong to the
surrounding PTL/TD record and are deliberately not part of this function.
"""

import re
from collections.abc import Mapping


class TableMetadataError(ValueError):
    """The requested settings cannot be faithfully encoded in table metadata."""


_GAME_KEYS = frozenset(("bm", "du", "st", "cb", "ca", "gs", "tl"))
_TABLE_KEYS = frozenset(("in", "lu", "ll", "au", "al", "co", "pa"))


def _integer(name, value, minimum, maximum, *, boolean=False):
    if not isinstance(value, int) or (isinstance(value, bool) and not boolean):
        raise TableMetadataError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise TableMetadataError(f"{name} must be in {minimum}..{maximum}")
    return int(value)


def _mapping(name, values, keys, optional=frozenset()):
    if not isinstance(values, Mapping):
        raise TableMetadataError(f"{name} must be a mapping")
    if not keys <= values.keys() or values.keys() - keys - optional:
        raise TableMetadataError(
            f"{name} requires exactly {sorted(keys)}"
            + (f" and optional {sorted(optional)}" if optional else "")
        )


def _alphahex(value, width):
    return bytes(65 + int(c, 16) for c in f"{value:0{width}X}")


def _password(table, enabled):
    if not enabled:
        if "pw" in table:
            raise TableMetadataError("pw is only representable when pa=1")
        return b"********"
    password = table.get("pw")
    if not isinstance(password, str) or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}){0,8}", password):
        raise TableMetadataError("pw must be a hex string containing at most eight bytes")
    raw = bytes.fromhex(password)
    if any(c in raw for c in (0, 6, 7, 10, 13)):
        raise TableMetadataError("pw contains a NUL or PTL/TD/IRC framing delimiter")
    first_pad = raw.find(b"*")
    if first_pad >= 0 and raw[first_pad:] != b"*" * (len(raw) - first_pad):
        raise TableMetadataError("pw cannot contain '*' except as trailing client padding")
    return raw.ljust(8, b"*")


def table_info(number, count, owner, game_rules, table_rules):
    """Return 49 bytes for PTL raw+0x28 or the seventh TD field, without NUL.

    number is 1..4096; count is 0..255; owner is the client's uint64 identity.
    Those are encoding widths, not room allocation/admission policy. The live
    client table collection has only 128 slots. Boolean rules accept 0/1 or
    False/True; all other numeric fields require integers, excluding bool.
    """
    number = _integer("number", number, 1, 4096)
    count = _integer("count", count, 0, 255)
    owner = _integer("owner", owner, 0, 0xFFFFFFFFFFFFFFFF)
    _mapping("game_rules", game_rules, _GAME_KEYS)
    _mapping("table_rules", table_rules, _TABLE_KEYS, frozenset(("pw",)))
    game = {
        key: _integer(key, game_rules[key], 0, 1, boolean=True) for key in ("du", "st", "cb", "ca")
    }
    game["bm"] = _integer("bm", game_rules["bm"], 0, 0xFFFF)
    game["gs"] = _integer("gs", game_rules["gs"], 0, 2)
    game["tl"] = _integer("tl", game_rules["tl"], 0, 5)
    table = {
        key: _integer(key, table_rules[key], 0, maximum)
        for key, maximum in (
            ("in", 2),
            ("co", 8),
            ("lu", 0xFFFFF),
            ("ll", 0xFFFFF),
            ("au", 0xFFFFFFFF),
            ("al", 0xFFFFFFFF),
        )
    }
    table["pa"] = _integer("pa", table_rules["pa"], 0, 1, boolean=True)
    if (table["au"], table["al"]) != (300, 100):
        raise TableMetadataError(
            "table metadata cannot encode au/al; original decoder fixes au=300, al=100"
        )
    if table["ll"] > table["lu"]:
        raise TableMetadataError("ll must not exceed lu")
    password = _password(table_rules, table["pa"])

    # Native 10066FD8 and 10066F5D read the low nibble before the high nibble.
    game_flags = game["du"] | game["st"] << 1 | game["cb"] << 2 | game["ca"] << 3
    flags = bytes(
        (
            65 + game_flags,
            65 + game["tl"],
            65 + game["gs"],
            65 + table["in"] + (table["pa"] << 2),
            65 + table["co"],
        )
    )
    return (
        b"A"
        + _alphahex(number - 1, 3)
        + _alphahex(count, 2)
        + password
        + _alphahex(owner, 16)
        + _alphahex(game["bm"], 4)
        + flags
        + _alphahex(table["lu"], 5)
        + _alphahex(table["ll"], 5)
    )
