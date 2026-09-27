"""PC profile framing, authenticated TCP, and service bootstrap regression tests."""

import asyncio
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tmserver import pol_wire as wire
from tmserver.identities import SHOP_RAW, game_id
from tmserver.profile_service import ProfileServer, answer, checked, checksum
from tmserver.services import teach_dv_answer


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.cipher = await asyncio.to_thread(wire.SqBlowfish, b"TMtest01")
        self.iv = 0x123456789ABCDEF0
        nick = wire.polid_scramble(wire.polid_from_string("EEEEEEEF"))
        game = SimpleNamespace(nicks={nick: SimpleNamespace(cipher=self.cipher, iv=self.iv)})
        self.server = ProfileServer(Path(self.temp.name) / "traffic.jsonl", game)
        self.port = await self.server.start(0)

    async def asyncTearDown(self):
        await self.server.stop()
        self.temp.cleanup()

    async def transact(self, cmd, sub, body=b"", password=b"tm-local-test", fragmented=False):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            opening = bytearray(40)
            opening[4] = 1
            writer.write(opening)
            await writer.drain()
            opened = await reader.readexactly(24)
            request = bytearray(40)
            request[:3] = bytes((2, cmd, sub))
            struct.pack_into("<I", request, 4, len(body))
            request[24:] = hashlib.md5(b"EEEEEEEF" + password + opened[20:24]).digest()
            packet = wire.crypt64(self.cipher, self.iv, request + body)
            if fragmented:
                for offset in range(0, len(packet), 7):
                    writer.write(packet[offset : offset + 7])
                    await writer.drain()
                    await asyncio.sleep(0)
            else:
                writer.write(packet)
                await writer.drain()
            head = wire.crypt64(self.cipher, self.iv, await reader.readexactly(24))
            size = struct.unpack_from("<I", head, 4)[0]
            tail = await reader.readexactly(size)
            payload = wire.crypt64(self.cipher, self.iv, bytes(24) + tail)[24:]
            return head[1], payload
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_handle_and_character_records(self):
        error, handles = await self.transact(0, 9)
        self.assertEqual((error, len(handles), handles[0]), (0, 8 + 0x88, 1))
        self.assertEqual(handles[24:35], b"LocalPlayer")
        error, characters = await self.transact(1, 3)
        self.assertEqual((error, len(characters), characters[0]), (0, 8 + 0x68, 1))
        self.assertEqual(struct.unpack_from("<H", characters, 16)[0], 2)

    async def test_fragmented_status_change_roundtrip(self):
        change = bytearray(0x24)
        change[0x14] = 2
        change[0x16] = 2
        change[0x19] = 1
        error, changed = await self.transact(4, 5, checked(change), fragmented=True)
        self.assertEqual((error, len(changed)), (0, 0x20))
        self.assertEqual(checksum(changed[:-4]), int.from_bytes(changed[-4:], "little"))
        error, status = await self.transact(4, 6, checked(bytes(20)))
        self.assertEqual((error, len(status), status[4], status[6]), (0, 0x80, 2, 2))
        self.assertEqual(checksum(status[:-4]), int.from_bytes(status[-4:], "little"))

    async def test_wrong_password_rejected(self):
        # No response cipher can be selected until full nonce authentication.
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.transact(0, 9, password=b"wrong")

    async def test_profile_context_survives_game_irc_reconnect(self):
        self.assertEqual((await self.transact(0, 9))[0], 0)
        # Playing closes/reopens game IRC. Native profile state still belongs
        # to the original Viewer session and must retain its original cipher.
        self.server.game_server.nicks.clear()
        self.assertEqual((await self.transact(1, 3))[0], 0)

    async def test_file_offset_and_missing_file(self):
        body = bytearray(0x19C)
        body[16:32] = b"U/g/TM0DataFile\0"
        struct.pack_into("<II", body, 0x190, 0, 3)
        error, payload = await self.transact(3, 0, checked(body))
        self.assertEqual((error, payload), (0, checked(b"\x2c\x01\x00")))
        body[16] = ord("X")
        self.assertEqual((await self.transact(3, 0, checked(body)))[0], 0x7E)

    async def test_bad_checksum_and_unknown_operation(self):
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.transact(4, 6, bytes(23) + b"\x01")
        self.assertEqual((await self.transact(99, 99))[0], 1)


class ProfileFormatTests(unittest.TestCase):
    def test_checksum_unaligned_tail(self):
        self.assertEqual(checksum(b"\x01"), 0x01000000)
        self.assertEqual(checksum(b"\x01\x02"), 0x02010000)
        self.assertEqual(checksum(b"\x01\x02\x03"), 0x03020100)
        self.assertEqual(checksum(b"\x01\x02\x03\x04\x05"), 0x09030201)

    def test_teach_directory_keeps_command_separator(self):
        self.assertIn(b"@TeachDVAns=/D=1/V=1", teach_dv_answer())
        self.assertIn(b"/Shm=" + f"{game_id(SHOP_RAW):016X}".encode() + b"|1|1", teach_dv_answer())


if __name__ == "__main__":
    unittest.main()
