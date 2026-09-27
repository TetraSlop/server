"""Actual encrypted two-account invitation, exchange and recovery tests."""
from collections import Counter
from pathlib import Path
import tempfile
import unittest

from server import ACCOUNTS, Server
from test_server import Client
from economy_service import named, parse_named
from identities import TRADE_RAW, game_id, player_id, service_nick
from test_trade_service import offer, data
from profile_service import ProfileServer, checked
from test_profile_accounts import transact as profile_request, file_request


class TradeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path=Path(self.temp.name)/'state.json'
        self.server=Server(state_path=self.path)
        self.port=await self.server.start(0)
        self.clients=[Client(),Client()]
        for client,nick in zip(self.clients,ACCOUNTS):
            await client.open(self.port,account=nick)
            self.assertIn(b' 422 ',await client.read())
        self.keys=[c.nick.decode() for c in self.clients]
        self.before=self.server.economy.snapshot()

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def flush(self,side):
        client=self.clients[side]
        await client.send(b'PING :barrier')
        out=[]
        for _ in range(20):
            line=await client.read()
            if line==b'PONG barrier':return out
            self.assertTrue(line)
            out.append(line)
        self.fail('too many replies')

    async def send(self,side,target,payload):
        await self.clients[side].send(b'NOTICE '+target+b' :'+payload)
        return await self.flush(side)

    async def control(self,side,family,body):
        other=1-side
        out=await self.send(side,self.clients[other].nick,named(family,0,0,body))
        self.assertEqual(out,[])
        relayed=await self.flush(other)
        self.assertEqual(len(relayed),1)
        self.assertTrue(relayed[0].startswith(b':'+self.clients[side].nick+b'!p@local NOTICE '))
        return relayed

    def metadata(self,side):
        return f'/NN={player_id(self.clients[side].nick):016X}/HID=0/Dm=0/Vol=0'

    async def enter(self):
        await self.control(0,0x21,'@Tr='+self.metadata(0))
        await self.control(1,0x22,'@TrAns=/Ans=1'+self.metadata(1))
        await self.control(0,0xa0,f'@TrID=/ID={game_id(TRADE_RAW):016X}')
        body='@TrEnt=/NN='+'|'.join(f'{player_id(c.nick):016X}' for c in self.clients)+'/HID=0|0/Dm=0|0/Vol=0|0'
        first=await self.send(0,service_nick(TRADE_RAW),named(0xa0,0,0,body))
        second=await self.flush(1)
        for replies in (first,second):
            parsed=[parse_named(p.split(b' :',1)[1]) for p in replies]
            self.assertEqual([p[:2] for p in parsed],[(0xa1,0),(0xa2,0x18)])
            self.assertTrue(all(p.startswith(b':'+service_nick(TRADE_RAW)+b'!p@local NOTICE ') for p in replies))
        self.session=parse_named(first[1].split(b' :',1)[1])[2]

    async def operation(self,side,subtype,body):
        replies=await self.send(side,service_nick(TRADE_RAW),named(0xa2,subtype,self.session,body))
        replies+=await self.flush(1-side)
        return [parse_named(p.split(b' :',1)[1]) for p in replies]

    async def settle(self):
        await self.enter()
        for side,key in enumerate(self.keys):
            await self.operation(side,0x1a,offer(side,self.server.economy.inventory(key)[:1]))
        for side in (0,1):await self.operation(side,0x1b,data(side,1,(0,)))
        for side in (0,1):await self.operation(side,0x1b,data(side,2,(0,)))
        await self.operation(0,0x1c,'@Ans=/P=0/M=2')
        replies=await self.operation(1,0x1c,'@Ans=/P=1/M=2')
        self.assertEqual([r[3] for r in replies],[{'Data':{'M':'4'}}]*2)

    async def test_trade_commits_both_accounts_and_reloads_from_disk(self):
        original=[self.server.economy.inventory(k) for k in self.keys]
        await self.settle()
        await self.operation(0,0x1d,'@Get=/P=0')
        self.assertEqual(self.server.economy.snapshot(),self.before)
        replies=await self.operation(1,0x1d,'@Get=/P=1')
        self.assertEqual(len(replies),2)
        for side,key in enumerate(self.keys):
            expected=original[side][1:]+original[1-side][:1]
            self.assertEqual(Counter(c.reduced() for c in self.server.economy.inventory(key)),Counter(c.reduced() for c in expected))
            self.assertFalse(self.server.economy.is_reserved(key))
        committed=self.server.economy.snapshot()
        await self.operation(0,0x1d,'@Get=/P=0')
        self.assertEqual(self.server.economy.snapshot(),committed)
        restored=Server(state_path=self.path)
        try:self.assertEqual(restored.economy.snapshot(),committed)
        finally:await restored.stop()
        profiles=ProfileServer(Path(self.temp.name)/'profile.jsonl',self.server)
        profile_port=await profiles.start(0)
        try:
            for client,key in zip(self.clients,self.keys):
                error,payload=await profile_request(profile_port,key,3,0,file_request(),context=(client.cipher,client.iv))
                self.assertEqual((error,payload),(0,checked(self.server.economy.profile_bytes(key))))
        finally:await profiles.stop()

    async def test_disconnect_after_first_get_rolls_back_and_resyncs_peer(self):
        await self.settle()
        await self.operation(0,0x1d,'@Get=/P=0')
        await self.clients[1].close()
        line=await self.clients[0].read()
        self.assertEqual(parse_named(line.split(b' :',1)[1])[1],0x27)
        self.assertEqual(await self.clients[0].read(),b'')
        self.assertEqual(self.server.economy.snapshot(),self.before)
        self.assertTrue(all(not self.server.economy.is_reserved(k) for k in self.keys))
        # The profile is authoritative after reconnect; no partial exchange.
        await self.clients[0].close()
        self.clients[0]=Client()
        await self.clients[0].open(self.port,account=next(iter(ACCOUNTS)))
        self.assertIn(b' 422 ',await self.clients[0].read())
        self.assertEqual(self.server.economy.snapshot(),self.before)

    async def test_cancellation_revokes_entry_and_forged_identity_is_rejected(self):
        await self.control(0,0x21,'@Tr='+self.metadata(0))
        await self.control(1,0x22,'@TrAns=/Ans=1'+self.metadata(1))
        await self.control(0,0x23,'@TrCan=')
        self.assertFalse(self.server.trade_router.grants)
        await self.clients[0].send(b'NOTICE '+self.clients[1].nick+b' :'+named(0x21,0,0,'@Tr='+self.metadata(1)))
        self.assertEqual(await self.clients[0].read(),b'')
        self.assertEqual(self.server.economy.snapshot(),self.before)
        self.assertEqual(await self.flush(1),[])


if __name__=='__main__':unittest.main()
