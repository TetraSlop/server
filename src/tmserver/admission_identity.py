"""Native admission metadata; POL handle HID is distinct from character GID.

The authenticated session selects the account. NN and optional CN must agree
with that account at network boundaries. HID never selects a character or an
inventory; original constructors obtain it independently from host slot +2F8.
"""

import re


def validate_admission(fields, section, *, nn=None, name=None, require_nn=False):
    allowed = {"NN", "HID", "Dm", "Vol", "CN", "HN"} | (
        {"Tm0", "Tm1"} if section == "Req" else {"L"}
    )
    if section not in ("Req", "GameEN", "GameENC") or set(fields) - allowed:
        raise ValueError("unsupported admission field")
    if require_nn and "NN" not in fields:
        raise ValueError("missing admission nickname")
    if "NN" in fields:
        value = fields["NN"]
        if not re.fullmatch(r"[0-9a-fA-F]{16}", value) or nn is not None and int(value, 16) != nn:
            raise ValueError("admission nickname differs from authenticated account")
    for key, maximum in (
        ("HID", 0x7FFFFFFF),
        ("Dm", 255),
        ("Vol", 65535),
        ("L", 255),
        ("Tm0", 0),
        ("Tm1", 0),
    ):
        if key in fields and (
            not re.fullmatch(r"[0-9]+", fields[key]) or not 0 <= int(fields[key]) <= maximum
        ):
            raise ValueError("invalid admission scalar " + key)
    for key, maximum in (("CN", 16), ("HN", 31)):
        if key not in fields:
            continue
        value = fields[key]
        if not re.fullmatch(
            r"(?:[0-9a-fA-F]{2}){0," + str(maximum) + "}", value
        ) or b"\0" in bytes.fromhex(value):
            raise ValueError("invalid admission text " + key)
        if key == "CN" and name is not None and bytes.fromhex(value) != name:
            raise ValueError("admission name differs from authenticated account")
