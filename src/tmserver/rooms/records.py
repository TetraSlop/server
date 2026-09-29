"""Shared native room, zone and PTL table/service records.

Names stay as bytes: event directory keys may contain non-ASCII characters.
Padding and the fixed native flags belong to the schema, not its callers.
"""

import struct

from construct import Byte, Bytes, Const, Int16ul, Int32ul, Int64ul, Padding, Struct
from construct import FormatFieldError


ROOM_RECORD = Struct(
    "identity" / Int64ul,  # 00
    Padding(8),
    "member_count" / Int32ul,  # 10
    Padding(4),
    "occupied_tables" / Int32ul,  # 18
    Padding(8),
    Const(1, Int16ul),  # 24
    Padding(1),
    Const(1, Byte),  # 27
    "rules_kind" / Bytes(1),  # 28: A=fixed, B=not set
    "computer" / Byte,  # 29: A=enabled, zero=disabled
    "name" / Bytes(31),  # 2A: includes the trailing '*'
    "rules" / Bytes(7),  # 49
    Padding(0x1A),
    "table_count" / Bytes(3),  # 6A: alphabetic hex
    "capacity" / Bytes(3),  # 6D: alphabetic hex
    Const(b"A"),  # 70
    Padding(0x47),
    "channel" / Bytes(16),  # B8
)

ZONE_RECORD = Struct(
    "member_count" / Int32ul,  # 00
    "room_count" / Int32ul,  # 04
    Padding(4),
    Const(b"ENAAA"),  # 0C
    "name" / Bytes(27),  # 11
    "address" / Bytes(16),  # 2C
    "index" / Byte,  # 3C
    Padding(3),
)


TABLE_RECORD = Struct(
    "identity" / Int64ul,  # 00
    Padding(12),  # Unknown DWORDs; E discovery requires DWORD+0C < 8.
    "state" / Byte,  # 14
    Padding(3),
    "channel" / Bytes(16),  # 18
    "metadata" / Bytes(49),  # 28: A/B table rules or D/E/F service marker.
    Padding(15),
)


def build_table_record(*, identity, channel, metadata, state=0):
    """Build one 0x68-byte PTL record; supplied opaque records bypass this codec."""
    try:
        return TABLE_RECORD.build(
            dict(
                identity=identity,
                state=state,
                channel=channel.ljust(16, b"\0"),
                metadata=metadata.ljust(49, b"\0"),
            )
        )
    except FormatFieldError as exc:
        # Construct wraps struct.pack; preserve its original numeric error.
        raise struct.error(str(exc.__context__)) from exc


def alphahex(value, width):
    if not 0 <= value < 16**width:
        raise ValueError("metadata value does not fit")
    return bytes(65 + int(x, 16) for x in f"{value:0{width}X}")


def build_room_record(
    *,
    identity,
    name,
    channel,
    member_count=0,
    occupied_tables=0,
    table_count=0,
    capacity=16,
    fixed_rules=False,
    computer_enabled=False,
    rules=b"\0" * 7,
):
    """Build one 0xC8-byte room; name is the complete wire key including '*'."""
    return ROOM_RECORD.build(
        dict(
            identity=identity,
            member_count=member_count,
            occupied_tables=occupied_tables,
            rules_kind=b"A" if fixed_rules else b"B",
            computer=ord("A") if computer_enabled else 0,
            name=name.ljust(31, b"\0"),
            rules=rules,
            table_count=alphahex(table_count, 3),
            capacity=alphahex(capacity, 3),
            channel=channel.ljust(16, b"\0"),
        )
    )


def build_zone_record(*, name, room_count, member_count=0, index=0, address=b"127.0.0.1"):
    """Build one 0x40-byte zone without decoding its directory name."""
    return ZONE_RECORD.build(
        dict(
            member_count=member_count,
            room_count=room_count,
            name=name.ljust(27, b"\0"),
            address=address.ljust(16, b"\0"),
            index=index,
        )
    )
