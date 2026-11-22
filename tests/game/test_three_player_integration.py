"""Three authenticated human seats over encrypted transport; no desktop use."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.game import test_versus_integration as versus_tests
from tests.support.irc import Client
from tests.support.memory_store import MemoryStore
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.game.packets import decode, packet
from tmserver.rankings.stats import PracticeStats
from tmserver.network.server import Server


class ThreePlayerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    flush = versus_tests.VersusIntegrationTests.flush

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.store = MemoryStore()
        self.server = Server(
            store=self.store, match_mode="versus", match_player_count=3, account_count=3
        )
        self.port = await self.server.start(0)
        self.clients = []
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def send(self, side, payload):
        await self.clients[side].send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + payload)
        results = [None] * 3
        results[side] = await self.flush(side)
        for other in range(3):
            if other != side:
                results[other] = await self.flush(other)
        return results

    async def admit(self):
        for side in range(3):
            nn = f"{player_id(self.clients[side].nick):016X}"
            await self.send(side, packet(0x41, 1, body="@GameEN=/NN=" + nn))
            await self.send(side, packet(1, body="@Req=/NN=" + nn))
        for side in range(3):
            await self.send(side, packet(0x41, 8, body="@GameOK="))
            self.assertEqual(
                self.server.default_table.match.phase, "selection" if side == 2 else "lobby"
            )

    async def round(self):
        match = self.server.default_table.match
        self.assertEqual(len(match.start_board.fields), 25)
        before = [self.server.economy.inventory(k) for k in self.keys]
        counts = [
            PracticeStats.from_profile(self.server.economy.profile_bytes(k)).vs_player_games
            for k in self.keys
        ]
        received = [[], [], []]
        for side in range(3):
            cards = before[side][:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
            )
            await self.send(side, packet(0x43, 7, side, body))
        for turn in range(15):
            self.assertEqual(match.sequence, turn)
            for side in range(3):
                await self.send(side, packet(0x43, 9, turn, f"@TurnData=/Ans={side}"))
                self.assertEqual(len(match.acks), side + 1)
            side = match.active
            results = await self.send(
                side, packet(0x43, 10, turn, f"@PutData=/P={side}/H=0/F={match.legal_fields()[0]}")
            )
            for seat in range(3):
                received[seat] += results[seat]
            while match.phase == "battle":
                results = await self.send(
                    side, packet(0x43, 11, turn, f"@BattleSelect=/B={match.targets[0]}")
                )
                for seat in range(3):
                    received[seat] += results[seat]
        self.assertEqual(match.phase, "result")
        for side, key in enumerate(self.keys):
            final = match.final_cards(self.server.nicks[self.clients[side].nick])
            after = self.server.economy.inventory(key)
            self.assertEqual(
                tuple("|".join(map(str, c.full())) for c in after[:5]),
                tuple(c.full() for c in final.updated),
            )
            self.assertEqual(after[5:], before[side][5:])
            result = [
                decode(p)
                for p in received[side]
                if decode(p).subtype == 13 and decode(p).primary == 0x43
            ]
            self.assertEqual(len(result), 1)
            self.assertIn(f"No{side}", result[0].sections)
            stats = PracticeStats.from_profile(self.server.economy.profile_bytes(key))
            self.assertEqual(stats.vs_player_games, (counts[side] + 1) & 65535)
            self.assertEqual(result[0].sections[f"No{side}"]["T"], f"0|0|{stats.vs_player_games}")
            packets = await self.send(side, packet(0x43, 14, 15, f"@Ready=/Ans={side}"))
            self.assertEqual([decode(p).subtype for p in packets[side]], [0x1A])
            self.assertLessEqual(len(packets[side][0]) - 13, 447)

    async def test_fifteen_moves_three_result_commits_rematch_and_restart(self):
        await self.admit()
        await self.round()
        for side in range(3):
            await self.send(side, packet(0x43, 17, side, "@Continue=/Ans=1/PR=0/Conf=0"))
            self.assertEqual(
                self.server.default_table.match.phase, "selection" if side == 2 else "result"
            )
        await self.round()
        await self.send(2, packet(0x43, 17, 2, "@Continue=/Ans=2/PR=0/Conf=0"))
        self.assertEqual(self.server.default_table.match.phase, "finished")
        for key in self.keys:
            self.assertFalse(self.server.economy.is_reserved(key))
        restored = Server(
            store=self.store, match_mode="versus", match_player_count=3, account_count=3
        )
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
        finally:
            await restored.stop()

    async def test_third_seat_disconnect_aborts_every_participant_without_growth(self):
        await self.admit()
        before = self.server.economy.snapshot()
        await self.clients[2].close()
        for client in self.clients[:2]:
            self.assertEqual(await client.read(), b"")
        self.assertEqual(self.server.default_table.match.players, [])
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.server.economy.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
