"""Chat contracts exercised against original consumers and encrypted sessions."""
from pathlib import Path
import sys
import unittest

from chat_service import ChatService, Member, relay_text, roster_pages
from match_service import decode, packet
from identities import ROOM_RAW, service_nick, player_id
from server import ACCOUNTS, Server
from test_server import Client


class ChatTests(unittest.TestCase):
    def test_entry_roster_replay_leave_and_identity(self):
        service=ChatService()
        def enter(peer,identity):
            return service.enter(peer,b'group',packet(0x42,1,body=f'@Chat=/NN={identity:016X}'),identity=identity,name=peer.encode())
        first=enter('Alice',1)
        self.assertEqual(decode(first[0].payload).sections,{'Chat':{'EN':'1'}})
        self.assertEqual(decode(first[1].payload).sections['Chat']['Num'],'0|1')
        second=enter('Bob',2)
        self.assertEqual(len(second),3)
        self.assertEqual(decode(second[1].payload).sections['Chat']['C'],'2')
        duplicate=enter('Bob',2)
        self.assertEqual(decode(duplicate[1].payload).sections['Chat']['C'],'2')
        left=service.leave('Alice')
        self.assertEqual(len(left),1)
        self.assertEqual(decode(left[0].payload).sections['Chat']['NN'],'0000000000000002')
        self.assertEqual(decode(left[0].payload).sections['Chat']['C'],'3')
        with self.assertRaises(ValueError):
            service.enter('Eve',b'group',packet(0x42,1,body='@Chat=/NN=0000000000000002'),identity=3,name=b'Eve')
        self.assertEqual(list(service.groups[b'group']),['Bob'])

    def test_raw_text_and_authenticated_name(self):
        body=b'GTM0G42000801@Chat=/La=1/Dt=#CHAT#Imposter\thello/a=b@Thing=\t\x82\xa0'
        outgoing=relay_text(body,b'LocalPlayer',private=True)
        self.assertEqual(outgoing,b'GTM0G42000801@Chat=/La=1/Dt=#CHAT#LocalPlayer\thello/a=b@Thing=\t\x82\xa0')
        for bad in (body.replace(b'01@Chat',b'00@Chat'),body.replace(b'#CHAT#',b''),body+b'\n',
                    body.replace(b'/La=1/',b'/La=2147483648/')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):relay_text(bad,b'LocalPlayer',private=True)

    def test_page_size_index_counter_and_empty_list(self):
        members=[Member(i,i+1,('Name'+str(i).zfill(11)).encode())for i in range(80)]
        pages=roster_pages(members,42)
        self.assertGreater(len(pages),1)
        self.assertTrue(all(len(p)-13<=447 for p in pages))
        parsed=[decode(p)for p in pages]
        self.assertTrue(all(p.argument==len(pages)-1 for p in parsed))
        self.assertEqual([p.sections['Chat']['Num'].split('|')[0]for p in parsed],[str(i)for i in range(len(pages))])
        empty=decode(roster_pages([],7)[0])
        self.assertEqual(empty.sections['Chat']['Num'],'0|0')
        with self.assertRaises(ValueError):roster_pages(members*4,42)

    def test_generated_pages_and_text_run_through_original_consumer(self):
        root=Path(__file__).resolve().parents[1]
        source=root/'protocol_map/chat_lifecycle_vectors.py'
        # Load only the existing oracle declarations, never its fixture-writing
        # driver. Original x86 parses our newly generated packets below.
        prefix=source.read_text().split('\nvectors=[]',1)[0]
        self.assertNotEqual(len(prefix),len(source.read_text()))
        namespace={'__file__':str(source),'__name__':'chat_test_oracle'}
        exec(compile(prefix,str(source),'exec'),namespace)
        VM=namespace['VM']
        members=[Member(i,i+1,('Member'+str(i).zfill(5)).encode())for i in range(24)]
        pages=roster_pages(members,17)
        vm=VM()
        vm.enqueue(*(p[5:].decode()for p in pages))
        result=vm.receive()
        self.assertEqual(result['queue'],0)
        self.assertEqual([row['text']for row in result['members']],[m.name.decode()for m in members])
        for private in (False,True):
            outgoing=relay_text(packet(0x42,8,int(private),'@Chat=/La=3/Dt=#CHAT#Alias\ttext/a=b@c'),b'Alice',private=private)
            vm.enqueue(outgoing[5:].decode())
            vm.receive()
            taken=vm.take()
            self.assertEqual(taken['text'],'Alice\ttext/a=b@c')
            self.assertEqual(taken['header'],int(private))
            self.assertEqual(taken['locale'],3)


class ChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server=Server()
        port=await self.server.start(0)
        self.clients=[Client(),Client()]
        for client,nick in zip(self.clients,ACCOUNTS):
            await client.open(port,account=nick)
            self.assertIn(b' 422 ',await client.read())

    async def asyncTearDown(self):
        for client in self.clients:await client.close()
        await self.server.stop()

    async def flush(self,side):
        client=self.clients[side]
        await client.send(b'PING :chat-barrier')
        replies=[]
        for _ in range(40):
            line=await client.read()
            self.assertTrue(line)
            if line==b'PONG chat-barrier':return replies
            replies.append(line)
        self.fail('chat response limit')

    async def test_entry_public_private_and_disconnect_roster(self):
        target=service_nick(ROOM_RAW)
        for side,client in enumerate(self.clients):
            entry=packet(0x42,1,body=f'@Chat=/NN={player_id(client.nick):016X}')
            await client.send(b'NOTICE '+target+b' :'+entry)
            lines=await self.flush(side)
            self.assertEqual(decode(lines[0].split(b' :',1)[1]).sections['Chat']['EN'],'1')
        observer=await self.flush(0)
        self.assertEqual(decode(observer[0].split(b' :',1)[1]).sections['Chat']['Num'],'0|2')
        for side,client in enumerate(self.clients):
            await client.send(b'JOIN #chat-test')
            await self.flush(side)
        await self.flush(0)
        public=packet(0x42,8,0,'@Chat=/La=1/Dt=#CHAT#WrongName\thello /x=@y')
        await self.clients[0].send(b'NOTICE #chat-test :'+public)
        self.assertEqual(await self.flush(0),[])
        lines=await self.flush(1)
        self.assertIn(b'#CHAT#LocalPlayer\thello /x=@y',lines[0])
        private=packet(0x42,8,1,'@Chat=/La=2/Dt=#CHAT#WrongName\tprivate')
        await self.clients[1].send(b'NOTICE '+self.clients[0].nick+b' :'+private)
        self.assertEqual(await self.flush(1),[])
        lines=await self.flush(0)
        self.assertIn(b'GTM0G42000801@Chat=/La=2/Dt=#CHAT#LocalPlayer2\tprivate',lines[0])
        await self.server.nicks[self.clients[1].nick].close()
        lines=await self.flush(0)
        self.assertEqual(decode(lines[0].split(b' :',1)[1]).sections['Chat']['Num'],'0|1')


if __name__=='__main__':unittest.main()

