"""PTL table/service bytes match independent pre-Construct builders."""

from dataclasses import replace
from hashlib import sha256
import struct
import unittest

from construct import ConstructError

from tests.events.test_event_rooms import config
from tmserver.protocol.identities import TRADE_RAW, game_id
from tmserver.rooms.packets import TRADE_CHANNEL
from tmserver.rooms.records import TABLE_RECORD, build_table_record
from tmserver.rooms.service import RoomService


def old_record(identity, channel, metadata, state=0):
    # Preserve the former struct/slice layout as an independent comparison.
    record = bytearray(0x68)
    struct.pack_into("<QIIIB", record, 0, identity, 0, 0, 0, state)
    record[0x18 : 0x18 + len(channel)] = channel
    record[0x28 : 0x28 + len(metadata)] = metadata
    return bytes(record)


class TableRecordTests(unittest.TestCase):
    def test_shared_record_keeps_fixed_offsets_and_raw_metadata_bytes(self):
        self.assertEqual(TABLE_RECORD.sizeof(), 0x68)
        for metadata in (b"", b"A", bytes(range(49)), b"\xff" * 49):
            with self.subTest(metadata=metadata):
                self.assertEqual(
                    build_table_record(
                        identity=0, channel=b"#MAXIMUMCHANNEL", metadata=metadata, state=255
                    ),
                    old_record(0, b"#MAXIMUMCHANNEL", metadata, 255),
                )

    def test_new_builder_rejects_oversized_fields_without_truncation(self):
        for changes in (dict(channel=b"x" * 17), dict(metadata=b"x" * 50)):
            with self.subTest(changes=changes), self.assertRaises(ConstructError):
                build_table_record(
                    **(dict(identity=1, channel=b"#A", metadata=b"A" * 49) | changes)
                )

    def test_frozen_default_ptl_and_event_service_hashes(self):
        self.assertEqual(
            sha256(RoomService().snapshot()).hexdigest(),
            "3b71a4e6ff013a708aae3164f10636d70abc94effa0ba337f93e1619e2582f25",
        )
        self.assertEqual(
            sha256(
                b"".join(config(match_enabled=True, card_service_enabled=True).service_records())
            ).hexdigest(),
            "160accc870ef4e590d3f29029d34950a457c1e30957e14b48aed8739de9d50c7",
        )

    def test_ordinary_record_and_snapshot_match_old_builder_at_boundaries(self):
        for identity in (1, 0x0123456789ABCDEF, 0xFFFFFFFFFFFFFFFF):
            for channel in (b"#A", b"#" + b"X" * 14):
                for computer in (False, True):
                    room = RoomService(
                        table_id=identity, table_channel=channel, computer_enabled=computer
                    )
                    for state in range(7):
                        with self.subTest(
                            identity=identity, channel=channel, computer=computer, state=state
                        ):
                            room.set_table(state, 0, 0)
                            expected = old_record(identity, channel, room.table_metadata, state)
                            self.assertEqual(room.table_record(), expected)
                            self.assertEqual(room.snapshot()[0x5850:0x58B8], expected)
                            self.assertEqual(len(expected), 0x68)

    def test_trade_and_enabled_event_records_keep_prefixes_and_order(self):
        self.assertEqual(
            RoomService().service_records,
            (old_record(game_id(TRADE_RAW), TRADE_CHANNEL, b"D" + b"A" * 48),),
        )
        for enabled_e in (False, True):
            for enabled_f in (False, True):
                event = replace(
                    config(match_enabled=enabled_e, card_service_enabled=enabled_f),
                    room_id=2,
                    init_id=1,
                    card_id=0xFFFFFFFFFFFFFFFF,
                    init_channel=b"#" + b"E" * 14,
                    card_channel=b"#" + b"F" * 14,
                )
                expected = []
                if enabled_e:
                    expected.append(old_record(event.init_id, event.init_channel, b"E" + b"A" * 48))
                if enabled_f:
                    expected.append(old_record(event.card_id, event.card_channel, b"F" + b"A" * 48))
                with self.subTest(enabled_e=enabled_e, enabled_f=enabled_f):
                    self.assertEqual(event.service_records(), tuple(expected))

    def test_caller_supplied_service_record_preserves_opaque_bytes_and_position(self):
        custom = bytearray(old_record(123456, b"#CUSTOM", b"Z" + b"\xff" * 48))
        custom[8:0x18] = b"\xa5" * 16
        custom[0x59:] = b"\x5a" * 15
        room = RoomService(service_records=(bytes(custom),))
        room.add_table(123457, b"#SECOND")
        snapshot = room.snapshot()
        self.assertEqual(struct.unpack_from("<I", snapshot, 0x48)[0], 3)
        self.assertEqual(snapshot[0x5850 + 2 * 0x68 : 0x5850 + 3 * 0x68], custom)
        self.assertEqual(room.service_records, (bytes(custom),))

    def test_existing_validation_and_raw_integer_error_contracts(self):
        for changes in (
            dict(table_id=0),
            dict(table_id=True),
            dict(table_id=1.0),
            dict(table_id=1 << 64),
            dict(table_channel=b"#" + b"x" * 15),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                RoomService(**changes)
        room = RoomService()
        for state in (-1, 7):
            with self.subTest(state=state), self.assertRaises(ValueError):
                room.set_table(state, 0, 0)
        # The raw record writer itself has the original unsigned-byte contract.
        room.table_state = 255
        self.assertEqual(
            room.table_record(),
            old_record(room.table_id, room.table_channel, room.table_metadata, 255),
        )
        for state in (-1, 256, 1.5):
            room.table_state = state
            with self.subTest(raw_state=state):
                with self.assertRaises(struct.error) as old:
                    old_record(room.table_id, room.table_channel, room.table_metadata, state)
                with self.assertRaises(struct.error) as current:
                    room.table_record()
                self.assertEqual(str(current.exception), str(old.exception))


if __name__ == "__main__":
    unittest.main()
