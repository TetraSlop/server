"""Chat contracts exercised against original consumers and encrypted sessions."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests.network.test_server import Client
from tmserver.network.chat import ChatService, Member, relay_text, roster_pages
from tmserver.protocol.identities import ROOM_RAW, player_id, service_nick
from tmserver.network.local_accounts import MAX_ACCOUNTS
from tmserver.game.packets import decode, packet
from tmserver.network.server import ACCOUNTS, Server
from tmserver.storage.json import JsonStateStore


class ChatTests(unittest.TestCase):
    def test_entry_roster_replay_leave_and_identity(self):
        service = ChatService()

        def enter(peer, identity):
            return service.enter(
                peer,
                b"group",
                packet(0x42, 1, body=f"@Chat=/NN={identity:016X}"),
                identity=identity,
                name=peer.encode(),
            )

        first = enter("Alice", 1)
        self.assertEqual(decode(first[0].payload).sections, {"Chat": {"EN": "1"}})
        self.assertEqual(decode(first[1].payload).sections["Chat"]["Num"], "0|1")
        second = enter("Bob", 2)
        self.assertEqual(len(second), 3)
        self.assertEqual(decode(second[1].payload).sections["Chat"]["C"], "2")
        duplicate = enter("Bob", 2)
        self.assertEqual(decode(duplicate[1].payload).sections["Chat"]["C"], "2")
        left = service.leave("Alice")
        self.assertEqual(len(left), 1)
        self.assertEqual(decode(left[0].payload).sections["Chat"]["NN"], "0000000000000002")
        self.assertEqual(decode(left[0].payload).sections["Chat"]["C"], "3")
        with self.assertRaises(ValueError):
            service.enter(
                "Eve",
                b"group",
                packet(0x42, 1, body="@Chat=/NN=0000000000000002"),
                identity=3,
                name=b"Eve",
            )
        self.assertEqual(list(service.groups[b"group"]), ["Bob"])

    def test_raw_text_and_authenticated_name(self):
        body = b"GTM0G42000801@Chat=/La=1/Dt=#CHAT#Imposter\thello/a=b@Thing=\t\x82\xa0"
        outgoing = relay_text(body, b"LocalPlayer", private=True)
        self.assertEqual(
            outgoing, b"GTM0G42000801@Chat=/La=1/Dt=#CHAT#LocalPlayer\thello/a=b@Thing=\t\x82\xa0"
        )
        for bad in (
            body.replace(b"01@Chat", b"00@Chat"),
            body.replace(b"#CHAT#", b""),
            body + b"\n",
            body.replace(b"/La=1/", b"/La=2147483648/"),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                relay_text(bad, b"LocalPlayer", private=True)

    def test_full_account_name_has_bounded_authenticated_presentation(self):
        name = b"ABCDEFGHIJKLMNOP"
        service = ChatService()
        entry = packet(0x42, 1, body="@Chat=/NN=0000000000000001")
        first = service.enter("peer", b"group", entry, identity=1, name=name)
        roster = decode(first[1].payload).sections["Chat"]
        self.assertEqual(bytes.fromhex(roster["CN"]), name[:15])
        self.assertEqual(roster["NN"], "0000000000000001")
        replay = service.enter("peer", b"group", entry, identity=1, name=name)
        self.assertEqual(replay[1].payload, first[1].payload)
        for private in (False, True):
            incoming = packet(
                0x42, 8, int(private), "@Chat=/La=1/Dt=#CHAT#SpoofedName1234X\thello/a=@b"
            )
            self.assertEqual(
                relay_text(incoming, name, private=private),
                packet(0x42, 8, int(private), "@Chat=/La=1/Dt=#CHAT#ABCDEFGHIJKLMNO\thello/a=@b"),
            )
        # Presentation serializers still reject an unbounded native name.
        with self.assertRaises(ValueError):
            roster_pages([Member("peer", 1, name)], 1)

    def test_invalid_names_are_rejected_before_truncation(self):
        service = ChatService()
        entry = packet(0x42, 1, body="@Chat=/NN=0000000000000001")
        prefix = b"GTM0G42000800@Chat=/La=1/Dt=#CHAT#"
        valid_message = prefix + b"Alice\thello"
        for name in (b"", b"A" * 17, b"A" * 15 + b"\x01", b"A" * 15 + b"\x7f", b"A" * 15 + b"\x82"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    service.enter("peer", b"group", entry, identity=1, name=name)
                self.assertEqual(service.groups, {})
                with self.assertRaises(ValueError):
                    relay_text(valid_message, name, private=False)
                with self.assertRaises(ValueError):
                    relay_text(prefix + name + b"\thello", b"Alice", private=False)

    def test_page_size_index_counter_and_empty_list(self):
        members = [Member(i, i + 1, ("Name" + str(i).zfill(11)).encode()) for i in range(80)]
        pages = roster_pages(members, 42)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(p) - 13 <= 447 for p in pages))
        parsed = [decode(p) for p in pages]
        self.assertTrue(all(p.argument == len(pages) - 1 for p in parsed))
        self.assertEqual(
            [p.sections["Chat"]["Num"].split("|")[0] for p in parsed],
            [str(i) for i in range(len(pages))],
        )
        empty = decode(roster_pages([], 7)[0])
        self.assertEqual(empty.sections["Chat"]["Num"], "0|0")
        with self.assertRaises(ValueError):
            roster_pages(members * 4, 42)

    def test_full_account_pool_preserves_identities_with_maximum_names_and_counter(self):
        service = ChatService()
        expected = []
        # Different canonical names can have the same display prefix; the
        # full authenticated NN, never the presentation name, identifies them.
        for index in range(MAX_ACCOUNTS):
            identity, name = index + 1, f"{index:016d}".encode()
            expected.append((f"{identity:016X}", name[:15].hex().upper()))
            service.enter(
                index,
                b"group",
                packet(0x42, 1, body=f"@Chat=/NN={identity:016X}"),
                identity=identity,
                name=name,
            )
        pages = roster_pages(service.groups[b"group"].values(), 0x7FFFFFFF)
        self.assertEqual(len(pages), 16)
        actual = []
        for index, payload in enumerate(pages):
            self.assertLessEqual(len(payload) - 13, 447)
            message = decode(payload)
            self.assertEqual(message.argument, len(pages) - 1)
            fields = message.sections["Chat"]
            self.assertEqual(fields["Num"], f"{index}|8")
            self.assertEqual(fields["C"], "2147483647")
            actual.extend(zip(fields["NN"].split("|"), fields["CN"].split("|"), strict=True))
        self.assertEqual(actual, expected)
        members = [Member(index, index + 1, b"A" * 15) for index in range(137)]
        self.assertEqual(len(roster_pages(members[:136], 0x7FFFFFFF)), 17)
        with self.assertRaisesRegex(ValueError, "page array"):
            roster_pages(members, 0x7FFFFFFF)


class ChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server()
        port = await self.server.start(0)
        self.clients = [Client(), Client()]
        for client, nick in zip(self.clients, ACCOUNTS):
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()

    async def flush(self, side):
        client = self.clients[side]
        await client.send(b"PING :chat-barrier")
        replies = []
        for _ in range(40):
            line = await client.read()
            self.assertTrue(line)
            if line == b"PONG chat-barrier":
                return replies
            replies.append(line)
        self.fail("chat response limit")

    async def test_entry_public_private_and_disconnect_roster(self):
        target = service_nick(ROOM_RAW)
        for side, client in enumerate(self.clients):
            entry = packet(0x42, 1, body=f"@Chat=/NN={player_id(client.nick):016X}")
            await client.send(b"NOTICE " + target + b" :" + entry)
            lines = await self.flush(side)
            self.assertEqual(decode(lines[0].split(b" :", 1)[1]).sections["Chat"]["EN"], "1")
        observer = await self.flush(0)
        self.assertEqual(decode(observer[0].split(b" :", 1)[1]).sections["Chat"]["Num"], "0|2")
        for side, client in enumerate(self.clients):
            await client.send(b"JOIN #chat-test")
            await self.flush(side)
        await self.flush(0)
        public = packet(0x42, 8, 0, "@Chat=/La=1/Dt=#CHAT#WrongName\thello /x=@y")
        await self.clients[0].send(b"NOTICE #chat-test :" + public)
        self.assertEqual(await self.flush(0), [])
        lines = await self.flush(1)
        self.assertIn(b"#CHAT#LocalPlayer\thello /x=@y", lines[0])
        private = packet(0x42, 8, 1, "@Chat=/La=2/Dt=#CHAT#WrongName\tprivate")
        await self.clients[1].send(b"NOTICE " + self.clients[0].nick + b" :" + private)
        self.assertEqual(await self.flush(1), [])
        lines = await self.flush(0)
        self.assertIn(b"GTM0G42000801@Chat=/La=2/Dt=#CHAT#LocalPlayer2\tprivate", lines[0])
        await self.server.nicks[self.clients[1].nick].close()
        lines = await self.flush(0)
        self.assertEqual(decode(lines[0].split(b" :", 1)[1]).sections["Chat"]["Num"], "0|1")


class ChatAccountNameIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def flush(self, client):
        await client.send(b"PING :name-barrier")
        replies = []
        for _ in range(40):
            line = await client.read()
            self.assertTrue(line)
            if line == b"PONG name-barrier":
                return replies
            replies.append(line)
        self.fail("chat response limit")

    async def exercise_chat(self, server, port, nicks, names):
        clients = [Client(), Client()]
        try:
            for client, nick in zip(clients, nicks, strict=True):
                await client.open(port, account=nick)
                self.assertIn(b" 422 ", await client.read())
                entry = packet(0x42, 1, body=f"@Chat=/NN={player_id(nick):016X}")
                await client.send(b"NOTICE " + service_nick(ROOM_RAW) + b" :" + entry)
                replies = await self.flush(client)
                ack = decode(replies[0].split(b" :", 1)[1])
                self.assertEqual(ack.sections, {"Chat": {"EN": "1"}})
            roster = decode((await self.flush(clients[0]))[0].split(b" :", 1)[1])
            fields = roster.sections["Chat"]
            self.assertEqual(fields["Num"], "0|2")
            self.assertEqual(fields["NN"], "|".join(f"{player_id(n):016X}" for n in nicks))
            self.assertEqual(fields["CN"], "|".join(n[:15].hex().upper() for n in names))
            for client in clients:
                await client.send(b"JOIN #long-names")
                await self.flush(client)
            await self.flush(clients[0])
            public = b"GTM0G42000800@Chat=/La=1/Dt=#CHAT#" + names[0] + b"\thello/a=@b"
            await clients[0].send(b"NOTICE #long-names :" + public)
            self.assertEqual(await self.flush(clients[0]), [])
            replies = await self.flush(clients[1])
            self.assertEqual(
                replies,
                [
                    b":"
                    + nicks[0]
                    + b"!p@local NOTICE #long-names :"
                    + public.replace(names[0], names[0][:15])
                ],
            )
            private = b"GTM0G42000801@Chat=/La=2/Dt=#CHAT#SpoofedName1234X\tprivate"
            await clients[1].send(b"NOTICE " + nicks[0] + b" :" + private)
            self.assertEqual(await self.flush(clients[1]), [])
            replies = await self.flush(clients[0])
            self.assertEqual(
                replies,
                [
                    b":"
                    + nicks[1]
                    + b"!p@local NOTICE "
                    + nicks[0]
                    + b" :"
                    + private.replace(b"SpoofedName1234X", names[1][:15])
                ],
            )
        finally:
            for client in clients:
                if hasattr(client, "writer"):
                    peer = server.nicks.get(client.nick)
                    if peer is not None:
                        await peer.close()
                    await client.close()
        self.assertEqual(server.chat.groups, {})

    async def test_full_names_enter_and_relay_before_after_reconnect_and_restore(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        nicks = list(ACCOUNTS)
        names = (b"ABCDEFGHIJKLMNOP", b"QRSTUVWXYZABCDEF")
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            for restored in (False, True):
                server = Server(state_path=state)
                try:
                    if not restored:
                        for index, (nick, name) in enumerate(zip(nicks, names, strict=True)):
                            server.economy.provision(
                                nick.decode(), nn=player_id(nick), player_id=index + 1, name=name
                            )
                            server.account_keys.add(nick.decode())
                        server.persist()
                    before = server.snapshot()
                    saved = state.read_bytes()
                    native = {key: server.economy.profile_bytes(key) for key in server.account_keys}
                    port = await server.start(0)
                    for reconnected in (False, True):
                        with self.subTest(restored=restored, reconnected=reconnected):
                            await self.exercise_chat(server, port, nicks, names)
                            self.assertEqual(server.snapshot(), before)
                            self.assertEqual(state.read_bytes(), saved)
                            for nick, name in zip(nicks, names, strict=True):
                                key = nick.decode()
                                self.assertEqual(server.economy.account_view(key).name, name)
                                self.assertEqual(server.economy.profile_bytes(key), native[key])
                    self.assertEqual(JsonStateStore(state).load()["accounts"], before["accounts"])
                finally:
                    await server.stop()


if __name__ == "__main__":
    unittest.main()
