"""Read-only spectator bootstrap, bounded pacing and original x86 consumers."""
from dataclasses import replace
from pathlib import Path
import struct
import sys
import unittest

from match_service import Card, Delivery, MatchService, decode, packet
from native_special_rules import Cell,start_board
from spectator_service import SpectatorService,SpectatorError,bootstrap,snapshot_fields
from test_match_service import service,select,ack,put,CARDS

WATCH=packet(0x43,0x25,0,'@Data=/Watch=0')


class SpectatorTests(unittest.TestCase):
    def setUp(self):
        self.match,self.initial=service('versus')
        self.now=0.0
        self.s=SpectatorService(enabled=True,clock=lambda:self.now)

    def watch(self,peer='watcher'):
        return self.sent(self.s.handle(peer,WATCH,self.match.observer_snapshot(),epoch=self.match))

    def sent(self,output):
        for delivery in output:
            self.assertTrue(self.s.record_sent(delivery.peer,delivery.payload,epoch=self.match))
        return output

    def publish(self,output):
        return self.sent(self.s.publish(output,self.match.observer_snapshot(),epoch=self.match))

    def poll(self,seconds=100):
        self.now+=seconds
        return self.sent(self.s.poll(self.match.observer_snapshot(),epoch=self.match))

    def move(self):
        result=[]
        for seat,p in enumerate(self.match.players):result+=self.match.handle(p.peer,ack(seat,self.match.sequence))
        result+=self.match.handle(self.match.players[self.match.active].peer,put(self.match.active,self.match.sequence,0,self.match.legal_fields()[0]))
        return result

    def test_disabled_by_default_and_watch_does_not_claim_player_or_rng(self):
        disabled=SpectatorService();before=self.match.snapshot();random=self.match.rng.state
        self.assertIsNone(disabled.handle('watcher',WATCH,self.match.observer_snapshot(),epoch=self.match))
        out=self.watch();self.assertEqual([decode(x.payload).subtype for x in out],[0x25,0x26,9])
        self.assertEqual(out[-1].payload,self.match.observer_snapshot().turn_packet)
        self.assertEqual(self.match.snapshot(),before);self.assertEqual(self.match.rng.state,random)
        self.assertEqual(self.match.acks,set());self.assertEqual(len(self.match.players),2)

    def test_selection_bootstrap_has_no_unproven_hand_or_turn_packet(self):
        m=MatchService(mode='versus');m.register('a',1,name=b'A');m.register('b',2,name=b'B');m.begin()
        out=bootstrap(m.observer_snapshot())
        self.assertEqual(len(out),1);msg=decode(out[0])
        self.assertEqual((msg.subtype,msg.argument),(0x25,0))
        self.assertNotIn('H',msg.sections['WatchInfo'])
        rules=dict(m.observer_snapshot().game_rules)
        self.assertEqual(msg.sections['WatchInfo']['R'],'|'.join(str(rules[k]) for k in ('st','cb','ca','tl','du','gs'))+'|'+str(m.table_rules['in']))

    def test_names_and_snapshots_are_bounded_without_mutating_live_state(self):
        snapshot=self.match.observer_snapshot()
        with self.assertRaises(SpectatorError):bootstrap(replace(snapshot,turn_packet=None))
        with self.assertRaises(SpectatorError):bootstrap(replace(snapshot,active=1))
        with self.assertRaises(SpectatorError):bootstrap(replace(snapshot,players=(replace(snapshot.players[0],name=b'A'*16),snapshot.players[1])))
        with self.assertRaises(SpectatorError):snapshot_fields(replace(snapshot,board=(replace(snapshot.board[0],marker=1),)+snapshot.board[1:]))
        self.assertEqual(snapshot,self.match.observer_snapshot())

    def test_original_field_encoding_for_all_special_tiles_and_visible_modifiers(self):
        snapshot=self.match.observer_snapshot()
        for value in range(1,19):
            fields=[0]*16;fields[5]=value;board=start_board(fields).cells
            actual,modifiers,cards=snapshot_fields(replace(snapshot,board=board))
            self.assertEqual(actual,tuple(fields));self.assertFalse(cards);self.assertEqual(modifiers,(0,)*16)
        for prop,low in ((0,0),(1,3),(2,4),(3,5)):
            for owner in (0,1):
                cell=Cell.ordinary(CARDS[0],owner,property=prop,modifier=-10)
                f,ch,cards=snapshot_fields(replace(snapshot,board=(cell,)+snapshot.board[1:]))
                self.assertEqual(f[0],low|((owner+1)<<6));self.assertEqual(ch[0],246)
                self.assertEqual(cards,((0,CARDS[0]),))

    def test_duplicate_watch_never_replays_bootstrap_and_participant_cannot_watch(self):
        self.watch();self.assertEqual(self.watch(),[]);self.assertEqual(self.s.count,1)
        with self.assertRaises(SpectatorError):self.watch('alice')
        bad=packet(0x43,0x25,0,'@Data=/Watch=1')
        with self.assertRaises(SpectatorError):self.s.handle('new',bad,self.match.observer_snapshot(),epoch=self.match)
        with self.assertRaises(SpectatorError):self.s.handle('watcher',put(0,0,0,0),self.match.observer_snapshot(),epoch=self.match)

    def test_generated_cards_can_exceed_selected_hand_count_in_snapshot(self):
        sys.path.insert(0,str(Path(__file__).parents[1]/'protocol_map'))
        from result_reward_vectors import VM
        snapshot=self.match.observer_snapshot()
        card=Card(0,1,0,1,1,0,0)
        for count in (11,16):
            board=tuple(Cell.ordinary(card,i%2)if i<count else cell for i,cell in enumerate(snapshot.board))
            payloads=bootstrap(replace(snapshot,board=board))
            vm=VM(role=4)
            for payload in payloads:vm.queue(payload[5:].decode())
            self.assertEqual(vm.receive(0x25),15)
            self.assertEqual(vm.receive(0x26),1)
            self.assertEqual(sum(vm.r16(vm.player+0x4d76+i*16)==0 for i in range(16)),count)
            self.assertEqual(vm.sends,[])

    def test_observer_never_extends_or_participates_in_turn_ack_barrier(self):
        self.watch();self.match.handle('alice',ack(0,0));self.assertEqual(self.match.acks,{'alice'})
        self.match.handle('bob',ack(1,0));self.assertEqual(self.match.acks,{'alice','bob'})
        output=self.match.handle('alice',put(0,0,0,0));self.publish(output)
        self.assertEqual(self.match.sequence,1)

    def test_ordered_live_queue_deduplicates_recipient_copies(self):
        self.watch();output=self.move();self.assertEqual(self.publish(output),[])
        first=self.poll(8);self.assertEqual(len(first),1);self.assertEqual(decode(first[0].payload).subtype,0x0A)
        self.assertEqual(self.poll(2),[])
        second=self.poll(1);self.assertEqual(len(second),1);self.assertEqual(decode(second[0].payload).subtype,9)
        self.assertEqual(decode(second[0].payload).argument,1)
        self.assertEqual(self.poll(),[])

    def test_equal_repeated_battle_packets_are_not_deduplicated_as_events(self):
        self.watch();battle=packet(0x43,0x0C,0,'@BattleData=/A=0|1|1|1|1/D=1|1|1|1|1/R=0|0|0|0|0|0|0|0')
        batch=[Delivery('alice',battle),Delivery('bob',battle)]*2
        self.publish(batch)
        self.assertEqual(self.poll()[0].payload,battle)
        self.assertEqual(self.poll()[0].payload,battle)
        self.assertEqual(self.poll(),[])

    def test_duplicate_turn_is_suppressed_and_gap_evicts_observer_only(self):
        self.watch();current=self.match.observer_snapshot().turn_packet
        self.publish([Delivery('alice',current),Delivery('bob',current)])
        self.assertEqual(self.poll(),[])
        gap=packet(0x43,9,3,'@TurnData=/A=1/S=0|0/R=0|0|0|0|0|0|0|0')
        before=self.match.snapshot();self.publish([Delivery('alice',gap)])
        self.assertEqual(self.s.take_evicted(),(('watcher','turn_gap'),))
        self.assertEqual(self.match.snapshot(),before)

    def test_deferred_battle_bootstrap_waits_for_stable_turn_and_skips_prior_batch(self):
        snapshot=self.match.observer_snapshot()
        self.assertEqual(self.s.handle('watcher',WATCH,replace(snapshot,phase='battle'),epoch=self.match),[])
        output=self.move();out=self.publish(output)
        self.assertEqual([decode(d.payload).subtype for d in out],[0x25,0x26,9])
        self.assertEqual(decode(out[-1].payload).argument,1)
        self.assertEqual(self.poll(),[])

    def test_backlog_and_capacity_are_bounded_and_do_not_close_participants(self):
        self.s=SpectatorService(enabled=True,clock=lambda:self.now,max_backlog=1,max_observers=1)
        self.watch()
        with self.assertRaises(SpectatorError):self.watch('second')
        before=self.match.snapshot();payload=packet(0x43,0x0A,0,'@PutCard=/A=0/H=0/F=255')
        self.publish([Delivery('alice',payload),Delivery('alice',payload)])
        self.assertEqual(self.s.take_evicted(),(('watcher','backlog_limit'),))
        self.assertEqual(self.match.snapshot(),before)

    def test_reset_and_stale_epoch_cannot_replay_into_replacement_match(self):
        self.watch();self.publish(self.move());old=self.match
        self.match,_=service('versus');self.s.reset(self.match)
        self.assertEqual(self.s.take_evicted(),(('watcher','match_replaced'),))
        self.watch('new')
        self.assertEqual(self.s.publish(self.initial,old.observer_snapshot(),epoch=old),[])
        self.assertEqual(self.s.poll(old.observer_snapshot(),epoch=old),[])
        self.assertTrue(self.s.active('new'));self.assertEqual(self.poll(),[])

    def test_private_hand_packets_are_not_relayed_and_preview_stops_at_result(self):
        self.watch();private=packet(0x43,0x1A,0,'@TCList=/H=0|0')
        self.publish([Delivery('alice',private)]);self.assertEqual(self.poll(),[])
        result=packet(0x43,0x0D,9,'@ResultData=/R=0|0|0|0|0|0|0|0/P=0')
        output=self.publish([Delivery('alice',result),Delivery('bob',result)])
        self.assertEqual((output or self.poll())[0].payload,result)
        self.publish(self.move());self.assertEqual(self.poll(),[])
        changed=replace(self.match.observer_snapshot(),round_id=2)
        self.s.publish([],changed,epoch=self.match)
        self.assertEqual(self.s.take_evicted(),(('watcher','round_replaced'),))

    def test_poll_late_does_not_flush_many_packets_at_once(self):
        self.watch();self.publish(self.move());self.now=10000
        self.assertEqual(len(self.s.poll(self.match.observer_snapshot(),epoch=self.match)),1)
        self.assertEqual(self.s.poll(self.match.observer_snapshot(),epoch=self.match),[])

    def test_actual_emission_controls_deadline_and_slow_writer_never_catches_up(self):
        out=self.s.handle('slow',WATCH,self.match.observer_snapshot(),epoch=self.match)
        self.publish(self.move());self.now=1000
        self.assertEqual(self.s.poll(self.match.observer_snapshot(),epoch=self.match),[])
        with self.assertRaises(SpectatorError):
            self.s.record_sent('slow',out[-1].payload,epoch=self.match)
        for delivery in out:
            self.assertTrue(self.s.record_sent('slow',delivery.payload,epoch=self.match))
        self.assertEqual(self.poll(7),[])
        pending=self.s.poll(self.match.observer_snapshot(),epoch=self.match)
        self.assertEqual(pending,[])
        self.now+=1;pending=self.s.poll(self.match.observer_snapshot(),epoch=self.match)
        self.assertEqual(len(pending),1)
        self.now+=10000
        self.assertEqual(self.s.poll(self.match.observer_snapshot(),epoch=self.match),[])
        self.assertTrue(self.s.record_sent('slow',pending[0].payload,epoch=self.match))
        self.assertEqual(self.poll(2),[])
        self.assertEqual(len(self.poll(1)),1)

    def test_failed_or_retired_delivery_does_not_advance_new_observer(self):
        out=self.s.handle('slow',WATCH,self.match.observer_snapshot(),epoch=self.match)
        old=self.match;self.match,_=service('versus');self.s.reset(self.match)
        self.assertFalse(self.s.record_sent('slow',out[0].payload,epoch=old))
        self.watch('slow');self.s.disconnect('slow')
        self.assertFalse(self.s.record_sent('slow',out[0].payload,epoch=self.match))


class NativeSpectatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from result_reward_vectors import VM
            from special_card_effect_vectors import Effects,G
            from turn_pacing_queue_vectors import boundary
        except ImportError as exc:raise unittest.SkipTest(str(exc))
        cls.VM,cls.Effects,cls.G,cls.boundary=VM,Effects,G,staticmethod(boundary)

    def test_original_watch_child_joins_then_Req_PLAYACK_without_GameEN(self):
        from table_admission_vectors import AdmissionVM,SERVICE,ACK
        from unicorn.x86_const import UC_X86_REG_EBP,UC_X86_REG_ESI
        v=AdmissionVM();calls=[]
        v.w32(0x102B624C,v.controller)
        v.u.mem_write(0x1028BB88,b'LOCALTABLE01\0')
        def join(sp):
            calls.append(('join',v.r64(sp+4),v.text(v.r32(sp+12)),len(v.sends)))
            v.ret(sp,1)
        v.hooks[0x101A0E50]=join
        v.w32(v.child+0x188,1)
        v.branch(0x10092B89,[0x10093137])
        self.assertEqual(calls,[('join',SERVICE,'LOCALTABLE01',1)])
        self.assertEqual([s['payload'] for s in v.sends],['2D000000@CheckJoinTable='])
        self.assertEqual(v.r32(v.child+0x188),2)
        v.hooks[0x10086240]=lambda sp:v.ret(sp,1)
        v.hooks[0x100A7AE0]=lambda sp:v.ret(sp)
        v.hooks[0x100A79B0]=lambda sp:v.ret(sp,8)
        v.hooks[0x100A8640]=lambda sp:v.ret(sp,1,12)
        def request(sp):
            calls.append(('Req',v.r32(sp+4),len(v.sends)));v.ret(sp,1,4)
        v.hooks[0x10086710]=request
        v.branch(0x10092C28,[0x10093137])
        self.assertEqual(calls[-1][0],'Req');self.assertEqual(v.r32(v.child+0x188),3)
        v.queue('02000000'+ACK)
        v.branch(0x10092D19,[0x10093137],{UC_X86_REG_EBP:100})
        self.assertEqual(v.r32(v.child+0x188),4)
        def event(sp):
            calls.append(('event',v.r32(sp+4)));v.ret(sp,1,8)
        v.hooks[0x1019CCE0]=event
        v.branch(0x10092D96,[0x10093137])
        self.assertEqual(calls[-1],('event',0x03001F5A))
        v.w32(0x200FF000+0x28,0x03001F5A)
        v.branch(0x100BB3E5,[0x100BB432],{UC_X86_REG_ESI:v.game})
        self.assertEqual([s['payload'] for s in v.sends],
                         ['2D000000@CheckJoinTable=','43002500@Data=/Watch=0'])

    def test_original_watch_info_and_card_parsers_accept_ten_cards_without_ack(self):
        m,_=service('versus');snapshot=m.observer_snapshot()
        board=list(snapshot.board)
        for index in range(10):
            board[index]=Cell.ordinary(CARDS[index%5],index%2,property=index%4,modifier=index-5)
        players=tuple(replace(p,hand=(),hand_slots=()) for p in snapshot.players)
        turn=packet(0x43,9,9,'@TurnData=/A=1/S=5|5/R=1|2|3|4|5|6|7|8')
        snapshot=replace(snapshot,players=players,board=tuple(board),sequence=9,active=1,turn_packet=turn)
        payloads=bootstrap(snapshot);self.assertTrue(all(len(p)<=460 for p in payloads))
        v=self.VM(role=4)
        for payload in payloads:v.queue(payload[5:].decode())
        self.assertEqual(v.receive(0x25),15);self.assertEqual(v.r8(v.player+0x33),10)
        self.assertEqual(v.receive(0x26),1)
        for i,cell in enumerate(board[:10]):
            b=v.player+0x4D70+16*i
            self.assertEqual((v.r16(b+6),v.r8(b+15),v.r8(b+4)),(cell.model,cell.owner+1,cell.modifier&255))
        self.assertEqual(v.receive(9),1);self.assertEqual(v.sends,[])

    def test_original_reconstruction_restores_CH_as_modifier_and_recomputes_points(self):
        from unicorn.x86_const import UC_X86_REG_ESI,UC_X86_REG_ECX,UC_X86_REG_ESP,UC_X86_REG_EIP
        m,_=service('versus');base=m.observer_snapshot()
        for prop in range(4):
            for modifier in (-128,-10,0,127):
                with self.subTest(property=prop,modifier=modifier):
                    c=Cell.ordinary(CARDS[1],1,property=prop,modifier=modifier)
                    snapshot=replace(base,board=(c,)+base.board[1:])
                    v=self.VM(role=4)
                    for payload in bootstrap(snapshot)[:2]:v.queue(payload[5:].decode())
                    self.assertEqual(v.receive(0x25),15);self.assertEqual(v.receive(0x26),1)
                    # The same original field constructor is tested below;
                    # isolate only reconstruction after it has produced prop.
                    v.w8(v.game+0x16C,0);v.w8(v.game+0x368,prop)
                    v.u.reg_write(UC_X86_REG_ESI,v.game);v.u.reg_write(UC_X86_REG_ECX,0)
                    v.u.reg_write(UC_X86_REG_ESP,0x200FF000)
                    v.u.emu_start(0x100C1CC3,0x100C1E02,count=1000000)
                    self.assertEqual(v.u.reg_read(UC_X86_REG_EIP),0x100C1E02)
                    rendered=v.game+0x344
                    self.assertEqual(v.r8(rendered+0x26),modifier&255)
                    self.assertEqual(v.r8(rendered+0x24),prop)
                    self.assertEqual(list(v.u.mem_read(rendered+0x1C,3)),[1,1,1])
                    expected=(CARDS[1].attack+CARDS[1].physical+CARDS[1].magical)*v.r8(0x20100000+12+4)//sum(v.r8(0x20100000+12+i) for i in (0,2,3))
                    self.assertEqual(v.r8(rendered+12),expected)
                    self.assertEqual(v.r8(v.player+0x33),0)

    def test_original_F_tile_setup_accepts_current_special_projection(self):
        m,_=service('versus');snapshot=m.observer_snapshot()
        for value in range(1,19):
            with self.subTest(field=value):
                fields=[0]*16;fields[5]=value;board=start_board(fields).cells
                actual,_,_=snapshot_fields(replace(snapshot,board=board))
                v=self.Effects();g=self.G
                v.byte(g+0x179,5);v.byte(g+0x1F5+5,actual[5]);v.byte(g+0x174,9);v.byte(g+0x175,9)
                v.block(0x100C1920,0x100C1BA3,eax=5,esi=g,ebp=0)
                got=v.state(5);expected=board[5]
                self.assertEqual((got['id'],got['owner'],got['property'],got['arrows']),
                                 (expected.model,expected.owner,expected.property,expected.arrows))

    def test_original_observer_cleanup_can_drop_future_moves_despite_ordering(self):
        v=self.VM(role=4)
        v.queue('43000901@TurnData=/A=1/S=1|0/R=0|0|0|0|0|0|0|0')
        v.queue('43000A01@PutCard=/A=1/H=0/F=255')
        self.boundary(v)
        self.assertEqual(v.r32(0x102B31C4),1)
        self.assertEqual(v.receive(9),1);self.assertEqual(v.receive(0x0A),0)
        self.assertEqual(v.sends,[])


if __name__=='__main__':unittest.main()
