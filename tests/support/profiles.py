"""Profile test helpers: saved preference reader and the legacy profile handshake."""

from tmserver.profiles.preferences import GAME_FIELDS, PROFILE_SIZE, TABLE_FIELDS


def read_preferences(profile):
    """Project the TM0DataFile bytes the native importer reads, without normalizing.

    For an old password lacking a NUL in the first ten bytes, this returns the
    imported prefix only.
    """
    assert len(profile) == PROFILE_SIZE
    result = {}
    for label, fields in (("Tet", GAME_FIELDS), ("Tab", TABLE_FIELDS)):
        result[label] = {
            key: int.from_bytes(profile[o : o + n], "little") for key, (o, n, _) in fields.items()
        }
    if result["Tab"]["pa"]:
        result["Tab"]["pw"] = bytes(profile[0xF4:0xFE]).split(b"\0", 1)[0].hex().upper()
    return result
