"""Saved-rule persistence and original importer/selector evidence; headless only."""
from pathlib import Path
import struct
import sys
import unittest
from economy_service import EconomyService,named
from table_preferences import (PreferenceError,TablePreferences,apply_preferences,
                               parse_save,read_preferences,GAME_FIELDS,TABLE_FIELDS)

GAME=dict(bm=1234,du=1,st=0,cb=1,ca=0,gs=2,tl=5)
TABLE={'in':2,'lu':98765,'ll':123,'au':321,'al':123,'co':8,'pa':1,'pw':b'abcd****'.hex()}


def save(game=GAME,table=TABLE,tbl=1,gm=1,primary=0x24):
    return named(primary,0,0,f'@Save=/Tbl={tbl}/Game={gm}'
                 +'@Tet='+''.join(f'/{k}={v}' for k,v in game.items())
                 +'@Tab='+''.join(f'/{k}={v}' for k,v in table.items()))


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.profile=bytes((i*17+3)&255 for i in range(0x3028))

    def test_selective_groups_preserve_every_other_profile_byte(self):
        for game,table in ((GAME,None),(None,TABLE),(GAME,TABLE)):
            out=apply_preferences(self.profile,game=game,table=table);allowed=set()
            for fields,enabled in ((GAME_FIELDS,game),(TABLE_FIELDS,table)):
                if enabled is not None:
                    for offset,size,_ in fields.values():allowed.update(range(offset,offset+size))
            if table is not None:allowed.update(range(0xF4,0xFE))
            self.assertTrue(all(a==b for i,(a,b) in enumerate(zip(self.profile,out)) if i not in allowed))
            values=read_preferences(out)
            if game:self.assertEqual(values['Tet'],game)
            if table:self.assertEqual(values['Tab'],dict(table,pw=table['pw'].upper()))
            self.assertEqual(len(out),0x3028)

    def test_parse_complete_sender_flags_select_only_the_named_group(self):
        for tbl,game in ((1,0),(0,1),(1,1)):
            args=parse_save(save(tbl=tbl,gm=game))
            self.assertEqual(args['game'],GAME if game else None)
            self.assertEqual(args['table'],dict(TABLE,pw=TABLE['pw'].upper()) if tbl else None)
        self.assertIsNone(parse_save(save(primary=0x14)))

    def test_bad_flags_headers_fields_and_values_fail_without_partial_result(self):
        malformed=[save(tbl=0,gm=0),save(tbl=2),save().replace(b'24000000',b'24000100'),
                   save().replace(b'/bm=1234',b'/bm=-1'),save().replace(b'/bm=1234',b'/bm=2001'),
                   save().replace(b'/du=1',b'/du=2'),save().replace(b'/tl=5',b'/tl=6'),
                   save().replace(b'/co=8',b'/co=9'),save().replace(b'/ll=123',b'/ll=99999'),
                   save().replace(b'/al=123',b'/al=99999'),save().replace(b'@Tet=',b'@Tet=/extra=1'),
                   save().replace(b'/Tbl=1',b'/Tbl=1/Tbl=1'),save().replace(b'/Game=1',b'')]
        for payload in malformed:
            with self.subTest(payload=payload),self.assertRaises(PreferenceError):parse_save(payload)
        for values in (dict(GAME,bm=True),dict(GAME,tl=6),dict(GAME,missing=0)):
            with self.assertRaises(PreferenceError):apply_preferences(self.profile,game=values)

    def test_password_roundtrip_cap_and_disabled_padding(self):
        for length in (0,3,8,9):
            table=dict(TABLE,pw=(b'x'*length).hex())
            out=apply_preferences(self.profile,table=table)
            self.assertEqual(read_preferences(out)['Tab']['pw'],table['pw'].upper())
        for value in ('00','AA0','GG','78'*10,'78'*12):
            with self.assertRaises(PreferenceError):apply_preferences(self.profile,table=dict(TABLE,pw=value))
        table=dict(TABLE,pa=0);table.pop('pw')
        out=apply_preferences(self.profile,table=table)
        self.assertEqual(out[0xF4:0xFE],b'********\0\0');self.assertNotIn('pw',read_preferences(out)['Tab'])

    def test_unselected_wire_password_can_be_twelve_bytes(self):
        table=dict(TABLE,pw=(b'123456789012').hex())
        self.assertEqual(parse_save(save(table=table,tbl=0))['game'],GAME)
        with self.assertRaises(PreferenceError):parse_save(save(table=table,tbl=1))

    def test_canonical_profile_and_at_least_one_group_required(self):
        for profile in (b'',bytes(0x3027),bytes(0x3029),'text'):
            with self.assertRaises(PreferenceError):apply_preferences(profile,game=GAME)
        with self.assertRaises(PreferenceError):apply_preferences(self.profile)

    def test_durable_account_restore_isolated_from_inventory_and_other_account(self):
        e=EconomyService(seed=4);e.provision('a',nn=1,name=b'A');e.provision('b',nn=2,name=b'B')
        adapter=TablePreferences(e);before=e.account_view('a');other=e.profile_bytes('b')
        self.assertEqual(adapter.handle('a',save()),[])
        restored=EconomyService.restore(e.snapshot())
        self.assertEqual(read_preferences(restored.profile_bytes('a')),
                         {'Tet':GAME,'Tab':dict(TABLE,pw=TABLE['pw'].upper())})
        self.assertEqual(restored.account_view('a'),before);self.assertEqual(restored.profile_bytes('b'),other)
        self.assertIsNone(adapter.handle('a',named(0x14,0,0,'@Tet=')))

    def test_atomic_rejection_does_not_change_template(self):
        e=EconomyService(seed=4);e.provision('a',nn=1,name=b'A');before=e.snapshot()
        with self.assertRaises(PreferenceError):
            e.save_table_preferences('a',game=GAME,table=dict(TABLE,pw='AA'*12))
        self.assertEqual(e.snapshot(),before)

    def test_save_during_pending_purchase_preserves_transaction_and_eventual_commit(self):
        e=EconomyService(seed=4);e.provision('a',nn=1,name=b'A')
        entry=e.handle('a',named(0xB0,0,0,'@ShReq=/NN=0000000000000001/HID=0/Dm=0/Vol=0/CN=41'))
        from economy_service import parse_named
        session=int(parse_named(entry[1])[3]['Init']['N'])
        e.handle('a',named(0xB2,0x14,session,'@Buy=/No=1'))
        before=e.account_view('a');e.save_table_preferences('a',game=GAME)
        self.assertEqual(e.account_view('a'),before)
        e.handle('a',named(0xB2,0x15,session,f'@Get=/No={session}'))
        self.assertGreater(len(e.inventory('a')),len(before.cards))
        self.assertEqual(read_preferences(e.profile_bytes('a'))['Tet'],GAME)

    def test_save_during_auction_reservation_keeps_reservation_live(self):
        e=EconomyService(seed=4);e.provision('a',nn=1,name=b'A')
        reservation=e.reserve_auction('a');e.save_table_preferences('a',table=TABLE)
        self.assertTrue(e.is_reserved('a'));e.release_auction(reservation)
        self.assertFalse(e.is_reserved('a'));self.assertEqual(read_preferences(e.profile_bytes('a'))['Tab']['lu'],98765)


class OriginalPreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from result_reward_vectors import VM
        from table_admission_vectors import AdmissionVM
        cls.VM,cls.AdmissionVM=VM,AdmissionVM

    def import_profile(self,profile,tail=b'xxxxxxxxxxxx\0'):
        v=self.VM();v.u.mem_write(0x10294BD0,profile);v.u.mem_write(0x102B5EF7,tail)
        for address in (0x100A7F10,0x100A7F70,0x100A7B60):v.hooks[address]=lambda sp:v.ret(sp)
        v.call(0x1014D9F0)
        game={'bm':v.r32(0x102B5ED4)}
        game.update({k:v.r8(0x102B5ED4+i) for k,i in (('du',4),('st',5),('cb',6),('ca',7),('gs',8),('tl',9))})
        table={'in':v.r8(0x102B5EE0),'lu':v.r32(0x102B5EE4),'ll':v.r32(0x102B5EE8),
               'au':v.r32(0x102B5EEC),'al':v.r32(0x102B5EF0),'co':v.r8(0x102B5EF4),'pa':v.r8(0x102B5EF5)}
        if table['pa']:table['pw']=bytes(v.u.mem_read(0x102B5EF7,13)).split(b'\0',1)[0].hex().upper()
        return v,{'Tet':game,'Tab':table}

    def test_original_importer_all_rules_and_password_lengths(self):
        for tl in range(6):
            for length in (0,3,8,9):
                with self.subTest(tl=tl,password_length=length):
                    game=dict(GAME,tl=tl);table=dict(TABLE,pw=(b'x'*length).hex().upper())
                    out=apply_preferences(bytes(0x3028),game=game,table=table)
                    v,loaded=self.import_profile(out)
                    self.assertEqual(loaded,{'Tet':game,'Tab':table})
                    # Original game-editor Reload uses imported saved defaults.
                    scene=0x20350000;v.w32(scene+0x150,0);v.hooks[0x100A7E90]=lambda sp:v.ret(sp,99999)
                    v.call(0x1004ECA0,this=scene)
                    self.assertEqual([v.r32(scene+0x18C+4*i) for i in range(7)],list(game.values()))
                    v.w32(scene+0x194,0);v.call(0x100515C0,this=scene)
                    self.assertEqual([v.r32(scene+o) for o in (0x1C4,0x1C8,0x1CC,0x1D0,0x1D4)],
                                     [table['co'],table['pa'],table['in'],table['ll'],table['lu']])
                    self.assertEqual(bytes(v.u.mem_read(scene+0x148,12)).split(b'\0',1)[0],
                                     bytes.fromhex(table['pw']))

    def test_original_importer_retains_stale_tail_for_ten_or_twelve_byte_password(self):
        for length in (10,12):
            profile=bytearray(0x3028);profile[0xF0]=1;profile[0xF4:0xF4+length]=b'1234567890AB'[:length]
            v,loaded=self.import_profile(bytes(profile))
            self.assertEqual(bytes(v.u.mem_read(0x102B5EF7,13)),b'1234567890xx\0')
            self.assertEqual(bytes.fromhex(loaded['Tab']['pw']),b'1234567890xx')

    def test_original_sender_Save_flags_and_complete_structs_roundtrip(self):
        from table_admission_vectors import SERVICE
        for tbl,game in ((1,0),(0,1),(1,1)):
            v=self.AdmissionVM();g=0x20370000;t=0x20371000
            v.w32(g,GAME['bm'])
            for i,k in enumerate(('du','st','cb','ca','gs','tl')):v.w8(g+4+i,GAME[k])
            for k,o in (('in',0),('co',20),('pa',21)):v.w8(t+o,TABLE[k])
            for k,o in (('lu',4),('ll',8),('au',12),('al',16)):v.w32(t+o,TABLE[k])
            v.u.mem_write(t+23,bytes.fromhex(TABLE['pw'])+b'\0')
            v.call(0x10087410,[t,g,SERVICE&0xffffffff,SERVICE>>32,tbl,game],this=v.controller)
            self.assertEqual(len(v.sends),1)
            parsed=parse_save(b'GTM0G'+v.sends[0]['payload'].encode())
            self.assertEqual(parsed,{'game':GAME if game else None,'table':dict(TABLE,pw=TABLE['pw'].upper()) if tbl else None})


if __name__=='__main__':unittest.main()
