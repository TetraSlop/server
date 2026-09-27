"""Encrypted auction/prize transactions, profile files and restart durability."""
import asyncio
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from server import ACCOUNTS, Server
from profile_service import ProfileServer
from test_server import Client
from test_profile_accounts import transact, file_request, delete_request
from test_auction_service import exhibit_request, bid_request, collection_request, request
from auction_service import listing_record, EXHIBIT_PATH, PENDING_PATHS
from economy_service import named, parse_named
from identities import ROOM_RAW, SHOP_RAW, service_nick
from tm_protocol import parse


class AuctionPrizeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration=1
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path=Path(self.temp.name)/'state.json'
        self.now=1700000000
        self.game=Server(state_path=self.path,auction_clock=lambda:self.now)
        self.port=await self.game.start(0)
        self.profile=ProfileServer(Path(self.temp.name)/'profile.jsonl',self.game)
        self.profile_port=await self.profile.start(0)
        self.clients=[Client(),Client()]
        for client,nick in zip(self.clients,ACCOUNTS):
            await client.open(self.port,account=nick)
            self.assertIn(b' 422 ',await client.read())
        self.keys=[c.nick.decode() for c in self.clients]

    async def asyncTearDown(self):
        for client in self.clients:await client.close()
        await self.profile.stop();await self.game.stop();self.temp.cleanup()

    async def send(self,side,payload,*,source=None):
        client=self.clients[side];target=service_nick(ROOM_RAW)
        await client.send(b'NOTICE '+target+b' :'+payload)
        await client.send(b'PING :exchange-barrier')
        out=[]
        for _ in range(100):
            line=await client.read()
            self.assertTrue(line,'unexpected disconnect')
            if line==b'PONG exchange-barrier':return out
            self.assertTrue(line.startswith(b':'+(source or target)+b'!p@local NOTICE '+client.nick+b' :'),line)
            out.append(line.split(b' :',1)[1])
        self.fail('exchange response limit exceeded')

    async def file(self,side,path,offset=0,size=4096,sub=0):
        client=self.clients[side]
        return await transact(self.profile_port,client.nick.decode(),3,sub,
            delete_request(path) if sub==2 else file_request(path,offset,size),context=(client.cipher,client.iv))

    async def enter_collection(self,side):
        replies=await self.send(side,collection_request(self.game.economy,self.keys[side]),source=service_nick(SHOP_RAW))
        self.assertEqual(parse_named(replies[0])[2],0)
        return int(parse_named(replies[0])[3]['Init']['N']),replies

    async def get_collection(self,side,number):
        return await self.send(side,named(0xB2,0x15,number,f'@Get=/No={number}'),source=service_nick(SHOP_RAW))

    async def sell(self):
        replies=await self.send(0,exhibit_request(self.game.economy,self.keys[0]))
        self.assertEqual(parse(replies[0][5:])[0].token,b'ES')
        item=self.game.auction_ledger.listings()[-1]
        replies=await self.send(1,bid_request(self.game.economy,self.keys[1],item.id,100,0))
        self.assertEqual(parse(replies[0][5:])[0].token,b'BS')
        self.now=item.expires
        # A real query advances the due ledger before publishing its snapshot.
        replies=await self.send(1,request(self.game.economy,self.keys[1],b'SI'))
        self.assertEqual(parse(replies[0][5:])[-1].payload,b'0')
        return item

    def prize_entry(self,side=0):
        a=self.game.economy.account_view(self.keys[side])
        return named(0x70,0,0,f'@CvReq=/NN={a.nn:016X}/HID=1/Dm=1/Vol=1/CN={a.name.hex()}')

    async def enter_prize(self,side=0):
        replies=await self.send(side,self.prize_entry(side),source=service_nick(SHOP_RAW))
        self.assertEqual([parse_named(r)[0] for r in replies],[0x71,0xB2])
        self.assertEqual(parse_named(replies[1])[2],0)
        return int(parse_named(replies[1])[3]['ExcInit']['N']),replies

    async def test_auction_query_files_are_isolated_immutable_and_removable(self):
        await self.send(0,exhibit_request(self.game.economy,self.keys[0]))
        item=self.game.auction_ledger.listing(1)
        replies=await self.send(0,request(self.game.economy,self.keys[0],b'SI'))
        path=parse(replies[0][5:])[0].payload.decode()
        error,whole=await self.file(0,path)
        self.assertEqual(error,0);self.assertEqual(whole[:-4],listing_record(item))
        error,slice_=await self.file(0,'./'+path,40,80)
        self.assertEqual(error,0);self.assertEqual(slice_[:-4],whole[40:120])
        self.assertEqual((await self.file(1,path))[0],0x7e)
        await self.send(1,bid_request(self.game.economy,self.keys[1],1,120,0))
        self.assertEqual((await self.file(0,path))[1],whole)
        self.assertEqual(await self.file(0,EXHIBIT_PATH,sub=2),(0,b''))
        self.assertEqual((await self.file(0,path))[0],0x7e)
        self.assertEqual((await self.file(0,'b/g/TM0AucData'))[0],0)

    async def test_auction_payment_card_claim_and_joint_restart(self):
        item=await self.sell()
        self.assertEqual(self.game.economy.balance(self.keys[1]),9900)
        for side in range(2):
            self.assertEqual((await self.file(side,PENDING_PATHS[0]))[0],0)
            number,replies=await self.enter_collection(side)
            self.assertEqual(parse_named(replies[1])[3]['Data']['M'],'100' if side==0 else '0')
            won=await self.get_collection(side,number)
            self.assertEqual(parse_named(won[0])[3]['Card']['E'],'0' if side==0 else '1')
            done=await self.get_collection(side,number)
            self.assertEqual([parse_named(r)[1] for r in done],[0x20,0x17])
            self.assertFalse(self.game.economy.is_reserved(self.keys[side]))
            self.assertEqual((await self.file(side,PENDING_PATHS[0]))[0],0x7e)
        self.assertEqual(self.game.economy.balance(self.keys[0]),10100)
        self.assertEqual(len(self.game.economy.inventory(self.keys[0])),9)
        self.assertEqual(self.game.economy.inventory(self.keys[1])[-1].reduced(),item.card.reduced())
        restored=Server(state_path=self.path,auction_clock=lambda:self.now)
        try:
            self.assertEqual(restored.economy.snapshot(),self.game.economy.snapshot())
            self.assertEqual(restored.auction_ledger.snapshot(),self.game.auction_ledger.snapshot())
            self.assertFalse(any(restored.auction_ledger.pending(k) for k in self.keys))
        finally:await restored.stop()

    async def test_unaccepted_auction_card_survives_disconnect_and_restart(self):
        await self.sell();number,_=await self.enter_collection(1)
        await self.get_collection(1,number)
        self.assertEqual(len(self.game.economy.inventory(self.keys[1])),10)
        await self.clients[1].close()
        peer=self.game.nicks.get(self.clients[1].nick)
        if peer is not None:await peer.close()
        self.assertFalse(self.game.economy.is_reserved(self.keys[1]))
        restored=Server(state_path=self.path,auction_clock=lambda:self.now)
        try:
            self.assertEqual(len(restored.economy.inventory(self.keys[1])),10)
            self.assertEqual(len(restored.auction_ledger.pending(self.keys[1])),1)
            replies=restored.auction.handle(self.keys[1],collection_request(restored.economy,self.keys[1]))
            number=int(parse_named(replies[0])[3]['Init']['N'])
            get=named(0xB2,0x15,number,f'@Get=/No={number}')
            restored.auction.handle(self.keys[1],get);restored.auction.handle(self.keys[1],get)
            restored.persist()
            self.assertEqual(len(restored.economy.inventory(self.keys[1])),11)
            self.assertFalse(restored.auction_ledger.pending(self.keys[1]))
        finally:await restored.stop()

    async def test_prize_award_and_purchase_commit_once_and_reload(self):
        key=self.keys[0]
        self.game.prize.grant_award(key,'integration.award',top30=5000,lucky=100,rookies=50)
        self.game.persist()
        number,replies=await self.enter_prize()
        self.assertEqual(parse_named(replies[1])[3]['ExcInit']['PP'],'0|5000|100|50')
        self.assertEqual(self.game.economy.prize_points(key),5150)
        self.assertEqual((await self.enter_prize())[1],replies)
        pack=45;cost=self.game.economy.catalog.packs[pack]['price']
        self.assertLessEqual(cost,5150)
        before=self.game.economy.inventory(key)
        buy=named(0xB2,0x14,number,f'@Buy=/No={pack}')
        cards=await self.send(0,buy,source=service_nick(SHOP_RAW))
        self.assertEqual(parse_named(cards[0])[3]['Card']['S'],'1')
        self.assertEqual(await self.send(0,buy,source=service_nick(SHOP_RAW)),cards)
        self.assertEqual(self.game.economy.inventory(key),before)
        get=named(0xB2,0x15,number,f'@Get=/No={number}')
        for _ in range(2):self.assertEqual(await self.send(0,get,source=service_nick(SHOP_RAW)),[])
        self.assertEqual(len(self.game.economy.inventory(key)),len(before)+1)
        self.assertEqual(self.game.economy.prize_points(key),5150-cost)
        updated=(await self.enter_prize())[1]
        self.assertEqual(parse_named(updated[1])[3]['ExcInit']['PP'],f'{5150-cost}|0|0|0')
        quit_=named(0xB2,0x17,number,f'@Quit=/No={number}')
        self.assertEqual(len(await self.send(0,quit_,source=service_nick(SHOP_RAW))),1)
        restored=Server(state_path=self.path)
        try:
            self.assertEqual(restored.economy.snapshot(),self.game.economy.snapshot())
            self.assertEqual(restored.prize.snapshot(),self.game.prize.snapshot())
            self.assertFalse(restored.prize.grant_award(key,'integration.award',top30=5000))
        finally:await restored.stop()

    async def test_failed_auction_save_sends_no_acceptance_and_retains_last_joint_state(self):
        before=self.path.read_bytes()
        with patch.object(Path,'replace',side_effect=OSError('injected failed exchange save')):
            await self.clients[0].send(b'NOTICE '+service_nick(ROOM_RAW)+b' :'+exhibit_request(self.game.economy,self.keys[0]))
            self.assertEqual(await self.clients[0].read(),b'')
        self.assertIsNotNone(self.game.storage_failed)
        self.assertEqual(self.path.read_bytes(),before)
        restored=Server(state_path=self.path)
        try:
            self.assertEqual(len(restored.economy.inventory(self.keys[0])),10)
            self.assertEqual(restored.auction_ledger.listings(),())
        finally:await restored.stop()

    async def test_prize_pending_purchase_disconnect_keeps_award_but_not_purchase(self):
        key=self.keys[0]
        self.game.prize.grant_award(key,'pending.purchase',top30=5000)
        self.game.persist()
        number,_=await self.enter_prize()
        before=self.game.economy.inventory(key)
        await self.send(0,named(0xB2,0x14,number,'@Buy=/No=45'),source=service_nick(SHOP_RAW))
        await self.clients[0].close()
        peer=self.game.nicks.get(self.clients[0].nick)
        if peer is not None:await peer.close()
        self.assertFalse(self.game.economy.is_reserved(key))
        restored=Server(state_path=self.path)
        try:
            self.assertEqual(restored.economy.inventory(key),before)
            self.assertEqual(restored.economy.prize_points(key),5000)
            self.assertFalse(restored.prize.grant_award(key,'pending.purchase',top30=5000))
        finally:await restored.stop()

    async def test_prize_award_save_failure_preserves_unredeemed_award(self):
        key=self.keys[0]
        self.game.prize.grant_award(key,'failed.award',top30=5000)
        self.game.persist();before=self.path.read_bytes()
        with patch.object(Path,'replace',side_effect=OSError('injected failed award save')):
            await self.clients[0].send(b'NOTICE '+service_nick(ROOM_RAW)+b' :'+self.prize_entry())
            self.assertEqual(await self.clients[0].read(),b'')
        self.assertEqual(self.path.read_bytes(),before)
        restored=Server(state_path=self.path)
        try:
            self.assertEqual(restored.economy.prize_points(key),0)
            restored.prize.handle(key,self.prize_entry())
            restored.persist()
            self.assertEqual(restored.economy.prize_points(key),5000)
            restored.prize.handle(key,self.prize_entry())
            self.assertEqual(restored.economy.prize_points(key),5000)
            self.assertIsNone(restored.prize.snapshot()['accounts'][key]['pending'])
        finally:await restored.stop()


if __name__=='__main__':unittest.main()
