"""Original file-owner bits and encrypted per-event file isolation."""

import asyncio
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.profiles.test_profile_accounts import file_request, transact
from tests.support.irc import Client
from tests.support.memory_store import MemoryStore
from tmserver.events.models import EventConfig, EventListing
from tmserver.events.packets import event_data
from tmserver.protocol.identities import game_id
from tmserver.profiles.location import FileOwner
from tmserver.profiles.service import ProfileServer
from tmserver.profiles.packets import checked
from tmserver.network.server import Server


def event(identifier, raw, label):
    return EventConfig(
        identifier,
        EventListing(b"Local", label, b"Local fixture"),
        event_data(classes=(label, b"Next"), steps_per_class=4),
        1,
        2000000000,
        game_id(raw),
        game_id(raw + 1),
        game_id(raw + 2),
        init_channel=("#" + identifier + "E").encode(),
        card_channel=("#" + identifier + "F").encode(),
    )


def request(path, raw, directory=1, volume=1, size=0x3028):
    data = bytearray(file_request(path, size=size))
    struct.pack_into("<Q", data, 8, FileOwner(raw, directory, volume).encode())
    return checked(data[:-4])


class EventFileOwnerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_owner_selects_correct_event_and_persists_members(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            configs = (event("Alpha", 0x1000, b"First"), event("Beta", 0x1100, b"Second"))
            game = Server(
                store=MemoryStore(),
                event_configs=configs,
                event_clock=lambda: 100,
            )
            profile = ProfileServer(Path(folder) / "profile.jsonl", game)
            client = Client()
            try:
                port = await game.start(0)
                pp = await profile.start(0)
                await client.open(port)
                self.assertIn(b" 422 ", await client.read())
                key = client.nick.decode()
                context = (client.cipher, client.iv)
                game.events.admit_room(key, "Alpha")
                game.events.update_progress(key, "Alpha", "fixture1", steps_delta=5)
                game.persist()
                for config, raw in zip(configs, (0x1000, 0x1100)):
                    error, data = await transact(
                        pp,
                        key,
                        3,
                        0,
                        request("b/g/TM0EventDataList", raw, 127, 65535),
                        context=context,
                    )
                    self.assertEqual(error, 0)
                    self.assertEqual(data[:-4], config.data)
                    error, data = await transact(
                        pp, key, 3, 0, request("b/g/TM0EventMemberList", raw), context=context
                    )
                    self.assertEqual(error, 0)
                    self.assertEqual(
                        struct.unpack_from("<I", data, 4)[0], int(config.id == "Alpha")
                    )
                for raw in (0, 0x1200, 0x1001):
                    error, _ = await transact(
                        pp, key, 3, 0, request("b/g/TM0EventDataList", raw), context=context
                    )
                    self.assertEqual(error, 0x7E)
                restored = Server(
                    store=game.store, event_configs=configs, event_clock=lambda: 100
                )
                try:
                    self.assertEqual(restored.events.snapshot(), game.events.snapshot())
                    self.assertIsNone(restored.events.admission(key))
                finally:
                    await restored.stop()
            finally:
                if hasattr(client, "writer"):
                    await client.close()
                await profile.stop()
                await game.stop()


if __name__ == "__main__":
    unittest.main()
