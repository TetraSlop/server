"""Replay unchanged application commands from a completed CPU match capture.

Fresh encrypted authentication replaces only the expired connection handshake.
Archived profile supplies the original inventory. Replay ends after selection;
later moves depend on the historical server's older AI and random policy.
"""

import asyncio
import json
import struct
import tempfile
import unittest
from pathlib import Path

from tests import FIXTURES, ROOT
from tests.support.irc import Client
from tmserver.economy.models import PROFILE_SIZE
from tmserver.cards.model import Card
from tmserver.protocol.identities import player_id
from tmserver.game.packets import decode
from tmserver.network.server import Server

FIXTURE = FIXTURES / "native_selection_replay.json"


def captured_profile():
    fixture = json.loads(FIXTURE.read_text(encoding="utf8"))
    data = bytes.fromhex(fixture["profile_hex"])
    assert len(data) == PROFILE_SIZE
    return data


def captured_start():
    fixture = json.loads(FIXTURE.read_text(encoding="utf8"))
    return [command.encode("latin1") for command in fixture["commands"]]


class NativeCaptureReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_native_room_table_chat_and_selection(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            server = Server(state_path=Path(folder) / "state.json")
            client = Client()
            profile = captured_profile()
            key = next(iter(server.accounts)).decode()
            count = struct.unpack_from("<H", profile, 0x3C)[0]
            cards = [
                Card(*struct.unpack_from("<H7B3x", profile, 0x148 + 12 * i)) for i in range(count)
            ]
            server.economy.provision(
                key,
                nn=player_id(key.encode()),
                player_id=1,
                name=b"LocalPlayer",
                guild=profile[0x3B],
                balance=struct.unpack_from("<i", profile, 0x34)[0],
                cards=cards,
                profile_template=profile,
            )
            server.account_keys.add(key)
            try:
                await client.open(await server.start(0))
                self.assertIn(b" 422 ", await client.read())
                received = []
                commands = captured_start()
                for index, command in enumerate(commands):
                    await client.send(command)
                    await client.send(b"PING :captured-barrier")
                    for _ in range(150):
                        line = await client.read()
                        self.assertTrue(
                            line, f"client disconnected on captured application command {index}"
                        )
                        if line == b"PONG captured-barrier":
                            break
                        if b" :GTM0G" in line:
                            received.append(decode(line.split(b" :", 1)[1]))
                    else:
                        self.fail("capture replay response limit")
                self.assertEqual(server.default_table.match.phase, "turn")
                self.assertEqual(server.default_table.match.players[0].character_id, 1)
                self.assertEqual(len(server.default_table.match.players[0].hand), 5)
                self.assertTrue(
                    any(r.primary == 2 and r.sections["PLAYACK"]["EN"] == "1" for r in received)
                )
                self.assertTrue(any(r.primary == 0x43 and r.subtype == 8 for r in received))
                self.assertEqual(len(server.default_table.match_reservations), 1)
            finally:
                if hasattr(client, "writer"):
                    await client.close()
                await server.stop()


if __name__ == "__main__":
    unittest.main()
