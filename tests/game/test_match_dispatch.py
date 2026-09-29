"""Dispatch boundaries preserve authentication, validation order and no-reply semantics."""

import unittest

from tmserver.game.errors import MatchError
from tmserver.game.match import MatchService
from tmserver.game.packets import packet


class MatchDispatchTests(unittest.TestCase):
    def setUp(self):
        self.match = MatchService(mode="versus")
        self.match.register("alice", character_id=1, nickname_id=0x123)
        self.match.register("bob", character_id=2, nickname_id=0x456)

    def test_decode_precedes_seat_lookup_and_table_validation(self):
        self.assertIsNone(self.match.handle("unregistered", b"another service"))
        for request in (
            packet(0xFE),
            packet(0x41, 0x11, body="@Viewer=/IN=1"),
            packet(0x43, 0xFE),
        ):
            with self.subTest(request=request), self.assertRaisesRegex(MatchError, "register"):
                self.match.handle("unregistered", request)
        malformed = b"GTM0G41001100@Viewer=/IN=0/IN=0"
        with self.assertRaisesRegex(MatchError, "duplicate field"):
            self.match.handle("unregistered", malformed)

    def test_departed_peer_cannot_reach_table_or_gameplay_controls(self):
        self.match.players[0].departed = True
        before = self.match.snapshot()
        for request in (
            packet(0xFE),
            packet(0x41, 1, body="@Wrong="),
            packet(0x41, 0x11, body="@Viewer=/IN=1"),
            packet(0x20, body="@Wrong="),
            packet(0x43, 7, body="@Wrong="),
        ):
            with self.subTest(request=request):
                self.assertEqual(self.match.handle("alice", request), [])
                self.assertEqual(self.match.snapshot(), before)
        self.assertFalse(self.match.viewer_peers)
        self.assertIsNone(self.match.handle("alice", b"another service"))
        with self.assertRaisesRegex(MatchError, "duplicate field"):
            self.match.handle("alice", b"GTM0G41001100@Viewer=/IN=0/IN=0")

    def test_unknown_messages_and_handled_notices_remain_distinct(self):
        before = self.match.snapshot()
        for request in (packet(0xFE), packet(0x41, 0xFE), packet(0x43, 0xFE)):
            with self.subTest(request=request):
                self.assertIsNone(self.match.handle("alice", request))
        self.assertEqual(self.match.handle("alice", packet(0x41, 0x11, body="@Viewer=/IN=0")), [])
        self.assertEqual(self.match.viewer_peers, {"alice"})
        self.assertEqual(self.match.snapshot(), before)

    def test_late_admission_is_ignored_but_lobby_control_still_validates_its_section(self):
        self.match.begin()
        before = self.match.snapshot()
        for request in (
            packet(0x41, 1, body="@Wrong="),
            packet(0x41, 0x12, body="@Wrong="),
            packet(1, body="@Req=/NN=invalid"),
            packet(0x41, 10, body="@GameReady="),
        ):
            with self.subTest(request=request):
                self.assertEqual(self.match.handle("alice", request), [])
        with self.assertRaisesRegex(MatchError, "table-control subtype"):
            self.match.handle("alice", packet(0x41, 10, body="@Wrong="))
        self.assertEqual(self.match.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
