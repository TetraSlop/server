"""Protocol tests use real TCP and the legacy key exchange, not mocked sockets."""

import asyncio
import hashlib
import json
import math
import unittest
from pathlib import Path

from tmserver import pol_wire as wire
from tmserver.server import ACCOUNTS, Server
from tmserver.tm_protocol import COMMANDS, Command, encode, inspect_service, parse


def prime_below(value):
    # Deterministic fixture search. Strong probable primes sufficient for a
    # throwaway 256-bit legacy RSA test key; never production key generation.
    while True:
        value -= 2
        if math.gcd(65535, value - 1) != 1:
            continue
        d, s = value - 1, 0
        while d % 2 == 0:
            d //= 2
            s += 1
        good = True
        for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
            x = pow(a, d, value)
            if x in (1, value - 1):
                continue
            for _ in range(s - 1):
                x = x * x % value
                if x == value - 1:
                    break
            else:
                good = False
                break
        if good:
            return value


P = prime_below((1 << 128) - 1)
Q = prime_below(P - 2)
N, D = P * Q, pow(65535, -1, (P - 1) * (Q - 1))
MODULUS = N.to_bytes(32, "little")


class Client:
    async def open(self, port, account=b"UGITW5T4U", password=b"tm-local-test", fragmented=False):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", port)
        self.cipher, self.iv, self.nick = None, 0, account
        challenge = (await self.read()).split()[-1]
        packet = wire.frame_line(b"USER x 8 * :" + wire.b64enc(MODULUS, pad=False))
        if fragmented:
            for offset in range(0, len(packet), 3):
                self.writer.write(packet[offset : offset + 3])
                await self.writer.drain()
                await asyncio.sleep(0)
        else:
            self.writer.write(packet)
        reply = await self.read()
        encrypted = wire.b64dec(reply.split()[-1])[:32]
        block = pow(int.from_bytes(encrypted, "big"), D, N).to_bytes(32, "big")
        assert block[:2] == b"\0\2"
        key = block[block.index(0, 2) + 1 :][::-1]
        assert len(key) == 8
        self.cipher = await asyncio.to_thread(wire.SqBlowfish, key)
        self.iv = int.from_bytes(MODULUS[:8], "little")
        digest = hashlib.md5(challenge + password).hexdigest().encode()
        await self.send(b"NICK " + account + b":" + digest + b":test")

    async def read(self):
        raw = await asyncio.wait_for(self.reader.readline(), 3)
        if not raw:
            return b""
        plain = wire.crypt64(self.cipher, self.iv, raw) if self.cipher else raw
        assert plain.endswith(b"\r\n")
        assert wire.checksum_chars(plain[:-6]) == plain[-6:-2]
        return plain[:-6]

    async def send(self, message):
        self.writer.write(wire.frame_line(message, self.cipher, self.iv))
        await self.writer.drain()

    async def join(self, channel=b"#TM0-test"):
        await self.send(b"JOIN " + channel)
        replies = [await self.read() for _ in range(3)]
        assert b"JOIN :" + channel in replies[0]
        assert b" 353 " in replies[1] and b" 366 " in replies[2]

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


class CodecTests(unittest.TestCase):
    def test_captured_first_game_request(self):
        self.assertEqual(
            inspect_service(b"GTM0GE1000000@TeachDV="),
            {"opcode": 225, "header_tail_hex": "303030303030", "body": "@TeachDV="},
        )
        self.assertIsNone(inspect_service(b"chat"))

    def test_original_tm_constructor_and_parser_vectors(self):
        fixture = Path(__file__).with_name("fixtures") / "native_codec_vectors.jsonl"
        rows = [json.loads(line) for line in fixture.read_text().splitlines()]
        self.assertEqual(len(rows), 206)
        for row in rows:
            token = COMMANDS[row["id"]][1]
            command = Command(token, b"probe" if row["text"] else b"17")
            native_bytes = bytes.fromhex(row["hex"])
            self.assertEqual(encode([command]), native_bytes)
            self.assertEqual(parse(native_bytes), [command])
            self.assertEqual(row["valid"], 0)
            self.assertEqual(command.ids[0], row["resolved"])

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

    def test_dictionary_duplicate(self):
        self.assertEqual(len(COMMANDS), 103)
        self.assertEqual(Command(b"PO", b"").ids, (0x60, 0x65))

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
        session = self.server.nicks[client.nick]
        self.assertEqual(session.tm_player["name"], b"LocalPlayer")
        await client.send(b"QUIT :done")
        self.assertEqual(await client.read(), b"")
        self.assertIsNone(session.tm_player)

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
