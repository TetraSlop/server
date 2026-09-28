"""Profile edits/restores must remain readable through every legal locator."""

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from tests.network.test_server import Client
from tests.profiles.test_character_profiles import request
from tmserver.economy.service import EconomyService
from tmserver.network.server import ACCOUNTS, Server
from tmserver.profiles.characters import CharacterProfiles
from tmserver.protocol.commands import Command, parse
from tmserver.protocol.identities import player_id
from tmserver.storage.json import JsonStateStore


def sized_record(profiles, key, length):
    fields = profiles._default(key)
    needed = length - (11 + len(b"\x06".join(v.encode() for v in fields)))
    for index in range(12, 16):
        count = min(126, needed)
        fields[index] += "0" * count
        needed -= count
    assert needed == 0
    return fields


class ProfileReplyBudgetTests(unittest.TestCase):
    def setUp(self):
        self.economy = EconomyService()
        self.economy.provision("alice", nn=0x101, player_id=1234567890, name=b"Alice")
        self.profiles = CharacterProfiles(self.economy)

    def test_restore_rejects_oversized_complete_reply_and_larger_locator(self):
        for length in (461, 465, 469, 470, 474):
            saved = {"version": 1, "records": {"alice": sized_record(self.profiles, "alice", length)}}
            before = copy.deepcopy(saved), self.economy.snapshot()
            with self.subTest(length=length), self.assertRaises(ValueError):
                CharacterProfiles.restore(self.economy, saved)
            self.assertEqual((saved, self.economy.snapshot()), before)

    def test_exact_boundary_reads_both_locators_and_failed_edit_preserves_record(self):
        saved = {"version": 1, "records": {"alice": sized_record(self.profiles, "alice", 460)}}
        profiles = CharacterProfiles.restore(self.economy, saved)
        for character, length in ((1, 460), (1234567890, 469)):
            reply = profiles.handle("alice", request(b"PG", 0x101, character))[0]
            self.assertEqual(len(reply), length)
            self.assertEqual(parse(reply[5:])[0].payload.split(b"\x06")[1], str(character).encode())
        for token in (b"CR", b"GR"):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "frame budget"):
                profiles.handle("alice", request(token, 0x101, extras=(Command(b"AN", b"Alice!"),)))
            self.assertEqual(profiles.snapshot(), saved)
        profiles.handle("alice", request(b"GR", 0x101, extras=(Command(b"FO", b"1"),)))
        self.assertEqual(profiles.records["alice"][8], "1")


class ProfileReplyBudgetIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_boundary_reply_and_failed_edit_survive_encrypted_restart(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        nick = next(iter(ACCOUNTS))
        key = nick.decode()
        economy = EconomyService()
        economy.provision(key, nn=player_id(nick), player_id=1234567890, name=b"Alice")
        profiles = CharacterProfiles(economy)
        profiles.records[key] = tuple(sized_record(profiles, key, 460))
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            JsonStateStore(state).save(dict(economy.snapshot(), character_profiles=profiles.snapshot()))
            for restarted in (False, True):
                server, client = Server(state_path=state), Client()
                try:
                    await client.open(await server.start(0), account=nick)
                    await client.read()
                    for character, size in ((1, 503), (1234567890, 512)):
                        await client.send(b"NOTICE UUIRT71SR :" + request(b"PG", 1234567890, character))
                        reply = await client.read()
                        self.assertEqual(len(reply) + 6, size)
                        self.assertEqual(parse(reply.split(b" :", 1)[1][5:])[0].token, b"PO")
                    if not restarted:
                        before = state.read_bytes(), server.character_profiles.snapshot()
                        await client.send(b"NOTICE UUIRT71SR :" + request(
                            b"GR", 1234567890, extras=(Command(b"AN", b"Alice!"),)
                        ))
                        self.assertEqual(await client.read(), b"")
                        self.assertEqual((state.read_bytes(), server.character_profiles.snapshot()), before)
                finally:
                    if hasattr(client, "writer"):
                        await client.close()
                    await server.stop()
