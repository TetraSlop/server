"""LOGIN over the game port mints the game password polcore then uses on both ports."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.accounts.test_provisioning import KEY, POL_ID, granted
from tests.profiles.test_profile_accounts import file_request, transact
from tests.support.irc import Client
from tests.support.memory_store import MemoryStore
from tmserver.economy.packets import named
from tmserver.network.server import Server
from tmserver.protocol.identities import ROOM_RAW, player_id, service_nick
from tmserver.protocol.wire import polid_from_string
from tmserver.profiles.service import ProfileServer


class LoginTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = folder / "game.jsonl"
        self.host = Server(self.log, store=MemoryStore())
        self.addAsyncCleanup(self.host.stop)
        self.profile = ProfileServer(folder / "profile.jsonl", self.host)
        self.addAsyncCleanup(self.profile.stop)
        self.port = await self.host.start(0)
        self.profile_port = await self.profile.start(0)

    async def test_login_then_game_and_encrypted_profile_with_the_game_password(self):
        reply, closed = await Client().login(self.port, POL_ID, b"my password")
        game_password, outcome = granted(reply)
        self.assertEqual((outcome, closed), (b"created", b""))
        client = Client()
        await client.open(self.port, KEY.encode(), game_password)
        try:
            self.assertIn(b" 422 ", await client.read())
            error, data = await transact(
                self.profile_port, KEY, 3, 0, file_request(), context=(client.cipher, client.iv),
                password=game_password, account_id=POL_ID,
            )
            self.assertEqual((error, len(data)), (0, 0x302C))
            # Init80 with the identities polcore derives from the POL ID alone.
            init = named(0x80, 0, 0, (
                f"@Init=/NN={player_id(KEY.encode()):016X}/HID=0/Dm=0/Vol=0/CN={POL_ID.hex()}"
                f"/GID={polid_from_string(POL_ID):016X}/L=1/GLD=0"))
            await client.send(b"NOTICE " + service_nick(ROOM_RAW) + b" :" + init)
            self.assertTrue((await client.read()).endswith(b"@InitAns=/Ans=1"))
        finally:
            await client.close()
        log = self.log.read_text()
        self.assertNotIn("my password", log)
        self.assertNotIn(game_password.decode(), log)
        # The player's own password never works as a game password.
        refused = Client()
        await refused.open(self.port, KEY.encode(), b"my password")
        self.assertEqual(await refused.read(), b"")
        await refused.close()

    async def test_a_second_line_or_a_bad_login_gets_one_error_then_closes(self):
        reply, closed = await Client().login(self.port, b"EEEEEEEF", b"tm-local-test")
        self.assertEqual((reply, closed), (b":srv 901 pol_id_unavailable :login refused", b""))

    async def test_built_in_accounts_sign_in_from_loopback_only(self):
        game_password, _ = granted((await Client().login(self.port, POL_ID, b"pw"))[0])
        with patch("tmserver.network.session.loopback", return_value=False), \
                patch("tmserver.profiles.service.loopback", return_value=False):
            fixture = Client()
            await fixture.open(self.port)
            self.assertEqual(await fixture.read(), b"")
            await fixture.close()
            self.assertEqual((await transact(self.profile_port, "UGITW5T4U", 0, 9))[0], 0xE4)
            registered = Client()
            await registered.open(self.port, KEY.encode(), game_password)
            try:
                self.assertIn(b" 422 ", await registered.read())
            finally:
                await registered.close()


if __name__ == "__main__":
    unittest.main()
