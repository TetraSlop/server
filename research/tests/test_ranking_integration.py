"""Ranking query plus account-scoped sliced file reads over both transports."""
import asyncio
from pathlib import Path
import struct
import tempfile
import unittest
from server import ACCOUNTS, Server
from profile_service import ProfileServer, checksum
from test_server import Client
from test_profile_accounts import transact, file_request, delete_request
from test_ranking_service import request
from ranking_service import RECORD_SIZE, RESULT_PATH
from identities import ROOM_RAW,service_nick,player_id
from tm_protocol import parse


class RankingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration=1
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.game=Server()
        self.port=await self.game.start(0)
        self.profile=ProfileServer(Path(self.temp.name)/'profile.jsonl',self.game)
        self.profile_port=await self.profile.start(0)
        self.clients=[Client(),Client()]
        for client,nick in zip(self.clients,ACCOUNTS):
            await client.open(self.port,account=nick)
            self.assertIn(b' 422 ',await client.read())

    async def asyncTearDown(self):
        for client in self.clients:await client.close()
        await self.profile.stop()
        await self.game.stop()
        self.temp.cleanup()

    async def query(self,side,category=2):
        client=self.clients[side]
        target=service_nick(ROOM_RAW)
        await client.send(b'NOTICE '+target+b' :'+request(category,player_id(client.nick)))
        line=await client.read()
        self.assertTrue(line.startswith(b':'+target+b'!p@local NOTICE '+client.nick+b' :'))
        commands=parse(line.split(b' :',1)[1][5:])
        self.assertEqual([c.token for c in commands],[b'RF',b'LN'])
        return commands[0].payload.decode(),int(commands[1].payload)

    async def file(self,side,path,offset=0,size=4096,sub=0):
        client=self.clients[side]
        return await transact(self.profile_port,client.nick.decode(),3,sub,
            delete_request(path) if sub==2 else file_request(path,offset,size),context=(client.cipher,client.iv))

    async def test_query_slice_account_isolation_and_deletion(self):
        path,count=await self.query(0)
        self.assertEqual(count,2)
        error,whole=await self.file(0,path)
        self.assertEqual(error,0)
        self.assertEqual(len(whole),2*RECORD_SIZE+4)
        self.assertEqual(checksum(whole[:-4]),int.from_bytes(whole[-4:],'little'))
        error,tail=await self.file(0,'./'+path,RECORD_SIZE,RECORD_SIZE)
        self.assertEqual(error,0)
        self.assertEqual(tail[:-4],whole[RECORD_SIZE:-4])
        self.assertEqual((await self.file(1,path))[0],0x7e)
        newer,_=await self.query(0,0)
        self.assertNotEqual(path,newer)
        self.assertEqual((await self.file(0,path))[1],whole)
        self.assertEqual(await self.file(0,path,sub=2),(0,b''))
        self.assertEqual((await self.file(0,path))[0],0x7e)
        self.assertEqual((await self.file(0,newer))[0],0)
        self.assertEqual(await self.file(0,RESULT_PATH,sub=2),(0,b''))
        self.assertEqual((await self.file(0,newer))[0],0x7e)
        error,metadata=await self.file(0,'b/g/TM0RkData')
        self.assertEqual(error,0)
        self.assertEqual(struct.unpack('<7I',metadata[:-4]),(0,2,0,0,0,0,0))

    async def test_empty_history_is_a_successful_empty_file(self):
        path,count=await self.query(0,1)
        self.assertEqual(count,0)
        self.assertEqual(await self.file(0,path),(0,bytes(4)))


if __name__=='__main__':unittest.main()
