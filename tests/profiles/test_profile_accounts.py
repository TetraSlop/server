"""Account isolation and Viewer key continuity over actual profile TCP sockets."""

import asyncio
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import ROOT
from tmserver.protocol import wire
from tmserver.economy.service import EconomyService
from tmserver.protocol.identities import player_id
from tmserver.profiles.service import MAX_CACHED_CIPHERS, SYNTHETIC_ACCOUNTS, ProfileServer
from tmserver.profiles.packets import checked, checksum

KEYS = tuple(SYNTHETIC_ACCOUNTS)


def file_request(name="U/g/TM0DataFile", offset=0, size=0x3028):
    data = bytearray(0x19C)
    encoded = name.encode("ascii")
    data[16 : 16 + len(encoded)] = encoded
    struct.pack_into("<II", data, 0x190, offset, size)
    return checked(data)


def delete_request(name):
    # Original Common344 builder has no read offset/maximum tail.
    data = bytearray(0x194)
    encoded = name.encode("ascii")
    if len(encoded) > 0x17F:
        raise ValueError("delete pathname exceeds native field")
    data[16 : 16 + len(encoded)] = encoded
    return checked(data)


async def transact(
    port,
    key,
    cmd,
    sub,
    body=b"",
    *,
    context=None,
    password=b"tm-local-test",
    corrupt_digest=False,
    fragmented=False,
    opening_tag=b"",
    account_id=None,
):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        async with asyncio.timeout(5):
            opening = bytearray(40)
            opening[1] = 1 if context is None else 0
            opening[4] = 1
            opening[8 : 8 + len(opening_tag)] = opening_tag
            writer.write(opening)
            await writer.drain()
            opened = await reader.readexactly(24)
            request = bytearray(40)
            request[:3] = bytes((2, cmd, sub))
            struct.pack_into("<I", request, 4, len(body))
            request[24:] = hashlib.md5(
                (SYNTHETIC_ACCOUNTS[key][0] if account_id is None else account_id)
                + password
                + opened[20:24]
            ).digest()
            if corrupt_digest:
                request[-1] ^= 1
            packet = bytes(request) + body
            if context is not None:
                packet = wire.crypt64(*context, packet)
            step = 7 if fragmented else len(packet)
            for offset in range(0, len(packet), step):
                writer.write(packet[offset : offset + step])
                await writer.drain()
                if fragmented:
                    await asyncio.sleep(0)
            head = await reader.readexactly(24)
            if context is not None:
                head = wire.crypt64(*context, head)
            size = struct.unpack_from("<I", head, 4)[0]
            if size > 1024 * 1024:
                raise AssertionError("invalid profile response size")
            payload = await reader.readexactly(size)
            if context is not None:
                payload = wire.crypt64(*context, bytes(24) + payload)[24:]
            return head[1], payload
    finally:
        writer.close()
        await writer.wait_closed()


class ProfileAccountsTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.ciphers = (wire.SqBlowfish(b"account1"), wire.SqBlowfish(b"account2"))

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.economy = EconomyService(seed=11)
        self.contexts = tuple(
            (cipher, 0x1020304050607080 + i) for i, cipher in enumerate(self.ciphers)
        )
        for i, key in enumerate(KEYS):
            self.economy.provision(
                key,
                nn=player_id(key.encode("ascii")),
                player_id=i + 1,
                name=(b"Alice", b"Boris")[i],
                balance=(12345, 54321)[i],
            )
        self.ensured = []

        def ensure_account(nick):
            key = nick.decode("ascii")
            self.economy.account_view(key)
            self.ensured.append(key)
            return key

        self.game = SimpleNamespace(
            economy=self.economy,
            ensure_account=ensure_account,
            nicks={
                key.encode("ascii"): SimpleNamespace(cipher=cipher, iv=iv)
                for key, (cipher, iv) in zip(KEYS, self.contexts)
            },
        )
        self.server = ProfileServer(Path(self.temp.name) / "traffic.jsonl", self.game)
        self.port = await self.server.start(0)

    async def asyncTearDown(self):
        await self.server.stop()
        self.temp.cleanup()

    async def request(self, index, cmd, sub, body=b"", **kwargs):
        kwargs.setdefault("context", self.contexts[index])
        return await transact(self.port, KEYS[index], cmd, sub, body, **kwargs)

    async def test_both_encrypted_profiles_are_distinct_and_never_shared(self):
        results = await asyncio.gather(
            *(self.request(i, 3, 0, file_request(), fragmented=True) for i in range(2))
        )
        for i, (error, data) in enumerate(results):
            self.assertEqual(error, 0)
            self.assertEqual(data, checked(self.economy.profile_bytes(KEYS[i])))
        self.assertNotEqual(results[0][1], results[1][1])
        self.assertNotIn("U/g/TM0DataFile", self.server.files)
        self.assertEqual((await self.request(0, 3, 0, file_request()))[1], results[0][1])
        self.assertEqual((await self.request(1, 3, 0, file_request()))[1], results[1][1])

    async def test_both_handle_character_records_use_authenticated_account(self):
        for i, key in enumerate(KEYS):
            error, handles = await self.request(
                i, 0, 9, opening_tag=SYNTHETIC_ACCOUNTS[KEYS[1 - i]][0]
            )
            self.assertEqual((error, len(handles)), (0, 0x90))
            self.assertEqual(struct.unpack_from("<Q", handles, 16)[0], i + 1)
            self.assertEqual(handles[24:40].split(b"\0")[0], (b"Alice", b"Boris")[i])
            error, characters = await self.request(i, 1, 3)
            self.assertEqual((error, len(characters)), (0, 0x70))
            self.assertEqual(struct.unpack_from("<IQ", characters, 20), (i + 1, i + 1))
            self.assertEqual(characters[32:48].split(b"\0")[0], (b"Alice", b"Boris")[i])

    async def test_status_changes_are_isolated_for_both_accounts(self):
        for i in range(2):
            data = bytearray(0x24)
            data[0x10:0x18] = bytes((i, 0, 0, 1, i + 2, 0, i + 2, 0))
            self.assertEqual((await self.request(i, 4, 5, checked(data)))[0], 0)
            untouched = (await self.request(1 - i, 4, 6, checked(bytes(20))))[1]
            self.assertEqual(untouched[4], 1 if i == 0 else 2)
        for i in range(2):
            error, status = await self.request(i, 4, 6, checked(bytes(20)))
            self.assertEqual((error, status[0], status[4], status[6]), (0, i, i + 2, i + 2))
            self.assertEqual(checksum(status[:-4]), int.from_bytes(status[-4:], "little"))

    async def test_cross_account_cipher_digest_pairs_fail_closed(self):
        before = self.economy.snapshot()
        for i in range(2):
            with self.subTest(account=i), self.assertRaises(asyncio.IncompleteReadError):
                await self.request(i, 3, 0, file_request(), context=self.contexts[1 - i])
        self.assertFalse(self.ensured)
        self.assertEqual(self.economy.snapshot(), before)
        self.assertTrue(all(not values for values in self.server.profile_ciphers.values()))

    async def test_all_digest_bytes_required_and_failed_auth_cannot_mutate_status(self):
        change = checked(bytes([255]) * 0x24)
        for i in range(2):
            with self.subTest(account=i), self.assertRaises(asyncio.IncompleteReadError):
                await self.request(i, 4, 5, change, corrupt_digest=True)
            self.assertEqual(self.server.statuses[KEYS[i]][4], 1)
        self.assertTrue(all(not values for values in self.server.profile_ciphers.values()))

    async def test_plain_mode_authenticates_both_accounts_without_irc_context(self):
        self.game.nicks.clear()
        for i in range(2):
            error, data = await self.request(i, 3, 0, file_request(), context=None)
            self.assertEqual((error, data), (0, checked(self.economy.profile_bytes(KEYS[i]))))
            error, data = await self.request(i, 0, 9, context=None, password=b"wrong")
            self.assertEqual((error, data), (0xE4, b""))
        self.assertTrue(all(not values for values in self.server.profile_ciphers.values()))

    async def test_unvalidated_irc_key_is_not_retained_after_disconnect(self):
        self.game.nicks.clear()
        for i in range(2):
            with self.subTest(account=i), self.assertRaises(asyncio.IncompleteReadError):
                await self.request(i, 0, 9)

    async def test_current_and_prior_validated_contexts_survive_reconnect_for_both(self):
        for i in range(2):
            self.assertEqual((await self.request(i, 0, 9))[0], 0)
            cipher, iv = self.contexts[i]
            replacement = (cipher, iv + 101)
            self.game.nicks[KEYS[i].encode("ascii")] = SimpleNamespace(
                cipher=cipher, iv=replacement[1]
            )
            self.assertEqual((await self.request(i, 1, 3, context=replacement))[0], 0)
            self.assertEqual((await self.request(i, 1, 3))[0], 0)
            self.game.nicks.pop(KEYS[i].encode("ascii"))
            for context in (replacement, self.contexts[i]):
                error, data = await self.request(i, 3, 0, file_request(), context=context)
                self.assertEqual((error, data), (0, checked(self.economy.profile_bytes(KEYS[i]))))

    async def test_cache_is_bounded_and_oldest_validated_context_expires(self):
        cipher, iv = self.contexts[0]
        contexts = [(cipher, iv + n) for n in (0, 100, 200)]
        for context in contexts:
            self.game.nicks[KEYS[0].encode("ascii")] = SimpleNamespace(
                cipher=context[0], iv=context[1]
            )
            self.assertEqual((await self.request(0, 0, 9, context=context))[0], 0)
            self.assertLessEqual(len(self.server.profile_ciphers[KEYS[0]]), MAX_CACHED_CIPHERS)
        self.game.nicks.clear()
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.request(0, 0, 9, context=contexts[0])
        for context in contexts[1:]:
            self.assertEqual((await self.request(0, 0, 9, context=context))[0], 0)

    async def test_private_file_fallback_cannot_leak_shared_or_other_account_data(self):
        self.server.files["U/g/private"] = b"wrong shared source"
        self.server.account_files[KEYS[0]]["U/g/private"] = b"account zero"
        self.assertEqual(
            await self.request(0, 3, 0, file_request("U/g/private")), (0, checked(b"account zero"))
        )
        self.assertEqual(await self.request(1, 3, 0, file_request("U/g/private")), (0x7E, b""))

    async def test_storage_failure_prevents_any_further_profile_operations(self):
        self.game.storage_failed = OSError("injected persistence failure")
        for i in range(2):
            for cmd, sub, body in ((3, 0, file_request()), (0, 9, b""), (4, 6, checked(bytes(20)))):
                with (
                    self.subTest(account=i, op=(cmd, sub)),
                    self.assertRaises(asyncio.IncompleteReadError),
                ):
                    await self.request(i, cmd, sub, body)
        self.assertFalse(self.ensured)


class ProfileRealGameContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_real_irc_logins_and_profile_reconnect(self):
        # These imports belong in tests, not profile_service's import graph.
        from tests.support.irc import Client
        from tests.support.irc import ACCOUNTS
        from tmserver.network.server import Server

        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.assertEqual(
            {key.encode("ascii"): value[1] for key, value in SYNTHETIC_ACCOUNTS.items()}, ACCOUNTS
        )
        clients = []
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as directory:
            game = Server(state_path=Path(directory) / "state.json")
            profile = ProfileServer(Path(directory) / "profile.jsonl", game)
            try:
                game_port, profile_port = await game.start(0), await profile.start(0)
                for key in KEYS:
                    client = Client()
                    clients.append(client)
                    await client.open(game_port, account=key.encode("ascii"))
                    self.assertIn(b" 422 ", await client.read())
                for i, key in enumerate(KEYS):
                    error, data = await transact(
                        profile_port,
                        key,
                        3,
                        0,
                        file_request(),
                        context=(clients[i].cipher, clients[i].iv),
                    )
                    self.assertEqual((error, data), (0, checked(game.economy.profile_bytes(key))))
                    self.assertEqual(struct.unpack_from("<I", data, 4)[0], i + 1)
                original = (clients[0].cipher, clients[0].iv)
                await clients[0].close()
                replacement = Client()
                clients.append(replacement)
                await replacement.open(game_port, account=KEYS[0].encode("ascii"))
                self.assertIn(b" 422 ", await replacement.read())
                for context in ((replacement.cipher, replacement.iv), original):
                    error, data = await transact(
                        profile_port, KEYS[0], 3, 0, file_request(), context=context
                    )
                    self.assertEqual(
                        (error, data), (0, checked(game.economy.profile_bytes(KEYS[0])))
                    )
                with self.assertRaises(asyncio.IncompleteReadError):
                    await transact(profile_port, KEYS[1], 0, 9, context=original)
            finally:
                for client in clients:
                    await client.close()
                await profile.stop()
                await game.stop()


if __name__ == "__main__":
    unittest.main()
