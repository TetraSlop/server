"""Pure saved Tet/Tab preferences in the original TM0DataFile header.

24/Save changes selected defaults, not current table rules. The original
1014D9F0 importer reads the fields below. Unconsumed bytes remain untouched.
Password persistence accepts at most nine bytes: the importer copies ten
bytes without clearing the destination tail, so the terminator must fit.
"""

import re
from collections.abc import Mapping

PROFILE_SIZE = 0x3028
GAME_FIELDS = {
    "bm": (0xB8, 4, 2000),
    "du": (0xBC, 1, 1),
    "st": (0xC0, 1, 1),
    "cb": (0xC4, 1, 1),
    "ca": (0xC8, 1, 1),
    "gs": (0xCC, 1, 2),
    "tl": (0xD0, 1, 5),
}
TABLE_FIELDS = {
    "in": (0xD8, 1, 2),
    "lu": (0xDC, 4, 99999),
    "ll": (0xE0, 4, 99999),
    "au": (0xE4, 4, 99999),
    "al": (0xE8, 4, 99999),
    "co": (0xEC, 1, 8),
    "pa": (0xF0, 1, 1),
}


class PreferenceError(ValueError):
    pass


def _profile(profile):
    if not isinstance(profile, (bytes, bytearray)) or len(profile) != PROFILE_SIZE:
        raise PreferenceError("saved preferences require canonical TM0DataFile")


def _values(values, fields, *, password=False, max_password=9):
    if not isinstance(values, Mapping):
        raise PreferenceError("preference group must be a mapping")
    if set(values) != set(fields) | ({"pw"} if password and values.get("pa") == 1 else set()):
        raise PreferenceError("missing or unknown preference fields")
    output = {}
    for key, (_, _, maximum) in fields.items():
        value = values[key]
        if type(value) is not int or not 0 <= value <= maximum:
            raise PreferenceError(key + " outside supported settings domain")
        output[key] = value
    if password:
        if output["ll"] > output["lu"] or output["al"] > output["au"]:
            raise PreferenceError("preference lower bound exceeds upper bound")
        if output["pa"]:
            raw = values["pw"]
            if not isinstance(raw, str) or not re.fullmatch(r"(?:[0-9A-Fa-f]{2})*", raw):
                raise PreferenceError("password must be hexadecimal bytes")
            raw = bytes.fromhex(raw)
            if b"\0" in raw or len(raw) > max_password:
                raise PreferenceError(
                    f"saved password must fit {max_password} bytes plus its terminator"
                )
            output["pw"] = raw.hex().upper()
    return output


def apply_preferences(profile, *, game=None, table=None):
    """Return a new canonical profile with just the selected groups changed.

    Domains follow the current local settings adapter/UI, not historic server
    policy. Callers authorize the account and persist the returned bytes before
    completing the no-ack24 request. No live table, inventory or money changes.
    """
    _profile(profile)
    if game is None and table is None:
        raise PreferenceError("at least one saved preference group is required")
    game = _values(game, GAME_FIELDS) if game is not None else None
    table = _values(table, TABLE_FIELDS, password=True) if table is not None else None
    output = bytearray(profile)
    for values, fields in ((game, GAME_FIELDS), (table, TABLE_FIELDS)):
        if values is None:
            continue
        for key, (offset, size, _) in fields.items():
            output[offset : offset + size] = values[key].to_bytes(size, "little")
    if table is not None:
        # Disabled editor writes this same local padding. A selected table save
        # owns exactly the ten imported password bytes, not adjacent opaque bytes.
        password = bytes.fromhex(table["pw"]) if table["pa"] else b"********"
        output[0xF4:0xFE] = password.ljust(10, b"\0")
    return bytes(output)


def read_preferences(profile):
    """Project the file bytes read by the native importer, without normalizing.

    For an old password lacking a NUL in the first ten bytes, this returns the
    imported prefix only. Native live-memory tail contents cannot be inferred.
    """
    _profile(profile)
    result = {}
    for label, fields in (("Tet", GAME_FIELDS), ("Tab", TABLE_FIELDS)):
        result[label] = {
            key: int.from_bytes(profile[o : o + n], "little") for key, (o, n, _) in fields.items()
        }
    if result["Tab"]["pa"]:
        result["Tab"]["pw"] = bytes(profile[0xF4:0xFE]).split(b"\0", 1)[0].hex().upper()
    return result


def parse_save(payload):
    """Return apply_preferences keyword arguments for a complete24/Save.

    None means another family. Both Tet/Tab are present in the original sender;
    only groups selected by Save flags are persisted. An unselected group's
    twelve-byte wire password is not constrained by the durable nine-byte cap.
    """
    if not isinstance(payload, bytes) or payload[:7].upper() != b"GTM0G24":
        return None
    # Lazy import allows EconomyService's public method to call this module.
    from tmserver.economy.service import parse_named

    try:
        family, subtype, argument, sections = parse_named(payload)
    except ValueError as exc:
        raise PreferenceError(str(exc)) from exc
    if (family, subtype, argument) != (0x24, 0, 0) or set(sections) != {"Save", "Tet", "Tab"}:
        raise PreferenceError("saved preferences require24/00/00 Save, Tet and Tab")
    flags = sections["Save"]
    if (
        set(flags) != {"Tbl", "Game"}
        or any(v not in ("0", "1") for v in flags.values())
        or not any(v == "1" for v in flags.values())
    ):
        raise PreferenceError("Save requires Boolean Tbl/Game and one selected group")
    groups = []
    for name, fields in (("Tet", GAME_FIELDS), ("Tab", TABLE_FIELDS)):
        values = {}
        for key, value in sections[name].items():
            if key == "pw":
                values[key] = value
                continue
            if not re.fullmatch(r"[0-9]{1,10}", value):
                raise PreferenceError("invalid decimal preference")
            values[key] = int(value)
        groups.append(_values(values, fields, password=name == "Tab", max_password=12))
    game, table = groups
    if flags["Tbl"] == "1":
        table = _values(table, TABLE_FIELDS, password=True)  # Durable round-trip check.
    return {
        "game": game if flags["Game"] == "1" else None,
        "table": table if flags["Tbl"] == "1" else None,
    }


class TablePreferences:
    """Small account adapter; its owner supplies authorization and durable save."""

    def __init__(self, economy):
        self.economy = economy

    def handle(self, key, payload):
        update = parse_save(payload)
        if update is None:
            return None
        self.economy.save_table_preferences(key, **update)
        return []  # No distinct original acknowledgement is mapped.
