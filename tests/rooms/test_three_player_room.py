"""Three-player table capacity and original room-consumer regressions."""

import struct
import sys
import unittest
from pathlib import Path

from tests.rooms.test_room_service import presence
from tmserver.rooms.service import RoomService
from tmserver.rooms.packets import directory_files
from tmserver.protocol.commands import parse


class ThreePlayerRoomTests(unittest.TestCase):
    def test_configured_occupancy_reaches_three_and_resets(self):
        room = RoomService(player_count=3)
        for i in range(3):
            room.handle(str(i), presence(identity=123 + i))
        for count in (1, 2, 3):
            out = room.set_table(1, count, 123)
            self.assertEqual(len(out), 3)
            self.assertEqual(room.table_metadata[:6], b"AAAAA" + bytes((65 + count,)))
            td = parse(out[0][1][5:])[-1]
            self.assertEqual(td.token, b"TD")
            self.assertEqual(td.payload.split(b"\x06")[-1], room.table_metadata)
            snapshot = room.snapshot()
            self.assertEqual(struct.unpack_from("<III", snapshot, 0x40), (room.sequence, 3, 2))
            self.assertEqual(snapshot[0x5878 : 0x5878 + 49], room.table_metadata)
        room.set_table(2, 3, 123)
        self.assertEqual(room.snapshot()[0x5864], 2)
        room.set_table(0, 0, 0)
        self.assertEqual((room.table_state, room.table_count, room.table_owner), (0, 0, 0))

    def test_capacity_rejection_is_atomic_and_default_stays_two(self):
        for maximum in (2, 3):
            room = RoomService(player_count=maximum)
            room.handle("one", presence())
            room.set_table(1, maximum, 123)
            before = room.snapshot(), list(room.history.items())
            with self.assertRaises(ValueError):
                room.set_table(1, maximum + 1, 123)
            self.assertEqual((room.snapshot(), list(room.history.items())), before)
        self.assertEqual(RoomService().player_count, 2)
        for invalid in (0, 1, 4, -1, True, 2.0, "3", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                RoomService(player_count=invalid)

    def test_room_directory_does_not_encode_per_table_seat_count(self):
        record = directory_files()["b/g/RL000"][0x48:]
        self.assertEqual(record[0x28], ord("B"))  # Custom rules, independent of seat count.
        self.assertEqual(record[0x6A:0x70], b"AABABA")  # one table, sixteen room members


if __name__ == "__main__":
    unittest.main()
