"""Oversized presence replies must never enter membership or replay history."""

import asyncio
import unittest

from tests.network.test_server import Client
from tests.rooms.test_room_service import presence
from tmserver.network.server import ACCOUNTS, Server
from tmserver.protocol.commands import Command, encode, parse
from tmserver.rooms.service import RoomService


def padded_presence(payload, sequence, reply_length):
    command = parse(payload[5:])[0]
    fields = command.payload.split(b"\x06")
    size = len(RoomService.wrap_delta(sequence, Command(b"PD", command.payload)))
    fields[1] = b"0" * (reply_length - size) + fields[1]
    return b"GTM0L" + encode([Command(command.token, b"\x06".join(fields))])


class RoomReplyBudgetTests(unittest.TestCase):
    def test_enter_and_update_preflight_before_membership_or_history(self):
        for token in (b"DE", b"PD"):
            room = RoomService()
            room.handle("other", presence(identity=456))
            if token == b"PD":
                room.handle("one", presence())
            before = room.snapshot(), dict(room.members), dict(room.history)
            for length in (470, 480):
                request = padded_presence(presence(token), room.sequence + 1, length)
                with self.subTest(token=token, length=length), self.assertRaises(ValueError):
                    room.handle("one", request)
                self.assertEqual((room.snapshot(), room.members, room.history), before)
            request = padded_presence(presence(token), room.sequence + 1, 469)
            deliveries = room.handle("one", request)
            self.assertEqual(len(deliveries[-1][1]), 469)
            replay = room.handle("other", b"GTM0L" + encode([Command(b"DR", b"100")]))
            self.assertEqual(replay[-1][1], deliveries[-1][1])

    def test_direct_delta_and_npc_rejection_preserve_state(self):
        room = RoomService()
        room.handle("one", presence())
        padded = parse(padded_presence(presence(identity=789), room.sequence + 1, 470)[5:])[0]
        before = room.snapshot(), dict(room.history), dict(room.npc_tables)
        for apply in (lambda: room.delta(Command(b"PD", padded.payload)), lambda: room.set_npc(padded.payload)):
            with self.assertRaises(ValueError):
                apply()
            self.assertEqual((room.snapshot(), room.history, room.npc_tables), before)


class RoomReplyBudgetIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_encrypted_replay_survives_rejected_enter_and_update(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        for token in (b"DE", b"PD"):
            server = Server()
            clients = [Client(), Client()]
            try:
                port = await server.start(0)
                for client, nick in zip(clients, ACCOUNTS):
                    await client.open(port, account=nick)
                    await client.read()
                table = server.ordinary_tables[0]
                room = table.room
                target = next(nick for nick, route in server.ordinary_room_routes.items() if route is table)

                def authenticated_presence(client, token=b"DE"):
                    account = server.economy.account_view(client.nick.decode())
                    fields = parse(presence(token, account.nn)[5:])[0].payload.split(b"\x06")
                    fields[1], fields[4] = str(account.player_id).encode(), account.name
                    fields[9] = f"0x{room.room_id:016X}".encode()
                    return b"GTM0L" + encode([Command(token, b"\x06".join(fields))])

                async def exchange(client, payload):
                    await client.send(b"NOTICE " + target + b" :" + payload)
                    await client.send(b"PING :budget")
                    replies = []
                    while (line := await client.read()) != b"PONG budget":
                        self.assertTrue(line)
                        replies.append(line)
                    return replies

                other, sender = clients
                await exchange(other, authenticated_presence(other))
                if token == b"PD":
                    await exchange(sender, authenticated_presence(sender))
                    await exchange(other, b"GTM0L" + encode([Command(b"DR", str(room.sequence).encode())]))
                baseline = room.sequence
                bad = padded_presence(authenticated_presence(sender, token), baseline + 1, 470)
                await sender.send(b"NOTICE " + target + b" :" + bad)
                self.assertEqual(await sender.read(), b"")
                # Disconnect may add a valid PC for the existing sender; its
                # rejected PD must never become an unframeable replay entry.
                replies = await exchange(other, b"GTM0L" + encode([Command(b"DR", b"100")]))
                self.assertTrue(replies)
                for line in replies:
                    self.assertLessEqual(len(line) + 6, 512)
                    self.assertNotIn(bad[5:], line)
                self.assertEqual(room.sequence, baseline + int(token == b"PD"))
            finally:
                for client in clients:
                    if hasattr(client, "writer"):
                        await client.close()
                await server.stop()
