"""Fresh preferences, original guild/reload branches, and authenticated Init80."""

import asyncio
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.profiles.test_profile_accounts import file_request, transact
from tests.support.irc import Client
from tests.support.memory_store import MemoryStore, failing_commits
from tmserver.economy.models import PROFILE_SIZE
from tmserver.economy.service import EconomyService
from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import named
from tmserver.protocol.identities import ROOM_RAW, SHOP_RAW, TABLE_RAW, player_id, service_nick
from tmserver.profiles.service import ProfileServer
from tmserver.rankings.stats import PracticeStats
from tmserver.network.server import Server

F = 0x10294BD0
OPTIONS = 0x102B5E8C


def request(service, key="a", guild=0, subtype=0, **changes):
    account = service.account_view(key)
    fields = dict(
        NN=f"{account.nn:016X}",
        HID="0",
        Dm="0",
        Vol="0",
        CN=account.name.hex(),
        GID=f"{account.player_id:016X}",
        L="1",
        GLD=str(guild),
    )
    fields.update(changes)
    return named(0x80, subtype, 0, "@Init=" + "".join(f"/{k}={v}" for k, v in fields.items()))


class NewProfileTests(unittest.TestCase):

    def test_identity_schema_conflicts_and_busy_registration_fail_atomically(self):
        economy = EconomyService(seed=1)
        economy.provision("a", nn=123, player_id=7, guild=0)
        for fields in (
            {"NN": "000000000000007C"},
            {"GID": "0000000000000008"},
            {"CN": "466F72676564"},
            {"HID": "1"},
            {"Dm": "256"},
            {"Vol": "65536"},
            {"L": "2147483648"},
            {"GLD": "4"},
            {"GLD": "-1"},
            {"NN": "123"},
            {"CN": "00"},
            {"Extra": "1"},
        ):
            before = economy.snapshot()
            with self.assertRaises(EconomyError):
                economy.initialize_profile("a", request(economy, guild=2, **fields))
            self.assertEqual(economy.snapshot(), before)
        token = economy.reserve_match("a")
        before = economy.snapshot()
        with self.assertRaises(EconomyError):
            economy.initialize_profile("a", request(economy, guild=2))
        self.assertEqual(economy.snapshot(), before)
        economy.release_match(token)
        economy.initialize_profile("a", request(economy, guild=2))
        before = economy.snapshot()
        with self.assertRaises(EconomyError):
            economy.initialize_profile("a", request(economy, guild=1))
        self.assertEqual(economy.snapshot(), before)
        # Native initial send commonly carries 0 before its file read finishes.
        self.assertEqual(
            economy.initialize_profile("a", request(economy)), named(0x81, 0, 0, "@InitAns=/Ans=1")
        )


class RegistrationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.store = MemoryStore()
        self.server = Server(store=self.store)
        self.key = "UGITW5T4U"
        self.server.economy.provision(self.key, nn=player_id(self.key.encode()), guild=0)
        self.server.account_keys.add(self.key)
        self.server.persist()
        self.port = await self.server.start(0)
        self.profile = ProfileServer(Path(self.temp.name) / "profile.jsonl", self.server)
        self.profile_port = await self.profile.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())

    async def asyncTearDown(self):
        await self.client.close()
        await self.profile.stop()
        await self.server.stop()
        self.temp.cleanup()

    async def init(self, guild, target=ROOM_RAW, **changes):
        payload = request(self.server.economy, self.key, guild, **changes)
        await self.client.send(b"NOTICE " + service_nick(target) + b" :" + payload)
        return await self.client.read()

    async def read_profile(self):
        error, payload = await transact(
            self.profile_port,
            self.key,
            3,
            0,
            file_request(),
            context=(self.client.cipher, self.client.iv),
        )
        self.assertEqual(error, 0)
        return payload[:-4]

    async def test_encrypted_registration_profile_reread_restart_and_replay(self):
        before = await self.read_profile()
        self.assertEqual(before[0x3B], 0)
        self.assertTrue((await self.init(0)).endswith(b"@InitAns=/Ans=1"))
        self.assertTrue((await self.init(3)).endswith(b"@InitAns=/Ans=2"))
        after = await self.read_profile()
        expected = bytearray(before)
        expected[0x3B] = 3
        self.assertEqual(after, expected)
        # Registration is durably available before the positive response.
        restored = Server(store=self.store)
        try:
            self.assertEqual(restored.economy.profile_bytes(self.key), after)
            self.assertEqual(
                restored.economy.initialize_profile(
                    self.key, request(restored.economy, self.key, 3)
                ),
                named(0x81, 0, 0, "@InitAns=/Ans=2"),
            )
        finally:
            await restored.stop()
        self.assertTrue((await self.init(3, target=TABLE_RAW)).endswith(b"@InitAns=/Ans=2"))
        self.assertTrue((await self.init(0, target=SHOP_RAW)).endswith(b"@InitAns=/Ans=1"))
        self.assertEqual(await self.read_profile(), after)

    async def test_encrypted_forged_identity_disconnects_without_mutation(self):
        before = self.store.load()
        self.assertEqual(await self.init(2, GID="0000000000000002"), b"")
        self.assertEqual(self.server.economy.account_view(self.key).guild, 0)
        self.assertEqual(self.store.load(), before)

    async def test_failed_registration_save_has_no_success_and_preserves_last_save(self):
        before = self.store.load()
        with failing_commits("injected registration save failure"):
            self.assertEqual(await self.init(2), b"")
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.store.load(), before)
        restored = Server(store=self.store)
        try:
            self.assertEqual(restored.economy.account_view(self.key).guild, 0)
        finally:
            await restored.stop()


if __name__ == "__main__":
    unittest.main()
