"""Two original NPCs, real encrypted play and durable human-only settlement."""

import asyncio
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.game import test_computer_menu_integration as computer
from tests.economy.test_match_prizes_integration import full
from tests.network.test_server import Client
from tmserver.protocol.identities import ROOM_RAW, TABLE_RAW, player_id, service_nick
from tmserver.economy.match_ledger import offline_payout
from tmserver.game.match import decode, packet
from tmserver.game.rules import CRTLCG
from tmserver.game.special_rules import choose_special_move
from tmserver.rankings.stats import PracticeStats
from tmserver.rooms.service import ROOM_ID
from tmserver.network.server import Server
from tmserver.protocol.commands import Command, encode


class ThreePlayerComputerIntegration(unittest.IsolatedAsyncioTestCase):
    server_options = dict(allow_wagers=True, card_prizes=True, rating_mode="local-history")
    menu = computer.ComputerMenuIntegrationTests.menu
    asyncTearDown = computer.ComputerMenuIntegrationTests.asyncTearDown

    async def request(self, raw, payload):
        replies = await computer.ComputerMenuIntegrationTests.request(self, raw, payload)
        # Room admission also subscribes to roster deltas, independently of the
        # named replies consumed by the Computer menu and battle scenes.
        return [reply for reply in replies if not reply.startswith(b"GTM0L")]

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.state_path = Path(self.temp.name) / "state.json"
        self.server = Server(state_path=self.state_path, **self.server_options)
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()
        self.other = self.server.ensure_account(list(self.server.accounts)[1])
        self.other_before = self.server.snapshot()["accounts"][self.other]
        fields = [
            f"0x{player_id(self.client.nick):016X}".encode(),
            b"1",
            b"0",
            b"0",
            b"LocalPlayer",
            b"2",
            b"0",
            b"0",
            b"0",
            f"0x{ROOM_ID:016X}".encode(),
            b"LocalPlayer    LAAAABAGDBAABIAB",
        ]
        await self.request(ROOM_RAW, b"GTM0L" + encode([Command(b"DE", b"\x06".join(fields))]))

    async def choose(self, opponents, *, stake=100, double=0, special=0):
        balance = self.server.economy.balance(self.key)
        body = (
            f"@ComGame=/Rule={stake}|{special}|{special}|{special}|2|{double}|0|1/Com="
            + "|".join(map(str, opponents))
        )
        replies = await self.request(TABLE_RAW, packet(0x43, 6, len(opponents) + 1, body))
        self.assertFalse(any(b"@StartData=" in r for r in replies))
        match = self.server.default_table.match
        self.assertEqual(match.player_count, len(opponents) + 1)
        self.assertEqual(tuple(p.npc_id for p in match.players[1:]), opponents)
        self.assertEqual(self.server.economy.balance(self.key), balance - stake)
        self.assertEqual(self.server.room.table_count, len(opponents) + 1)
        self.assertEqual(len(self.server.room.npcs), len(opponents))
        self.assertEqual(
            set(self.server.default_table.match_reservations), {self.server.nicks[self.client.nick]}
        )
        return replies

    async def play(self, *, strategic=False):
        match = self.server.default_table.match
        # A separate deterministic client strategy gives the recovery fixture
        # a real human win without changing the server's random stream/cards.
        choices = CRTLCG(4)
        before = self.server.economy.inventory(self.key)
        stats_before = PracticeStats.from_profile(self.server.economy.profile_bytes(self.key))
        ratings = tuple(p.rating for p in match.players)
        balance = self.server.economy.balance(self.key)
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(before[:5])
        )
        received = await self.request(TABLE_RAW, packet(0x43, 7, 0, body))
        start = next(decode(r).sections["StartData"] for r in received if b"@StartData=" in r)
        self.assertEqual(len(start["F"].split("|")), (match.player_count + 2) ** 2)
        self.assertEqual(len(start["L"].split("|")), match.player_count)
        turns = set()
        for _ in range(60):
            if match.phase == "result":
                break
            turns.add(match.active)
            received += await self.request(
                TABLE_RAW, packet(0x43, 9, match.sequence, "@TurnData=/Ans=0")
            )
            if match.phase == "turn" and match.active == 0:
                hand, field = 0, match.legal_fields()[0]
                if strategic:
                    move = choose_special_move(
                        match.players[0].hand, match.observer_snapshot().board, 0, choices
                    )
                    hand, field = move.hand_index, move.field
                received += await self.request(
                    TABLE_RAW,
                    packet(0x43, 10, match.sequence, f"@PutData=/P=0/H={hand}/F={field}"),
                )
            while match.phase == "battle":
                self.assertEqual(match.active, 0, "CPU battle choices must run without human input")
                received += await self.request(
                    TABLE_RAW,
                    packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}"),
                )
        self.assertEqual(match.phase, "result")
        self.assertEqual(turns, set(range(match.player_count)))
        received += await self.request(
            TABLE_RAW, packet(0x43, 14, 5 * match.player_count, "@Ready=/Ans=0")
        )
        for _ in range(3):
            choice = match.prize_choice
            if choice is None:
                break
            self.assertEqual(choice.winner, 0, "NPC prize choices must not strand the human result")
            received += await self.request(
                TABLE_RAW, packet(0x43, 15, 0, f"@GetSelect=/H={choice.options[0].hand_index}")
            )
        self.assertIsNone(match.prize_choice)
        self.assertFalse(match.prize_commit_required)
        result = next(decode(r).sections for r in received if b"@ResultData=" in r)
        self.assertEqual(int(result["ResultData"]["P"]), match.player_count)
        reward = (
            max(0, offline_payout(match.observer_snapshot().scores, ratings, match.round_stake))
            if match.allow_wagers
            else 0
        )
        self.assertEqual(int(result["No0"]["D"].split("|")[1]), reward)
        self.assertEqual(self.server.economy.balance(self.key), balance + reward)
        stats = PracticeStats.from_profile(self.server.economy.profile_bytes(self.key))
        self.assertEqual(stats.vs_player_games, stats_before.vs_player_games)
        self.assertEqual(stats.sequence, (stats_before.sequence + 1) & 0xFFFF)
        transfers = match.final_prizes().settlement.transfers if match.card_prizes else ()
        lost = {award.original_slot for award in transfers if award.loser == 0}
        kept = [full(c) for i, c in enumerate(match.players[0].updated) if i not in lost]
        kept += list(map(full, before[5:]))
        earned = [full(award.card) for award in transfers if award.winner == 0]
        self.assertEqual(Counter(map(full, self.server.economy.inventory(self.key))), Counter(kept))
        self.assertEqual(
            self.server.result_rewards.status(self.key)["working_count"], len(kept + earned)
        )
        self.assertEqual(self.server.snapshot()["accounts"][self.other], self.other_before)
        return kept, earned, received

    async def continue_game(self, *, conf=0, leave=False):
        replies = await self.request(
            TABLE_RAW,
            packet(0x43, 17, 0, f"@Continue=/Ans={2 if leave else 1}/PR=0/Conf={conf}"),
        )
        self.assertTrue(any(b"@ComList=" in p if conf else b"@Continue=" in p for p in replies))

    async def test_two_npcs_rematch_and_two_three_seat_reconfiguration(self):
        await self.menu()
        await self.choose((1, 3), double=1, special=1)
        kept, earned, _ = await self.play()
        old_hands = tuple(tuple(p.selected) for p in self.server.default_table.match.players[1:])
        before = self.server.economy.balance(self.key)
        await self.continue_game()
        self.assertEqual(self.server.economy.balance(self.key), before - 200)
        self.assertEqual(
            Counter(map(full, self.server.economy.inventory(self.key))), Counter(kept + earned)
        )
        self.assertNotEqual(
            tuple(tuple(p.selected) for p in self.server.default_table.match.players[1:]), old_hands
        )
        await self.play()
        before = self.server.economy.balance(self.key)
        await self.continue_game(conf=1)
        self.assertEqual(self.server.economy.balance(self.key), before)
        await self.choose((1,))
        await self.play()
        await self.continue_game(conf=1)
        await self.choose((1, 30))
        kept, earned, _ = await self.play()
        await self.continue_game(leave=True)
        self.assertEqual(self.server.default_table.match.phase, "finished")
        self.assertFalse(self.server.room.npcs)
        self.assertFalse(self.server.economy.is_reserved(self.key))
        self.assertEqual(
            Counter(map(full, self.server.economy.inventory(self.key))), Counter(kept + earned)
        )
        restored = Server(state_path=self.state_path, rating_mode="local-history")
        try:
            self.assertEqual(restored.snapshot(), self.server.snapshot())
            self.assertEqual(set(restored.account_keys), {self.key, self.other})
        finally:
            await restored.stop()

    async def test_three_seat_interrupted_stake_recovers_only_once(self):
        await self.menu()
        await self.choose((1, 30), stake=200)
        crash = self.state_path.with_name("interrupted.json")
        crash.write_bytes(self.state_path.read_bytes())
        restored = Server(state_path=crash, rating_mode="local-history")
        try:
            self.assertEqual(restored.economy.balance(self.key), 10000)
            self.assertEqual(restored.snapshot()["accounts"][self.other], self.other_before)
            self.assertEqual(set(restored.account_keys), {self.key, self.other})
            again = Server(state_path=crash, rating_mode="local-history")
            try:
                self.assertEqual(again.snapshot(), restored.snapshot())
            finally:
                await again.stop()
        finally:
            await restored.stop()

    async def test_completed_three_seat_rewards_survive_lost_continue(self):
        await self.menu()
        await self.choose((1, 3))
        kept, earned, _ = await self.play(strategic=True)
        self.assertTrue(earned, "the interrupted result must contain an actual human award")
        crash = self.state_path.with_name("unaccepted.json")
        crash.write_bytes(self.state_path.read_bytes())
        restored = Server(state_path=crash, rating_mode="local-history")
        try:
            self.assertEqual(
                Counter(map(full, restored.economy.inventory(self.key))), Counter(kept)
            )
            self.assertEqual(
                Counter(full(row.card) for row in restored.auction_ledger.pending(self.key)),
                Counter(earned),
            )
            self.assertEqual(
                restored.economy.balance(self.key), self.server.economy.balance(self.key)
            )
            again = Server(state_path=crash, rating_mode="local-history")
            try:
                self.assertEqual(again.snapshot(), restored.snapshot())
            finally:
                await again.stop()
        finally:
            await restored.stop()

    async def test_failed_three_seat_admission_debit_publishes_no_game(self):
        await self.menu()
        saved = self.state_path.read_bytes()
        with patch.object(
            self.server.store, "save_changes", side_effect=OSError("injected debit failure")
        ):
            await self.client.send(
                b"NOTICE "
                + service_nick(TABLE_RAW)
                + b" :"
                + packet(0x43, 6, 3, "@ComGame=/Rule=100|0|0|0|2|0|0|1/Com=1|3")
            )
            self.assertEqual(await self.client.read(), b"")
            await self.server.stop()
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.state_path.read_bytes(), saved)
        self.assertFalse(self.server.room.npcs)
        self.assertFalse(self.server.economy.is_reserved(self.key))


class ThreePlayerComputerPractice(unittest.IsolatedAsyncioTestCase):
    server_options = {}
    asyncSetUp = ThreePlayerComputerIntegration.asyncSetUp
    asyncTearDown = ThreePlayerComputerIntegration.asyncTearDown
    request = ThreePlayerComputerIntegration.request
    menu = ThreePlayerComputerIntegration.menu
    choose = ThreePlayerComputerIntegration.choose
    play = ThreePlayerComputerIntegration.play
    continue_game = ThreePlayerComputerIntegration.continue_game

    async def test_default_practice_keeps_money_and_ownership_through_three_seat_result(self):
        await self.menu()
        await self.choose((1, 30), stake=0)
        kept, earned, _ = await self.play()
        self.assertEqual(len(kept), 10)
        self.assertEqual(earned, [])
        self.assertEqual(self.server.economy.balance(self.key), 10000)
        await self.continue_game(leave=True)
        self.assertFalse(self.server.economy.is_reserved(self.key))
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.snapshot(), self.server.snapshot())
        finally:
            await restored.stop()


if __name__ == "__main__":
    unittest.main()
