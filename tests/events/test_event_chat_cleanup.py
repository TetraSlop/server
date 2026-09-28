"""Event exits retire exact table chat memberships without an IRC PART."""

import unittest

from tests.events import test_event_spectators as events
from tmserver.protocol.identities import GAME_KEY, TABLE_RAW, service_nick
from tmserver.game.packets import decode, packet


class EventChatCleanupTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = events.EventSpectatorsIntegration.asyncSetUp
    asyncTearDown = events.EventSpectatorsIntegration.asyncTearDown
    start = events.EventSpectatorsIntegration.start
    drain = events.EventSpectatorsIntegration.drain
    send = events.EventSpectatorsIntegration.send
    identity = events.EventSpectatorsIntegration.identity
    enter_selection = events.EventSpectatorsIntegration.enter_selection
    admit = events.EventSpectatorsIntegration.admit
    engine = events.EventSpectatorsIntegration.engine
    play = events.EventSpectatorsIntegration.play
    observer_send = events.EventSpectatorsIntegration.observer_send
    read_watch = events.EventSpectatorsIntegration.read_watch
    admit_watch = events.EventSpectatorsIntegration.admit_watch
    watch = events.EventSpectatorsIntegration.watch

    async def barrier(self, client):
        await client.send(b"PING :event-chat")
        replies = []
        for _ in range(250):
            line = await client.read()
            self.assertTrue(line, "chat transition closed a healthy connection")
            if line == b"PONG event-chat":
                return replies
            if b" NOTICE " in line:
                replies.append((line.split(b"!", 1)[0][1:], line.split(b" :", 1)[1]))
        self.fail("event chat barrier missing")

    async def chat(self, client, target):
        view = self.server.economy.account_view(client.nick.decode())
        await client.send(
            b"NOTICE " + target + b" :" + packet(0x42, 1, body=f"@Chat=/NN={view.nn:016X}")
        )
        return await self.barrier(client)

    async def prepare(self):
        await self.start()
        await self.admit()
        await self.watch()
        self.peers = [self.server.nicks[c.nick] for c in self.clients]
        self.observer_peer = self.server.nicks[self.observer.nick]
        self.room_source = service_nick(self.cfg.room_id ^ GAME_KEY)
        for client in (*self.clients, self.observer):
            await self.chat(client, self.target)
            await self.chat(client, self.room_source)
        await self.drain()
        await self.barrier(self.observer)
        self.assertEqual(
            set(self.server.chat.groups[self.target]), set(self.peers + [self.observer_peer])
        )

    def assert_room_chat(self):
        self.assertEqual(
            set(self.server.chat.groups[self.room_source]),
            set(self.peers + [self.observer_peer]),
        )

    async def test_observer_exit_removes_table_chat_and_preserves_active_round(self):
        await self.prepare()
        before = self.engine.snapshot(), self.server.snapshot()
        coordinator = self.server.event_matches.coordinator
        tokens = {peer: coordinator._entries[peer].reservation for peer in self.peers}
        await self.observer_send(packet(32, body="@ExitCh="))
        await self.barrier(self.observer)
        await self.drain()
        self.assertNotIn(self.observer_peer, self.server.event_matches.spectators.admissions)
        self.assertEqual(set(self.server.chat.groups[self.target]), set(self.peers))
        for box in self.inbox:
            rosters = [
                decode(payload).sections["Chat"]
                for source, payload in box
                if source == self.target and payload.startswith(b"GTM0G420004")
            ]
            self.assertEqual(
                rosters[-1]["NN"],
                "|".join(f"{self.server.economy.account_view(k).nn:016X}" for k in self.keys),
            )
        self.assert_room_chat()
        self.assertEqual((self.engine.snapshot(), self.server.snapshot()), before)
        for peer, token in tokens.items():
            self.assertIs(coordinator._entries[peer].reservation, token)

        destination = service_nick(TABLE_RAW)
        await self.chat(self.observer, destination)
        counters = dict(self.server.chat.counters)
        await self.observer_send(packet(32, body="@ExitCh="))
        await self.barrier(self.observer)
        self.assertEqual(set(self.server.chat.groups[destination]), {self.observer_peer})
        self.assertEqual(self.server.chat.counters, counters)
        self.assert_room_chat()

    async def test_completed_participants_leave_chat_before_table_reuse(self):
        await self.prepare()
        coordinator = self.server.event_matches.coordinator
        old_epoch = coordinator.snapshot("festival")["epoch"]
        await self.play()
        for side in range(2):
            await self.send(side, self.table.table_id, packet(0x43, 14, 10, f"@Ready=/Ans={side}"))
        self.assertIsNone(coordinator.snapshot("festival"))
        self.assertFalse(any(self.server.economy.is_busy(key) for key in self.keys))
        self.assertEqual(set(self.server.chat.groups[self.target]), {self.observer_peer})
        self.assert_room_chat()
        # The observer keeps its completed preview until its own explicit exit.
        self.assertIn(self.observer_peer, self.server.event_matches.spectators.admissions)
        await self.observer_send(packet(32, body="@ExitCh="))
        await self.barrier(self.observer)
        self.assertNotIn(self.target, self.server.chat.groups)
        self.assertNotIn(self.observer_peer, self.server.event_matches.spectators.admissions)
        self.assert_room_chat()

        self.inbox = [[], []]
        for side in range(2):
            await self.enter_selection(side)
        for _ in range(20):
            if coordinator.snapshot("festival") is not None:
                break
            await self.drain()
        self.assertNotEqual(coordinator.snapshot("festival")["epoch"], old_epoch)
        replies = await self.chat(self.clients[0], self.target)
        self.assertEqual(set(self.server.chat.groups[self.target]), {self.peers[0]})
        rosters = [
            decode(p).sections["Chat"]
            for source, p in replies
            if source == self.target and p.startswith(b"GTM0G420004")
        ]
        self.assertEqual(rosters[-1]["Num"], "0|1")
        self.assert_room_chat()
        for client, peer in zip((*self.clients, self.observer), (*self.peers, self.observer_peer)):
            self.assertIs(self.server.nicks[client.nick], peer)
            await self.barrier(client)

    async def test_active_quit_clears_table_chat_on_the_surviving_connection(self):
        await self.prepare()
        profiles = {key: self.server.economy.profile_bytes(key) for key in self.keys}
        request = packet(0x43, 5, 3, "@Quit=/No=0")
        await self.clients[0].send(b"NOTICE " + self.target + b" :" + request)
        replies = await self.barrier(self.clients[0])
        self.assertIn((self.target, packet(0x43, 5, 3, "@Quit=/No=0/B=0")), replies)
        for client in (self.clients[1], self.observer):
            for _ in range(100):
                if not await client.read():
                    break
            else:
                self.fail("aborted counterpart stayed connected")
        self.assertIsNone(self.server.event_matches.coordinator.snapshot("festival"))
        self.assertNotIn(self.target, self.server.chat.groups)
        self.assertFalse(self.server.event_matches.spectators.admissions)
        self.assertEqual(set(self.server.chat.groups[self.room_source]), {self.peers[0]})
        self.assertFalse(any(self.server.economy.is_busy(key) for key in self.keys))
        self.assertEqual(
            {key: self.server.economy.profile_bytes(key) for key in self.keys}, profiles
        )
        counters = dict(self.server.chat.counters)
        await self.clients[0].send(b"NOTICE " + self.target + b" :" + request)
        await self.barrier(self.clients[0])
        self.assertEqual(self.server.chat.counters, counters)
        self.assertIs(self.server.nicks[self.clients[0].nick], self.peers[0])


if __name__ == "__main__":
    unittest.main()
