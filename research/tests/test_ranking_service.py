"""Local ranking policy plus original x86 request/callback/file/UI consumers."""
from pathlib import Path
import struct
import sys
import unittest

from economy_service import EconomyService, PROFILE_SIZE
from identities import game_id
from ranking_service import (RankingService, RankingHistory, RankingError,
                             RECORD_SIZE, RESULT_PATH, METADATA_PATH, UI_CATEGORIES)
from tm_protocol import Command, encode, parse


def request(category, identity, *, directory=1, version=1, selector=0):
    return b'GTM0R' + encode([Command(b'RR', str(category).encode()),
        Command(b'PI', f'0x{identity:016X}\x06{directory}\x06{version}\x06{selector}'.encode())])


def fixture():
    economy = EconomyService(seed=1)
    for key, raw, name, rating, total in [('alice', 1, b'Alice', 400, 900),
                                           ('bob', 2, b'Bob', 500, 300)]:
        template = bytearray(PROFILE_SIZE)
        struct.pack_into('<I', template, 0x30, rating)
        struct.pack_into('<I', template, 0x50, total)
        economy.provision(key, nn=game_id(raw), player_id=raw, name=name,
                          profile_template=bytes(template))
    return economy


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.economy = fixture()
        self.service = RankingService(self.economy)

    def query(self, category=2, who='alice'):
        reply = self.service.handle(who, request(category, self.economy.account_view(who).nn))[0]
        parsed = parse(reply[5:])
        self.assertEqual([x.token for x in parsed], [b'RF', b'LN'])
        path, count = parsed[0].payload.decode(), int(parsed[1].payload)
        data = self.service.file(who, path)
        self.assertEqual(len(data), count * RECORD_SIZE)
        return path, data

    def test_current_rating_and_total_prize_use_distinct_committed_fields(self):
        _, rating = self.query(2)
        self.assertEqual([rating[i:i+16].split(b'\0')[0] for i in range(0,len(rating),RECORD_SIZE)], [b'Bob', b'Alice'])
        self.assertEqual([struct.unpack_from('<I', rating,i+0xA0)[0] for i in range(0,len(rating),RECORD_SIZE)], [500,400])
        _, total = self.query(0)
        self.assertEqual(total[:6], b'Alice\0')
        self.assertEqual(struct.unpack_from('<I',total,0x94)[0],900)
        self.assertNotEqual(struct.unpack_from('<I',total,0x94)[0],self.economy.balance('alice'))

    def test_historical_categories_are_empty_without_supplied_history(self):
        for category in (1,3,4):
            with self.subTest(category=category):
                _, data = self.query(category)
                self.assertEqual(data,b'')
        self.assertEqual(struct.unpack('<7I',self.service.metadata()),(0,2,0,0,0,0,0))

    def test_explicit_history_produces_weekly_top_and_rookie_records(self):
        self.service = RankingService(self.economy, update_time=1700000000, historical_stats={
            'alice':RankingHistory(weekly_prize=25,last_week_rating=333,rookie=True,previous_ranks=(7,6,5,4,3)),
            'bob':RankingHistory(weekly_prize=50,last_week_rating=444)})
        _, weekly = self.query(1)
        self.assertEqual(weekly[:4],b'Bob\0')
        _, top = self.query(3)
        self.assertEqual(struct.unpack_from('<I',top,0xA4)[0],444)
        _, rookie = self.query(4)
        self.assertEqual(len(rookie),RECORD_SIZE)
        self.assertEqual(rookie[:6],b'Alice\0')
        self.assertEqual(list(rookie[0xC3:0xC8]),[3,4,5,7,6])
        self.assertEqual(struct.unpack('<7I',self.service.metadata()),(0,2,2,1700000000,0,2,1))

    def test_name_termination_anonymity_and_rating_qualification(self):
        self.economy.provision('long',nn=game_id(3),player_id=3,name=b'ABCDEFGHIJKLMNOP')
        self.service = RankingService(self.economy,historical_stats={'bob':RankingHistory(anonymous=True,qualified=False)})
        _, data = self.query(2)
        self.assertEqual(len(data),2*RECORD_SIZE)
        self.assertNotIn(b'Bob\0',data)
        self.assertIn(b'ABCDEFGHIJKLMNO\0',data)
        _, data = self.query(0)
        row = next(data[i:i+RECORD_SIZE] for i in range(0,len(data),RECORD_SIZE) if data[i:i+4]==b'Bob\0')
        self.assertEqual((row[0xC0],row[0xC2]),(1,0))
        self.assertEqual(self.economy.account_view('long').name,b'ABCDEFGHIJKLMNOP')

    def test_prior_query_and_other_account_snapshots_remain_immutable(self):
        first, before = self.query(2)
        bob_path, bob_data = self.query(0,'bob')
        self.economy.provision('new',nn=game_id(3),player_id=3,name=b'New')
        second, after = self.query(0)
        self.assertNotEqual(first,second)
        self.assertEqual(self.service.file('alice',first),before)
        self.assertEqual(len(after),3*RECORD_SIZE)
        self.assertIsNone(self.service.file('bob',first))
        self.assertIsNone(self.service.file('alice',bob_path))
        self.assertEqual(self.service.file('bob',bob_path),bob_data)

    def test_profile_privacy_controls_new_files_without_renaming_account(self):
        path,before=self.query(0)
        self.economy.save_ranking_privacy('alice',1)
        _,hidden=self.query(0)
        self.assertEqual(hidden[0xC0],1);self.assertEqual(hidden[:16],before[:16])
        self.assertEqual(self.service.file('alice',path),before)
        self.economy.save_ranking_privacy('alice',0)
        _,shown=self.query(0)
        self.assertEqual(shown[0xC0],0);self.assertEqual(self.economy.account_view('alice').name,b'Alice')

    def test_exact_delete_and_canonical_cleanup_are_account_scoped(self):
        first,_ = self.query()
        second,_ = self.query(0)
        bob,_ = self.query(0,'bob')
        self.assertTrue(self.service.delete('alice',first))
        self.assertTrue(self.service.delete('alice',first))
        self.assertIsNone(self.service.file('alice',first))
        self.assertIsNotNone(self.service.file('alice',second))
        self.assertTrue(self.service.delete('alice',RESULT_PATH))
        self.assertIsNone(self.service.file('alice',second))
        self.assertIsNotNone(self.service.file('bob',bob))
        self.assertFalse(self.service.delete('alice',METADATA_PATH))
        self.assertFalse(self.service.delete('alice','../'+RESULT_PATH))

    def test_capacity_failure_does_not_evict_old_file_and_delete_recovers(self):
        self.service = RankingService(self.economy,max_snapshots=1)
        path, old = self.query()
        reply = self.service.handle('alice',request(0,game_id(1)))[0]
        self.assertEqual(parse(reply[5:]),[Command(b'RG',b'-8809')])
        self.assertEqual(self.service.file('alice',path),old)
        self.service.delete('alice',RESULT_PATH)
        self.query(0)

    def test_invalid_rank_returns_mapped_error_without_snapshot(self):
        reply = self.service.handle('alice',request(5,game_id(1)))[0]
        self.assertEqual(parse(reply[5:]),[Command(b'RG',b'-8811')])
        self.assertIsNone(self.service.file('alice',RESULT_PATH+'_00000001'))

    def test_malformed_or_other_owner_requests_never_store_data(self):
        cases = [request(2,game_id(2)), request(2,game_id(1),directory=256),
                 request(2,game_id(1),version=65536), request(2,game_id(1),selector=1),
                 request(2,game_id(1)).replace(b'<PI>',b'<RR>'),
                 request(2,game_id(1)).replace(b'0xB62D',b'B62D'),
                 request(2,game_id(1))+b'garbage']
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.service.handle('alice',data)
        path,_=self.query()
        self.assertEqual(path,RESULT_PATH+'_00000001')
        self.assertIsNone(self.service.handle('alice',b'GTM0GE1000000@TeachDV='))
        self.assertIsNone(self.service.handle('alice',b'GTM0R'+encode([Command(b'GO',b'opaque')])))

    def test_saved_account_restore_gives_same_order_and_scores(self):
        before=self.economy.snapshot()
        restored=EconomyService.restore(before,catalog=self.economy.catalog)
        other=RankingService(restored)
        for category in range(5):
            self.assertEqual(other.records(category),self.service.records(category))
        self.assertEqual(self.economy.snapshot(),before)

    def test_equal_scores_have_stable_identity_then_character_order(self):
        self.economy.provision('z',nn=game_id(3),player_id=3,name=b'Z')
        self.economy.provision('a',nn=game_id(4),player_id=4,name=b'A')
        data=self.service.records(0)
        zero=[struct.unpack_from('<QI',data,i+0x10) for i in range(2*RECORD_SIZE,len(data),RECORD_SIZE)]
        self.assertEqual(zero,sorted(zero))

    def test_reader_limits_and_top30_limit_are_explicit_policy(self):
        for index in range(3,105):
            self.economy.provision(str(index),nn=game_id(index),player_id=index,name=str(index).encode(),cards=[])
        history={key:RankingHistory(last_week_rating=1,rookie=True) for key in self.economy.snapshot()['accounts']}
        self.service=RankingService(self.economy,historical_stats=history)
        self.assertEqual(len(self.service.records(0)),100*RECORD_SIZE)
        self.assertEqual(len(self.service.records(3)),30*RECORD_SIZE)
        self.assertEqual(len(self.service.records(4)),100*RECORD_SIZE)
        self.assertEqual(struct.unpack_from('<I',self.service.metadata(),0x14)[0],104)

    def test_bad_configuration_rejected(self):
        for kwargs in ({'update_time':-1},{'max_snapshots':0},{'historical_stats':{'alice':{}}}):
            with self.subTest(kwargs=kwargs),self.assertRaises(RankingError):
                RankingService(self.economy,**kwargs)
        for kwargs in ({'weekly_prize':-1},{'rookie':1},{'previous_ranks':(0,0,0,0,128)}):
            with self.subTest(kwargs=kwargs),self.assertRaises(RankingError):
                RankingHistory(**kwargs)


class NativeRankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from auction_rank_vectors import Machine, SCENE, OUT
            from profile_contract_vectors import ProfileVM
        except ImportError as exc:
            raise unittest.SkipTest('original-code emulator unavailable: '+str(exc))
        cls.Machine,cls.ProfileVM,cls.SCENE,cls.OUT=Machine,ProfileVM,SCENE,OUT

    def test_original_rr_builder_matches_supported_request(self):
        base=self.ProfileVM
        class RankVM(base):
            def hook(self,u,a,n,d):
                if a==0x1019E9C0:
                    from unicorn.x86_const import UC_X86_REG_ESP
                    sp=u.reg_read(UC_X86_REG_ESP)
                    self.sent.append({'payload':self.c(self.d(sp+16)),'target':self.q(sp+4)})
                    self.ret(1)
                else:
                    super().hook(u,a,n,d)
        for category in range(5):
            with self.subTest(category=category):
                vm=RankVM()
                for address,value in [(0x10324148,0x100),(0x1032414C,0),
                    (0x10324150,game_id(1)&0xFFFFFFFF),(0x10324154,game_id(1)>>32),
                    (0x10324158,1),(0x1032415A,1),(0x1032415C,0),(0x10324160,0)]:
                    vm.w(address,value)
                self.assertEqual(vm.call(0x101A6CB0,self.OUT,category),0)
                self.assertEqual(vm.sent[-1]['payload'],request(category,game_id(1))[5:])
                self.assertEqual(vm.sent[-1]['target'],0x100)

    def test_original_rf_callback_poll_and_file_slice_accept_generated_reply(self):
        service=RankingService(fixture())
        for category in (2,1):  # Nonempty and deliberately empty historical result.
            with self.subTest(category=category):
                reply=service.handle('alice',request(category,game_id(1)))[0]
                vm=self.Machine()
                vm.w32(0x10324160,1)
                vm.w32(0x10324168,self.OUT)
                vm.callback(0x101A6BD0,41,reply[5:].decode('ascii'))
                self.assertEqual(vm.call(0x101A6E70),1)
                count=2 if category==2 else 0
                self.assertEqual(vm.r32(self.OUT),count)
                path=vm.text(0x10324170)
                self.assertEqual(len(service.file('alice',path)),count*RECORD_SIZE)
                vm.call(0x101A6F70,[self.OUT+0x100,1,1])
                self.assertEqual((vm.files[-1]['filename'],vm.files[-1]['size'],vm.files[-1]['offset']),
                                 (path,RECORD_SIZE,RECORD_SIZE))

    def test_original_rg_callback_returns_service_error(self):
        service=RankingService(fixture())
        reply=service.handle('alice',request(5,game_id(1)))[0]
        vm=self.Machine();vm.w32(0x10324160,1)
        vm.callback(0x101A6BD0,42,reply[5:].decode('ascii'))
        self.assertEqual(vm.call(0x101A6E70),-8811)

    def test_original_projection_reads_all_generated_categories_and_own_row(self):
        economy=fixture()
        history={'alice':RankingHistory(weekly_prize=50,last_week_rating=600,rookie=True,previous_ranks=(7,6,5,4,3)),
                 'bob':RankingHistory(weekly_prize=50,last_week_rating=700,rookie=True)}
        economy.save_ranking_privacy('bob',1)
        service=RankingService(economy,historical_stats=history)
        for rank_id,category in UI_CATEGORIES.items():
            with self.subTest(rank_id=rank_id):
                data=service.records(rank_id);count=len(data)//RECORD_SIZE
                vm=self.Machine();scene=self.SCENE
                vm.w32(scene+0x63AC,category);vm.w32(0x102F46F0+4*category,count)
                vm.w32(0x102FF4A0,0x20370000);vm.w32(0x20370084,0x20370100);vm.string(0x20370100,'-------')
                vm.w32(0x102B52A0,game_id(1)&0xFFFFFFFF);vm.w32(0x102B52A4,game_id(1)>>32);vm.w32(0x102B529C,1)
                vm.u.mem_write(scene+0x120,data)
                for i in range(count):vm.w32(0x102F4748+(category*100+i)*0x2C+0x28,i+1)
                vm.block(0x101752A8,0x101754F8,esi=scene,ebp=0)
                score_offset={0:0x94,1:0x9C,2:0xA0,3:0xA4,4:0xA4}[rank_id]
                scores=[struct.unpack_from('<I',data,i*RECORD_SIZE+score_offset)[0] for i in range(count)]
                current_rank=0;last=None;own_rank=None
                for i in range(count):
                    addr=0x102F4748+(category*100+i)*0x2C
                    record=data[i*RECORD_SIZE:(i+1)*RECORD_SIZE]
                    name='-------' if record[0xC0] else record[:16].split(b'\0')[0].decode()
                    self.assertEqual(vm.text(addr),name)
                    self.assertEqual(vm.r32(addr+0x20),scores[i])
                    current_rank=current_rank if scores[i]==last else i+1
                    self.assertEqual(vm.r32(addr+0x28),current_rank)
                    if struct.unpack_from('<Q',record,0x10)[0]==game_id(1):own_rank=current_rank
                    last=scores[i]
                self.assertEqual(vm.r32(0x102F470C+4*category),own_rank)

    def test_original_metadata_import_accepts_exact_28_bytes(self):
        service=RankingService(fixture(),update_time=1700000000,historical_stats={
            'alice':RankingHistory(weekly_prize=1,last_week_rating=2,rookie=True)})
        vm=self.Machine();vm.u.mem_write(0x102923AC,service.metadata())
        vm.block(0x101756E3,0x10175724,esi=self.SCENE)
        self.assertEqual([vm.r32(0x102F46D4+4*i) for i in range(6)],[2,1,1,0,2,1])
        self.assertEqual(vm.r32(0x102F46CC),1700000000)


if __name__=='__main__':
    unittest.main()
