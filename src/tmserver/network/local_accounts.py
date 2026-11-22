"""Local test accounts shared by the game and profile listeners."""

from tmserver.protocol.wire import polid_from_string, polid_scramble, polid_to_string

MAX_ACCOUNTS = 128  # Bounded local fixture pool, below reserved service raw IDs.


def account_name(character_id):
    if type(character_id) is not int or not 1 <= character_id <= MAX_ACCOUNTS:
        raise ValueError("local character ID outside fixture pool")
    return polid_to_string(character_id)


NAMES = tuple(account_name(i) for i in range(1, MAX_ACCOUNTS + 1))


def account_specs(count=2):
    if type(count) is not int or not 2 <= count <= MAX_ACCOUNTS:
        raise ValueError(f"local account count must be 2..{MAX_ACCOUNTS}")
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
