"""Original PC ranking file layouts and binary row encoding."""

import struct

RECORD_SIZE = 0xE8
METADATA_PATH = "b/g/TM0RkData"
RESULT_PATH = "U/g/TM0_RANKLIST"
SCORE_OFFSETS = {0: 0x94, 1: 0x9C, 2: 0xA0, 3: 0xA4, 4: 0xA4}
PREVIOUS_OFFSETS = {0: 0xC6, 1: 0xC7, 2: 0xC5, 3: 0xC4, 4: 0xC3}
UI_CATEGORIES = {0: 4, 1: 5, 2: 0, 3: 1, 4: 2}


def encode_record(entry):
    """Only fields consumed by the audited rank UI are populated; rest zero."""
    output = bytearray(RECORD_SIZE)
    # Original 101752F7 copies until NUL, even though the source name field is 16.
    # Keep a guaranteed terminator; this display-only form does not rename saves.
    output[:16] = entry.name[:15].ljust(16, b"\0")
    struct.pack_into("<QI", output, 0x10, entry.identity, entry.character_id)
    struct.pack_into("<I", output, 0x94, entry.total_prize)
    struct.pack_into("<I", output, 0x9C, entry.history.weekly_prize or 0)
    struct.pack_into("<I", output, 0xA0, entry.rating)
    struct.pack_into("<I", output, 0xA4, entry.history.last_week_rating or 0)
    output[0xC0] = int(entry.history.anonymous)
    output[0xC2] = int(entry.history.qualified)
    for rank_id, offset in PREVIOUS_OFFSETS.items():
        output[offset] = entry.history.previous_ranks[rank_id]
    return bytes(output)
