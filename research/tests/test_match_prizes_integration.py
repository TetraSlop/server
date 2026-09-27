"""Real encrypted ordinary prize choices, durable losses and recovery."""
import asyncio
import copy
import random
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import Server
from test_server import Client
import test_versus_integration as versus
from identities import TABLE_RAW,service_nick,player_id
from match_service import packet,decode


def full(card):
    value=card.full()
    return tuple(map(int,value.split('|'))) if isinstance(value,str) else tuple(value)


class PrizeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    player_count=2
    wagers=False
    flush=versus.VersusIntegrationTests.flush

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration=1
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path=Path(self.temp.name)/'state.json'
        self.server=Server(state_path=self.path,match_mode='versus',match_player_count=self.player_count,
                           account_count=self.player_count,card_prizes=True,allow_wagers=self.wagers)
        self.port=await self.server.start(0)
        self.clients=[]
        for nick in self.server.accounts:
            client=Client();self.clients.append(client)
            await client.open(self.port,account=nick)
            self.assertIn(b' 422 ',await client.read())
        self.keys=[c.nick.decode() for c in self.clients]

    async def asyncTearDown(self):
        for client in self.clients:await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def send(self,side,payload):
        await self.clients[side].send(b'NOTICE '+service_nick(TABLE_RAW)+b' :'+payload)
        results=[None]*self.player_count
        results[side]=await self.flush(side)
        for other in range(self.player_count):
            if other!=side:results[other]=await self.flush(other)
        return results

    async def admit(self):
        for side,client in enumerate(self.clients):
            nn=f'{player_id(client.nick):016X}'
            await self.send(side,packet(0x41,1,body='@GameEN=/NN='+nn))
            await self.send(side,packet(1,body='@Req=/NN='+nn))
        for side in range(self.player_count):await self.send(side,packet(0x41,8,body='@GameOK='))
        self.assertEqual(self.server.match.phase,'selection')

    async def play(self):
        match=self.server.match
        rng=random.Random(0)
        self.before=[self.server.economy.inventory(k) for k in self.keys]
        for side,cards in enumerate(self.before):
            body='@CardSelect=/C=5'+''.join(f'@N{i}=/D='+'|'.join(map(str,c.full())) for i,c in enumerate(cards[:5]))
            await self.send(side,packet(0x43,7,side,body))
        received=[[] for _ in self.clients]
        for turn in range(5*self.player_count):
            for side in range(self.player_count):await self.send(side,packet(0x43,9,turn,f'@TurnData=/Ans={side}'))
            side=match.active
            field=rng.choice(match.legal_fields()) if self.player_count==2 else match.legal_fields()[0]
            replies=await self.send(side,packet(0x43,10,turn,f'@PutData=/P={side}/H=0/F={field}'))
            for seat in range(self.player_count):received[seat]+=replies[seat]
            while match.phase=='battle':
                replies=await self.send(side,packet(0x43,11,turn,f'@BattleSelect=/B={match.targets[0]}'))
                for seat in range(self.player_count):received[seat]+=replies[seat]
        self.assertEqual(match.phase,'result')
        self.result_messages=received
        for seat in range(self.player_count):
            self.assertEqual(sum(decode(p).primary==0x43 and decode(p).subtype==13 for p in received[seat]),1)
            replies=await self.send(seat,packet(0x43,14,match.sequence,f'@Ready=/Ans={seat}'))
            self.assertEqual([decode(p).subtype for p in replies[seat]],[0x1a])
        return match

    async def choose(self):
        match=self.server.match
        choices=0
        while match.prize_choice is not None:
            choice=match.prize_choice;choices+=1
            replies=await self.send(choice.winner,packet(0x43,15,choice.winner,f'@GetSelect=/H={choice.options[0].hand_index}'))
            for packets in replies:self.assertEqual([decode(p).subtype for p in packets],[15])
        self.assertFalse(match.prize_commit_required)
        return choices,match.final_prizes().settlement

    def expected(self,settlement,seat):
        lost={a.original_slot for a in settlement.transfers if a.loser==seat}
        grown=self.server.match.players[seat].updated
        kept=[full(c) for i,c in enumerate(grown) if i not in lost]+[full(c) for c in self.before[seat][5:]]
        awards=[full(a.card) for a in settlement.transfers if a.winner==seat]
        return kept,awards

    async def test_two_rounds_conserve_cards_and_commit_after_continue(self):
        await self.admit()
        for _ in range(2):
            await self.play();_,settlement=await self.choose()
            expected=[]
            for seat,key in enumerate(self.keys):
                kept,awards=self.expected(settlement,seat);expected.append(Counter(kept+awards))
                self.assertEqual(Counter(map(full,self.server.economy.inventory(key))),Counter(kept))
                self.assertEqual(self.server.result_rewards.status(key)['working_count'],len(kept)+len(awards))
            for seat in range(self.player_count):await self.send(seat,packet(0x43,17,seat,'@Continue=/Ans=1/PR=0/Conf=0'))
            self.assertEqual(self.server.match.phase,'selection')
            for key,cards in zip(self.keys,expected):
                self.assertEqual(Counter(map(full,self.server.economy.inventory(key))),cards)
                self.assertEqual(self.server.economy.balance(key),10000)
            self.assertEqual(sum(sum(x.values()) for x in expected),sum(len(x) for x in self.before))
        self.assertEqual(self.server.match.completed_matches,2)

    async def test_restart_recovers_unaccepted_prizes_once(self):
        await self.admit();await self.play();_,settlement=await self.choose()
        self.assertTrue(settlement.transfers,'fixture must exercise an actual card transfer')
        crash=self.path.with_name('crash.json');crash.write_bytes(self.path.read_bytes())
        restored=Server(state_path=crash)
        try:
            for seat,key in enumerate(self.keys):
                kept,awards=self.expected(settlement,seat)
                self.assertEqual(Counter(map(full,restored.economy.inventory(key))),Counter(kept))
                self.assertEqual(len(restored.auction_ledger.pending(key)),len(awards))
                self.assertFalse(restored.result_rewards.has_pending(key))
            again=Server(state_path=crash)
            try:self.assertEqual(again.auction_ledger.snapshot(),restored.auction_ledger.snapshot())
            finally:await again.stop()
        finally:await restored.stop()

    async def test_failed_last_choice_save_never_acknowledges_or_loses_cards(self):
        await self.admit();await self.play()
        choice=self.server.match.prize_choice
        self.assertIsNotNone(choice,'fixture must require a winner choice')
        while True:
            preview=copy.deepcopy(self.server.match._prizes)
            preview.select(choice.winner,choice.options[0].hand_index,choice_id=choice.choice_id)
            if preview.complete:break
            await self.send(choice.winner,packet(0x43,15,choice.winner,f'@GetSelect=/H={choice.options[0].hand_index}'))
            choice=self.server.match.prize_choice
        before=self.path.read_bytes()
        with patch.object(Path,'replace',side_effect=OSError('injected prize save failure')):
            await self.clients[choice.winner].send(b'NOTICE '+service_nick(TABLE_RAW)+b' :'+packet(0x43,15,choice.winner,f'@GetSelect=/H={choice.options[0].hand_index}'))
            for client in self.clients:self.assertEqual(await client.read(),b'')
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.path.read_bytes(),before)
        restored=Server(state_path=self.path)
        try:
            for key,cards in zip(self.keys,self.before):
                self.assertEqual(len(restored.economy.inventory(key)),len(cards))
                self.assertFalse(restored.auction_ledger.pending(key))
        finally:await restored.stop()


class ThreeSeatPrizeIntegrationTests(PrizeIntegrationTests):
    player_count=3


if __name__=='__main__':unittest.main()
