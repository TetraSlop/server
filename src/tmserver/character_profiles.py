"""Generic GTM0P character profiles, separate from TM0DataFile.

All71 PO positions are retained. Known editor tags apply partial updates;
opaque positions stay intact. Registration/name/summary mapping is explicit
local policy from PROFILE_CONTRACT.md, not a historical database reconstruction.
"""

import re
from copy import copy

from tmserver.resources import read_json
from tmserver.tm_protocol import Command, encode, parse

LAYOUT = tuple(read_json("profile_contract.json")["layout"]["wire_fields"])
VISIBILITY = {b"FO": 8, b"VO": 40}
NO_VISIBILITY = {6: 47, 14: 55}


def decimal(value, maximum):
    if re.fullmatch(rb"[0-9]{1,10}", value) is None or int(value) > maximum:
        raise ValueError("profile integer outside field domain")
    return int(value)


def locator(value):
    fields = value.split(b"\x06")
    if len(fields) != 2 or re.fullmatch(rb"0x[0-9A-Fa-f]{16}", fields[0]) is None:
        raise ValueError("profile locator requires identity and character key")
    return int(fields[0], 16), decimal(fields[1], 0xFFFFFFFF)


def bounded(value, size):
    if len(value) > size or any(c in value for c in (0, 6, 7, 10, 13)):
        raise ValueError("profile text outside terminated wire field")
    return value.decode("latin1")


class CharacterProfiles:
    def __init__(self, economy):
        self.economy = economy
        self.records = {}

    def stage_state(self):
        # Retain the full read-only economy for cross-account public lookups.
        staged = copy(self)
        staged.records = dict(self.records)  # Immutable record tuples.
        return staged

    def publish_state(self, staged):
        self.records = staged.records

    def _default(self, key):
        account = self.economy.account_view(key)
        fields = ["0"] * 71
        for field in LAYOUT:
            if field["type"] == "hex-u64":
                fields[field["index"]] = "0x0000000000000000"
            elif field["type"].startswith("string"):
                fields[field["index"]] = ""
        fields[0] = f"0x{account.player_id:016X}"
        fields[1] = "1"
        # PO uses a terminated string[16]; TM0DataFile allows all 16 bytes.
        # Shorten only this default presentation, never the account identity.
        fields[2] = bounded(account.name[:15], 15)
        for index in (8, 40, 47, 55):
            fields[index] = "3"
        # Card-level/title/history have no asserted cross-format mapping here.
        return fields

    def _fields(self, key):
        record = self.records.get(key)
        return self._default(key) if record is None else list(record)

    def _owned(self, key, address):
        account = self.economy.account_view(key)
        identity, character = address
        return identity in (account.player_id, account.nn) and character in (1, account.player_id)

    def _find(self, address):
        for key in self.economy.snapshot()["accounts"]:
            if self._owned(key, address):
                return key
        return None

    @staticmethod
    def _response(token, payload=b""):
        return b"GTM0P" + encode([Command(token, payload)])

    def handle(self, key, payload):
        if not payload.startswith(b"GTM0P"):
            return None
        commands = parse(payload[5:])
        if not commands or commands[0].token not in (b"PG", b"GR", b"CR"):
            return None
        first = commands[0]
        address = locator(first.payload)
        if first.token == b"PG":
            if len(commands) != 1:
                raise ValueError("unexpected fields after profile read")
            owner = self._find(address)
            if owner is None:
                return [self._response(b"PM", b"-8706")]
            fields = self._fields(owner)
            fields[0] = f"0x{address[0]:016X}"
            fields[1] = str(address[1])
            return [self._response(b"PO", b"\x06".join(value.encode("latin1") for value in fields))]
        if not self._owned(key, address):
            return (
                [b"GTM0P" + encode([Command(b"GG", b""), Command(b"PE", b"-8706")])]
                if first.token == b"GR"
                else [self._response(b"NS", b"-8706")]
            )
        fields = self._fields(key)
        fields[0] = f"0x{address[0]:016X}"
        fields[1] = str(address[1])
        seen = set()
        for command in commands[1:]:
            token, value = command.token, command.payload
            unique = (token, value.split(b"\x06", 1)[0]) if token == b"NO" else (token, None)
            if unique in seen:
                raise ValueError("duplicate profile update field")
            seen.add(unique)
            if token == b"AN":
                fields[2] = bounded(value, 15)
                if not fields[2]:
                    raise ValueError("profile name cannot be empty")
            elif token == b"CI":
                locale, separator, summary = value.partition(b"\x06")
                if (
                    not separator
                    or re.fullmatch(rb"-?[0-9]{1,11}", locale) is None
                    or not -(1 << 31) <= int(locale) < (1 << 31)
                ):
                    raise ValueError("invalid profile language/summary")
                fields[9] = bounded(summary, 63)
            elif token == b"PP":
                fields[3] = str(decimal(value, 5))
            elif token in VISIBILITY:
                number = decimal(value, 3)
                if number not in (1, 3):
                    raise ValueError("profile visibility must be private1 or public3")
                fields[VISIBILITY[token]] = str(number)
            elif token == b"NO":
                selector, separator, flag = value.partition(b"\x06")
                if not separator:
                    raise ValueError("missing visibility selector")
                selector = decimal(selector, 255)
                number = decimal(flag, 3)
                if selector not in NO_VISIBILITY or number not in (1, 3):
                    raise ValueError("unsupported profile visibility selector")
                fields[NO_VISIBILITY[selector]] = str(number)
            else:
                raise ValueError("unsupported partial profile update")
        # Validate the complete response before committing the partial update.
        self._response(b"PO", b"\x06".join(value.encode("latin1") for value in fields))
        self.records[key] = tuple(fields)
        return [self._response(b"CS" if first.token == b"CR" else b"GK")]

    def snapshot(self):
        return {
            "version": 1,
            "records": {key: list(fields) for key, fields in self.records.items()},
        }

    @classmethod
    def restore(cls, economy, snapshot=None):
        result = cls(economy)
        if snapshot is None:
            return result
        if snapshot.get("version") != 1 or not isinstance(snapshot.get("records"), dict):
            raise ValueError("unsupported character profile snapshot")
        for key, values in snapshot["records"].items():
            economy.account_view(key)
            if (
                not isinstance(values, list)
                or len(values) != 71
                or any(not isinstance(v, str) for v in values)
            ):
                raise ValueError("character profile needs71 preserved fields")
            for field, value in zip(LAYOUT, values):
                raw = value.encode("latin1")
                kind = field["type"]
                if kind.startswith("string"):
                    bounded(raw, int(kind[7:-1]) - 1)
                elif kind == "hex-u64":
                    if re.fullmatch(rb"0x[0-9A-Fa-f]{16}", raw) is None:
                        raise ValueError("invalid stored profile identity")
                elif kind == "decimal-f32":
                    if re.fullmatch(rb"-?[0-9]+(?:\.[0-9]+)?", raw) is None:
                        raise ValueError("invalid stored profile float")
                else:
                    decimal(raw, (1 << int(kind[len("decimal-u") :])) - 1)
            if not result._owned(key, (int(values[0], 16), int(values[1]))):
                raise ValueError("stored profile belongs to another account")
            result._response(b"PO", b"\x06".join(v.encode("latin1") for v in values))
            result.records[key] = tuple(values)
        return result
