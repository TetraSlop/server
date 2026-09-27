"""Original asset, NPC setup and local generated-hand comparisons; no UI."""
from pathlib import Path
import hashlib
import struct
import sys
import unittest
from native_opponents import OPPONENTS,ROWS,SOURCE_SHA256,DECODED_SHA256,opponent,prepare_opponent,OpponentError
from economy_service import Catalog,Card

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'protocol_map'))
from economy_vectors import Machine
from economy_generation_vectors import Generator,Rng
from incoming_gameplay_vectors import ReceiveVM
from unicorn.x86_const import *


class NPCMachine(Generator):
    def hook(self,u,a,n,d):
        if a==0x100b8550: # Card art lookup only; compact import remains original.
            sp=u.reg_read(UC_X86_REG_ESP)
            u.reg_write(UC_X86_REG_EAX,0);u.reg_write(UC_X86_REG_EIP,self.rd(sp));u.reg_write(UC_X86_REG_ESP,sp+8)
        else:super().hook(u,a,n,d)


class OpponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=(BASE/'data/PlPrm.BIN').read_bytes();cls.source=source
        m=Machine();m.u.mem_write(0x20100000,source)
        m.call(0x1017b6c0,[0x20100010,0x20200000])
        cls.decoded=bytes(m.u.mem_read(0x20200000,struct.unpack_from('<I',source)[0]))
        cls.catalog=Catalog()

    def test_all_embedded_records_match_original_decompression_and_strings(self):
        d=self.decoded
        self.assertEqual(hashlib.sha256(self.source).hexdigest(),SOURCE_SHA256)
        self.assertEqual(hashlib.sha256(d).hexdigest(),DECODED_SHA256)
        end,count,kind,reserved=struct.unpack_from('<4I',d)
        self.assertEqual((end,count,kind,reserved),(636,31,15,0))
        tail=end;rows=[]
        for i in range(count):
            raw=d[16+20*i:36+20*i];names=[]
            for off in (12,16):
                value=struct.unpack_from('<I',raw,off)[0]
                self.assertEqual(value&0xfffff,0xfffff)
                length=value>>20;names.append(d[tail:tail+length]);self.assertEqual(d[tail+length],0);tail+=length+1
            if i:rows.append((i,*struct.unpack_from('<HHI4B',raw),names[0]))
        self.assertEqual(tuple(rows),ROWS);self.assertEqual(tail,len(d))

    def test_original_NPC_setup_copies_personality_pack_and_threshold_name(self):
        m=Machine();table=0x20100000;record=0x20110000
        m.u.mem_write(table,self.decoded[16:636]);m.wr(0x102bb630,table)
        for npc in OPPONENTS:
            for cp in (max(0,npc.name_threshold-1),npc.name_threshold,65535):
                m.u.mem_write(record,bytes(0x30));m.call(0x10110a70,[npc.npc_id,cp],this=record)
                raw=bytes(m.u.mem_read(record,0x30))
                self.assertEqual(tuple(raw[8:12]),(npc.personality.score_chance,npc.personality.preference,
                    npc.personality.preference_chance,npc.pack))
                name_selector=struct.unpack_from('<I',raw,12)[0]
                expected=struct.unpack_from('<I',self.decoded,16+20*(npc.npc_id if cp>=npc.name_threshold else 0)+12)[0]
                self.assertEqual(name_selector,expected)
                self.assertEqual((raw[0x1e],raw[0x21]),(npc.npc_id,1))
                self.assertEqual(npc.known_name(cp),npc.name if cp>=npc.name_threshold else None)

    def test_com_list_replaces_sequential_npc_ratings_not_selectors(self):
        for subtype in (2,4):
            for count in (0,1,15,30):
                with self.subTest(subtype=subtype,count=count):
                    vm=ReceiveVM();table=0x20100000
                    vm.u.mem_write(table,self.decoded[16:636]);vm.w32(0x102bb630,table)
                    before=bytes(vm.u.mem_read(table,620))
                    ratings=[1000+i for i in range(count)]
                    body='@ComList=/L='+str(count)+'/D='+'|'.join(map(str,ratings))
                    vm.queue(f'4300{subtype:02X}00'+body)
                    self.assertEqual(vm.receive(subtype),1)
                    self.assertEqual(vm.r8(vm.game+0x18c),count)
                    expected=bytearray(before)
                    for i,value in enumerate(ratings):struct.pack_into('<I',expected,20*(i+1)+4,value)
                    self.assertEqual(bytes(vm.u.mem_read(table,620)),bytes(expected))

    def test_com_init_sets_menu_fields_and_learns_sender_before_com_list(self):
        vm=ReceiveVM();table=0x20100000
        vm.u.mem_write(table,self.decoded[16:636]);vm.w32(0x102bb630,table)
        body='@ComGameInit=/RA=321/M=12345/CP=234/S=7/R=0|1|2|3|4|5|6@ComList=/L=1/D=299'
        vm.queue('43000200'+body,sender=(0x11223344,0x55667788))
        self.assertEqual(vm.receive(2),1)
        p=vm.player
        self.assertEqual([vm.r32(p+0x28),vm.r32(p+0x2c)],[0x11223344,0x55667788])
        self.assertEqual((vm.r32(p+4),vm.r32(p+0xc8),vm.r16(p),vm.r16(p+0xec)),(321,12345,234,7))
        self.assertEqual(vm.r32(p+0xdc),0)
        self.assertEqual([vm.r8(p+off) for off in (0xe0,0xe1,0xe2,0xe3,0xe4,0xd9)],[1,2,3,4,5,6])
        self.assertEqual(vm.r32(table+24),299)
        unchanged=bytes(vm.u.mem_read(table,620))
        vm.queue('43000400@ComList=/L=1/D=222',sender=(9,10))
        self.assertEqual(vm.receive(4),0)
        self.assertEqual(bytes(vm.u.mem_read(table,620)),unchanged)

    def test_complete_original_pool_loop_and_five_card_selection_match(self):
        for npc in OPPONENTS:
            for guild in (0,1,2,3):
                with self.subTest(npc=npc.npc_id,guild=guild):
                    seed=1000*npc.npc_id+guild
                    original_rng=Rng(seed);ported_rng=Rng(seed);m=NPCMachine(original_rng)
                    # Exact ten-card menu generation loop, not a Python loop
                    # around the original individual card generator.
                    stack=0x200ff000;edi=0x20140020;pool=0x102bb420
                    m.u.reg_write(UC_X86_REG_ESP,stack);m.u.reg_write(UC_X86_REG_EDI,edi)
                    m.u.reg_write(UC_X86_REG_EBP,pool);m.wr(stack+0x14,10)
                    m.u.mem_write(edi-0x14,bytes([npc.pack]));m.u.mem_write(0x102b648f,bytes([guild]))
                    m.u.emu_start(0x100ec17c,0x100ec1a4,count=4000000)
                    self.assertEqual(m.u.reg_read(UC_X86_REG_EIP),0x100ec1a4)
                    # Exact ordinary local selection function: first pool0,
                    # then four random remaining positions; art alone hooked.
                    rendered=0x20150000
                    m.wr(0x102bb62c,0x20140000);m.u.mem_write(0x20140000,b'\0')
                    m.u.mem_write(0x102b64ca,b'\0');m.u.mem_write(0x102b64c5,b'\2')
                    m.u.mem_write(0x102b65aa,b'\1');m.u.mem_write(0x102b657d,b'\0')
                    m.wr(0x102b65a4,rendered)
                    m.call(0x100d9de0,[1])
                    actual=[]
                    for i in range(5):
                        raw=bytes(m.u.mem_read(rendered+i*0x9c+8,16))
                        actual.append(Card(struct.unpack_from('<H',raw,6)[0],*raw[:6],raw[14]))
                    expected=prepare_opponent(npc.npc_id,self.catalog,ported_rng.next,guild)
                    self.assertEqual(tuple(actual),expected.hand)
                    self.assertEqual(original_rng.used,ported_rng.used)
                    self.assertEqual(len(expected.unused),5)

    def test_API_rejects_reserved_ID_and_preserves_known_NPC_names(self):
        for selector in (0,31,-1,True):
            with self.assertRaises(OpponentError):opponent(selector)
        self.assertEqual(opponent(1).name,b'Flower Girl Natasha')
        self.assertEqual(opponent(3).name,b'Quina')
        self.assertEqual(opponent(30).name,b'Regent Cid')
        for guild in (-1,4,True):
            with self.assertRaises(OpponentError):prepare_opponent(1,self.catalog,lambda:0,guild)


if __name__=='__main__':unittest.main()
