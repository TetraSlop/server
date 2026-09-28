"""Room chat departure preserves startup; a new room releases an idle lobby."""

import asyncio
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.events.test_event_rooms import presence
from tests.network.test_server import Client
from tests.protocol.test_admission_identity import native_identity
from tests.rooms.test_room_rules import settings
from tests.rooms import test_room_transitions as transitions
from tmserver.game.packets import decode, packet
from tmserver.network import dispatch as services
from tmserver.network.server import Server
from tmserver.protocol.identities import GAME_KEY, game_id, service_nick
from tmserver.protocol.commands import parse
from tmserver.rooms.config import load_ordinary_rooms


class OrdinaryRoomPartTests(unittest.IsolatedAsyncioTestCase):
    drain = transitions.RoomTransitions.drain
    send = transitions.RoomTransitions.send
    entry = transitions.RoomTransitions.entry
    enter = transitions.RoomTransitions.enter

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.server = Server(
            account_count=3,
            ordinary_rooms=load_ordinary_rooms(ROOT / "examples/auto_rooms.example.json"),
        )
        self.clients, self.inbox = [], [[], []]
        port = await self.server.start(0)
        for nick in list(self.server.accounts)[:2]:
            client = Client()
            self.clients.append(client)
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.peers = [self.server.nicks[c.nick] for c in self.clients]
        self.keys = [c.nick.decode() for c in self.clients]
        self.quick = self.server.ordinary_tables[-1]
        self.local = self.server.default_table

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()

    async def channel(self, side, verb, channel):
        await self.clients[side].send(verb + b" :" + channel)
        await self.drain(side)

    async def room(self, side, table):
        await self.enter(side, table.room)
        await self.channel(side, b"JOIN", table.room.room_channel)

    async def admit(self, side, table, *, computer=False):
        view = self.server.economy.account_view(self.keys[side])
        await self.send(
            side,
            table.room.table_id,
            packet(
                0x41,
                0x12 if computer else 1,
                body=native_identity(view, "GameENC" if computer else "GameEN"),
            ),
        )

    async def chat(self, side, target):
        view = self.server.economy.account_view(self.keys[side])
        await self.send(side, target, packet(0x42, 1, body=f"@Chat=/NN={view.nn:016X}"))

    async def move_to_local(self):
        return await services.dispatch(
            self.peers[0],
            service_nick(self.local.room.room_id ^ GAME_KEY),
            self.entry(0, self.local.room),
        )

    async def computer_menu(self):
        await self.room(0, self.local)
        await self.admit(0, self.local, computer=True)
        match = self.local.match
        token = self.local.match_reservations[self.peers[0]]
        # Captured native startup: GameENC -> room PART -> CheckJoinTable ->
        # Req -> table JOIN -> room PD. PART is only a chat-channel transition.
        await self.channel(0, b"PART", self.local.room.room_channel)
        self.assertIs(self.local.match, match)
        self.assertTrue(match.computer_menu)
        self.assertIs(self.local.match_reservations[self.peers[0]], token)
        self.assertIn(self.peers[0], self.local.room.members)
        await self.send(0, self.local.room.table_id, packet(0x2D, body="@CheckJoinTable="))
        view = self.server.economy.account_view(self.keys[0])
        await self.send(0, self.local.room.table_id, packet(1, body=native_identity(view, "Req")))
        await self.channel(0, b"JOIN", self.local.table_channel)
        await self.send(
            0, self.local.room.room_id, self.entry(0, self.local.room).replace(b"<DE>", b"<PD>", 1)
        )
        self.assertEqual(self.local.match.phase, "computer_menu")
        source = service_nick(self.local.table_raw)
        replies = [decode(p) for s, p in self.inbox[0] if s == source and p.startswith(b"GTM0G")]
        self.assertTrue(any("ComList" in reply.sections for reply in replies))

    async def test_room_part_without_table_channel_allows_computer_menu(self):
        await self.room(0, self.quick)
        await self.admit(0, self.quick)
        await self.send(
            0, self.quick.room.table_id, packet(0x14, body=settings(self.quick.room_rules))
        )
        before = self.server.economy.snapshot()
        self.assertNotIn(self.quick.table_channel, self.peers[0].channels)
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        await self.channel(0, b"PART", self.quick.room.room_channel)
        self.assertIs(self.server.table_for_peer(self.peers[0]), self.quick)
        await self.room(0, self.local)
        self.assertIsNone(self.server.table_for_peer(self.peers[0]))
        self.assertNotIn(self.peers[0], self.quick.room.members)
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
        self.assertEqual(self.quick.room.table_state, 0)
        self.assertEqual(self.server.economy.snapshot(), before)
        await self.computer_menu()
        # A repeated old PART is harmless even after the new menu reserves us.
        match = self.local.match
        token = self.local.match_reservations[self.peers[0]]
        await self.channel(0, b"PART", self.quick.room.room_channel)
        self.assertIs(self.local.match, match)
        self.assertIs(self.local.match_reservations[self.peers[0]], token)

    async def test_room_part_preserves_other_lobby_player_and_their_reservation(self):
        for side in (0, 1):
            await self.room(side, self.quick)
            await self.admit(side, self.quick)
            await self.chat(side, self.quick.room.room_id)
            await self.chat(side, self.quick.room.table_id)
        await self.send(
            0, self.quick.room.table_id, packet(0x14, body=settings(self.quick.room_rules))
        )
        match = self.quick.match
        token = self.quick.match_reservations[self.peers[1]]
        self.inbox = [[], []]
        await self.channel(0, b"PART", self.quick.room.room_channel)
        await self.room(0, self.local)
        self.assertIs(self.quick.match, match)
        self.assertEqual([p.peer for p in match.players], [self.peers[1]])
        self.assertIs(match.owner_peer, self.peers[1])
        self.assertIs(self.quick.match_reservations[self.peers[1]], token)
        self.assertNotIn(self.peers[0], self.quick.room.members)
        self.assertIn(self.peers[1], self.quick.room.members)
        for group in (self.quick.room.room_id, self.quick.room.table_id):
            self.assertEqual(
                set(self.server.chat.groups[service_nick(group ^ GAME_KEY)]), {self.peers[1]}
            )
        self.assertFalse(any(p.startswith(b"GTM0G41000E") for _, p in self.inbox[0]))
        await self.computer_menu()

    async def test_late_old_room_part_preserves_new_computer_admission(self):
        await self.room(0, self.quick)
        await self.computer_menu()
        await self.chat(0, self.local.room.room_id)
        await self.chat(0, self.local.room.table_id)
        groups = {group: dict(members) for group, members in self.server.chat.groups.items()}
        match = self.local.match
        token = self.local.match_reservations[self.peers[0]]
        before = self.server.economy.snapshot(), match.snapshot(), self.local.room.snapshot()
        await self.channel(0, b"PART", self.quick.room.room_channel)
        self.assertIs(self.local.match, match)
        self.assertIs(self.local.match_reservations[self.peers[0]], token)
        self.assertIn(self.peers[0], self.local.room.members)
        self.assertEqual(self.server.chat.groups, groups)
        self.assertEqual(
            (self.server.economy.snapshot(), match.snapshot(), self.local.room.snapshot()), before
        )

    async def test_native_room_to_table_sequence_starts_computer_match(self):
        await self.computer_menu()
        await self.send(
            0,
            self.local.room.table_id,
            packet(0x43, 6, 2, "@ComGame=/Rule=0|0|0|0|2|0|0|1/Com=1"),
        )
        self.assertEqual(self.local.match.phase, "selection")
        cards = self.server.economy.inventory(self.keys[0])[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(cards)
        )
        await self.send(0, self.local.room.table_id, packet(0x43, 7, body=body))
        self.assertEqual(self.local.match.phase, "turn")
        self.assertIn(self.peers[0], self.local.room.members)
        self.assertFalse(self.peers[0].closed)

    async def test_explicit_table_exit_from_computer_configuration_allows_reentry(self):
        await self.computer_menu()
        before = self.server.economy.snapshot()
        await self.send(0, self.local.room.table_id, packet(0x41, 13, body="@GameExit="))
        await self.channel(0, b"PART", self.local.table_channel)
        self.assertIsNone(self.server.table_for_peer(self.peers[0]))
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
        self.assertEqual(self.server.economy.snapshot(), before)
        await self.computer_menu()

    async def test_full_or_forged_room_entry_preserves_idle_table(self):
        await self.room(0, self.quick)
        await self.admit(0, self.quick)
        await self.channel(0, b"PART", self.quick.room.room_channel)
        match = self.quick.match
        token = self.quick.match_reservations[self.peers[0]]
        before = self.server.snapshot(), match.snapshot(), self.quick.room.snapshot()
        with self.assertRaisesRegex(ValueError, "different room"):
            await services.dispatch(
                self.peers[0],
                service_nick(self.local.room.room_id ^ GAME_KEY),
                self.entry(0, self.quick.room),
            )
        self.assertNotIn(self.peers[0], self.local.room.members)
        fake_peers = [object() for _ in range(16)]
        try:
            for i, peer in enumerate(fake_peers):
                self.local.room.handle(
                    peer, presence(self.local.room.room_id, game_id(20 + i), 20 + i, b"Capacity")
                )
            await self.enter(0, self.local.room)
            self.assertNotIn(self.peers[0], self.local.room.members)
            self.assertEqual(self.inbox[0][-1][1], b"GTM0L<DF>\x07-1\x07")
            self.assertIs(self.quick.match, match)
            self.assertIs(self.quick.match_reservations[self.peers[0]], token)
            self.assertEqual(
                (self.server.snapshot(), match.snapshot(), self.quick.room.snapshot()), before
            )
        finally:
            for peer in fake_peers:
                self.local.room.members.pop(peer, None)

    async def test_table_entry_cannot_bypass_room_navigation_or_reset_unconfigured_peers(self):
        for side in (0, 1):
            await self.room(side, self.quick)
            await self.admit(side, self.quick)
        await self.channel(0, b"PART", self.quick.room.room_channel)
        match = self.quick.match
        before = match.snapshot(), dict(self.quick.match_reservations)
        with self.assertRaisesRegex(ValueError, "current table"):
            await self.move_to_local()
        view = self.server.economy.account_view(self.keys[0])
        with self.assertRaisesRegex(ValueError, "current ordinary table"):
            await self.local.dispatch_table(
                self.peers[0], packet(0x41, 0x12, body=native_identity(view, "GameENC"))
            )
        self.assertEqual((match.snapshot(), self.quick.match_reservations), before)
        self.assertNotIn(self.peers[0], self.local.room.members)

    async def test_room_change_rechecks_start_handshake_after_waiting_for_lock(self):
        for side in (0, 1):
            await self.room(side, self.quick)
            await self.admit(side, self.quick)
        await self.send(
            0, self.quick.room.table_id, packet(0x14, body=settings(self.quick.room_rules))
        )
        await self.channel(0, b"PART", self.quick.room.room_channel)
        match = self.quick.match
        async with self.quick.match_lock:
            move = asyncio.create_task(self.move_to_local())
            await asyncio.sleep(0)
            self.assertFalse(move.done())
            match.handle(self.peers[0], packet(0x41, 10, body="@GameReady="))
            self.assertTrue(match.start_pending)
            before = match.snapshot(), dict(self.quick.match_reservations)
        with self.assertRaisesRegex(ValueError, "current table"):
            await asyncio.wait_for(move, 2)
        self.assertIs(self.quick.match, match)
        self.assertEqual((match.snapshot(), self.quick.match_reservations), before)
        self.assertIn(self.peers[0], self.quick.room.members)
        self.assertNotIn(self.peers[0], self.local.room.members)

    async def test_started_computer_setup_cannot_be_abandoned_by_room_entry(self):
        await self.computer_menu()
        match = self.local.match
        for phase in ("computer_menu", "selection"):
            with self.subTest(phase=phase):
                self.assertEqual(match.phase, phase)
                before = match.snapshot(), dict(self.local.match_reservations)
                with self.assertRaisesRegex(ValueError, "current table"):
                    await services.dispatch(
                        self.peers[0],
                        service_nick(self.quick.room.room_id ^ GAME_KEY),
                        self.entry(0, self.quick.room),
                    )
                self.assertEqual((match.snapshot(), self.local.match_reservations), before)
                self.assertNotIn(self.peers[0], self.quick.room.members)
                if phase == "computer_menu":
                    await self.send(
                        0,
                        self.local.room.table_id,
                        packet(0x43, 6, 2, "@ComGame=/Rule=0|0|0|0|2|0|0|1/Com=1"),
                    )

    async def test_pending_computer_entry_can_be_abandoned_by_new_room(self):
        await self.room(0, self.local)
        await self.admit(0, self.local, computer=True)
        await self.channel(0, b"PART", self.local.room.room_channel)
        self.assertTrue(self.local.match.computer_menu)
        self.assertEqual(self.local.match.phase, "lobby")
        await self.room(0, self.quick)
        self.assertIsNone(self.server.table_for_peer(self.peers[0]))
        self.assertNotIn(self.peers[0], self.local.room.members)
        self.assertIn(self.peers[0], self.quick.room.members)
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))

    async def test_destination_updates_do_not_overtake_entry_during_old_table_cleanup(self):
        await self.room(1, self.local)
        await self.room(0, self.quick)
        await self.admit(0, self.quick)
        await self.channel(0, b"PART", self.quick.room.room_channel)
        self.inbox = [[], []]
        sequence = self.local.room.sequence
        reached, resume = asyncio.Event(), asyncio.Event()
        publish = self.quick.publish_room

        async def paused(deliveries):
            reached.set()
            await resume.wait()
            await publish(deliveries)

        with patch.object(self.quick, "publish_room", paused):
            move = asyncio.create_task(self.move_to_local())
            try:
                await asyncio.wait_for(reached.wait(), 2)
                await services.dispatch(
                    self.peers[1],
                    service_nick(self.local.room.room_id ^ GAME_KEY),
                    self.entry(1, self.local.room).replace(b"<DE>", b"<PD>", 1),
                )
            finally:
                resume.set()
                await asyncio.wait_for(move, 2)
        await self.drain()
        source = service_nick(self.local.room.room_id ^ GAME_KEY)
        sequences = [
            int(command.payload)
            for sender, payload in self.inbox[1]
            if sender == source and payload.startswith(b"GTM0L")
            for command in parse(payload[5:])
            if command.token == b"DC"
        ]
        self.assertEqual(sequences, [sequence + 1, sequence + 2])


if __name__ == "__main__":
    unittest.main()
