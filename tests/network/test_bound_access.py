"""Authenticated play bindings retain legacy bytes and isolate accounts and keys."""

import asyncio
import hashlib
import json
import struct
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

from tests.network.test_server import Client
from tests.profiles.test_profile_accounts import file_request, transact
from tmserver.network.access import AccountAccess
from tmserver.network.server import Server
from tmserver.profiles.packets import checked
from tmserver.profiles.service import ProfileServer
from tmserver.protocol import wire


class AccountAccessTests(unittest.TestCase):
    def test_deadline_and_revocation_are_independent_of_wall_clock(self):
        now = [10]
        access = AccountAccess("account", 20, clock=lambda: now[0])
        access.check()
        now[0] = 20
        with self.assertRaises(ValueError):
            access.check()
        now[0] = 11
        access.check()
        access.revoke()
        with self.assertRaises(ValueError):
            access.check()


class BoundAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory()
        self.game_log = Path(self.temp.name) / "game.jsonl"
        self.profile_log = Path(self.temp.name) / "profile.jsonl"
        self.game = Server(log_path=self.game_log)
        self.profile = ProfileServer(self.profile_log, self.game)
        self.keys = tuple(self.game.account_specs)
        self.listeners = []
        self.clients = []

    async def asyncTearDown(self):
        for listener in self.listeners:
            listener.close()
        for client in self.clients:
            await client.close()
        await self.profile.stop()
        await self.game.stop()
        for listener in self.listeners:
            await listener.wait_closed()
        self.temp.cleanup()

    def access(self, index=0):
        return AccountAccess(self.keys[index], 100, clock=lambda: 0)

    async def port(self, kind, access):
        handler = self.game.accept if kind == "game" else self.profile.handle
        listener = await asyncio.start_server(partial(handler, access=access), "127.0.0.1", 0)
        self.listeners.append(listener)
        return listener.sockets[0].getsockname()[1]

    async def connect(self, access, account=None):
        client = Client()
        self.clients.append(client)
        await client.open(
            await self.port("game", access),
            account=(account or access.account_key).encode("ascii"),
            fragmented=True,
        )
        return client, await client.read()

    async def test_bound_game_and_encrypted_profile_preserve_bytes_and_redact_auth_logs(self):
        access = self.access()
        client, reply = await self.connect(access)
        self.assertEqual(reply, b":srv 422 " + client.nick + b" :no motd")
        self.assertIs(self.game.nicks[client.nick], access.game)
        await client.send(b"PING :bound")
        self.assertEqual(await client.read(), b"PONG bound")
        result = await transact(
            await self.port("profile", access),
            access.account_key,
            3,
            0,
            file_request(),
            context=(client.cipher, client.iv),
            fragmented=True,
        )
        self.assertEqual(result, (0, checked(self.game.economy.profile_bytes(access.account_key))))
        self.assertEqual(len(access.profile_ciphers), 1)
        self.assertFalse(any(self.profile.profile_ciphers.values()))
        records = [json.loads(line) for line in self.game_log.read_text().splitlines()]
        self.assertFalse(any(row.get("text", "").startswith(("USER ", "NICK ")) for row in records))
        profile_records = [json.loads(line) for line in self.profile_log.read_text().splitlines()]
        request = next(row for row in profile_records if row["event"] == "request")
        self.assertEqual(bytes.fromhex(request["wire"])[24:], bytes(16))

    async def test_correct_password_cannot_select_a_different_game_account(self):
        before = self.game.economy.snapshot()
        access = self.access()
        client, reply = await self.connect(access, self.keys[1])
        self.assertEqual(reply, b"")
        self.assertIsNone(access.game)
        self.assertNotIn(client.nick, self.game.nicks)
        self.assertEqual(self.game.economy.snapshot(), before)

    async def test_bound_profile_rejects_other_plain_and_encrypted_account(self):
        first, second = self.access(), self.access(1)
        client_a, _ = await self.connect(first)
        client_b, _ = await self.connect(second)
        port = await self.port("profile", first)
        self.assertEqual(await transact(port, self.keys[1], 0, 9), (0xE4, b""))
        for client in (client_a, client_b):
            with self.subTest(cipher=client.nick), self.assertRaises(asyncio.IncompleteReadError):
                await transact(port, self.keys[1], 0, 9, context=(client.cipher, client.iv))
        self.assertFalse(first.profile_ciphers)
        self.assertFalse(any(self.profile.profile_ciphers.values()))
        self.assertEqual((await transact(port, self.keys[0], 0, 9))[0], 0)

    async def test_profile_cache_survives_same_binding_reconnect_but_not_new_generation(self):
        old = self.access()
        client, _ = await self.connect(old)
        context = client.cipher, client.iv
        old_port = await self.port("profile", old)
        self.assertEqual((await transact(old_port, self.keys[0], 0, 9, context=context))[0], 0)
        # Populate the local-mode cache too: the new binding must ignore both caches.
        local_port = await self.port("profile", None)
        self.assertEqual((await transact(local_port, self.keys[0], 0, 9, context=context))[0], 0)
        await old.game.close()
        self.assertIsNone(old.game)
        self.assertEqual((await transact(old_port, self.keys[0], 0, 9, context=context))[0], 0)
        replacement = self.access()
        current, _ = await self.connect(replacement)
        new_port = await self.port("profile", replacement)
        with self.assertRaises(asyncio.IncompleteReadError):
            await transact(new_port, self.keys[0], 0, 9, context=context)
        self.assertEqual(
            (await transact(new_port, self.keys[0], 0, 9, context=(current.cipher, current.iv)))[0],
            0,
        )
        old.revoke()
        with self.assertRaises(asyncio.IncompleteReadError):
            await transact(old_port, self.keys[0], 0, 9, context=context)
        self.assertIs(self.game.nicks[current.nick], replacement.game)

    async def test_duplicate_game_does_not_replace_binding_and_close_allows_reconnect(self):
        access = self.access()
        client, _ = await self.connect(access)
        original = access.game
        reader, writer = await asyncio.open_connection("127.0.0.1", await self.port("game", access))
        try:
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
        finally:
            writer.close()
            await writer.wait_closed()
        self.assertIs(access.game, original)
        await original.close()
        replacement, reply = await self.connect(access)
        self.assertIn(b" 422 ", reply)
        self.assertIsNot(access.game, original)
        await original.close()
        self.assertIs(self.game.nicks[replacement.nick], access.game)

    async def test_revoked_and_expired_game_cannot_dispatch_buffered_commands(self):
        for expire in (False, True):
            access = self.access()
            client, _ = await self.connect(access)
            before = self.game.economy.snapshot()
            client.writer.write(wire.frame_line(b"JOIN #TM0-test", client.cipher, client.iv))
            if expire:
                access.clock = lambda: 100
            else:
                access.revoke()
            await client.writer.drain()
            self.assertEqual(await client.read(), b"")
            self.assertIsNone(access.game)
            self.assertFalse(self.game.channels)
            self.assertEqual(self.game.economy.snapshot(), before)

    async def test_revoked_recipient_does_not_disconnect_healthy_sender(self):
        first, second = self.access(), self.access(1)
        client_a, _ = await self.connect(first)
        client_b, _ = await self.connect(second)
        await client_a.join()
        await client_b.join()
        await client_a.read()  # Other player's JOIN.
        first.revoke()
        await client_b.send(b"PRIVMSG #TM0-test :hello")
        await client_b.send(b"PING :survived")
        reply = await client_b.read()
        while b"QUIT" in reply:
            reply = await client_b.read()
        self.assertEqual(reply, b"PONG survived")
        self.assertIsNone(first.game)
        self.assertIs(self.game.nicks[client_b.nick], second.game)

    async def test_revocation_after_profile_header_prevents_body_mutation(self):
        access = self.access()
        client, _ = await self.connect(access)
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", await self.port("profile", access)
        )
        try:
            opening = bytearray(40)
            opening[4] = 1
            writer.write(opening)
            opened = await reader.readexactly(24)
            body = checked(bytes([255]) * 0x24)
            request = bytearray(40)
            request[:3] = bytes((2, 4, 5))
            struct.pack_into("<I", request, 4, len(body))
            account_id, password, _ = self.game.account_specs[access.account_key]
            request[24:] = hashlib.md5(account_id + password + opened[20:24]).digest()
            ready = asyncio.Event()
            remember = self.profile._remember_cipher

            def remembered(*args, **kwargs):
                remember(*args, **kwargs)
                ready.set()

            before = bytes(self.profile.statuses[access.account_key])
            packet = wire.crypt64(client.cipher, client.iv, request + body)
            with patch.object(self.profile, "_remember_cipher", side_effect=remembered):
                writer.write(packet[:40])
                await asyncio.wait_for(ready.wait(), 2)
                access.revoke()
                writer.write(packet[40:])
                await writer.drain()
                self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
            self.assertEqual(bytes(self.profile.statuses[access.account_key]), before)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_cancelled_game_handler_releases_binding_and_socket(self):
        access = self.access()
        reader, writer = await asyncio.open_connection("127.0.0.1", await self.port("game", access))
        try:
            await reader.readline()
            handler = next(iter(self.game.tasks))
            handler.cancel()
            await asyncio.gather(handler, return_exceptions=True)
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
            self.assertIsNone(access.game)
            self.assertFalse(self.game.sessions)
            self.assertFalse(self.game.tasks)
        finally:
            writer.close()
            await writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
