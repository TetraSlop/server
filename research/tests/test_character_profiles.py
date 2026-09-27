"""Generic profile partial updates, original71-field parser and socket save."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from character_profiles import CharacterProfiles
from economy_service import EconomyService
from tm_protocol import Command,encode,parse
from server import Server,ACCOUNTS
from test_server import Client

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'protocol_map'))
from profile_contract_vectors import ProfileVM,PROFILE,INPUT


def request(token,identity=1,key=1,extras=()):
    return b'GTM0P'+encode([Command(token,f'0x{identity:016X}\x06{key}'.encode()),*extras])


def settings():
    return (Command(b'PP',b'5'),Command(b'NO',b'6\x063'),Command(b'NO',b'14\x061'),
            Command(b'VO',b'1'),Command(b'FO',b'3'))


class CharacterProfileTests(unittest.TestCase):
    def setUp(self):
        self.economy=EconomyService()
        self.economy.provision('alice',nn=0x101,player_id=1,name=b'Alice')
        self.economy.provision('bob',nn=0x102,player_id=2,name=b'Bob')
        self.service=CharacterProfiles(self.economy)

    def test_registration_partial_updates_and_opaque_preservation(self):
        self.assertEqual(self.service.handle('alice',request(b'CR')),[b'GTM0P<CS>\x07\x07'])
        baseline=list(self.service.records['alice'])
        baseline[17]='1234'
        self.service.records['alice']=tuple(baseline)
        self.service.handle('alice',request(b'GR',extras=settings()))
        saved=self.service.records['alice']
        self.assertEqual([saved[i]for i in (3,47,55,40,8)],['5','3','1','1','3'])
        self.assertEqual(saved[17],'1234')
        self.service.handle('alice',request(b'CR',extras=(Command(b'AN',b'Alice'),Command(b'CI',b'0\x062.50'))))
        self.assertEqual(self.service.records['alice'][55],'1')
        self.assertEqual(self.service.records['alice'][9],'2.50')
        restored=CharacterProfiles.restore(self.economy,self.service.snapshot())
        self.assertEqual(restored.snapshot(),self.service.snapshot())

    def test_read_another_profile_but_cannot_change_it(self):
        self.service.handle('alice',request(b'GR',extras=settings()))
        before=self.service.snapshot()
        reply=self.service.handle('bob',request(b'GR',extras=(Command(b'PP',b'0'),)))[0]
        self.assertEqual([c.token for c in parse(reply[5:])],[b'GG',b'PE'])
        self.assertEqual(self.service.snapshot(),before)
        own=self.service.handle('alice',request(b'PG'))[0]
        other=self.service.handle('bob',request(b'PG'))[0]
        self.assertEqual(own,other)
        unknown=self.service.handle('alice',request(b'PG',999))[0]
        self.assertEqual(parse(unknown[5:])[0].token,b'PM')
        rejected=self.service.handle('bob',request(b'CR'))[0]
        self.assertEqual(parse(rejected[5:])[0].token,b'NS')

    def test_invalid_updates_do_not_apply_earlier_fields(self):
        before=self.service.snapshot()
        for extra in (Command(b'NO',b'6\x062'),Command(b'AN',b'X'*16),Command(b'CI',b'0\x06'+b'x'*64),
                      Command(b'PP',b'6'),Command(b'NO',b'15\x061')):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                self.service.handle('alice',request(b'GR',extras=(Command(b'FO',b'1'),extra)))
            self.assertEqual(self.service.snapshot(),before)
        with self.assertRaises(ValueError):
            self.service.handle('alice',request(b'GR',extras=(Command(b'NO',b'6\x061'),Command(b'NO',b'6\x063'))))

    def test_original_po_decoder_and_gr_completion_accept_generated_records(self):
        self.service.handle('alice',request(b'GR',extras=(*settings(),Command(b'CI',b'1\x062.50'))))
        reply=self.service.handle('alice',request(b'PG'))[0]
        vm=ProfileVM()
        vm.parse(reply[5:])
        self.assertEqual(vm.call(0x101a3880,PROFILE,INPUT+0x22,0x60),0)
        self.assertEqual(vm.c(PROFILE),b'Alice')
        self.assertEqual(vm.q(PROFILE+0x10),1)
        self.assertEqual(vm.d(PROFILE+0x18),1)
        self.assertEqual(vm.c(PROFILE+0x30),b'2.50')
        self.assertEqual([vm.u.mem_read(PROFILE+off,1)[0]for off in (0x1c,0xcf,0xd7,0xc8,0x2a)],[5,3,1,1,3])
        vm.w(0x10323f40,1)
        vm.parse(self.service.handle('alice',request(b'GR'))[0][5:])
        vm.call(0x101a61e0,INPUT)
        self.assertEqual(vm.call(0x101a6090),1)


class CharacterProfileIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_read_and_restart_preserve_both_visibility_tags(self):
        asyncio.get_running_loop().slow_callback_duration=1
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            state=Path(folder)/'state.json'
            server=Server(state_path=state)
            client=Client()
            try:
                port=await server.start(0)
                await client.open(port)
                await client.read()
                target=b'UUIRT71SR'
                for payload,expected in ((request(b'CR'),b'CS'),(request(b'GR',extras=settings()),b'GK')):
                    await client.send(b'NOTICE '+target+b' :'+payload)
                    reply=await client.read()
                    self.assertEqual(parse(reply.split(b' :',1)[1][5:])[0].token,expected)
                await client.send(b'NOTICE '+target+b' :'+request(b'PG'))
                reply=await client.read()
                fields=parse(reply.split(b' :',1)[1][5:])[0].payload.split(b'\x06')
                self.assertEqual([fields[i]for i in (3,47,55,40,8)],[b'5',b'3',b'1',b'1',b'3'])
                restored=Server(state_path=state)
                try:
                    self.assertEqual(restored.character_profiles.records[client.nick.decode()],
                                     server.character_profiles.records[client.nick.decode()])
                    self.assertEqual(restored.economy.profile_bytes(client.nick.decode()),
                                     server.economy.profile_bytes(client.nick.decode()))
                finally:await restored.stop()
            finally:
                if hasattr(client,'writer'):await client.close()
                await server.stop()


if __name__=='__main__':unittest.main()

