"""Protocol tests use real TCP and the legacy key exchange, not mocked sockets."""

import asyncio
import json
import unittest

from tests import FIXTURES
from tmserver.protocol import wire
from tests.support.irc import ACCOUNTS, Client
from tmserver.network.server import Server
from tmserver.protocol.commands import Command, encode, parse


class CodecTests(unittest.TestCase):
    def test_original_tm_constructor_and_parser_vectors(self):
        fixture = FIXTURES / "native_codec_vectors.jsonl"
        rows = [json.loads(line) for line in fixture.read_text().splitlines()]
        self.assertEqual(len(rows), 206)
        for row in rows:
            native_bytes = bytes.fromhex(row["hex"])
            command = Command(native_bytes[1:3], b"probe" if row["text"] else b"17")
            self.assertEqual(encode([command]), native_bytes)
            self.assertEqual(parse(native_bytes), [command])
            self.assertEqual(row["valid"], 0)

    def test_recovered_vectors(self):
        self.assertEqual(wire.checksum_chars(b"PING a"), b"iExH")
        self.assertEqual(wire.checksum_chars(b"NOTICE U:x"), b"DiyS")
        self.assertEqual(wire.b64enc(bytes(range(6))), b"TTIGTt7n")
        self.assertEqual(wire.polid_scramble(0x123456789), b"UGHC46QC8")
        self.assertEqual(wire.b32enc(bytes(range(25))), b"NNNEVNZVNFONS3NY41HEZO1S4A143VEQ3E2D0HZZ")
        bf = wire.SqBlowfish(bytes(range(1, 9)))
        self.assertEqual(bf.encrypt_u64(0), 0xC6E03B0EB7E710D8)
        self.assertEqual(
            wire.frame_line(b":s 422 U :x", bf, 0x0807060504030201).hex(),
            "7da78ca82024678f0662404389e4660d0a",
        )

    def test_crypto_preserves_line_framing(self):
        bf = wire.SqBlowfish(bytes(range(8)))
        original = bytes(range(256))
        encrypted = wire.crypt64(bf, 12345, original)
        self.assertEqual(wire.crypt64(bf, 12345, encrypted), original)
        self.assertEqual([i for i, c in enumerate(encrypted) if c in (10, 13)], [10, 13])

    def test_command_payload_is_opaque(self):
        commands = [Command(b"RI", b"a b,17;42"), Command(b"NN", b"")]
        self.assertEqual(parse(encode(commands)), commands)
        self.assertEqual(encode(commands), b"<RI>\x07a b,17;42\x07<NN>\x07\x07")

    def test_reject_malformed_envelopes(self):
        for value in (
            b"<RI>x",
            b"<RI>\x07x",
            b"<RI>\x07x\x07junk",
            b"<R>\x07\x07",
            b"<RI>\x07\0\x07",
            b"<RI>\x07\n\x07",
            b"<RI>\x07" + b"a" * 464 + b"\x07",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse(value)


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # The intentionally old pure-Python cipher schedule takes ~150 ms.
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.server = Server()
        self.port = await self.server.start(0)
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()

    async def client(self, index=0, **kwargs):
        client = Client()
        self.clients.append(client)
        await client.open(self.port, account=list(ACCOUNTS)[index], **kwargs)
        return client

    async def test_encrypted_login_fragmentation_and_coalesced_lines(self):
        client = await self.client(fragmented=True)
        self.assertIn(b" 422 ", await client.read())
        client.writer.write(
            b"".join(wire.frame_line(x, client.cipher, client.iv) for x in (b"PING a", b"PING b"))
        )
        self.assertEqual(await client.read(), b"PONG a")
        self.assertEqual(await client.read(), b"PONG b")

    async def test_two_clients_relay_and_part(self):
        one, two = await self.client(0), await self.client(1)
        await one.read()
        await two.read()
        await one.join()
        await two.join()
        self.assertIn(b"JOIN", await one.read())
        payload = b"<RI>\x07opaque payload\x07<NN>\x0717\x07"
        await one.send(b"NOTICE #TM0-test :" + payload)
        self.assertEqual(
            await two.read(), b":" + one.nick + b"!p@local NOTICE #TM0-test :" + payload
        )
        await two.send(b"PART #TM0-test")
        self.assertIn(b"PART", await one.read())
        self.assertIn(b"PART", await two.read())
        await one.send(b"PART #TM0-test")
        await one.read()
        self.assertEqual(self.server.channels, {})

    async def test_native_player_registration(self):
        client = await self.client()
        await client.read()
        # Byte-for-byte request captured from original TM 1.6.11.0 startup.
        request = (
            b"GTM0P<CR>\x070x0000000000000001\x061\x07"
            b"<AN>\x07LocalPlayer\x07<CI>\x071\x06Card Level 0\x07"
        )
        await client.send(b"NOTICE UUIRT71SR :" + request)
        self.assertEqual(
            await client.read(), b":UUIRT71SR!p@local NOTICE UGITW5T4U :GTM0P<CS>\x07\x07"
        )
        record = self.server.character_profiles.records[client.nick.decode("ascii")]
        self.assertEqual(record[2], "LocalPlayer")
        await client.send(b"QUIT :done")
        self.assertEqual(await client.read(), b"")

    async def test_bad_checksum_dropped(self):
        client = await self.client()
        await client.read()
        client.writer.write(wire.crypt64(client.cipher, client.iv, b"PING bad????\r\n"))
        await client.send(b"PING good")
        self.assertEqual(await client.read(), b"PONG good")

    async def test_bad_password_rejected(self):
        client = await self.client(password=b"wrong")
        self.assertEqual(await client.read(), b"")
        self.assertEqual(self.server.nicks, {})

    async def test_unjoined_sender_rejected(self):
        client = await self.client()
        await client.read()
        await client.send(b"NOTICE #TM0-test :x")
        self.assertIn(b" 442 ", await client.read())

    async def test_prefix_spoof_rejected(self):
        client = await self.client()
        await client.read()
        await client.send(b":someone PING a")
        self.assertEqual(await client.read(), b"")

    async def test_oversized_frame_disconnects(self):
        client = await self.client()
        await client.read()
        client.writer.write(b"x" * 600 + b"\r\n")
        self.assertEqual(await client.read(), b"")

    async def test_disconnect_cleans_membership(self):
        client = await self.client()
        await client.read()
        await client.join()
        await client.send(b"QUIT :test done")
        self.assertEqual(await client.read(), b"")
        self.assertEqual(self.server.nicks, {})
        self.assertEqual(self.server.channels, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
