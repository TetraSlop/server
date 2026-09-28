"""Optional original offline rating over complete encrypted hosted matches."""

import asyncio
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.game import test_computer_menu_integration as computer
from tests.economy import test_match_prizes_integration as prizes
from tests.economy import test_match_wagers_integration as wagers
from tests.network.test_server import Client
from tmserver.rankings.history import HistoryState, classify_result
from tmserver.game.packets import decode, packet
from tmserver.rankings.service import RECORD_SIZE
from tmserver.rankings.stats import PracticeStats
from tmserver.network.server import Server


class RatingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    player_count = 2
    asyncTearDown = prizes.PrizeIntegrationTests.asyncTearDown
    flush = prizes.PrizeIntegrationTests.flush
    send = prizes.PrizeIntegrationTests.send
    admit = wagers.WagerIntegrationTests.admit
    play = prizes.PrizeIntegrationTests.play
    choose = prizes.PrizeIntegrationTests.choose

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.server = Server(
            state_path=self.path,
            match_mode="versus",
            match_player_count=self.player_count,
            account_count=self.player_count,
            card_prizes=True,
            allow_wagers=True,
            rating_mode="local-history",
        )
        self.port = await self.server.start(0)
        self.clients = []
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]

    def assert_ratings(self, history_before):
        match = self.server.default_table.match
        scores = match.observer_snapshot().scores
        for seat, key in enumerate(self.keys):
            state = history_before[seat].next(classify_result(scores, seat))
            stats = PracticeStats.from_profile(self.server.economy.profile_bytes(key))
            self.assertEqual(self.server.ratings.history.state(key), state)
            self.assertEqual((stats.rating, stats.sequence), (state.rating, state.sequence))
            self.assertEqual(stats.vs_player_games, match.completed_matches)
            result = next(decode(p) for p in self.result_messages[seat] if b"@ResultData=" in p)
            values = list(map(int, result.sections[f"No{seat}"]["D"].split("|")))
            self.assertEqual((values[2], values[4]), (state.rating, state.sequence))
            self.assertEqual(result.sections[f"No{seat}"]["T"], f"0|0|{stats.vs_player_games}")
            self.assertEqual(match.players[seat].rating, state.rating)
        rows = self.server.ranking.records(2)
        ratings = [struct.unpack_from("<I", rows, i + 0xA0)[0]
                   for i in range(0, len(rows), RECORD_SIZE)]
        self.assertEqual(len(ratings), self.player_count)
        self.assertEqual(ratings, sorted(ratings))

    async def test_paid_card_prize_rounds_rating_rematch_and_restart(self):
        self.assertEqual(self.server.ranking.records(2), b"")
        await self.admit()
        expected = [HistoryState() for _ in self.keys]
        for _ in range(2):
            await self.play()
            await self.choose()
            self.assert_ratings(expected)
            expected = [self.server.ratings.history.state(k) for k in self.keys]
            for seat in range(self.player_count):
                await self.send(seat, packet(0x43, 17, seat, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.assertEqual(
            sum(self.server.economy.balance(k) for k in self.keys)
            + self.server.default_table.match.round_stake * self.player_count,
            10000 * self.player_count,
        )
        crash = self.path.with_name("restart.json")
        crash.write_bytes(self.path.read_bytes())
        restored = Server(
            state_path=crash, account_count=self.player_count, rating_mode="local-history"
        )
        try:
            self.assertEqual(restored.ratings.snapshot(), self.server.ratings.snapshot())
            self.assertEqual(restored.ranking.records(2), self.server.ranking.records(2))
            for seat, key in enumerate(self.keys):
                stats = PracticeStats.from_profile(restored.economy.profile_bytes(key))
                self.assertEqual((stats.rating, stats.sequence), (expected[seat].rating, 2))
            self.assertEqual(
                sum(restored.economy.balance(k) for k in self.keys), 10000 * self.player_count
            )
        finally:
            await restored.stop()

    async def test_final_save_failure_does_not_persist_rating_or_history(self):
        await self.admit()
        previous = {
            key: PracticeStats.from_profile(self.server.economy.profile_bytes(key))
            for key in self.keys
        }
        original = self.server.persist_changes

        def save(accounts, journals):
            if any(a["results"] for a in journals["ratings"]["history"]["accounts"].values()):
                with patch.object(
                    Path, "replace", side_effect=OSError("injected rated-result save failure")
                ):
                    return original(accounts, journals)
            return original(accounts, journals)

        with patch.object(self.server, "persist_changes", side_effect=save):
            with self.assertRaises((AssertionError, ConnectionError)):
                await self.play()
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["ratings"]["history"]["accounts"], {})
        crash = self.path.with_name("restart.json")
        crash.write_bytes(self.path.read_bytes())
        restored = Server(
            state_path=crash, account_count=self.player_count, rating_mode="local-history"
        )
        try:
            for key in self.keys:
                stats = PracticeStats.from_profile(restored.economy.profile_bytes(key))
                self.assertEqual(stats, previous[key])
                self.assertEqual(restored.economy.balance(key), 10000)
        finally:
            await restored.stop()


class ThreePlayerRatingIntegrationTests(RatingIntegrationTests):
    player_count = 3


class CpuRatingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    request = computer.ComputerMenuIntegrationTests.request
    menu = computer.ComputerMenuIntegrationTests.menu
    play = computer.ComputerMenuIntegrationTests.play
    select = wagers.ComputerWagerIntegrationTests.select
    asyncTearDown = computer.ComputerMenuIntegrationTests.asyncTearDown

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.state_path = Path(self.temp.name) / "state.json"
        self.server = Server(
            state_path=self.state_path, allow_wagers=True, rating_mode="local-history"
        )
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()

    async def test_cpu_rating_and_payout_use_consistent_original_inputs(self):
        await self.menu()
        await self.select(200, npc=30)
        await self.play()
        match = self.server.default_table.match
        scores = match.observer_snapshot().scores
        expected = HistoryState().next(classify_result(scores, 0))
        self.assertEqual(self.server.ratings.history.state(self.key), expected)
        self.assertEqual(
            PracticeStats.from_profile(self.server.economy.profile_bytes(self.key)).rating,
            expected.rating,
        )
        self.assertEqual(
            self.server.match_economy.status(self.server.default_table.match_economy_round)[
                "payouts"
            ][0],
            match.final_cards(self.server.nicks[self.client.nick]).stats.current_reward,
        )


if __name__ == "__main__":
    unittest.main()
