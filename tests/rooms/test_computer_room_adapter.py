"""Two-NPC room publication and human-only durable round admission."""

import struct
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol.test_admission_identity import native_identity
from tests.rooms.test_room_service import presence
from tests.rooms.harness import local_peer
from tmserver.protocol.identities import GAME_KEY, game_id
from tmserver.game.models import Player
from tmserver.game.packets import packet
from tmserver.rooms.service import RoomService
from tmserver.network.server import Server
from tmserver.protocol.commands import parse


def opponent(identity, character=2, name=b"Computer"):
    return SimpleNamespace(nickname_id=identity, character_id=character, name=name)


class ComputerRoomTests(unittest.TestCase):
    def setUp(self):
        self.room = RoomService()
        self.room.handle("one", presence())
        self.room.handle("two", presence(identity=124))
        self.sibling = self.room.add_table(game_id(0x201), b"#TABLE2")
        self.first = opponent(0x200)
        self.second = opponent(0x400, 3)
        self.other = opponent(0x201)
        self.sibling.practice_opponents("two", (self.other,))

    def state(self):
        return self.room.snapshot(), dict(self.room.npc_tables), dict(self.room.history)

    def test_two_opponents_shrink_retry_and_cleanup_preserve_sibling(self):
        out = self.room.practice_opponents("one", (self.first, self.second))
        self.assertEqual(
            [parse(p[5:])[-1].token for peer, p in out if peer == "one"], [b"PD", b"PD"]
        )
        snapshot = self.room.snapshot()
        self.assertEqual(len(snapshot), 0xC050)
        self.assertEqual(struct.unpack_from("<I", snapshot, 0x44)[0], 5)
        self.assertEqual(set(self.room.npcs), {0x200, 0x400, 0x201})
        before = self.state()
        self.assertEqual(self.room.practice_opponents("one", (self.first, self.second)), [])
        self.assertEqual(self.state(), before)
        out = self.room.practice_opponents("one", (self.first,))
        self.assertEqual([parse(p[5:])[-1].token for peer, p in out if peer == "one"], [b"PC"])
        self.assertEqual(set(self.room.npcs), {0x200, 0x201})
        self.room.clear_npcs()
        self.assertEqual(set(self.room.npcs), {0x201})
        self.sibling.practice_opponents("two", (self.other, opponent(0x401, 3)))
        self.sibling.clear_npcs()
        self.assertFalse(self.room.npcs)

    def test_invalid_replacement_cannot_remove_old_npcs_or_touch_sibling(self):
        self.room.practice_opponents("one", (self.first, self.second))
        before = self.state()
        changed = opponent(0x200, name=b"New name")
        for rows in (
            (changed, opponent(0x400, -1)),
            (changed, changed),
            (changed, self.other),
            (changed, opponent(123)),
            (changed, self.second, opponent(0x500)),
        ):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.room.practice_opponents("one", rows)
            self.assertEqual(self.state(), before)

    def test_dynamic_capacity_does_not_change_ordinary_table_capacity(self):
        for slot in (self.room, self.sibling):
            slot.set_table(2, 3, 123, computer_menu=True)
            self.assertEqual((slot.player_count, slot.table_count), (2, 3))
            before = self.state()
            with self.assertRaises(ValueError):
                slot.set_table(2, 3, 123)
            self.assertEqual(self.state(), before)
            slot.set_table(2, 2, 123)

    def test_npc_ranges_are_disjoint_from_each_other_and_game_identities(self):
        first = {0x1FF + number for number in range(1, 128)}
        second = {0x3FF + number for number in range(1, 128)}
        self.assertFalse(first & second)
        # All configured raw identities are 41-bit, so XOR preserves these high bits.
        self.assertNotEqual(GAME_KEY >> 41, 0)
        self.assertTrue(all(identity >> 41 == 0 for identity in first | second))
        for raw in (0, 1, 128, 0x100, 0x200, 0x400, 0x1FFFFFFFFFF):
            self.assertEqual(game_id(raw) >> 41, GAME_KEY >> 41)


class ComputerAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server(card_prizes=True, allow_wagers=True)
        self.peers = [local_peer(self.server, nick) for nick in self.server.accounts]
        self.peer = self.peers[0]
        self.key = self.peer.nick.decode()
        self.view = self.server.economy.account_view(self.key)
        await self.server.default_table.dispatch_table(
            self.peer, packet(0x41, 0x12, body=native_identity(self.view, "GameENC"))
        )

    async def asyncTearDown(self):
        await self.server.stop()

    async def test_selected_count_is_saved_with_one_debit_before_engine_resize(self):
        match = self.server.default_table.match
        self.assertEqual(match.player_count, 2)
        before = self.server.snapshot()
        other = self.peers[1].nick.decode()

        def save(accounts, journals):
            self.assertEqual(self.server.snapshot(), before)
            self.assertEqual(match.player_count, 2)
            self.assertEqual(set(accounts), {self.key})
            row = journals["match_economy"]["rounds"][0]
            self.assertEqual(
                (row["mode"], row["player_count"], row["seats"]), ("computer", 3, {"0": self.key})
            )

        with patch.object(self.server, "persist_changes", side_effect=save) as write:
            balances = self.server.default_table.authorize_match_round(
                match, 1, 100, player_count=3
            )
        write.assert_called_once()
        self.assertEqual(balances, {0: self.view.balance - 100})
        self.assertEqual(
            self.server.economy.snapshot()["accounts"][other], before["accounts"][other]
        )
        self.assertEqual(match.player_count, 2)
        self.assertEqual(set(self.server.default_table.match_reservations), {self.peer})

    async def test_failed_override_save_preserves_tokens_journals_and_engine(self):
        before = deepcopy(self.server.snapshot())
        token = self.server.default_table.match_reservations[self.peer]
        with patch.object(self.server, "persist_changes", side_effect=OSError("save failed")):
            with self.assertRaises(OSError):
                self.server.default_table.authorize_match_round(
                    self.server.default_table.match, 1, 100, player_count=3
                )
        self.assertEqual(self.server.snapshot(), before)
        self.assertIs(self.server.default_table.match_reservations[self.peer], token)
        self.assertEqual(self.server.economy.match_account(token), token.account)
        self.assertEqual(self.server.default_table.match.player_count, 2)
        self.assertIsNone(self.server.default_table.match_economy_round)

    async def test_computer_admission_remains_one_human(self):
        peer = self.peers[1]
        view = self.server.economy.account_view(peer.nick.decode())
        await self.server.default_table.dispatch_table(
            peer, packet(0x41, 0x12, body=native_identity(view, "GameENC"))
        )
        self.assertEqual([p.peer for p in self.server.default_table.match.players], [self.peer])
        self.assertEqual(set(self.server.default_table.match_reservations), {self.peer})
        self.assertFalse(self.server.economy.is_reserved(peer.nick.decode()))
        self.assertFalse(peer.closed)

    async def test_publish_updates_both_npcs_then_removes_only_second(self):
        room = self.server.room
        room.handle(self.peer, presence(identity=self.view.nn))
        match = self.server.default_table.match
        self.assertEqual(match.npc_nickname_id, 0x200)
        self.assertEqual(match.second_npc_nickname_id, 0x400)
        match.players += [
            Player(None, 2, b"First", match.npc_nickname_id),
            Player(None, 3, b"Second", match.second_npc_nickname_id),
        ]
        match._resize_lobby(3)
        match.phase = "selection"
        await self.server.default_table.publish_match(match, [])
        self.assertEqual((room.table_count, set(room.npcs)), (3, {0x200, 0x400}))
        before = room.snapshot()
        await self.server.default_table.publish_match(match, [])
        self.assertEqual(room.snapshot(), before)
        match.players.pop()
        match._resize_lobby(2)
        await self.server.default_table.publish_match(match, [])
        self.assertEqual((room.table_count, set(room.npcs)), (2, {0x200}))


if __name__ == "__main__":
    unittest.main()
