"""Isolated event combat + unmodified original receiver/exit-controller checks."""
from pathlib import Path
import sys
import unittest
from event_matches import EventMatchRound,EventParticipant,event_session_init,event_session_quit
from match_service import Card,MatchError,decode,packet

P=Path(__file__).resolve().parents[1]/'protocol_map'
sys.path.insert(0,str(P))
from event_match_vectors import EventVM

CARDS=tuple(Card(i,50,0,40,30,1,0)for i in range(5))

def make(n=2,cpu=False,arrows=0):
    cards=tuple(Card(i,50,0,40,30,1,arrows)for i in range(5))
    return EventMatchRound(tuple(EventParticipant(str(i),100+i,('Player'+str(i)).encode(),10+i,cards)
        for i in range(1 if cpu else n)),cpu_cards=cards if cpu else None,seed=17)

def finish(s):
    output=[]
    for _ in range(300):
        if s.phase=='result':return output
        if s.pending_work:output+=s.advance(1);continue
        if s.phase=='turn':
            sequence=s.sequence
            for seat,p in enumerate(s.players):
                if p.peer is not None:output+=s.handle(p.peer,packet(0x43,9,sequence,f'@TurnData=/Ans={seat}'))
            if s.phase=='turn' and s.players[s.active].peer is not None:
                p=s.players[s.active]
                field=next(i for i,c in enumerate(s.snapshot()['board'])if c is None)
                output+=s.handle(p.peer,packet(0x43,10,s.sequence,f'@PutData=/P={s.active}/H=0/F={field}'))
        elif s.phase=='battle':
            p=s.players[s.active]
            output+=s.handle(p.peer,packet(0x43,11,s.sequence,'@BattleSelect=/B='+str(s.snapshot()['battle_targets'][0])))
        else:raise AssertionError(s.phase)
    raise AssertionError('event round did not finish')


class EventRoundTests(unittest.TestCase):
    def test_two_three_and_cpu_complete_without_inventory_or_stats_commit(self):
        for n,cpu in [(2,False),(3,False),(2,True)]:
            with self.subTest(n=n,cpu=cpu):
                s=make(n,cpu);initial_stats=tuple(p.result_stats for p in s.players)
                start=s.begin();self.assertEqual(s.begin(),[])
                result=finish(s)
                self.assertEqual(s.sequence,5*n)
                self.assertEqual(sum(s.outcome.scores),5*n)
                self.assertFalse(s.outcome.aborted)
                self.assertEqual(tuple(p.result_stats for p in s.players if p.peer is not None),initial_stats)
                kinds=[next(iter(decode(d.payload).sections))for d in start+result]
                self.assertIn('EventResult',kinds)
                self.assertFalse(set(kinds)&{'ResultData','VsGameInit','CardSelect','TCList','TradeCard'})
                for seat,p in enumerate(s.players):
                    if p.peer is None:continue
                    self.assertIsNone(s.final_cards(p.peer))
                    out=s.handle(p.peer,packet(0x43,14,5*n,f'@Ready=/Ans={seat}'))
                    self.assertEqual(out[0].payload,packet(0x43,17,2,'@Continue=/Ans='+'|'.join(['3']*n)+'/B='+'|'.join(['0']*n)))
                    self.assertEqual(s.handle(p.peer,packet(0x43,14,5*n,f'@Ready=/Ans={seat}')),[])
                self.assertEqual(s.phase,'finished')

    def test_shared_combat_runs_with_reciprocal_arrows(self):
        s=make(arrows=255);s.begin();out=finish(s)
        self.assertTrue(any(b'@BattleData='in d.payload for d in out))
        self.assertEqual(s.phase,'result')
        self.assertTrue(all(s.final_cards(p.peer)is None for p in s.players))

    def test_deals_are_per_recipient_and_original_parser_agrees(self):
        for n in [2,3]:
            s=make(n);out=s.begin()
            for seat,p in enumerate(s.players):
                v=EventVM(n,seat);v.w32(0x10294bd4,p.character_id)
                own=[d.payload for d in out if d.peer==p.peer]
                self.assertEqual([decode(d).subtype for d in own],[1,7,8,9])
                for payload in own[:3]:
                    subtype=decode(payload).subtype
                    v.queue(payload[5:].decode());self.assertEqual(v.receive(subtype),2 if subtype==7 else 1)
                self.assertEqual(v.r8(v.player+0x19b),seat)
                native=v.hands()[0]
                expected=s.players[seat].selected
                self.assertEqual([c['id']for c in native],[c.model for c in expected])
                self.assertEqual([c['compact']for c in native],[[c.attack,c.kind,c.physical,c.magical,c.points,c.arrows]for c in expected])
                deal=decode(own[1]).sections['EventCard']
                self.assertEqual(set(deal),{'Ok',*(f'D{seat*5+i}'for i in range(5))})

    def test_actual_original_event_result_exit_gate(self):
        s=make();s.begin();out=finish(s)
        for seat,p in enumerate(s.players):
            v=EventVM(2,seat);g,c=v.game,0x20150000
            v.w8(g+0x197,1);v.w8(v.player+0xea,2);v.w32(g+0x150,0x20160000)
            v.w32(v.player+0xcc,1234);v.w32(v.player+4,345);v.w16(v.player+0xb8,21)
            result=next(d.payload for d in out if d.peer==p.peer and b'@EventResult='in d.payload)
            before=v.result_state();v.queue(result[5:].decode());self.assertEqual(v.receive(13),1)
            self.assertEqual(v.result_state(),before)
            v.hooks[0x101951d0]=lambda sp:v.ret(sp)
            v.call(0x1010b780,[0],this=c);v.w16(c+0x2e,500)
            self.assertEqual((v.r8(c+0x184),v.r8(v.player+0x1a6)),(7,2))
            self.assertEqual(v.sends,[])
            reply=s.handle(p.peer,packet(0x43,14,10,f'@Ready=/Ans={seat}'))[0].payload
            v.queue(reply[5:].decode());v.call(0x1010c480,this=c)
            self.assertEqual(v.r8(c+0x184),12)
            self.assertEqual(v.sends,[])

    def test_session_init_and_quit_original(self):
        for status in range(4):
            v=EventVM();v.w32(v.player+0xc8,5000)
            payload=event_session_init(7,rating=321,balance=100,points=42,count=0,rt=123,status=status)
            v.queue(payload[5:].decode());self.assertEqual(v.receive(19),1)
            self.assertEqual(v.session()['balance'],5000)
            self.assertEqual(v.session()['flags'][:2],[1,1])
            v.queue(event_session_quit(7,status=status)[5:].decode())
            self.assertEqual(v.receive(23),1)
            self.assertEqual(v.session()['flags'],[status&1,int(not(status&2)),int(bool(status&2))])
            self.assertEqual(v.session()['online'],0)

    def test_untrusted_routes_and_wrong_result_release_rejected_atomically(self):
        s=make();s.begin()
        bad=[packet(0x43,7,body='@CardSelect=/C=5'),packet(0x43,17,body='@Continue=/Ans=1/Conf=0'),
            packet(0x43,14,10,'@Ready=/Ans=0'),packet(0x43,15,body='@GetSelect=/E=0/H=0')]
        for payload in bad:
            before=s.snapshot()
            with self.assertRaises(MatchError):s.handle('0',payload)
            self.assertEqual(s.snapshot(),before)
        finish(s)
        for payload in [packet(0x43,14,9,'@Ready=/Ans=0'),packet(0x43,14,10,'@Ready=/Ans=1')]:
            before=s.snapshot()
            with self.assertRaises(MatchError):s.handle('0',payload)
            self.assertEqual(s.snapshot(),before)
        with self.assertRaises(MatchError):s.handle('stranger',packet(0x43,14,10,'@Ready=/Ans=0'))

    def test_cancel_never_exposes_inventory_replacement(self):
        s=make();s.begin();s.cancel()
        self.assertEqual(s.phase,'finished');self.assertFalse(s.pending_work)
        self.assertTrue(s.outcome.aborted);self.assertEqual(s.advance(),[])
        self.assertIsNone(s.final_cards('0'))
        unstarted=make();unstarted.cancel()
        self.assertEqual(unstarted.begin(),[])
        self.assertIsNone(unstarted.outcome)

    def test_invalid_configuration_rejected(self):
        a=EventParticipant('0',1,b'Alice',1,CARDS)
        for ps,kw in [((a,),{}),((a,a),{}),((a,),{'cpu_cards':CARDS,'turn_limit':6}),
                      ((a,a),{'cpu_cards':CARDS}),((EventParticipant('0',0,b'Alice',1,CARDS),a),{})]:
            with self.assertRaises((ValueError,MatchError)):EventMatchRound(ps,**kw)
        for args in [dict(session=256),dict(session=1,status=4)]:
            with self.assertRaises(MatchError):event_session_quit(**args)

if __name__=='__main__':unittest.main()
