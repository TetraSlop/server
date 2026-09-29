"""Shared directory codecs preserve the previous complete binary file output."""

import struct
import unittest
from hashlib import sha256

from construct import ConstructError

from tests.events.test_event_rooms import config, presence
from tmserver.economy.service import EconomyService
from tmserver.events.rooms import EventRooms
from tmserver.events.service import EventHost
from tmserver.protocol.identities import game_id
from tmserver.rooms.packets import directory_files
from tmserver.rooms.records import (
    ROOM_RECORD,
    ZONE_RECORD,
    build_room_record,
    build_zone_record,
)


class DirectoryRecordTests(unittest.TestCase):
    def test_default_directory_matches_pre_construct_bytes(self):
        # Captured from the old struct/slice encoders before replacing them.
        self.assertEqual(
            {path: sha256(data).hexdigest() for path, data in directory_files().items()},
            {
                "b/g/ZL": "40cfbf54b5b86a62af3361b4075ae0bc8007efc119349f7d4ea9bffcd886f40c",
                "b/g/RL000": "3e253a551a71c322cf9b9191c4c9e07b89ce8b6b30779821a8753ba02340d0cf",
            },
        )

    def test_event_directories_match_pre_construct_bytes(self):
        configs = (
            config("event", 0x1100, b"Local Lobby", b"\xe9" * 30),
            config("remote", 0x2100, b"\xff" * 26, b"Final"),
        )
        events = EventRooms(EventHost(EconomyService(), configs))
        for index, binding in enumerate(events.rooms):
            events.bind_match_table(
                binding.event_id, game_id(0x1200 + index), f"#PLAY{index}".encode()
            )
            binding.room.handle(
                f"peer{index}", presence(binding.room_id, game_id(index + 1), index + 1)
            )
            binding.room.set_table(1, 2, game_id(index + 1))
        files = events.directory_files()
        self.assertEqual(
            {path: sha256(data).hexdigest() for path, data in files.items()},
            {
                "b/g/ZL": "be2a59adf7a95039e92153c1f4f256a2034e8a2c71e58a5ba1f4eb5a1b8bcfa4",
                "b/g/RL000": "2d026fdd9d91616825ebff36b49c0d94a34d93c6bf7d0cd0fe42414a1dbbec10",
                "b/g/RL001": "2c185e55948a61da6c13e6e4568f0a7e4afcfa1631618ce28d2e770643e15bd1",
            },
        )
        first = files["b/g/RL000"][0x110:0x1D8]
        self.assertEqual(first[0x28:0x2A], b"B\0")
        self.assertEqual(first[0x2A:0x49], b"\xe9" * 30 + b"*")
        self.assertEqual(first[0x49:0x50], bytes(7))

    def test_fixed_room_fields_preserve_exact_native_offsets(self):
        record = build_room_record(
            identity=0x0123456789ABCDEF,
            name=b"\xe9" * 30 + b"*",
            channel=b"#FIXEDROOM",
            member_count=0x12345678,
            occupied_tables=2,
            table_count=128,
            capacity=256,
            fixed_rules=True,
            computer_enabled=True,
            rules=b"ABCDEFG",
        )
        self.assertEqual(ROOM_RECORD.sizeof(), 0xC8)
        self.assertEqual(len(record), 0xC8)
        self.assertEqual(struct.unpack_from("<Q", record)[0], 0x0123456789ABCDEF)
        self.assertEqual(struct.unpack_from("<I", record, 0x10)[0], 0x12345678)
        self.assertEqual(struct.unpack_from("<I", record, 0x18)[0], 2)
        self.assertEqual(record[0x24:0x2A], b"\x01\0\0\x01AA")
        self.assertEqual(record[0x2A:0x50], b"\xe9" * 30 + b"*ABCDEFG")
        self.assertEqual(record[0x6A:0x71], b"AIABAAA")
        self.assertEqual(record[0xB8:], b"#FIXEDROOM".ljust(16, b"\0"))

    def test_zone_fields_preserve_raw_name_and_native_offsets(self):
        record = build_zone_record(
            name=b"\xff" * 26, room_count=256, member_count=255, index=31, address=b"host"
        )
        self.assertEqual(ZONE_RECORD.sizeof(), 0x40)
        self.assertEqual(len(record), 0x40)
        self.assertEqual(struct.unpack_from("<II", record), (255, 256))
        self.assertEqual(record[0x0C:0x11], b"ENAAA")
        self.assertEqual(record[0x11:0x2C], b"\xff" * 26 + b"\0")
        self.assertEqual(record[0x2C:0x3C], b"host".ljust(16, b"\0"))
        self.assertEqual(record[0x3C:], b"\x1f\0\0\0")

    def test_oversized_fields_never_resize_or_truncate_records(self):
        room = dict(identity=1, name=b"Room*", channel=b"#ROOM")
        for change in (dict(name=b"x" * 32), dict(channel=b"x" * 17), dict(rules=b"x" * 8)):
            with self.subTest(change=change), self.assertRaises(ConstructError):
                build_room_record(**(room | change))
        for change in (dict(name=b"x" * 28), dict(address=b"x" * 17)):
            with self.subTest(change=change), self.assertRaises(ConstructError):
                build_zone_record(**(dict(name=b"Zone", room_count=1) | change))


if __name__ == "__main__":
    unittest.main()
