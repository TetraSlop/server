"""Three-player WatchInfo/WatchCard consumers and encrypted observer isolation."""

import asyncio
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests import test_three_player_integration as versus
from tests.test_match_service import CARDS, select
from tests.test_profile_accounts import file_request, transact
from tests.test_server import Client
from tmserver.identities import TABLE_RAW, player_id, service_nick
from tmserver.local_accounts import account_specs
from tmserver.match_service import Card, MatchService, decode, packet
from tmserver.native_special_rules import Cell
from tmserver.profile_service import ProfileServer
from tmserver.room_service import TABLE_CHANNEL
from tmserver.run_server import arguments
from tmserver.server import Server
from tmserver.spectator_service import SpectatorService, bootstrap


class ThreePlayerSpectatorIntegration(unittest.IsolatedAsyncioTestCase):
    flush = versus.ThreePlayerIntegrationTests.flush
    send = versus.ThreePlayerIntegrationTests.send
    admit = versus.ThreePlayerIntegrationTests.admit
    round = versus.ThreePlayerIntegrationTests.round

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.temp.name) / "state.json"
        self.server = Server(
            state_path=self.path,
            account_count=4,
            match_mode="versus",
            match_player_count=3,
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        self.port = await self.server.start(0)
        self.clients = []
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients[:3]]
        self.target = service_nick(TABLE_RAW)

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def read_watch(self):
        line = await self.clients[3].read()
        self.assertTrue(line)
        self.assertTrue(line.startswith(b":" + self.target + b"!p@local NOTICE "))
        return line.split(b" :", 1)[1]

    async def test_fourth_account_watches_full_three_player_round_and_profile(self):
        await self.admit()
        observer = self.clients[3]
        key = observer.nick.decode()
        before = self.server.economy.profile_bytes(key)
        await observer.send(
            b"NOTICE " + self.target + b" :" + packet(0x2D, body="@CheckJoinTable=")
        )
        self.assertEqual(
            decode(await self.read_watch()).sections["CheckJoinTable_Ans"],
            {"Join": "1", "Mem": "0"},
        )
        await observer.join(TABLE_CHANNEL)
        await observer.send(
            b"NOTICE "
            + self.target
            + b" :"
            + packet(1, body=f"@Req=/NN={player_id(observer.nick):016X}/HID=4")
        )
        self.assertEqual(decode(await self.read_watch()).primary, 2)
        await observer.send(
            b"NOTICE " + self.target + b" :" + packet(0x43, 0x25, 0, "@Data=/Watch=0")
        )
        boot = decode(await self.read_watch())
        self.assertEqual((boot.argument, boot.sections["WatchInfo"]["N"]), (0, "3"))
        self.assertFalse(self.server.economy.is_busy(key))
        await self.round()
        live = []
        for _ in range(200):
            msg = decode(await self.read_watch())
            live.append(msg)
            if msg.subtype == 13:
                break
        else:
            self.fail("observer never received final result")
        self.assertEqual([m.argument for m in live if m.subtype == 9], list(range(15)))
        self.assertEqual([m.argument for m in live if m.subtype == 10], list(range(15)))
        self.assertFalse(any(m.subtype == 0x1A for m in live))
        self.assertEqual(self.server.economy.profile_bytes(key), before)
        profile = ProfileServer(Path(self.temp.name) / "profile.jsonl", self.server)
        port = await profile.start(0)
        try:
            row = account_specs(4)[key]
            error, data = await transact(
                port,
                key,
                3,
                0,
                file_request(),
                context=(observer.cipher, observer.iv),
                account_id=row[0],
            )
            self.assertEqual(error, 0)
            self.assertEqual(data[:-4], before)
        finally:
            await profile.stop()
        await observer.send(
            b"NOTICE " + self.target + b" :" + packet(0x43, 9, 15, "@TurnData=/Ans=3")
        )
        self.assertEqual(await observer.read(), b"")
        self.assertTrue(all(not self.server.nicks[c.nick].closed for c in self.clients[:3]))

    def test_four_accounts_option_survives_three_player_auto_enrollment(self):
        args = arguments(["--mode", "versus", "--players", "3", "--accounts", "4"])
        self.assertEqual(args.accounts, 4)


if __name__ == "__main__":
    unittest.main()
