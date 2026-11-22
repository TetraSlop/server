"""Legacy IRC test client and the local accounts it logs in as."""

import asyncio
import hashlib

from tmserver.network.local_accounts import account_specs
from tmserver.protocol import wire

ACCOUNTS = {key.encode("ascii"): value[1] for key, value in account_specs().items()}

# Throwaway 256-bit legacy RSA test key: two 128-bit primes with gcd(p - 1, 65535) == 1.
P = (1 << 128) - 173
Q = (1 << 128) - 233
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

    async def read(self, *, timeout=10):
        # A hang guard, not a protocol deadline: legacy key setup can exceed
        # three seconds under load. Timing regressions use explicit deadlines.
        raw = await asyncio.wait_for(self.reader.readline(), timeout)
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
