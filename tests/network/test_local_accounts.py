"""Optional third synthetic account uses the same identity on both transports."""

import asyncio
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from tests import ROOT
from tests.profiles.test_profile_accounts import file_request, transact
from tests.network.test_server import Client
from tmserver.protocol.identities import player_id
from tmserver.network.local_accounts import MAX_ACCOUNTS, account_name, account_specs
from tmserver.protocol.wire import polid_from_string, polid_unscramble
from tmserver.profiles.service import ProfileServer
from tmserver.cli.serve import arguments, run
from tmserver.network.server import Server


class ThirdAccountTests(unittest.IsolatedAsyncioTestCase):
    async def test_nine_accounts_hold_game_connections_and_read_profiles_concurrently(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            game = Server(account_count=9)
            profile = ProfileServer(Path(folder) / "profile.jsonl", game)
            clients = []
            try:
                port = await game.start(0)
                profile_port = await profile.start(0)
                for key in game.accounts:
                    client = Client()
                    clients.append(client)
                    await client.open(port, account=key)
                    self.assertIn(b" 422 ", await client.read())
                self.assertEqual(len(game.sessions), 9)
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
                finally:
                    writer.close()
                    await writer.wait_closed()
                replies = await asyncio.gather(
                    *(
                        transact(
                            profile_port,
                            c.nick.decode(),
                            3,
                            0,
                            file_request(),
                            account_id=account_name(i + 1).encode(),
                            context=(c.cipher, c.iv),
                        )
                        for i, c in enumerate(clients)
                    )
                )
                for i, (error, data) in enumerate(replies):
                    self.assertEqual(error, 0)
                    self.assertEqual(struct.unpack_from("<I", data, 4)[0], i + 1)
                for client in clients:
                    await client.send(b"PING :pool-capacity")
                    self.assertEqual(await client.read(), b"PONG pool-capacity")
            finally:
                for client in clients:
                    await client.close()
                await profile.stop()
                await game.stop()

    async def test_highest_local_account_has_matching_encrypted_game_and_profile(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            game = Server(account_count=MAX_ACCOUNTS, state_path=Path(folder) / "state.json")
            profile = ProfileServer(Path(folder) / "profile.jsonl", game)
            client = Client()
            try:
                port = await game.start(0)
                profile_port = await profile.start(0)
                key = list(account_specs(MAX_ACCOUNTS))[-1]
                await client.open(port, account=key.encode())
                self.assertIn(b" 422 ", await client.read())
                error, data = await transact(
                    profile_port,
                    key,
                    3,
                    0,
                    file_request(),
                    account_id=account_name(MAX_ACCOUNTS).encode(),
                    context=(client.cipher, client.iv),
                )
                self.assertEqual(error, 0)
                self.assertEqual(
                    struct.unpack_from("<IQ", data, 4), (MAX_ACCOUNTS, player_id(client.nick))
                )
                self.assertEqual(data[0x10:0x20].split(b"\0")[0], b"LocalPlayer128")
                game.persist()
                restored = Server(account_count=MAX_ACCOUNTS, state_path=game.state_path)
                try:
                    self.assertEqual(
                        restored.economy.profile_bytes(key), game.economy.profile_bytes(key)
                    )
                finally:
                    await restored.stop()
            finally:
                if hasattr(client, "writer"):
                    await client.close()
                await profile.stop()
                await game.stop()

    async def test_cli_lists_pool_without_creating_state_or_listeners(self):
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            args = arguments(
                [
                    "--accounts",
                    "6",
                    "--list-accounts",
                    "--log-dir",
                    folder,
                    "--state",
                    str(Path(folder) / "state.json"),
                ]
            )
            stream = StringIO()
            with redirect_stdout(stream):
                await run(args)
            self.assertEqual(len(stream.getvalue().splitlines()), 6)
            self.assertIn("EEEEEEEY", stream.getvalue())
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_explicit_third_account_game_and_encrypted_profile(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            game = Server(account_count=3)
            profile = ProfileServer(Path(folder) / "profile.jsonl", game)
            client = Client()
            try:
                port = await game.start(0)
                profile_port = await profile.start(0)
                key = list(account_specs(3))[-1]
                await client.open(port, account=key.encode())
                self.assertIn(b" 422 ", await client.read())
                error, data = await transact(
                    profile_port,
                    key,
                    3,
                    0,
                    file_request(),
                    account_id=b"EEEEEEEA",
                    context=(client.cipher, client.iv),
                )
                self.assertEqual(error, 0)
                self.assertEqual(struct.unpack_from("<IQ", data, 4), (3, player_id(client.nick)))
                self.assertEqual(data[0x10:0x20].split(b"\0")[0], b"LocalPlayer3")
                error, characters = await transact(
                    profile_port,
                    key,
                    1,
                    3,
                    account_id=b"EEEEEEEA",
                    context=(client.cipher, client.iv),
                )
                self.assertEqual(error, 0)
                self.assertEqual(struct.unpack_from("<I", characters, 20)[0], 3)
                self.assertEqual(len(profile.accounts), 3)
            finally:
                if hasattr(client, "writer"):
                    await client.close()
                await profile.stop()
                await game.stop()


class AccountPoolTests(unittest.TestCase):
    def test_fixture_ids_are_stable_unique_and_below_service_routes(self):
        specs = account_specs(MAX_ACCOUNTS)
        self.assertEqual(len(specs), MAX_ACCOUNTS)
        self.assertEqual(
            [v[0] for v in list(specs.values())[:4]],
            [b"EEEEEEEF", b"EEEEEEEK", b"EEEEEEEA", b"EEEEEEEO"],
        )
        for nick, (login, _, character) in specs.items():
            self.assertEqual(polid_from_string(login.decode()), character)
            self.assertEqual(polid_unscramble(nick.encode()), character)
        self.assertEqual(account_specs(), dict(list(specs.items())[:2]))

    def test_pool_limits_and_cli_three_player_preserve_larger_count(self):
        for count in (True, 1, 129, 2.0):
            with self.assertRaises(ValueError):
                account_specs(count)
        with redirect_stderr(StringIO()):
            for count in ("1", "129"):
                with self.assertRaises(SystemExit):
                    arguments(["--accounts", count])
        self.assertEqual(
            arguments(["--mode", "versus", "--players", "3", "--accounts", "8"]).accounts, 8
        )


if __name__ == "__main__":
    unittest.main()
