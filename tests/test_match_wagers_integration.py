"""Opt-in wager deduction, coherent ResultData and recovery over TCP."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests import test_computer_menu_integration as computer
from tests import test_match_prizes_integration as prizes
from tests.test_match_service import NATIVE_SETTINGS
from tests.test_server import Client
from tmserver.identities import TABLE_RAW, player_id
from tmserver.match_economy import offline_payout
from tmserver.match_service import decode, packet
from tmserver.result_stats import PracticeStats
from tmserver.server import Server


class WagerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    player_count = 2
    wagers = True
    asyncSetUp = prizes.PrizeIntegrationTests.asyncSetUp
    asyncTearDown = prizes.PrizeIntegrationTests.asyncTearDown
    flush = prizes.PrizeIntegrationTests.flush
    send = prizes.PrizeIntegrationTests.send
    play = prizes.PrizeIntegrationTests.play
    choose = prizes.PrizeIntegrationTests.choose

    async def admit(self):
        for side, client in enumerate(self.clients):
            nn = f"{player_id(client.nick):016X}"
            await self.send(side, packet(0x41, 1, body="@GameEN=/NN=" + nn))
            if side == 0:
                settings = NATIVE_SETTINGS.replace("/bm=0/du=0", "/bm=200/du=1")
                for key, value in self.server.match.special_flags.items():
                    settings = settings.replace(f"/{key}=0", f"/{key}={value}")
                await self.send(side, packet(0x14, body=settings))
            await self.send(side, packet(1, body="@Req=/NN=" + nn))
        self.assertEqual(
            [self.server.economy.balance(k) for k in self.keys], [10000] * self.player_count
        )
        for side in range(self.player_count):
            results = await self.send(side, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(
            [self.server.economy.balance(k) for k in self.keys], [9800] * self.player_count
        )
        for packets in results:
            init = next(decode(p) for p in packets if b"@VsGameInit=" in p)
            for seat in range(self.player_count):
                self.assertEqual(init.sections[f"No{seat}"]["M"], "9800")

    async def test_pot_result_double_up_and_abort_refund(self):
        await self.admit()
        await self.play()
        await self.choose()
        match = self.server.match
        completed = self.server.match_economy.status(self.server.match_economy_round)
        payouts = completed["payouts"]
        self.assertEqual(sum(payouts.values()), 200 * self.player_count)
        after = [9800 + payouts[s] for s in range(self.player_count)]
        self.assertEqual([self.server.economy.balance(k) for k in self.keys], after)
        for seat, key in enumerate(self.keys):
            result = next(decode(p) for p in self.result_messages[seat] if b"@ResultData=" in p)
            self.assertEqual(int(result.sections[f"No{seat}"]["D"].split("|")[1]), payouts[seat])
            stats = PracticeStats.from_profile(self.server.economy.profile_bytes(key))
            tied = len(set(match.observer_snapshot().scores)) == 1
            self.assertEqual(stats.reward_sum, 0 if tied else payouts[seat])
        for seat in range(self.player_count):
            await self.send(seat, packet(0x43, 17, seat, "@Continue=/Ans=1/PR=0/Conf=0"))
            if seat < self.player_count - 1:
                self.assertEqual([self.server.economy.balance(k) for k in self.keys], after)
        self.assertEqual(match.round_stake, 400)
        self.assertEqual(
            [self.server.economy.balance(k) for k in self.keys], [v - 400 for v in after]
        )
        await self.clients[0].close()
        for client in self.clients[1:]:
            self.assertEqual(await client.read(), b"")
        self.assertEqual([self.server.economy.balance(k) for k in self.keys], after)
        restored = Server(state_path=self.path)
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
        finally:
            await restored.stop()

    async def test_restart_mid_round_refunds_every_stake_once(self):
        await self.admit()
        crash = self.path.with_name("crash.json")
        crash.write_bytes(self.path.read_bytes())
        restored = Server(state_path=crash)
        try:
            self.assertEqual(
                [restored.economy.balance(k) for k in self.keys], [10000] * self.player_count
            )
            again = Server(state_path=crash)
            try:
                self.assertEqual(again.economy.snapshot(), restored.economy.snapshot())
            finally:
                await again.stop()
        finally:
            await restored.stop()


class ThreeSeatWagerIntegrationTests(WagerIntegrationTests):
    player_count = 3


class ComputerWagerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    request = computer.ComputerMenuIntegrationTests.request
    menu = computer.ComputerMenuIntegrationTests.menu
    play = computer.ComputerMenuIntegrationTests.play
    asyncTearDown = computer.ComputerMenuIntegrationTests.asyncTearDown

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.state_path = Path(self.temp.name) / "state.json"
        self.server = Server(state_path=self.state_path, allow_wagers=True)
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()

    async def select(self, stake, npc=1, double=1):
        await self.request(
            TABLE_RAW, packet(0x43, 6, 2, f"@ComGame=/Rule={stake}|0|0|0|2|{double}|0|1/Com={npc}")
        )

    async def test_menu_debits_only_confirmation_and_pays_original_formula(self):
        await self.menu()
        self.assertEqual(self.server.economy.balance(self.key), 10000)
        await self.request(TABLE_RAW, packet(0x43, 6, 0, "@ComGame=/Rule=200|0|0|0|2|1|0"))
        self.assertEqual(self.server.economy.balance(self.key), 10000)
        await self.select(200)
        self.assertEqual(self.server.economy.balance(self.key), 9800)
        await self.play()
        match = self.server.match
        reward = max(
            0,
            offline_payout(
                match.observer_snapshot().scores, tuple(p.rating for p in match.players), 200
            ),
        )
        self.assertEqual(match.players[0].result_stats.current_reward, reward)
        self.assertEqual(self.server.economy.balance(self.key), 9800 + reward)
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=1/PR=0/Conf=1"))
        self.assertEqual(match.phase, "computer_menu")
        self.assertEqual(self.server.economy.balance(self.key), 9800 + reward)
        await self.select(900, npc=30, double=0)
        self.assertEqual(self.server.economy.balance(self.key), 8900 + reward)
        crash = self.state_path.with_name("crash.json")
        crash.write_bytes(self.state_path.read_bytes())
        restored = Server(state_path=crash)
        try:
            self.assertEqual(restored.economy.balance(self.key), 9800 + reward)
        finally:
            await restored.stop()


if __name__ == "__main__":
    unittest.main()
