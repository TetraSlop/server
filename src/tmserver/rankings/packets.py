"""Original PC ranking file layouts and binary row encoding."""

import struct

RECORD_SIZE = 0xE8
METADATA_PATH = "b/g/TM0RkData"
RESULT_PATH = "U/g/TM0_RANKLIST"
SCORE_OFFSETS = {0: 0x94, 1: 0x9C, 2: 0xA0, 3: 0xA4}


def encode_record(entry):
    """Only fields consumed by the audited rank UI are populated; rest zero.

    Previous-rank bytes C3..C7 stay zero (unavailable): no rank history is kept.
    """
    output = bytearray(RECORD_SIZE)
    # Original 101752F7 copies until NUL, even though the source name field is 16.
    # Keep a guaranteed terminator; this display-only form does not rename saves.
    output[:16] = entry.name[:15].ljust(16, b"\0")
    struct.pack_into("<QI", output, 0x10, entry.identity, entry.character_id)
    for rank_id, offset in SCORE_OFFSETS.items():
        struct.pack_into("<I", output, offset, entry.score(rank_id) or 0)
    output[0xC0] = int(entry.anonymous)
    output[0xC2] = int(entry.qualified)
    return bytes(output)
