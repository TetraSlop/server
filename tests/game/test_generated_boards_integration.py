"""Settings/menu-generated boards over authenticated encrypted loopback TCP.

Portable board integration fixtures shared by custom-board tests. Original x86
generator checks remain in research/tests/test_generated_boards_integration.py.
"""

import asyncio
import itertools
import json
import random
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from tests import ROOT
from tests.game import test_versus_integration as versus
from tests.game.test_match_service import NATIVE_SETTINGS
from tests.network.test_server import Client
from tmserver.economy.service import EconomyService
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.economy.match_ledger import offline_payout
from tmserver.game.match import decode, packet
from tmserver.game.rules import CRTLCG
from tmserver.rankings.stats import PracticeStats
from tmserver.network.server import Server


class GeneratedBoardsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    flush = versus.VersusIntegrationTests.flush

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.case_number = 0

    async def asyncTearDown(self):
        self.temp.cleanup()

    @asynccontextmanager
    async def scenario(self, n=2, *, cpu=False, money=False, start_fields=None):
        self.case_number += 1
        self.path = Path(self.temp.name) / f"state-{self.case_number}.json"
        self.server = Server(
            state_path=self.path,
            match_mode="computer" if cpu else "versus",
            match_player_count=n,
            account_count=n,
            allow_wagers=money,
            match_start_fields=start_fields,
        )
        self.cpu = cpu
        self.n = n
        self.humans = 1 if cpu else n
        self.clients = []
        self.received = [[] for _ in range(self.humans)]
        self.money = money
        port = await self.server.start(0)
        try:
            for nick in tuple(self.server.accounts)[: self.humans]:
                client = Client()
                self.clients.append(client)
                await client.open(port, account=nick)
                self.assertIn(b" 422 ", await client.read())
            self.keys = [client.nick.decode() for client in self.clients]
            yield
        finally:
            for client in self.clients:
                await client.close()
            await self.server.stop()

    async def send(self, seat, payload):
        await self.clients[seat].send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + payload)
        replies = [[] for _ in range(self.humans)]
        for index in (seat, *(i for i in range(self.humans) if i != seat)):
            replies[index] = await self.flush(index)
            self.received[index].extend(replies[index])
        return replies

    async def admit(self, flags, *, stake=0, npc=1):
        if self.cpu:
            identity = f"{player_id(self.clients[0].nick):016X}"
            await self.send(0, packet(0x41, 0x12, body=f"@GameENC=/NN={identity}/HID=1"))
            replies = await self.send(0, packet(1, body=f"@Req=/NN={identity}/HID=1"))
            self.assertTrue(any(b"@ComGameInit=" in reply for reply in replies[0]))
            initial_rng = self.server.default_table.match.rng.state
            base = [stake, *flags, 3, 0, 2]
            await self.send(0, packet(0x43, 6, 0, "@ComGame=/Rule=" + "|".join(map(str, base))))
            self.assertEqual(
                self.server.default_table.match.rng.state,
                initial_rng,
                "menu preview must not generate cards or a board",
            )
            self.assertEqual(self.server.economy.balance(self.keys[0]), 10000)
            await self.send(
                0,
                packet(
                    0x43, 6, 2, "@ComGame=/Rule=" + "|".join(map(str, base + [1])) + f"/Com={npc}"
                ),
            )
        else:
            for seat, client in enumerate(self.clients):
                identity = f"{player_id(client.nick):016X}"
                await self.send(seat, packet(0x41, 1, body="@GameEN=/NN=" + identity))
                if seat == 0:
                    settings = NATIVE_SETTINGS.replace(
                        "/st=0/cb=0/ca=0", f"/st={flags[0]}/cb={flags[1]}/ca={flags[2]}"
                    )
                    settings = (
                        settings.replace("/bm=0", f"/bm={stake}")
                        .replace("/tl=0", "/tl=3")
                        .replace("/gs=0", "/gs=1")
                    )
                    await self.send(0, packet(0x14, body=settings))
                    replies = await self.send(0, packet(0x12, body="@Rule="))
                    rules = next(
                        decode(reply).sections["Tet"] for reply in replies[0] if b"@Tet=" in reply
                    )
                    self.assertEqual(tuple(int(rules[k]) for k in ("st", "cb", "ca")), flags)
                await self.send(seat, packet(1, body="@Req=/NN=" + identity))
            for seat in range(self.humans):
                replies = await self.send(seat, packet(0x41, 8, body="@GameOK="))
            expected_rules = [stake, *flags, 3, 0, 1]
            for messages in replies:
                init = next(decode(reply) for reply in messages if b"@VsGameInit=" in reply)
                self.assertEqual(
                    list(map(int, init.sections["VsGameInit"]["R"].split("|"))), expected_rules
                )
        self.assertEqual(self.server.default_table.match.phase, "selection")
        self.assertEqual(
            tuple(self.server.default_table.match.game_rules[k] for k in ("st", "cb", "ca")), flags
        )
        self.assertEqual(
            [self.server.economy.balance(key) for key in self.keys], [10000 - stake] * self.humans
        )

    async def finish_round(self, *, seed=0):
        match = self.server.default_table.match
        moves = random.Random(seed)
        for _ in range(300):
            if match.phase == "result":
                break
            self.assertIsNone(
                self.server.default_table.match_task, "request barrier must drain scheduled work"
            )
            if match.phase == "battle":
                self.assertIsNotNone(
                    match.players[match.active].peer,
                    "computer target choice must drain without human input",
                )
                await self.send(
                    match.active,
                    packet(
                        0x43, 11, match.sequence, f"@BattleSelect=/B={moves.choice(match.targets)}"
                    ),
                )
                continue
            self.assertEqual(match.phase, "turn")
            sequence = match.sequence
            for seat in range(self.humans):
                await self.send(seat, packet(0x43, 9, sequence, f"@TurnData=/Ans={seat}"))
            if (
                match.phase == "turn"
                and match.sequence == sequence
                and match.players[match.active].peer is not None
            ):
                legal = match.legal_fields()
                effects = [field for field in legal if 3 <= match.start_board.fields[field] <= 5]
                field = moves.choice(effects or legal)
                await self.send(
                    match.active,
                    packet(0x43, 10, sequence, f"@PutData=/P={match.active}/H=0/F={field}"),
                )
        self.assertEqual(match.phase, "result")
        self.assertEqual(match.sequence, 5 * self.n)
        self.assertEqual(sum(match.observer_snapshot().scores), 5 * self.n)
        for seat in range(self.humans):
            await self.send(seat, packet(0x43, 14, match.sequence, f"@Ready=/Ans={seat}"))
        for key in self.keys:
            self.assertEqual(len(self.server.economy.inventory(key)), 10)
            for card in self.server.economy.inventory(key):
                self.server.economy.catalog.validate(card)
        return match

    async def assert_persisted_result(self, *, count):
        for seat, key in enumerate(self.keys):
            results = [decode(reply) for reply in self.received[seat] if b"@ResultData=" in reply]
            self.assertEqual(len(results), count)
            latest = list(map(int, results[-1].sections[f"No{seat}"]["D"].split("|")))
            stats = PracticeStats.from_profile(self.server.economy.profile_bytes(key))
            self.assertEqual(
                (stats.count, stats.rating, stats.reward_sum), (count, latest[2], latest[6])
            )
            self.assertEqual(
                latest[1], self.server.default_table.match.players[seat].result_stats.current_reward
            )
        durable = EconomyService.restore(json.loads(self.path.read_text()))
        self.assertEqual(durable.snapshot(), self.server.economy.snapshot())


if __name__ == "__main__":
    unittest.main()
