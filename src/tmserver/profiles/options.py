"""Native84/85 option save; persistence precedes the positive acknowledgement."""

import re

from tmserver.economy.service import EconomyError, named, parse_named


def save_options(economy, key, payload):
    if not payload.startswith(b"GTM0G84"):
        return None
    family, subtype, argument, sections = parse_named(payload)
    if subtype or argument or set(sections) != {"Opt"}:
        raise EconomyError("invalid options envelope")
    f = sections["Opt"]
    account = economy.account_view(key)
    keys = {
        "NN",
        "HID",
        "Dm",
        "Vol",
        "CN",
        "Ar",
        "Cu",
        "Vi",
        "Se",
        "Bgm",
        "Per",
        "Ran",
        "CMD",
        "CAD",
        "CL",
        "CT",
        "HNSS",
    }
    if set(f) != keys:
        raise EconomyError("incomplete options fields")
    if not re.fullmatch("[0-9A-Fa-f]{16}", f["NN"]) or int(f["NN"], 16) != account.nn:
        raise EconomyError("options identity mismatch")
    if (
        not re.fullmatch("(?:[0-9A-Fa-f]{2}){1,16}", f["CN"])
        or bytes.fromhex(f["CN"]) != account.name
    ):
        raise EconomyError("options name mismatch")
    numeric = {}
    for name in keys - {"NN", "CN"}:
        if not re.fullmatch("[0-9]{1,10}", f[name]):
            raise EconomyError("invalid decimal option")
        numeric[name] = int(f[name])
    for name, maximum in (("HID", 0x7FFFFFFF), ("Dm", 255), ("Vol", 65535)):
        if numeric.pop(name) > maximum:
            raise EconomyError("options profile metadata out of range")
    economy.save_options(key, numeric)
    return named(0x85, 0, 0, "@OptAns=/Ans=1")
