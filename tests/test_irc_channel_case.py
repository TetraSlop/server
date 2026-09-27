"""Encrypted channel aliases retain advertised names and exact service ownership."""

import unittest

from tests import test_chat_service as chats
from tests import test_event_room_part as events
from tests import test_spectator_integration as spectators
from tests.test_admission_identity import native_identity
from tmserver.identities import service_nick
from tmserver.match_service import decode, packet


class ChannelCaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = chats.ChatIntegrationTests.asyncSetUp
    asyncTearDown = chats.ChatIntegrationTests.asyncTearDown
    flush = chats.ChatIntegrationTests.flush

    async def test_case_aliases_share_roster_relay_and_membership_limit(self):
        channel = b"#MiXeD"
        peers = [self.server.nicks[c.nick] for c in self.clients]
        for side, spelling in enumerate((channel, channel.lower())):
            await self.clients[side].send(b"JOIN " + spelling)
            lines = await self.flush(side)
            self.assertEqual(lines[0], peers[side].prefix + b"JOIN :" + channel)
            self.assertEqual(
                lines[1].split(b" :", 1)[1].split(),
                sorted(c.nick for c in self.clients[: side + 1]),
            )
        self.assertEqual(self.server.channels[channel], set(peers))
        self.assertEqual(set(self.server.channels), {channel})
        self.assertEqual(await self.flush(0), [peers[1].prefix + b"JOIN :" + channel])

        for index in range(7):
            await self.clients[0].send(f"JOIN #extra{index}".encode())
            await self.flush(0)
        await self.clients[0].send(b"JOIN " + channel.upper())
        self.assertEqual(await self.flush(0), [peers[0].prefix + b"JOIN :" + channel])
        self.assertEqual(len(peers[0].channels), 8)
        self.assertEqual(await self.flush(1), [])

        for verb in (b"NOTICE", b"PRIVMSG"):
            await self.clients[0].send(verb + b" " + channel.swapcase() + b" :hello")
            self.assertEqual(await self.flush(0), [])
            self.assertEqual(
                await self.flush(1), [peers[0].prefix + verb + b" " + channel + b" :hello"]
            )
        await self.clients[0].send(b"PART " + channel.upper())
        self.assertEqual(await self.flush(0), [peers[0].prefix + b"PART " + channel])
        self.assertEqual(await self.flush(1), [peers[0].prefix + b"PART " + channel])
        self.assertNotIn(channel, peers[0].channels)
        self.assertEqual(self.server.channels[channel], {peers[1]})
        await peers[1].close()
        self.assertNotIn(channel, self.server.channels)


class EventChannelCaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = events.EventRoomPartTests.asyncSetUp
    asyncTearDown = events.EventRoomPartTests.asyncTearDown
    drain = events.EventRoomPartTests.drain
    send = events.EventRoomPartTests.send
    identity = events.EventRoomPartTests.identity
    enter_selection = events.EventRoomPartTests.enter_selection
    enter = events.EventRoomPartTests.enter
    flush = chats.ChatIntegrationTests.flush

    async def test_mixed_case_room_part_releases_selection_and_room_chat(self):
        await self.enter_selection(0, False)
        await self.enter(1, self.current.room)
        source = service_nick(self.current.raw_owner)
        channel = self.current.room_channel
        for side in range(2):
            await self.clients[side].send(b"JOIN " + channel)
            await self.flush(side)
            view = self.server.economy.account_view(self.keys[side])
            await self.send(
                side, self.current.room_id, packet(0x42, 1, body=f"@Chat=/NN={view.nn:016X}")
            )
        await self.flush(0)
        before = self.server.snapshot()
        coordinator = self.server.event_matches.coordinator
        self.assertIsNotNone(coordinator.session(self.peers[0]))
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))

        await self.clients[0].send(b"PART " + channel.lower())
        lines = await self.flush(0)
        self.assertIn(self.peers[0].prefix + b"PART " + channel, lines)
        self.assertFalse(any(b" 442 " in line for line in lines))
        self.assertIsNone(coordinator.session(self.peers[0]))
        self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
        self.assertNotIn(self.keys[0], self.server.events._present)
        self.assertNotIn(self.peers[0], self.current.room.members)
        self.assertNotIn(channel, self.peers[0].channels)
        self.assertEqual(self.server.channels[channel], {self.peers[1]})
        self.assertEqual(set(self.server.chat.groups[source]), {self.peers[1]})
        rosters = [
            decode(line.split(b" :", 1)[1]).sections["Chat"]
            for line in await self.flush(1)
            if b" :GTM0G420004" in line
        ]
        view = self.server.economy.account_view(self.keys[1])
        self.assertEqual([row["NN"] for row in rosters], [f"{view.nn:016X}"])
        self.assertEqual(self.server.snapshot(), before)
        self.assertFalse(self.peers[0].closed)


class OrdinaryChannelCaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = spectators.SpectatorIntegrationTests.asyncSetUp
    asyncTearDown = spectators.SpectatorIntegrationTests.asyncTearDown
    player = spectators.SpectatorIntegrationTests.player
    read_watch = spectators.SpectatorIntegrationTests.read_watch
    request_watch = spectators.SpectatorIntegrationTests.request_watch
    flush = chats.ChatIntegrationTests.flush

    async def test_first_lowercase_join_uses_advertised_table_for_observer(self):
        client = self.clients[1]
        peer = self.server.nicks[client.nick]
        channel = self.server.table_channel
        before = self.server.snapshot(), self.server.match.snapshot()
        reservations = dict(self.server.match_reservations)
        await client.send(b"JOIN " + channel.lower())
        lines = await self.flush(1)
        self.assertEqual(lines[0], peer.prefix + b"JOIN :" + channel)
        self.assertEqual(peer.channels, {channel})
        view = self.server.economy.account_view(client.nick.decode())
        await client.send(
            b"NOTICE " + self.target + b" :" + packet(1, body=native_identity(view, "Req"))
        )
        self.assertEqual(decode(await self.read_watch()).sections["PLAYACK"]["EN"], "1")
        await self.request_watch()
        self.assertEqual([decode(await self.read_watch()).subtype for _ in range(3)], [37, 38, 9])
        await client.send(
            b"NOTICE " + self.target + b" :" + packet(0x42, 1, body=f"@Chat=/NN={view.nn:016X}")
        )
        await self.flush(1)
        self.assertIn(peer, self.server.spectator_admissions)
        await client.send(b"PART " + channel.lower())
        self.assertEqual(await self.flush(1), [peer.prefix + b"PART " + channel])
        self.assertNotIn(peer, self.server.spectator_admissions)
        self.assertNotIn(channel, self.server.channels)
        self.assertNotIn(self.target, self.server.chat.groups)
        self.assertEqual((self.server.snapshot(), self.server.match.snapshot()), before)
        self.assertEqual(self.server.match_reservations, reservations)
        self.assertFalse(peer.closed)
        self.assertFalse(self.server.economy.is_reserved(client.nick.decode()))


if __name__ == "__main__":
    unittest.main()
