"""Native Computer route with authoritative CPU card prizes and persistence."""

import asyncio
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tests import ROOT
from tests.game import test_computer_menu_integration as computer
from tests.economy.test_match_prizes_integration import full
from tests.network.test_server import Client
from tmserver.protocol.identities import TABLE_RAW
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server


class CPUPrizeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    request = computer.ComputerMenuIntegrationTests.request
    menu = computer.ComputerMenuIntegrationTests.menu
    play = computer.ComputerMenuIntegrationTests.play
    asyncTearDown = computer.ComputerMenuIntegrationTests.asyncTearDown
    wagers = True

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.state_path = Path(self.temp.name) / "state.json"
        self.server = Server(state_path=self.state_path, allow_wagers=self.wagers, card_prizes=True)
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()

    async def round(self, npc):
        before = self.server.economy.inventory(self.key)
        await self.request(
            TABLE_RAW, packet(0x43, 6, 2, f"@ComGame=/Rule=0|0|0|0|2|0|0|1/Com={npc}")
        )
        await self.play()
        match = self.server.default_table.match
        while match.prize_choice is not None:
            choice = match.prize_choice
            self.assertEqual(choice.winner, 0, "CPU choices must resolve without human input")
            replies = await self.request(
                TABLE_RAW, packet(0x43, 15, 0, f"@GetSelect=/H={choice.options[0].hand_index}")
            )
            self.assertTrue(any(decode(p).subtype == 15 for p in replies))
        self.assertFalse(match.prize_commit_required)
        if not self.wagers:
            self.assertEqual(self.server.economy.balance(self.key), 10000)
            self.assertEqual(match.players[0].result_stats.current_reward, 0)
        settlement = match.final_prizes().settlement
        lost = {a.original_slot for a in settlement.transfers if a.loser == 0}
        kept = [full(c) for i, c in enumerate(match.players[0].updated) if i not in lost] + list(
            map(full, before[5:])
        )
        earned = [full(a.card) for a in settlement.transfers if a.winner == 0]
        self.assertEqual(Counter(map(full, self.server.economy.inventory(self.key))), Counter(kept))
        self.assertEqual(
            self.server.result_rewards.status(self.key)["working_count"], len(kept) + len(earned)
        )
        return match, kept, earned, settlement

    async def test_two_original_opponents_settle_choices_and_reconfigure(self):
        await self.menu()
        transfers = []
        for npc in (1, 30):
            match, kept, earned, settlement = await self.round(npc)
            transfers.extend(settlement.transfers)
            await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=1/PR=0/Conf=1"))
            self.assertEqual(match.phase, "computer_menu")
            self.assertEqual(
                Counter(map(full, self.server.economy.inventory(self.key))), Counter(kept + earned)
            )
        self.assertTrue(transfers, "fixture must exercise an actual CPU card transfer")
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
            self.assertFalse(restored.auction_ledger.pending(self.key))
        finally:
            await restored.stop()

    async def test_crash_before_continue_recovers_only_human_prizes(self):
        await self.menu()
        match, kept, earned, settlement = await self.round(1)
        crash = self.state_path.with_name("crash.json")
        crash.write_bytes(self.state_path.read_bytes())
        restored = Server(state_path=crash)
        try:
            self.assertEqual(
                Counter(map(full, restored.economy.inventory(self.key))), Counter(kept)
            )
            self.assertEqual(
                Counter(full(p.card) for p in restored.auction_ledger.pending(self.key)),
                Counter(earned),
            )
            self.assertEqual(set(restored.account_keys), {self.key})
        finally:
            await restored.stop()


class CPUPrizesWithoutMoneyTests(CPUPrizeIntegrationTests):
    wagers = False


if __name__ == "__main__":
    unittest.main()
