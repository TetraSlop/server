"""Settings/menu-generated boards over authenticated encrypted loopback TCP.

Board expectations execute original x86 generation instructions; no GUI or
native game process. Only server-owned APIs are inspected for test assertions.
"""
import asyncio
from contextlib import asynccontextmanager
import itertools
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

from economy_service import EconomyService
from identities import TABLE_RAW, player_id, service_nick
from match_economy import offline_payout
from match_service import decode, packet
from native_rules import CRTLCG
from result_stats import PracticeStats
from server import Server
from test_match_service import NATIVE_SETTINGS
from test_server import Client
import test_versus_integration as versus

sys.path.insert(0, str(Path(__file__).parents[1]/'protocol_map'))
from board_generation_vectors import BoardVM


class GeneratedBoardsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    flush = versus.VersusIntegrationTests.flush

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.case_number = 0
        self.oracle = BoardVM()

    async def asyncTearDown(self):
        self.temp.cleanup()

    @asynccontextmanager
    async def scenario(self, n=2, *, cpu=False, money=False, start_fields=None):
        self.case_number += 1
        self.path = Path(self.temp.name)/f'state-{self.case_number}.json'
        self.server = Server(state_path=self.path, match_mode='computer' if cpu else 'versus',
                             match_player_count=n, account_count=n, allow_wagers=money,
                             match_start_fields=start_fields)
        self.cpu = cpu
        self.n = n
        self.humans = 1 if cpu else n
        self.clients = []
        self.received = [[] for _ in range(self.humans)]
        self.money = money
        port = await self.server.start(0)
        try:
            for nick in tuple(self.server.accounts)[:self.humans]:
                client = Client()
                self.clients.append(client)
                await client.open(port, account=nick)
                self.assertIn(b' 422 ', await client.read())
            self.keys = [client.nick.decode() for client in self.clients]
            yield
        finally:
            for client in self.clients:
                await client.close()
            await self.server.stop()

    async def send(self, seat, payload):
        await self.clients[seat].send(b'NOTICE '+service_nick(TABLE_RAW)+b' :'+payload)
        replies = [[] for _ in range(self.humans)]
        for index in (seat, *(i for i in range(self.humans) if i != seat)):
            replies[index] = await self.flush(index)
            self.received[index].extend(replies[index])
        return replies

    async def admit(self, flags, *, stake=0, npc=1):
        if self.cpu:
            identity = f'{player_id(self.clients[0].nick):016X}'
            await self.send(0, packet(0x41, 0x12, body=f'@GameENC=/NN={identity}/HID=1'))
            replies = await self.send(0, packet(1, body=f'@Req=/NN={identity}/HID=1'))
            self.assertTrue(any(b'@ComGameInit=' in reply for reply in replies[0]))
            initial_rng = self.server.match.rng.state
            base = [stake, *flags, 3, 0, 2]
            await self.send(0, packet(0x43, 6, 0, '@ComGame=/Rule='+'|'.join(map(str, base))))
            self.assertEqual(self.server.match.rng.state, initial_rng, 'menu preview must not generate cards or a board')
            self.assertEqual(self.server.economy.balance(self.keys[0]), 10000)
            await self.send(0, packet(0x43, 6, 2, '@ComGame=/Rule='+'|'.join(map(str, base+[1]))+f'/Com={npc}'))
        else:
            for seat, client in enumerate(self.clients):
                identity = f'{player_id(client.nick):016X}'
                await self.send(seat, packet(0x41, 1, body='@GameEN=/NN='+identity))
                if seat == 0:
                    settings = NATIVE_SETTINGS.replace('/st=0/cb=0/ca=0', f'/st={flags[0]}/cb={flags[1]}/ca={flags[2]}')
                    settings = settings.replace('/bm=0', f'/bm={stake}').replace('/tl=0', '/tl=3').replace('/gs=0', '/gs=1')
                    await self.send(0, packet(0x14, body=settings))
                    replies = await self.send(0, packet(0x12, body='@Rule='))
                    rules = next(decode(reply).sections['Tet'] for reply in replies[0] if b'@Tet=' in reply)
                    self.assertEqual(tuple(int(rules[k]) for k in ('st', 'cb', 'ca')), flags)
                await self.send(seat, packet(1, body='@Req=/NN='+identity))
            for seat in range(self.humans):
                replies = await self.send(seat, packet(0x41, 8, body='@GameOK='))
            expected_rules = [stake, *flags, 3, 0, 1]
            for messages in replies:
                init = next(decode(reply) for reply in messages if b'@VsGameInit=' in reply)
                self.assertEqual(list(map(int, init.sections['VsGameInit']['R'].split('|'))), expected_rules)
        self.assertEqual(self.server.match.phase, 'selection')
        self.assertEqual(tuple(self.server.match.game_rules[k] for k in ('st', 'cb', 'ca')), flags)
        self.assertEqual([self.server.economy.balance(key) for key in self.keys], [10000-stake]*self.humans)

    async def select_and_check_original(self, flags):
        match = self.server.match
        state = match.rng.state
        if any(flags):
            expected = self.oracle.generate(self.n, state, flags)
        else:
            rng = CRTLCG(state)
            expected = dict(fields=[0]*match.board_size, active=0, blocks=0, effects=0,
                            shared=[rng() & 255 for _ in range(8)], rng_state=rng.state)
        last = None
        for seat, key in enumerate(self.keys):
            cards = self.server.economy.inventory(key)[:5]
            body = '@CardSelect=/C=5'+''.join(f'@N{i}=/D='+'|'.join(map(str, c.full())) for i, c in enumerate(cards))
            last = await self.send(seat, packet(0x43, 7, seat, body))
            if seat < self.humans-1:
                self.assertEqual(match.phase, 'selection')
                self.assertEqual(match.rng.state, state, 'no board draw before all selections')
        for messages in last:
            starts = [decode(reply) for reply in messages if b'@StartData=' in reply]
            turns = [decode(reply) for reply in messages if b'@TurnData=' in reply]
            self.assertEqual((len(starts), len(turns)), (1, 1))
            start, turn = starts[0].sections['StartData'], turns[0].sections['TurnData']
            self.assertEqual(list(map(int, start['F'].split('|'))), expected['fields'])
            self.assertEqual([int(start[k]) for k in ('S', 'B', 'E')],
                             [expected[k] for k in ('active', 'blocks', 'effects')])
            self.assertEqual(list(map(int, start['L'].split('|'))), [0]*self.n)
            self.assertEqual(int(turn['A']), expected['active'])
            self.assertEqual(list(map(int, turn['R'].split('|'))), expected['shared'])
            self.assertEqual(turns[0].argument, 0)
        self.assertEqual(match.rng.state, expected['rng_state'], 'first R must reuse generated draws')
        self.assertGreaterEqual(len(match.legal_fields()), 5*self.n)
        self.assertNotIn(18, expected['fields'], 'ordinary generator does not invent CBC tiles')
        return tuple(expected['fields'])

    async def finish_round(self, *, seed=0):
        match = self.server.match
        moves = random.Random(seed)
        for _ in range(300):
            if match.phase == 'result':
                break
            self.assertIsNone(self.server.match_task, 'request barrier must drain scheduled work')
            if match.phase == 'battle':
                self.assertIsNotNone(match.players[match.active].peer, 'computer target choice must drain without human input')
                await self.send(match.active, packet(0x43, 11, match.sequence, f'@BattleSelect=/B={moves.choice(match.targets)}'))
                continue
            self.assertEqual(match.phase, 'turn')
            sequence = match.sequence
            for seat in range(self.humans):
                await self.send(seat, packet(0x43, 9, sequence, f'@TurnData=/Ans={seat}'))
            if match.phase == 'turn' and match.sequence == sequence and match.players[match.active].peer is not None:
                legal = match.legal_fields()
                effects = [field for field in legal if 3 <= match.start_board.fields[field] <= 5]
                field = moves.choice(effects or legal)
                await self.send(match.active, packet(0x43, 10, sequence, f'@PutData=/P={match.active}/H=0/F={field}'))
        self.assertEqual(match.phase, 'result')
        self.assertEqual(match.sequence, 5*self.n)
        self.assertEqual(sum(match.observer_snapshot().scores), 5*self.n)
        for seat in range(self.humans):
            await self.send(seat, packet(0x43, 14, match.sequence, f'@Ready=/Ans={seat}'))
        for key in self.keys:
            self.assertEqual(len(self.server.economy.inventory(key)), 10)
            for card in self.server.economy.inventory(key):
                self.server.economy.catalog.validate(card)
        return match

    async def assert_persisted_result(self, *, count):
        for seat, key in enumerate(self.keys):
            results = [decode(reply) for reply in self.received[seat] if b'@ResultData=' in reply]
            self.assertEqual(len(results), count)
            latest = list(map(int, results[-1].sections[f'No{seat}']['D'].split('|')))
            stats = PracticeStats.from_profile(self.server.economy.profile_bytes(key))
            self.assertEqual((stats.count, stats.rating, stats.reward_sum), (count, latest[2], latest[6]))
            self.assertEqual(latest[1], self.server.match.players[seat].result_stats.current_reward)
        durable = EconomyService.restore(json.loads(self.path.read_text()))
        self.assertEqual(durable.snapshot(), self.server.economy.snapshot())

    async def test_every_toggle_two_and_three_player_wire_matches_original(self):
        for n in (2, 3):
            for flags in itertools.product((0, 1), repeat=3):
                with self.subTest(players=n, flags=flags):
                    async with self.scenario(n):
                        await self.admit(flags)
                        before = self.server.economy.snapshot()
                        await self.select_and_check_original(flags)
                        self.assertEqual(self.server.economy.snapshot(), before)

    async def test_every_toggle_original_computer_menu_matches_original(self):
        for flags in itertools.product((0, 1), repeat=3):
            with self.subTest(flags=flags):
                async with self.scenario(cpu=True):
                    await self.admit(flags, npc=30)
                    before = self.server.economy.snapshot()
                    await self.select_and_check_original(flags)
                    self.assertEqual(self.server.economy.snapshot(), before)

    async def test_two_three_player_special_rounds_pay_and_persist_valid_cards(self):
        for n in (2, 3):
            with self.subTest(players=n):
                async with self.scenario(n, money=True):
                    flags = (1, 1, 1)
                    await self.admit(flags, stake=137)
                    fields = await self.select_and_check_original(flags)
                    self.assertTrue(any(fields))
                    await self.finish_round(seed=7)
                    self.assertEqual(sum(self.server.economy.balance(key) for key in self.keys), 10000*n)
                    await self.assert_persisted_result(count=1)
                    for seat in range(self.humans):
                        await self.send(seat, packet(0x43, 17, seat, '@Continue=/Ans=2/PR=0/Conf=0'))
                    self.assertEqual(self.server.match.phase, 'finished')
                    self.assertFalse(self.server.match_reservations)
                    await self.assert_persisted_result(count=1)

    async def test_computer_generated_rematch_consumes_new_stream_and_pays_exactly(self):
        async with self.scenario(cpu=True, money=True):
            flags = (1, 1, 1)
            await self.admit(flags, stake=137, npc=1)
            boards = []
            balance = 10000
            for number in (1, 2):
                balance -= 137
                boards.append(await self.select_and_check_original(flags))
                match = await self.finish_round(seed=number)
                reward = max(0, offline_payout(match.observer_snapshot().scores,
                                              tuple(player.rating for player in match.players), 137))
                balance += reward
                self.assertEqual(self.server.economy.balance(self.keys[0]), balance)
                await self.assert_persisted_result(count=number)
                answer = 1 if number == 1 else 2
                await self.send(0, packet(0x43, 17, 0, f'@Continue=/Ans={answer}/PR=0/Conf=0'))
            self.assertNotEqual(boards[0], boards[1])
            self.assertEqual(self.server.match.phase, 'finished')
            self.assertFalse(self.server.economy.is_reserved(self.keys[0]))
            await self.assert_persisted_result(count=2)


if __name__ == '__main__':
    unittest.main()
