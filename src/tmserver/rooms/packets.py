"""Native room directory, table metadata and roster record codecs.

Layouts: protocol_map/ROOM_SEQUENCE.md, ROOM_CONTRACT_VECTORS.md and
DIRECTORY_UI_CONTRACT.md. Default routes and capacities are local policy.
"""

import struct

from tmserver.game.models import DEFAULT_GAME_RULES, DEFAULT_TABLE_RULES
from tmserver.protocol.identities import ROOM_RAW, TABLE_RAW, game_id
from tmserver.rooms.metadata import table_info as encode_table_info
from tmserver.rooms.records import alphahex as alphahex
from tmserver.rooms.records import build_room_record, build_zone_record
from tmserver.rooms.rules import RoomRules

ROOM_ID = game_id(ROOM_RAW)
TABLE_ID = game_id(TABLE_RAW)
ROOM_CHANNEL = b"#LOCALROOM01"
TABLE_CHANNEL = b"#LOCALTABLE1"
TRADE_CHANNEL = b"#LOCALTRADE1"


def table_info(
    number=1, count=0, owner=0, game_rules=None, table_rules=None, *, computer_enabled=True
):
    encoded = encode_table_info(
        number,
        count,
        owner,
        DEFAULT_GAME_RULES if game_rules is None else game_rules,
        DEFAULT_TABLE_RULES if table_rules is None else table_rules,
    )
    return (b"A" if computer_enabled else b"B") + encoded[1:]


def directory_files():
    zones = bytearray(0x848)
    struct.pack_into("<I", zones, 0x40, 1)
    zones[0x48:0x88] = build_zone_record(name=b"Local Lobby", room_count=1)
    rooms = bytearray(0xC848)
    struct.pack_into("<I", rooms, 0x40, 1)
    rooms[0x48:0x110] = build_room_record(
        identity=ROOM_ID,
        name=b"Local Room*",
        channel=ROOM_CHANNEL,
        table_count=1,
        computer_enabled=True,
        rules=RoomRules(**DEFAULT_GAME_RULES).packed(),
    )
    return {"b/g/ZL": bytes(zones), "b/g/RL000": bytes(rooms)}


def player_record(payload):
    fields = payload.split(b"\x06")
    if len(fields) != 11:
        raise ValueError("DE/PD requires eleven fields")
    b = bytearray(0x58)
    for index, offset, fmt, base in [
        (0, 0, "Q", 16),
        (1, 0x18, "I", 10),
        (2, 0x26, "B", 10),
        (3, 0x24, "H", 10),
        (5, 0x27, "B", 10),
        (7, 0x1C, "I", 10),
        (8, 0x20, "I", 10),
        (9, 8, "Q", 16),
    ]:
        try:
            struct.pack_into("<" + fmt, b, offset, int(fields[index], base))
        except (ValueError, struct.error) as exc:
            raise ValueError("invalid player field") from exc
    # Info needs a terminator in its 32-byte native buffer; preserve the
    # separate full-width canonical name contract. See ROOM_INFO_BOUNDARY.md.
    for index, offset, limit in [(4, 0x28, 16), (10, 0x38, 31)]:
        if len(fields[index]) > limit or b"\0" in fields[index]:
            raise ValueError("player string exceeds record")
        b[offset : offset + len(fields[index])] = fields[index]
    return struct.unpack_from("<Q", b)[0], bytes(b)
