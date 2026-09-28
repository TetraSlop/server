"""Explicit synthetic local accounts shared by both legacy transports."""

from tmserver.protocol.wire import POLID_CHARS, polid_from_string, polid_scramble

MAX_ACCOUNTS = 128  # Bounded local fixture pool, below reserved service raw IDs.


def account_name(character_id):
    if type(character_id) is not int or not 1 <= character_id <= MAX_ACCOUNTS:
        raise ValueError("local character ID outside fixture pool")
    digits = bytearray(8)
    value = character_id
    for index in range(7, -1, -1):
        value, digit = divmod(value, 36)
        digits[index] = POLID_CHARS[digit]
    return digits.decode("ascii")


NAMES = tuple(account_name(i) for i in range(1, MAX_ACCOUNTS + 1))


def account_specs(count=2):
    if type(count) is not int or not 2 <= count <= MAX_ACCOUNTS:
        raise ValueError(f"local account count must be2..{MAX_ACCOUNTS}")
    return {
        polid_scramble(polid_from_string(name)).decode("ascii"): (
            name.encode("ascii"),
            b"tm-local-test",
            index + 1,
        )
        for index, name in enumerate(NAMES[:count])
    }


def display_name(character_id):
    return b"LocalPlayer" if character_id == 1 else f"LocalPlayer{character_id}".encode("ascii")
