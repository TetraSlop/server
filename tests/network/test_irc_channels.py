"""Channel rosters use real session framing without launching the native client."""

import unittest

from tests.support.irc import Client
from tests.network.test_session_recovery import Writer
from tmserver.protocol import wire
from tmserver.network.session import MAX_LINE, Session
from tmserver.network.server import Server


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server(account_count=128)

    async def asyncTearDown(self):
        await self.server.stop()

    def peer(self, index):
        peer = Session(self.server, None, Writer(), index + 1)
        peer.nick = list(self.server.accounts)[index]
        self.server.nicks[peer.nick] = peer
        self.server.sessions.add(peer)
        return peer

    def messages(self, peer):
        messages = []
        for closed, frame in peer.writer.writes:
            self.assertFalse(closed)
            self.assertLessEqual(len(frame), MAX_LINE)
            self.assertEqual(frame[-2:], b"\r\n")
            self.assertEqual(frame[-6:-2], wire.checksum_chars(frame[:-6]))
            messages.append(frame[:-6])
        return messages

    async def test_join_full_account_pool_delivers_complete_bounded_roster(self):
        channel = b"#" + b"x" * 63  # Largest supported channel name.
        members = {self.peer(index) for index in range(127)}
        self.server.channels[channel] = members
        for member in members:
            member.channels.add(channel)
        newcomer = self.peer(127)
        await newcomer.command(b"JOIN", [channel])

        replies = self.messages(newcomer)
        self.assertEqual(replies[0], newcomer.prefix + b"JOIN :" + channel)
        self.assertIn(b" 366 ", replies[-1])
        pages = replies[1:-1]
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(b" 353 " in page for page in pages))
        names = [nick for page in pages for nick in page.split(b" :", 1)[1].split()]
        self.assertEqual(names, sorted(self.server.accounts))
        self.assertEqual(len(self.server.channels[channel]), 128)
        for member in members - {newcomer}:
            self.assertEqual(self.messages(member), [newcomer.prefix + b"JOIN :" + channel])

    async def test_repeated_join_is_allowed_at_channel_limit(self):
        peer = self.peer(0)
        for index in range(8):
            channel = f"#room{index}".encode()
            peer.channels.add(channel)
            self.server.channels[channel] = {peer}
        await peer.command(b"JOIN", [b"#room0"])
        self.assertEqual(self.messages(peer), [peer.prefix + b"JOIN :#room0"])
        self.assertEqual(len(peer.channels), 8)
        with self.assertRaisesRegex(ValueError, "channel joins"):
            await peer.command(b"JOIN", [b"#ninth"])
        self.assertNotIn(b"#ninth", self.server.channels)

    async def test_small_roster_preserves_original_three_reply_sequence(self):
        peer = self.peer(0)
        await peer.command(b"JOIN", [b"#room"])
        self.assertEqual(
            self.messages(peer),
            [
                peer.prefix + b"JOIN :#room",
                b":srv 353 " + peer.nick + b" = #room :" + peer.nick,
                b":srv 366 " + peer.nick + b" #room :End of NAMES list",
            ],
        )


class ChannelNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_roster_crosses_encrypted_connection_without_disconnect(self):
        server = Server(account_count=128)
        port = await server.start(0)
        client = Client()
        try:
            await client.open(port, account=list(server.accounts)[-1])
            self.assertIn(b" 422 ", await client.read())
            channel = b"#" + b"x" * 63
            # The joining peer uses real TCP/authentication/encryption. Existing
            # roster members need only transport sinks for the JOIN broadcast.
            members = set()
            for index, nick in enumerate(list(server.accounts)[:-1]):
                peer = Session(server, None, Writer(), index + 1000)
                peer.nick = nick
                peer.channels.add(channel)
                server.sessions.add(peer)
                server.nicks[nick] = peer
                members.add(peer)
            server.channels[channel] = members
            await client.send(b"JOIN " + channel)
            self.assertIn(b"JOIN :" + channel, await client.read())
            names = []
            for _ in range(10):
                reply = await client.read()
                self.assertTrue(reply)
                self.assertLessEqual(len(reply) + 6, MAX_LINE)
                if b" 366 " in reply:
                    break
                self.assertIn(b" 353 ", reply)
                names.extend(reply.split(b" :", 1)[1].split())
            else:
                self.fail("Channel roster never completed")
            self.assertEqual(names, sorted(server.accounts))
            await client.send(b"PING :roster-complete")
            self.assertEqual(await client.read(), b"PONG roster-complete")
        finally:
            if hasattr(client, "writer"):
                await client.close()
            await server.stop()
