"""Generic profile partial updates, original 71-field parser and socket save."""

import asyncio
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.support.irc import Client
from tmserver.profiles.characters import CharacterProfiles
from tmserver.economy.service import EconomyService
from tmserver.protocol.identities import player_id
from tests.support.irc import ACCOUNTS
from tests.support.memory_store import MemoryStore
from tmserver.network.server import Server
from tmserver.protocol.commands import Command, encode, parse



def request(token, identity=1, key=1, extras=()):
    return b"GTM0P" + encode([Command(token, f"0x{identity:016X}\x06{key}".encode()), *extras])


def settings():
    return (
        Command(b"PP", b"5"),
        Command(b"NO", b"6\x063"),
        Command(b"NO", b"14\x061"),
        Command(b"VO", b"1"),
        Command(b"FO", b"3"),
    )


class CharacterProfileTests(unittest.TestCase):
    def setUp(self):
        self.economy = EconomyService()
        self.economy.provision("alice", nn=0x101, player_id=1, name=b"Alice")
        self.economy.provision("bob", nn=0x102, player_id=2, name=b"Bob")
        self.service = CharacterProfiles(self.economy)

    def test_full_native_name_has_a_bounded_default_without_renaming_account(self):
        name = b"ABCDEFGHIJKLMNOP"
        self.economy.provision("long", nn=0x103, player_id=3, name=name)
        before = self.economy.snapshot()
        native = self.economy.profile_bytes("long")
        for reader in ("long", "alice"):
            reply = self.service.handle(reader, request(b"PG", 0x103))[0]
            fields = parse(reply[5:])[0].payload.split(b"\x06")
            self.assertEqual(fields[2], name[:15])
        self.assertNotIn("long", self.service.records)
        for token, expected in ((b"CR", b"CS"), (b"GR", b"GK")):
            reply = self.service.handle("long", request(token, 0x103))[0]
            self.assertEqual(parse(reply[5:])[0].token, expected)
            self.assertEqual(self.service.records["long"][2], name[:15].decode())
        self.assertEqual(self.economy.snapshot(), before)
        self.assertEqual(self.economy.profile_bytes("long"), native)
        self.assertEqual(native[0x10:0x20], name)

    def test_restored_override_never_evaluates_default_profile(self):
        self.economy.provision("long", nn=0x103, player_id=3, name=b"ABCDEFGHIJKLMNOP")
        self.service.handle("long", request(b"CR", 0x103, extras=(Command(b"AN", b"Saved name"),)))
        record = list(self.service.records["long"])
        record[17] = "1234"
        self.service.records["long"] = tuple(record)
        restored = CharacterProfiles.restore(self.economy, self.service.snapshot())
        native = self.economy.snapshot()
        with patch.object(restored, "_default", side_effect=AssertionError("unused default")):
            for token in (b"PG", b"CR", b"GR"):
                reply = restored.handle("long", request(token, 0x103))[0]
                if token == b"PG":
                    self.assertEqual(parse(reply[5:])[0].payload.split(b"\x06")[2], b"Saved name")
                self.assertEqual(restored.records["long"], tuple(record))
            restored.handle("long", request(b"GR", 0x103, extras=(Command(b"FO", b"1"),)))
        self.assertEqual(restored.records["long"][8], "1")
        self.assertEqual(restored.records["long"][2], "Saved name")
        self.assertEqual(restored.records["long"][17], "1234")
        self.assertEqual(self.economy.snapshot(), native)

    def test_registration_partial_updates_and_opaque_preservation(self):
        self.assertEqual(self.service.handle("alice", request(b"CR")), [b"GTM0P<CS>\x07\x07"])
        baseline = list(self.service.records["alice"])
        baseline[17] = "1234"
        self.service.records["alice"] = tuple(baseline)
        self.service.handle("alice", request(b"GR", extras=settings()))
        saved = self.service.records["alice"]
        self.assertEqual([saved[i] for i in (3, 47, 55, 40, 8)], ["5", "3", "1", "1", "3"])
        self.assertEqual(saved[17], "1234")
        self.service.handle(
            "alice", request(b"CR", extras=(Command(b"AN", b"Alice"), Command(b"CI", b"0\x062.50")))
        )
        self.assertEqual(self.service.records["alice"][55], "1")
        self.assertEqual(self.service.records["alice"][9], "2.50")
        restored = CharacterProfiles.restore(self.economy, self.service.snapshot())
        self.assertEqual(restored.snapshot(), self.service.snapshot())

    def test_read_another_profile_but_cannot_change_it(self):
        self.service.handle("alice", request(b"GR", extras=settings()))
        before = self.service.snapshot()
        reply = self.service.handle("bob", request(b"GR", extras=(Command(b"PP", b"0"),)))[0]
        self.assertEqual([c.token for c in parse(reply[5:])], [b"GG", b"PE"])
        self.assertEqual(self.service.snapshot(), before)
        own = self.service.handle("alice", request(b"PG"))[0]
        other = self.service.handle("bob", request(b"PG"))[0]
        self.assertEqual(own, other)
        unknown = self.service.handle("alice", request(b"PG", 999))[0]
        self.assertEqual(parse(unknown[5:])[0].token, b"PM")
        rejected = self.service.handle("bob", request(b"CR"))[0]
        self.assertEqual(parse(rejected[5:])[0].token, b"NS")

    def test_invalid_updates_do_not_apply_earlier_fields(self):
        before = self.service.snapshot()
        for extra in (
            Command(b"NO", b"6\x062"),
            Command(b"AN", b"X" * 16),
            Command(b"CI", b"0\x06" + b"x" * 64),
            Command(b"PP", b"6"),
            Command(b"NO", b"15\x061"),
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.service.handle("alice", request(b"GR", extras=(Command(b"FO", b"1"), extra)))
            self.assertEqual(self.service.snapshot(), before)
        with self.assertRaises(ValueError):
            self.service.handle(
                "alice",
                request(b"GR", extras=(Command(b"NO", b"6\x061"), Command(b"NO", b"6\x063"))),
            )

    def test_numeric_selector_aliases_cannot_overwrite_an_earlier_privacy_update(self):
        for registered in (False, True):
            if registered:
                self.service.handle("alice", request(b"CR", extras=settings()))
            before = self.service.snapshot(), self.economy.snapshot()
            for token in (b"CR", b"GR"):
                for selector in (b"6", b"14"):
                    for first, second in ((selector, b"0" + selector), (b"0" + selector, selector)):
                        with self.subTest(registered=registered, token=token, first=first):
                            extras = (
                                Command(b"AN", b"Uncommitted"),
                                Command(b"NO", first + b"\x061"),
                                Command(b"NO", second + b"\x063"),
                            )
                            with self.assertRaisesRegex(ValueError, "duplicate profile"):
                                self.service.handle("alice", request(token, extras=extras))
                            self.assertEqual(
                                (self.service.snapshot(), self.economy.snapshot()), before
                            )
        # A single decimal alias remains valid, and 6/14 are distinct fields.
        self.service.handle(
            "alice",
            request(b"GR", extras=(Command(b"NO", b"006\x061"), Command(b"NO", b"014\x063"))),
        )
        restored = CharacterProfiles.restore(self.economy, self.service.snapshot())
        self.assertEqual((restored.records["alice"][47], restored.records["alice"][55]), ("1", "3"))

    def test_restore_rejects_unsafe_float_text_without_changing_input_or_accounts(self):
        self.service.handle("alice", request(b"CR"))
        economy = self.economy.snapshot()
        for index in range(12, 16):
            for value in ("0" * 128, "1" + "0" * 39, "-1" + "0" * 39):
                saved = self.service.snapshot()
                saved["records"]["alice"][index] = value
                before = copy.deepcopy(saved)
                with self.subTest(index=index, value=value):
                    with self.assertRaisesRegex(ValueError, "profile float"):
                        CharacterProfiles.restore(self.economy, saved)
                    self.assertEqual(saved, before)
                    self.assertEqual(self.economy.snapshot(), economy)

    def test_restore_preserves_safe_float_spelling_in_public_responses(self):
        self.service.handle("alice", request(b"CR"))
        for index in range(12, 16):
            for value in (
                "0" * 127,
                "-" + "0" * 126,
                "340282346638528859811704183484516925440",
                "-340282346638528859811704183484516925440",
                "-12.50",
                "0." + "0" * 44 + "1",  # Rounds to a float32 subnormal.
                "0." + "0" * 100 + "1",  # Underflows to zero without overflow.
            ):
                saved = self.service.snapshot()
                saved["records"]["alice"][index] = value
                with self.subTest(index=index, value=value):
                    restored = CharacterProfiles.restore(self.economy, saved)
                    self.assertEqual(restored.snapshot(), saved)
                    reply = restored.handle("bob", request(b"PG"))[0]
                    self.assertEqual(
                        parse(reply[5:])[0].payload.split(b"\x06")[index], value.encode()
                    )


class CharacterProfileIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_native_name_registers_reads_and_restarts_over_encrypted_service(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        nick = next(iter(ACCOUNTS))
        key = nick.decode("ascii")
        name = b"ABCDEFGHIJKLMNOP"
        economy = EconomyService(seed=1)
        economy.provision(key, nn=player_id(nick), player_id=1, name=name)
        native = economy.profile_bytes(key)
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            store = MemoryStore()
            store.save(economy.snapshot())
            for restarted in (False, True):
                with self.subTest(restarted=restarted):
                    server = Server(store=store)
                    client = Client()
                    try:
                        await client.open(await server.start(0), account=nick)
                        self.assertIn(b" 422 ", await client.read())

                        async def exchange(token, extras=()):
                            await client.send(b"NOTICE UUIRT71SR :" + request(token, extras=extras))
                            reply = await client.read()
                            self.assertTrue(reply.startswith(b":UUIRT71SR!p@local NOTICE "))
                            return parse(reply.split(b" :", 1)[1][5:])[0]

                        first = await exchange(b"PG")
                        self.assertEqual(first.token, b"PO")
                        self.assertEqual(
                            first.payload.split(b"\x06")[2],
                            b"Saved name" if restarted else name[:15],
                        )
                        extra = () if restarted else (Command(b"AN", b"Saved name"),)
                        self.assertEqual((await exchange(b"CR", extra)).token, b"CS")
                        self.assertEqual(
                            (await exchange(b"GR", (Command(b"FO", b"1"),))).token, b"GK"
                        )
                        fields = (await exchange(b"PG")).payload.split(b"\x06")
                        self.assertEqual((fields[2], fields[8]), (b"Saved name", b"1"))
                        self.assertEqual(server.economy.profile_bytes(key), native)
                        saved = store.load()
                        self.assertEqual(
                            saved["accounts"][key], economy.snapshot()["accounts"][key]
                        )
                        self.assertEqual(
                            saved["character_profiles"]["records"][key][2], "Saved name"
                        )
                    finally:
                        if hasattr(client, "writer"):
                            await client.close()
                        await server.stop()

    async def test_edit_read_and_restart_preserve_both_visibility_tags(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            store = MemoryStore()
            server = Server(store=store)
            client = Client()
            try:
                port = await server.start(0)
                await client.open(port)
                await client.read()
                target = b"UUIRT71SR"
                for payload, expected in (
                    (request(b"CR"), b"CS"),
                    (request(b"GR", extras=settings()), b"GK"),
                ):
                    await client.send(b"NOTICE " + target + b" :" + payload)
                    reply = await client.read()
                    self.assertEqual(parse(reply.split(b" :", 1)[1][5:])[0].token, expected)
                await client.send(b"NOTICE " + target + b" :" + request(b"PG"))
                reply = await client.read()
                fields = parse(reply.split(b" :", 1)[1][5:])[0].payload.split(b"\x06")
                self.assertEqual(
                    [fields[i] for i in (3, 47, 55, 40, 8)], [b"5", b"3", b"1", b"1", b"3"]
                )
                restored = Server(store=store)
                try:
                    self.assertEqual(
                        restored.character_profiles.records[client.nick.decode()],
                        server.character_profiles.records[client.nick.decode()],
                    )
                    self.assertEqual(
                        restored.economy.profile_bytes(client.nick.decode()),
                        server.economy.profile_bytes(client.nick.decode()),
                    )
                finally:
                    await restored.stop()
            finally:
                if hasattr(client, "writer"):
                    await client.close()
                await server.stop()


if __name__ == "__main__":
    unittest.main()
