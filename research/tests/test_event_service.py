"""Event file builders against original consumer instructions, without UI."""
from pathlib import Path
from dataclasses import replace
import copy
import json
import struct
import sys
import unittest
from event_service import (SIZES, EventDirectory, EventListing, PrizeDisplay,
    EventMember, event_list, event_data, event_members, announcement, time_reply)
from event_service import EventHost,EventConfig,EventError
from economy_service import EconomyService,Catalog,named,parse_named

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'protocol_map'))
from event_contract_vectors import EventVM,EL,ED,EM,OUT,VIEW,HOST,PLAYER


class EventFileTests(unittest.TestCase):
    def test_empty_directory_has_complete_extents_and_no_events(self):
        service=EventDirectory()
        for path,size in SIZES.items():self.assertEqual(len(service.file(path)),size)
        self.assertEqual(struct.unpack_from('<I',service.file('b/g/TM0EventList'),4)[0],0)
        self.assertEqual(struct.unpack_from('<I',service.file('b/g/TM0EventMemberList'),4)[0],0)
        self.assertIsNone(service.file('b/g/unknown'))

    def test_list_key_pair_and_text_run_through_original_lookup(self):
        data=event_list([EventListing(b'REGION',b'Festival',b'Local test',1001,(1,2,3,4),3600,7200)])
        vm=EventVM();vm.u.mem_write(EL,data)
        vm.put(OUT,b'REGION');vm.put(OUT+64,b'Festival')
        self.assertEqual(vm.signedcall(0x1008b670,OUT,OUT+64),0)
        vm.put(OUT+64,b'festival')
        self.assertEqual(vm.signedcall(0x1008b670,OUT,OUT+64),-1)
        self.assertEqual(data[0x58:0x62],b'Local test')

    def test_configured_prizes_classes_missions_project_in_original(self):
        data=event_data(start=(2026,9,26,6,3600),end=(2026,9,26,6,7200),
            classes=(b'Alpha',b'Beta'),steps_per_class=4,
            prizes=(PrizeDisplay((5,9),500,b'First reward'),),missions={0:10,2:30,5:60})
        members=event_members([EventMember(PLAYER,b'Alice',171,0x21,0)])
        vm=EventVM();vm.u.mem_write(ED,data);vm.u.mem_write(EM,members)
        vm.call(0x10077860,this=VIEW)
        self.assertEqual(list(struct.unpack('<3H',vm.u.mem_read(VIEW+0x174,6))),[5,9,65535])
        self.assertEqual(vm.d(VIEW+0x170),500)
        self.assertEqual(vm.c(VIEW+0x17c),b'First reward')
        self.assertEqual(vm.d(VIEW+0x1414),2)
        vm.block(0x1006fad2,0x1006fc15,esi=VIEW)
        self.assertEqual((vm.d(VIEW+0x1d0),vm.d(VIEW+0x1d4)),(1,0))
        vm.block(0x100728fd,0x100729a5,esi=VIEW,ebp=VIEW+0x230,ebx=0)
        self.assertEqual(vm.d(VIEW+0x22c),2)
        self.assertEqual(vm.d(VIEW+0x2b0),4)
        self.assertEqual([vm.c(VIEW+0x230+i*32)for i in range(2)],[b'Alpha',b'Beta'])

    def test_opaque_template_bytes_are_preserved(self):
        template=bytes([0xa5])*0x1308
        result=event_data(template=template)
        for at in (0,0x34,0x4c,0x58,0x1000,0x1101,0x110c,0x12f8):
            self.assertEqual(result[at],0xa5)
        member=event_members([EventMember(1,b'Alice')],template=bytes([0xa5])*0x2808)
        self.assertEqual(member[8+0x18:8+0x1c],bytes([0xa5])*4)

    def test_time_reply_is_original_end_minus_now_contract(self):
        vm=EventVM();vm.queue(time_reply(100,500,200)[5:].decode())
        self.assertEqual(vm.signedcall(0x1008c270,OUT,OUT+4,OUT+8),1)
        self.assertEqual([vm.d(OUT+4*i)for i in range(3)],[100,500,300])

    def test_long_announcements_are_complete_original_page_groups(self):
        for length in (1,199,200,1000,1900):
            contents=(b'Event announcement. '*100)[:length]
            pages=announcement(1234567,contents,2)
            self.assertTrue(all(len(p)-13<=447 for p in pages))
            self.assertLessEqual(len(pages),10)
            vm=EventVM()
            results=[]
            for page in pages:
                vm.queue(page[5:].decode())
                results.append(vm.signedcall(0x1008c790))
            self.assertEqual(results,[0]*(len(pages)-1)+[len(pages)])
            self.assertEqual(b''.join(vm.c(HOST+0x5ad0+i*200)for i in range(len(pages))),contents)
            self.assertEqual(vm.d(HOST+0x5ac8),2)

    def test_client_array_and_string_bounds_are_enforced(self):
        bad=[
            lambda:event_list([EventListing(b'R'*32,b'E',b'')]),
            lambda:event_list([EventListing(b'R',b'E',b'')]*33),
            lambda:event_list([EventListing(b'R',b'E',b'')]*2),
            lambda:event_members([EventMember(1,b'X'*16)]),
            lambda:event_members([EventMember(1,b'A')]*2),
            lambda:event_members([EventMember(1,b'A',0x80000000)]),
            lambda:event_data(classes=(b'A',b'B'),steps_per_class=0),
            lambda:event_data(missions={0:1,1:1,2:1,3:1}),
            lambda:event_data(missions={7:0}),
            lambda:event_data(prizes=(PrizeDisplay(description=b'X'*40),)),
            lambda:announcement(2147483647,b'x'*1991,2147483647),
        ]
        for index,function in enumerate(bad):
            with self.subTest(case=index),self.assertRaises(ValueError):function()


class EventHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.catalog=Catalog()

    def setUp(self):
        self.e=EconomyService(seed=77,catalog=self.catalog)
        self.e.provision('one',nn=PLAYER,player_id=7,name=b'Alice')
        self.e.provision('two',nn=PLAYER+1,player_id=8,name=b'Bob')
        self.now=200
        self.config=EventConfig('festival',EventListing(b'REGION',b'Festival',b'Explicit local test'),
            event_data(classes=(b'Bronze',b'Silver',b'Gold'),steps_per_class=4,missions={0:10,2:3,4:2}),
            100,500,0x1111111111111111,0x2222222222222222,0x3333333333333333,
            match_enabled=True,card_service_enabled=True)
        self.h=EventHost(self.e,(self.config,),clock=lambda:self.now)

    def entry(self,key='one',kind='init',**overrides):
        view=self.e.account_view(key)
        values=dict(NN=f'{view.nn:016X}',HID='12345',Dm='0',Vol='0',CN=view.name.hex().upper())
        if kind=='init':values['GID']=f'{view.player_id:016X}'
        values.update(overrides)
        body='@'+('EInitReq' if kind=='init' else 'CardReq')+'='+''.join('/'+k+'='+v for k,v in values.items())
        return self.h.handle(key,named(0x98 if kind=='init' else 0x90,0,0,body),event_id='festival',route=kind)

    def join(self,key='one'):return self.h.admit_room(key,'festival')

    def answer(self,replies):return int(next(iter(parse_named(replies[0])[3].values()))['Ans'])

    def restart(self):
        snapshot=json.loads(json.dumps(self.h.snapshot()))
        self.e=EconomyService.restore(json.loads(json.dumps(self.e.snapshot())),catalog=self.catalog)
        self.h=EventHost.restore(self.e,(self.config,),snapshot,clock=lambda:self.now)

    def test_membership_entry_aliases_disconnect_and_reconnect(self):
        self.assertEqual(self.answer(self.entry()),-32870)
        self.assertEqual(self.answer(self.join()),1)
        for gid in (self.e.account_view('one').player_id,PLAYER):
            self.assertEqual(self.answer(self.entry(GID=f'{gid:016X}')),1)
            admission=self.h.admission('one')
            self.assertEqual((admission.key,admission.event_id,admission.mode),('one','festival',4))
        self.assertEqual(self.answer(self.entry(kind='card')),1)
        self.assertEqual(self.h.complete_admission('one').kind,'card')
        self.assertIsNone(self.h.admission('one'))
        self.h.disconnect('one')
        self.assertEqual(self.h.member('one','festival').identity,PLAYER)
        self.assertEqual(self.answer(self.entry()),-32870)
        self.assertEqual(self.answer(self.join()),1)
        self.assertEqual(len(self.h.snapshot()['events'][0]['members']),1)

    def test_authentication_and_route_fail_without_mutation(self):
        self.join();before=self.h.snapshot();inventory=self.e.snapshot()
        for changes in ({'NN':f'{PLAYER+1:016X}'},{'GID':'0000000000000008'},
                        {'CN':b'Bob'.hex()},{'Dm':'256'},{'Vol':'65536'},{'HID':'-1'},
                        {'GID':'1'},{'NN':'0'*17},{'CN':'00'}):
            with self.subTest(changes=changes),self.assertRaises(EventError):self.entry(**changes)
        payload=named(0xd1,4,0,f'@EventTimeReq=/NN={PLAYER+1:016X}')
        with self.assertRaises(EventError):self.h.handle('one',payload,event_id='festival',route='room')
        payload=named(0xd1,4,0,f'@EventTimeReq=/NN={PLAYER:016X}')
        with self.assertRaises(EventError):self.h.handle('one',payload,event_id='festival',route='init')
        self.assertEqual(self.h.snapshot(),before);self.assertEqual(self.e.snapshot(),inventory)
        self.assertIsNone(self.h.handle('one',b'GTM0G43000000@Select=',event_id='festival',route='init'))

    def test_schedule_inclusive_start_exclusive_end_and_exclusion(self):
        self.now=99;self.assertEqual(self.answer(self.join()),1)
        self.assertEqual(self.answer(self.entry()),-32870)
        self.now=100;self.assertEqual(self.answer(self.entry()),1)
        self.now=499;self.assertEqual(self.answer(self.entry()),1)
        self.now=500;self.assertEqual(self.answer(self.entry()),-32968)
        self.assertIsNone(self.h.admission('one'))
        self.assertEqual(self.answer(self.join('two')),-32870)
        self.assertIsNone(self.h.member('two','festival'))
        self.now=200;self.h.set_eligibility('one','festival',-1)
        self.assertEqual(self.answer(self.entry()),-32872)
        self.assertEqual(self.answer(self.entry(kind='card')),-32870)
        self.assertEqual(self.answer(self.join()),-32870)
        self.h.set_eligibility('one','festival',0)
        self.assertEqual(self.answer(self.join()),1)

    def test_match_capability_gate_and_economy_reservation(self):
        disabled=replace(self.config,match_enabled=False,card_service_enabled=False)
        self.h=EventHost(self.e,(disabled,),clock=lambda:self.now)
        self.join();self.assertEqual(disabled.service_records(),())
        self.assertEqual(self.answer(self.entry()),-32870)
        self.h=EventHost.restore(self.e,(self.config,),self.h.snapshot(),clock=lambda:self.now)
        self.join();reservation=self.e.reserve_match('one')
        self.assertEqual(self.answer(self.entry()),-32870)
        self.e.release_match(reservation)
        self.assertEqual(self.answer(self.entry()),1)
        self.e.handle('one',named(0xb0,0,0,f'@ShReq=/NN={PLAYER:016X}/HID=0/Dm=0/Vol=0/CN=416C696365'))
        self.assertEqual(self.answer(self.entry()),-32870)
        self.e.disconnect('one')
        self.assertEqual(self.answer(self.entry()),1)

    def test_fewer_than_five_cards_does_not_create_match_admission(self):
        self.e.provision('poor',nn=PLAYER+2,player_id=9,name=b'Poor',cards=[])
        self.join('poor')
        self.assertEqual(self.answer(self.entry('poor')),1)
        self.assertIsNone(self.h.admission('poor'))
        vm=EventVM();vm.queue(self.entry('poor')[0][5:].decode())
        vm.w(VIEW+0x190,3);vm.call(0x100761f0,this=VIEW)
        self.assertEqual(vm.d(VIEW+0x190),10)  # Original own-card shortage dialog.
        self.assertEqual(self.answer(self.entry('poor',kind='card')),1)
        self.assertIsNone(self.h.admission('poor').mode)  # F collection is not a match.

    def test_durable_idempotent_progress_and_account_isolation(self):
        self.join();self.join('two');inventory=self.e.snapshot()
        self.assertTrue(self.h.update_progress('one','festival','round1',steps_delta=3,completed_missions=1))
        self.assertFalse(self.h.update_progress('one','festival','round1',steps_delta=3,completed_missions=1))
        before=self.h.snapshot()
        for kwargs in ({'steps_delta':4,'completed_missions':1},{'steps_delta':-1},
                       {'completed_missions':2},{'steps_delta':True}):
            with self.assertRaises(ValueError):self.h.update_progress('one','festival','round1',**kwargs)
        self.assertEqual(self.h.snapshot(),before)
        self.restart();self.assertIsNone(self.h.admission('one'))
        self.assertEqual(self.answer(self.entry()),-32870)  # DE must be authenticated again.
        self.assertFalse(self.h.update_progress('one','festival','round1',steps_delta=3,completed_missions=1))
        self.assertTrue(self.h.update_progress('one','festival','round2',steps_delta=4,completed_missions=0x14))
        m=self.h.member('one','festival');self.assertEqual((m.steps,m.completed_missions),(7,0x15))
        self.assertEqual(self.h.member('two','festival').steps,0)
        self.assertEqual(self.e.snapshot(),inventory)  # No hidden currency/card/prize effect.

    def test_progress_overflow_and_excluded_account_are_atomic(self):
        self.join();self.h.update_progress('one','festival','max',steps_delta=0x7fffffff)
        before=self.h.snapshot()
        with self.assertRaises(EventError):self.h.update_progress('one','festival','overflow',steps_delta=1)
        self.assertEqual(self.h.snapshot(),before)
        self.h.set_eligibility('one','festival',-1)
        with self.assertRaises(EventError):self.h.update_progress('one','festival','banned',completed_missions=1)

    def test_restore_rejects_forged_owner_progress_and_configuration(self):
        self.join();self.h.update_progress('one','festival','round',steps_delta=4,completed_missions=1)
        snapshot=self.h.snapshot()
        modifications=[lambda d:d['events'][0]['members'][0].update(identity=PLAYER+1),
            lambda d:d['events'][0]['members'][0].update(steps=5),
            lambda d:d['events'][0]['members'][0].update(completed_missions=2),
            lambda d:d['events'][0]['updates'].append(d['events'][0]['updates'][0]),
            lambda d:d['events'].append(d['events'][0]),
            lambda d:d['events'][0].update(configuration='0'*64)]
        for mutate in modifications:
            candidate=copy.deepcopy(snapshot);mutate(candidate)
            with self.assertRaises(EventError):EventHost.restore(self.e,(self.config,),candidate)
        changed=replace(self.config,end=600)
        with self.assertRaises(EventError):EventHost.restore(self.e,(changed,),snapshot)

    def test_files_are_event_scoped_and_empty_default_stays_compatible(self):
        self.join();self.h.update_progress('one','festival','round',steps_delta=7,completed_missions=5)
        data=self.h.file('b/g/TM0EventMemberList')
        self.assertEqual(struct.unpack_from('<Q',data,8)[0],PLAYER)
        self.assertEqual(struct.unpack_from('<IIi',data,0x24),(7,5,0))
        empty=EventHost(self.e)
        for path in SIZES:self.assertEqual(empty.file(path),EventDirectory().file(path))
        second=replace(self.config,id='second',listing=replace(self.config.listing,key_event=b'Second'),
            room_id=11,init_id=12,card_id=13,init_channel=b'#SECONDE',card_channel=b'#SECONDF')
        both=EventHost(self.e,(self.config,second))
        with self.assertRaises(EventError):both.file('b/g/TM0EventDataList')
        self.assertEqual(both.file('b/g/TM0EventDataList','second'),second.data)
        self.assertEqual(struct.unpack_from('<I',both.file('b/g/TM0EventList'),4)[0],2)
        self.assertEqual(both.event_for_service(12),('second','init'))
        self.assertIsNone(both.event_for_service(99))

    def test_original_nn_to_member_identity_copy_and_progress_projection(self):
        vm=EventVM();vm.block(0x1014bc36,0x1014bc5b)
        self.assertEqual(vm.q(0x102b5308),PLAYER)
        self.join();self.h.update_progress('one','festival','round',steps_delta=511,completed_missions=5)
        vm.u.mem_write(ED,self.h.file('b/g/TM0EventDataList'))
        vm.u.mem_write(EM,self.h.file('b/g/TM0EventMemberList'))
        vm.call(0x10077860,this=VIEW)
        self.assertEqual(vm.d(VIEW+0x1414),2)

    def test_actual_original_requests_are_authenticated_and_responses_parse(self):
        self.join()
        for fn,route,family,receiver in ((0x1008b720,'card',0x91,0x1008b9b0),
                                       (0x1008bb30,'init',0x99,0x1008bdf0),
                                       (0x1008c190,'room',0xd1,0x1008c270)):
            vm=EventVM();vm.w(0x102b52a0,7);vm.w(0x102b52a4,0)
            service={'card':self.config.card_id,'init':self.config.init_id,'room':self.config.room_id}[route]
            vm.call(fn,service&0xffffffff,service>>32)
            request=b'GTM0G'+vm.sends[0]['payload'].encode()
            replies=self.h.handle('one',request,event_id='festival',route=route)
            self.assertEqual(parse_named(replies[0])[0],family)
            vm=EventVM();vm.queue(replies[0][5:].decode(),sender=service)
            args=(OUT,OUT+4,OUT+8) if route=='room' else (OUT,) if route=='init' else ()
            self.assertEqual(vm.signedcall(receiver,*args),1)
            if route=='room':self.assertEqual([vm.d(OUT+4*i) for i in range(3)],[100,500,300])
            else:self.assertEqual(vm.q(0x102b2958),service)
        vm=EventVM();vm.queue(self.join()[0][5:].decode())
        self.assertEqual(vm.signedcall(0x1008cdf0),1)

    def test_original_raw_e_and_f_discovery_selects_advertised_services(self):
        class DiscoveryVM(EventVM):
            def hook(self,u,a,n,data):
                if a==0x10086010:self.ret()
                else:super().hook(u,a,n,data)
        for fn,expected in ((0x100761f0,self.config.init_id),(0x1009a080,self.config.card_id)):
            vm=DiscoveryVM();rows=self.config.service_records()
            vm.u.mem_write(0x1028bb70,b''.join(rows));vm.w(0x102b290c,len(rows))
            vm.w(VIEW+0x190,1);vm.w(VIEW+0x178,1);vm.w(VIEW+0x17c,0)
            vm.call(fn,this=VIEW)
            self.assertEqual(vm.q(VIEW+0x188),expected)
            self.assertEqual(vm.d(VIEW+0x190),2)
        self.assertEqual([r[0x28:0x29] for r in replace(self.config,card_service_enabled=False).service_records()],[b'E'])
        self.assertEqual([r[0x28:0x29] for r in replace(self.config,match_enabled=False).service_records()],[b'F'])

    def test_original_time_updates_and_notices_use_distinct_now_contracts(self):
        vm=EventVM();vm.queue(self.h.time_update('festival')[5:].decode())
        self.assertEqual(vm.signedcall(0x1008bfa0,OUT,OUT+4,OUT+8),1)
        self.assertEqual([vm.d(OUT+4*i) for i in range(3)],[100,500,200])
        for subtype,args,value,ecm in ((1,{},100,None),(2,{},500,None),(8,{'close':510,'ecm':2},510,2)):
            vm=EventVM();vm.queue(self.h.time_notice('festival',subtype,**args)[5:].decode())
            self.assertEqual(vm.signedcall(0x1008c480,OUT,subtype,OUT+4),1)
            self.assertEqual(vm.d(OUT),value)
            if ecm is not None:self.assertEqual(vm.d(OUT+4),ecm)

    def test_untrusted_configuration_fails_before_publication(self):
        for changes in ({'end':100},{'room_id':0},{'init_id':self.config.room_id},
                        {'entry_answer':3},{'init_channel':b'bad name'},{'data':b''},
                        {'match_enabled':1}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(self.config,**changes)
        data=bytearray(self.config.data);struct.pack_into('<I',data,0x54,5)
        with self.assertRaises(EventError):replace(self.config,data=bytes(data))
        with self.assertRaises(EventError):EventHost(self.e,(self.config,self.config))

    def test_member_capacity_rejects_new_member_but_preserves_reentry(self):
        self.join();cards=self.e.inventory('one')[:5]
        for number in range(255):
            key=f'guest{number}'
            self.e.provision(key,nn=number+1,player_id=number+1,name=key.encode(),cards=cards)
            self.assertEqual(self.answer(self.join(key)),1)
        self.assertEqual(self.answer(self.join()),1)
        before=self.h.snapshot()
        self.assertEqual(self.answer(self.join('two')),-32870)
        self.assertEqual(self.h.snapshot(),before)
        self.assertEqual(struct.unpack_from('<I',self.h.file('b/g/TM0EventMemberList'),4)[0],256)


if __name__=='__main__':unittest.main()
