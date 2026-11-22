import struct
import unittest
from types import SimpleNamespace

from tmserver.protocol.identities import ROOM_RAW, game_id, player_id, service_nick
from tmserver.game.models import DEFAULT_GAME_RULES, DEFAULT_TABLE_RULES
from tmserver.rooms.packets import (
    ROOM_CHANNEL,
    ROOM_ID,
    TABLE_CHANNEL,
    TABLE_ID,
    directory_files,
)
from tmserver.rooms.service import RoomService
from tmserver.protocol.commands import Command, encode, parse


def presence(token=b"DE", identity=123, *, info=b"A" * 31):
    fields = [
        f"0x{identity:016X}".encode(),
        b"1",
        b"2",
        b"0",
        b"LocalPlayer",
        b"0",
        b"0",
        b"0",
        b"0",
        f"0x{ROOM_ID:016X}".encode(),
        info,
    ]
    return b"GTM0L" + encode([Command(token, b"\x06".join(fields))])


class RoomTests(unittest.TestCase):
    def test_info_bounds_reject_before_presence_or_replay_changes(self):
        for token in (b"DE", b"PD"):
            with self.subTest(token=token):
                room = RoomService()
                if token == b"PD":
                    room.handle("one", presence())
                before = room.snapshot(), dict(room.history)
                for info in (b"A" * 32, b"A" * 33, b"A\0B"):
                    with self.subTest(info=info), self.assertRaises(ValueError):
                        room.handle("one", presence(token, info=info))
                    self.assertTrue(
                        (room.snapshot(), dict(room.history)) == before,
                        "rejected info changed the room or replay history",
                    )
                for info in (b"", b"A" * 30, b"A" * 31):
                    output = room.handle("one", presence(token, info=info))
                    self.assertEqual(parse(output[-1][1][5:])[-1].payload.split(b"\x06")[-1], info)
                    self.assertEqual(room.members["one"][1][0x38:0x58], info.ljust(32, b"\0"))

    def test_single_and_paired_cpu_info_preserve_status_and_termination(self):
        for count in (1, 2):
            with self.subTest(count=count):
                room = RoomService(player_count=count + 1)
                info = b"A" * 25 + b"BCDEFG"
                room.handle("one", presence(info=info))
                opponents = [
                    SimpleNamespace(name=b"Local Computer", nickname_id=512 + i, character_id=2 + i)
                    for i in range(count)
                ]
                output = room.practice_opponents("one", opponents)
                self.assertEqual(len(output), count)
                for index, (_, packet) in enumerate(output):
                    published = parse(packet[5:])[-1].payload.split(b"\x06")[-1]
                    self.assertEqual(len(published), 31)
                    self.assertEqual(published[23:], b"DBBCDEFG")
                    record = room.npcs[512 + index][1]
                    self.assertEqual(record[0x38:], published + b"\0")
                    offset = 0x50 + (index + 1) * 0x58
                    self.assertEqual(room.snapshot()[offset : offset + 0x58], record)

    def test_rule_changes_publish_even_without_occupancy_change(self):
        room = RoomService()
        room.handle("one", presence())
        game = dict(DEFAULT_GAME_RULES, gs=0, tl=0)
        table = dict(DEFAULT_TABLE_RULES, lu=100)
        out = room.set_table(1, 2, 123, game, table)
        info = parse(out[0][1][5:])[-1].payload.split(b"\x06")[-1]
        self.assertEqual(info[35:37], b"AA")
        self.assertEqual(info[39:44], b"AAAGE")
        self.assertEqual(room.snapshot()[0x5878 : 0x5878 + 49], info)
        self.assertEqual(room.set_table(1, 2, 123, game, table), [])
        game["tl"] = 3
        out = room.set_table(1, 2, 123, game, table)
        self.assertEqual(len(out), 1)
        self.assertEqual(room.table_metadata[35], ord("D"))
        before = room.snapshot()
        with self.assertRaises(ValueError):
            room.set_table(1, 2, 123, game, dict(table, au=99999))
        self.assertEqual(room.snapshot(), before)

    def test_identity_round_trip_and_directory_capacity(self):
        self.assertEqual(player_id(service_nick(ROOM_RAW)), game_id(ROOM_RAW))
        files = directory_files()
        self.assertEqual(len(files["b/g/RL000"]), 0xC848)
        r = files["b/g/RL000"][0x48:]
        self.assertEqual(struct.unpack_from("<Q", r)[0], ROOM_ID)
        self.assertEqual(r[0x6D:0x70], b"ABA")
        self.assertEqual(r[0x28:0x2A], b"BA")  # Custom rules, Computer enabled.
        self.assertEqual(r[0x49:0x50], b"AAAAACB")
        self.assertEqual(r[0xB8:0xC5].split(b"\0")[0], ROOM_CHANNEL)
        self.assertTrue(ROOM_CHANNEL.startswith(b"#") and TABLE_CHANNEL.startswith(b"#"))
        self.assertLessEqual(max(len(ROOM_CHANNEL), len(TABLE_CHANNEL)), 12)

    def test_table_and_cpu_presence_are_sequenced_and_in_snapshot(self):
        r = RoomService()
        r.handle("one", presence())
        cpu = SimpleNamespace(name=b"Local Computer", nickname_id=512, character_id=2)
        npc = r.practice_opponents("one", (cpu,))
        self.assertEqual(parse(npc[0][1][5:])[-1].token, b"PD")
        self.assertEqual(r.npcs[512][1][0x38 + 23 : 0x38 + 25], b"DB")
        self.assertEqual(r.practice_opponents("one", (cpu,)), [])
        out = r.set_table(1, 2, 123)
        fields = parse(out[0][1][5:])[-1].payload.split(b"\x06")
        self.assertEqual(fields[:4], [TABLE_CHANNEL, b"0", f"0x{TABLE_ID:016X}".encode(), b"1"])
        self.assertEqual(r.set_table(1, 2, 123), [])
        snap = r.snapshot()
        self.assertEqual(struct.unpack_from("<III", snap, 0x40), (103, 2, 2))
        self.assertEqual(struct.unpack_from("<Q", snap, 0x50 + 0x58)[0], 512)
        self.assertEqual(snap[0x5850 + 0x14], 1)
        r.clear_npcs()
        r.set_table(0, 0, 0)
        self.assertEqual(struct.unpack_from("<III", r.snapshot(), 0x40), (105, 1, 2))

    def test_join_reply_then_delta_and_snapshot(self):
        r = RoomService()
        out = r.handle("one", presence())
        self.assertEqual(parse(out[0][1][5:]), [Command(b"DS", f"0x{ROOM_ID:016X}".encode())])
        self.assertEqual([c.token for c in parse(out[1][1][5:])], [b"DD", b"DN", b"DC", b"PD"])
        snap = r.snapshot()
        self.assertEqual(len(snap), 0xC050)
        self.assertEqual(struct.unpack_from("<III", snap, 0x40), (101, 1, 2))
        self.assertEqual(struct.unpack_from("<Q", snap, 0x50)[0], 123)
        self.assertEqual(struct.unpack_from("<Q", snap, 0x5850)[0], TABLE_ID)
        self.assertEqual(snap[0x50 + 0x38 : 0x50 + 0x58], b"A" * 31 + b"\0")

    def test_replay_and_disconnect(self):
        r = RoomService()
        r.handle("one", presence())
        r.handle("two", presence(identity=124))
        replay = r.handle("one", b"GTM0L" + encode([Command(b"DR", b"100")]))
        self.assertEqual([int(parse(p[5:])[2].payload) for _, p in replay], [101, 102])
        self.assertEqual(r.handle("one", b"GTM0L" + encode([Command(b"DR", b"102")])), [])
        out = r.leave("one")
        self.assertEqual(len(out), 1)
        self.assertEqual(parse(out[0][1][5:])[-1], Command(b"PC", b"0x000000000000007B"))
        self.assertEqual(struct.unpack_from("<I", r.snapshot(), 0x44)[0], 1)
        stale = r.handle("two", b"GTM0L" + encode([Command(b"DR", b"0")]))
        self.assertEqual(parse(stale[0][1][5:])[0].token, b"DO")

    def test_bad_record_does_not_mutate_state(self):
        r = RoomService()
        with self.assertRaises(ValueError):
            r.handle("bad", b"GTM0L" + encode([Command(b"DE", b"bad")]))
        self.assertEqual(r.sequence, 100)
        self.assertEqual(r.members, {})

    def test_authenticated_presence_cannot_change_or_duplicate_identity(self):
        room = RoomService()
        before = room.snapshot()
        for payload, auth in (
            (presence(identity=124), (123, 1)),
            (presence(), (123, 2)),
            (presence(b"PD"), (123, 1)),
        ):
            with self.assertRaises(ValueError):
                room.handle("one", payload, authenticated_identity=auth)
            self.assertEqual(room.snapshot(), before)
        room.handle("one", presence(), authenticated_identity=(123, 1))
        before = room.snapshot()
        for peer, payload in (("two", presence()), ("one", presence(b"PD", 124))):
            with self.assertRaises(ValueError):
                room.handle(peer, payload)
            self.assertEqual(room.snapshot(), before)
        room.handle("one", presence(b"PD"), authenticated_identity=(123, 1))
        self.assertEqual(room.sequence, 102)


if __name__ == "__main__":
    unittest.main()
