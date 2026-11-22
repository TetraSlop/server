"""Failure injection: no success reply or stranded identities after disk errors."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import ROOT
from tests.profiles.test_options_service import request
from tests.support.irc import Client
from tmserver.protocol.identities import SHOP_RAW, player_id, service_nick
from tests.support.irc import ACCOUNTS
from tmserver.network.server import Server
from tmserver.network.session import Session


class Writer:
    def __init__(self):
        self.closed = False

    def get_extra_info(self, name):
        return ("127.0.0.1", 1234)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class StorageFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_always_releases_socket_identity_and_membership(self):
        server = Server()
        writer = Writer()
        peer = Session(server, None, writer, 1)
        peer.nick = next(iter(ACCOUNTS))
        server.ensure_account(peer.nick)
        server.nicks[peer.nick] = peer
        server.sessions.add(peer)
        server.default_table.match.register(peer)
        key = peer.nick.decode()
        server.default_table.match_reservations[peer] = server.economy.reserve_match(key)
        server.room.set_table(0, 1, player_id(peer.nick))
        peer.channels.add(b"#test")
        server.channels[b"#test"] = {peer}
        server.store = Mock()
        server.store.save_changes.side_effect = OSError("injected disk failure")
        before = server.economy.snapshot()
        await peer.close()
        server.store.save_changes.assert_called_once()
        self.assertIsNotNone(server.storage_failed)
        self.assertTrue(peer.closed and writer.closed)
        self.assertNotIn(peer.nick, server.nicks)
        self.assertNotIn(peer, server.sessions)
        self.assertFalse(server.channels)
        await server.stop()
        self.assertEqual(server.default_table.match.players, [])
        self.assertFalse(server.economy.is_reserved(key))
        self.assertEqual(server.economy.snapshot(), before)

    async def test_failed_replace_sends_no_ack_stops_mutations_and_preserves_save(self):
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            path = Path(folder) / "state.json"
            server = Server(state_path=path)
            port = await server.start(0)
            client = Client()
            await client.open(port)
            await client.read()
            before = path.read_bytes()
            try:
                with patch.object(Path, "replace", side_effect=OSError("injected replace failure")):
                    await client.send(
                        b"NOTICE "
                        + service_nick(SHOP_RAW)
                        + b" :"
                        + request(nn=player_id(client.nick))
                    )
                    self.assertEqual(await client.read(), b"")
                    await server.stop()
                self.assertIsNotNone(server.storage_failed)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(server.nicks or server.sessions)
                with self.assertRaises(OSError):
                    server.persist()
                restored = Server(state_path=path)
                try:
                    self.assertEqual(restored.economy.profile_bytes(client.nick.decode())[0x126], 0)
                finally:
                    await restored.stop()
            finally:
                await client.close()
                await server.stop()


if __name__ == "__main__":
    unittest.main()
