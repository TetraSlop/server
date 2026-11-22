"""A choice the operator disabled is dropped, never a reason to disconnect."""

import tempfile
import unittest

from tests import ROOT
from tests.economy import test_match_prizes_integration as prizes
from tests.economy import test_match_wagers_integration as wagers
from tests.game import test_computer_menu_integration as computer
from tests.support.irc import Client
from tests.support.memory_store import MemoryStore
from tests.game.test_match_service import NATIVE_SETTINGS
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server
from tmserver.protocol.identities import TABLE_RAW, player_id


class DisabledComputerWagerTests(unittest.IsolatedAsyncioTestCase):
    request = computer.ComputerMenuIntegrationTests.request
    menu = computer.ComputerMenuIntegrationTests.menu
    play = computer.ComputerMenuIntegrationTests.play
    asyncTearDown = computer.ComputerMenuIntegrationTests.asyncTearDown
    select = wagers.ComputerWagerIntegrationTests.select

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.store = MemoryStore()
        self.server = Server(store=self.store, allow_wagers=False)
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()

    def assert_no_stake(self):
        match = self.server.default_table.match
        self.assertEqual((match.game_rules["bm"], match.game_rules["du"]), (0, 0))
        self.assertEqual(match.round_stake, 0)
        self.assertEqual(self.server.economy.balance(self.key), 10000)
        self.assertIn(self.client.nick, self.server.nicks)

    async def test_menu_stake_and_double_up_play_without_a_wager(self):
        await self.menu()
        await self.request(TABLE_RAW, packet(0x43, 6, 0, "@ComGame=/Rule=200|0|0|0|2|1|0"))
        self.assert_no_stake()
        await self.select(200)
        self.assertEqual(self.server.default_table.match.phase, "selection")
        self.assert_no_stake()
        await self.play()
        self.assert_no_stake()
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=1/PR=0/Conf=1"))
        self.assertEqual(self.server.default_table.match.phase, "computer_menu")
        await self.select(900, npc=30, double=0)
        self.assertEqual(self.server.default_table.match.phase, "selection")
        self.assert_no_stake()

    async def test_three_player_menu_stake_plays_without_a_wager(self):
        await self.menu()
        await self.request(
            TABLE_RAW, packet(0x43, 6, 3, "@ComGame=/Rule=100|0|0|0|2|1|0|1/Com=1|3")
        )
        match = self.server.default_table.match
        self.assertEqual((match.phase, match.player_count), ("selection", 3))
        self.assert_no_stake()


class DisabledTableWagerTests(unittest.IsolatedAsyncioTestCase):
    player_count = 2
    wagers = False
    asyncSetUp = prizes.PrizeIntegrationTests.asyncSetUp
    asyncTearDown = prizes.PrizeIntegrationTests.asyncTearDown
    flush = prizes.PrizeIntegrationTests.flush
    send = prizes.PrizeIntegrationTests.send
    play = prizes.PrizeIntegrationTests.play
    choose = prizes.PrizeIntegrationTests.choose

    async def test_apply_stake_and_double_up_start_without_a_wager(self):
        for side, client in enumerate(self.clients):
            nn = f"{player_id(client.nick):016X}"
            await self.send(side, packet(0x41, 1, body="@GameEN=/NN=" + nn))
            if side == 0:
                settings = NATIVE_SETTINGS.replace("/bm=0/du=0", "/bm=200/du=1")
                await self.send(side, packet(0x14, body=settings))
                match = self.server.default_table.match
                self.assertTrue(match.settings_applied)
                self.assertEqual((match.game_rules["bm"], match.game_rules["du"]), (0, 0))
            await self.send(side, packet(1, body="@Req=/NN=" + nn))
        for side in range(self.player_count):
            results = await self.send(side, packet(0x41, 8, body="@GameOK="))
        for packets in results:
            init = next(decode(p) for p in packets if b"@VsGameInit=" in p)
            self.assertEqual(init.sections["VsGameInit"]["R"].split("|")[:6:5], ["0", "0"])
            for seat in range(self.player_count):
                self.assertEqual(init.sections[f"No{seat}"]["M"], "10000")
        await self.play()
        await self.choose()
        self.assertEqual([self.server.economy.balance(k) for k in self.keys], [10000] * 2)
        self.assertEqual(len(self.server.nicks), self.player_count)


if __name__ == "__main__":
    unittest.main()
