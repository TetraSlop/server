"""State-machine and original-client parser checks; no native process/network."""
from pathlib import Path
import random
import sys
import unittest
from match_service import Card, BoardCard, MatchService, MatchError, packet, decode
from native_rules import CRTLCG, calculate_battle, grow_card
from result_stats import PracticeStats


CARDS = tuple(Card(i,50,0,40,30,1,0) for i in range(5))
NATIVE_SETTINGS='@Tet=/bm=0/du=0/st=0/cb=0/ca=0/gs=0/tl=0@Tab=/in=0/lu=100/ll=0/au=300/al=100/co=0/pa=0'


def select(cards=CARDS, seat=0):
    return packet(0x43,7,seat,'@CardSelect=/C=5'+''.join(f'@N{i}=/D={c.full()}' for i,c in enumerate(cards)))


def ack(seat, seq):
    return packet(0x43,9,seq,f'@TurnData=/Ans={seat}')


def put(seat, seq, h, f):
    return packet(0x43,10,seq,f'@PutData=/P={seat}/H={h}/F={f}')


def service(mode='computer', cards=CARDS):
    s = MatchService(mode=mode,seed=11,ai_cards=cards)
    s.register('alice',character_id=1,nickname_id=0x123,name=b'Alice')
    if mode == 'versus':
        s.register('bob',character_id=2,nickname_id=0x456,name=b'Bob')
    s.begin()
    out = s.handle('alice',select(cards))
    if mode == 'versus':
        out += s.handle('bob',select(cards,1))
    return s,out


def finish_arrowless_round(s):
    """Play a whole round through the public wire API, including human barriers."""
    out=[]
    for _ in range(20):
        if s.phase == 'result':return out
        if s.phase != 'turn':raise AssertionError(s.phase)
        seq=s.sequence
        for seat,p in enumerate(s.players):
            if p.peer is not None:out += s.handle(p.peer,ack(seat,seq))
        if s.phase == 'turn' and s._unlocked() and s.players[s.active].peer is not None:
            cell=next(i for i,c in enumerate(s.board) if c is None)
            out += s.handle(s.players[s.active].peer,put(s.active,s.sequence,0,cell))
    raise AssertionError('round did not finish')


class MatchTests(unittest.TestCase):
    def test_default_rank_bounds_match_directory_and_saved_bounds_remain_separate(self):
        s=MatchService();s.register('alice')
        self.assertEqual((s.table_rules['au'],s.table_rules['al']),(300,100))
        self.assertIn(b'/au=300/al=100',s.playack('alice'))
        custom=NATIVE_SETTINGS.replace('/au=300/al=100','/au=99999/al=0')
        s.handle('alice',packet(0x24,body='@Save=/Tbl=1/Game=0'+custom))
        self.assertEqual(s.saved_preferences['alice']['Tab']['au'],99999)
        self.assertEqual(s.saved_preferences['alice']['Tab']['al'],0)
        before=s.snapshot()
        with self.assertRaises(MatchError):s.handle('alice',packet(0x14,body=custom))
        self.assertEqual(s.snapshot(),before)

    def test_observed_native_settings_then_roster_and_host_start(self):
        s=MatchService();s.register('alice',nickname_id=1)
        s.handle('alice',packet(0x41,1,body='@GameEN=/NN=B62DC763E1554B74'))
        self.assertEqual(s.owner_id,0xB62DC763E1554B74)
        self.assertEqual(s.admission_notice('alice'),[])
        self.assertEqual(s.handle('alice',packet(0x14,body=NATIVE_SETTINGS)),[])
        self.assertEqual(s.phase,'lobby')
        self.assertEqual(s.settings_revision,1)
        self.assertTrue(s.settings_applied)
        notices=s.admission_notice('alice')
        self.assertTrue(s.ready_to_start)
        self.assertEqual([(decode(d.payload).primary,decode(d.payload).subtype) for d in notices],[(0x41,4),(0x1a,0)])
        self.assertIn(b'@GameML=/Num=2',notices[0].payload)
        self.assertIn(b'/MLID=B62DC763E1554B74|0000000000000200',notices[0].payload)
        self.assertIn(b'@GameOwner=/NN=B62DC763E1554B74',notices[1].payload)
        rule=s.handle('alice',packet(0x12,body='@Rule='))[0].payload
        self.assertEqual(rule,packet(0x13,body=NATIVE_SETTINGS))
        self.assertEqual(s.handle('alice',packet(0x41,10,body='@GameReady=')),[])
        out=s.handle('alice',packet(1,body='@Req='))
        self.assertIn(NATIVE_SETTINGS.encode(),out[0].payload)
        start=s.handle('alice',packet(0x41,8,body='@GameOK='))
        self.assertEqual([decode(d.payload).subtype for d in start],[9,1])
        self.assertIn(b'/R=0|0|0|0|0|0|0',start[-1].payload)

    def test_settings_errors_are_atomic_and_current_owner_only(self):
        s=MatchService(mode='versus');s.register('alice',1);s.register('bob',2)
        s.handle('alice',packet(0x41,1,body='@GameEN='))
        for who,text in [('bob',NATIVE_SETTINGS),('alice',NATIVE_SETTINGS.replace('/tl=0','/tl=6')),
                         ('alice',NATIVE_SETTINGS.replace('/ll=0','/ll=101')),
                         ('alice',NATIVE_SETTINGS.replace('/st=0','/st=2')),
                         ('alice',NATIVE_SETTINGS.replace('/bm=0','/bm=1')),
                         ('alice',NATIVE_SETTINGS.replace('/au=300','/au=301')),
                         ('alice',NATIVE_SETTINGS.replace('/al=100','/al=99'))]:
            before=s.snapshot()
            with self.assertRaises(MatchError):s.handle(who,packet(0x14,body=text))
            self.assertEqual(s.snapshot(),before)
        s.handle('alice',packet(0x14,body=NATIVE_SETTINGS))
        s.begin()
        with self.assertRaises(MatchError):s.handle('alice',packet(0x14,body=NATIVE_SETTINGS))

    def test_save_preferences_do_not_change_current_table_or_revision(self):
        s=MatchService();s.register('alice')
        s.handle('alice',packet(0x14,body=NATIVE_SETTINGS))
        before=s.rules_body()
        saved=NATIVE_SETTINGS.replace('/tl=0','/tl=5').replace('/st=0','/st=1').replace('/pa=0','/pa=1/pw=6162632A2A2A2A2A')
        self.assertEqual(s.handle('alice',packet(0x24,body='@Save=/Tbl=0/Game=1'+saved)),[])
        self.assertEqual(s.saved_preferences['alice']['Tet']['tl'],5)
        self.assertNotIn('Tab',s.saved_preferences['alice'])
        s.handle('alice',packet(0x24,body='@Save=/Tbl=1/Game=0'+saved))
        self.assertEqual(s.saved_preferences['alice']['Tab']['pw'],'6162632A2A2A2A2A')
        self.assertEqual(s.rules_body(),before)
        self.assertEqual(s.settings_revision,1)
        snapshot=dict(s.saved_preferences['alice'])
        with self.assertRaises(MatchError):s.handle('alice',packet(0x24,body='@Save=/Tbl=0/Game=0'+saved))
        self.assertEqual(s.saved_preferences['alice'],snapshot)

    def test_active_password_preserves_original_padded_field_and_public_metadata(self):
        from table_metadata import table_info
        s=MatchService();s.register('alice',nickname_id=0x123)
        body=NATIVE_SETTINGS.replace('/pa=0','/pa=1/pw=4162432A2A2A2A2A')
        self.assertEqual(s.handle('alice',packet(0x14,body=body)),[])
        self.assertEqual(s.rules_body(),body)
        self.assertEqual(s.table_rules['pw'],'4162432A2A2A2A2A')
        self.assertEqual(table_info(1,1,s.owner_id,s.game_rules,s.table_rules)[6:14],b'AbC*****')
        rule=s.handle('alice',packet(0x12,body='@Rule='))[0]
        self.assertEqual(rule.payload,packet(0x13,body=body))
        self.assertEqual(decode(s.playack('alice')).sections['Tab']['pw'],'4162432A2A2A2A2A')

    def test_unrepresentable_active_password_rejects_before_mutation_but_twelve_byte_save_is_valid(self):
        s=MatchService();s.register('alice')
        s.handle('alice',packet(0x14,body=NATIVE_SETTINGS))
        before=s.snapshot()
        invalid=(b'123456789',b'123456789012',b'A*B*****',b'A\x06B*****',b'A\x07B*****',
                 b'A\nB*****',b'A\rB*****')
        for raw in invalid:
            body=NATIVE_SETTINGS.replace('/pa=0','/pa=1/pw='+raw.hex().upper())
            with self.assertRaises(MatchError):s.handle('alice',packet(0x14,body=body))
            self.assertEqual(s.snapshot(),before)
        saved=NATIVE_SETTINGS.replace('/pa=0','/pa=1/pw='+b'123456789012'.hex().upper())
        s.handle('alice',packet(0x24,body='@Save=/Tbl=1/Game=0'+saved))
        self.assertEqual(s.saved_preferences['alice']['Tab']['pw'],b'123456789012'.hex().upper())
        self.assertEqual(s.snapshot(),before)

    def test_native_table_admission_order_and_identity(self):
        s=MatchService()
        s.register('a')
        out=s.handle('a',packet(0x41,1,body='@GameEN=/NN=B62DC763E1554B74'))
        self.assertEqual(out[0].payload,packet(0x41,2,body='@GameEA=/EN=1'))
        with self.assertRaises(MatchError):s.handle('a',packet(0x41,8,body='@GameOK='))
        out=s.handle('a',packet(1,body='@Req=/NN=B62DC763E1554B74'))
        self.assertIn(b'@PLAYACK=/EN=1',out[0].payload)
        self.assertIn(b'B62DC763E1554B74',out[0].payload)
        out=s.handle('a',packet(0x41,8,body='@GameOK='))
        self.assertEqual([(decode(x.payload).primary,decode(x.payload).subtype) for x in out],[(0x41,9),(0x43,1)])
        self.assertEqual(s.handle('a',packet(0x41,8,body='@GameOK=')),[])

    def test_selection_start_and_turn_are_ordered(self):
        s,out=service()
        self.assertEqual([decode(x.payload).subtype for x in out],[7,8,9])
        self.assertEqual(s.snapshot()['hand_counts'],[5,5])
        self.assertEqual(s.phase,'turn')
        self.assertIn(b'/Ok=1',out[0].payload)

    def test_selection_validation_has_no_partial_mutation(self):
        s=MatchService(selected_card_validator=lambda p,c:False)
        s.register('alice');s.begin()
        with self.assertRaises(MatchError):s.handle('alice',select())
        self.assertFalse(s.players[0].selected)
        self.assertEqual(s.phase,'selection')

    def test_real_economy_inventory_validator_and_AI_card_adapter(self):
        from economy_service import EconomyService
        economy=EconomyService(seed=3);economy.provision('account')
        owned=economy.inventory('account')
        selected=tuple(Card(*(getattr(c,n) for n in Card.__dataclass_fields__)) for c in owned[:5])
        before=economy.snapshot()
        s=MatchService(ai_cards=owned[:5],selected_card_validator=lambda peer,c:economy.validate_selection('account',c))
        s.register('alice');s.begin()
        accepted=s.handle('alice',select(selected))
        self.assertEqual([decode(d.payload).subtype for d in accepted],[7,8,9])
        self.assertEqual(economy.snapshot(),before)
        self.assertIsInstance(s.players[1].selected[0].full(),str)
        # Forging one field is rejected by the shared inventory validator.
        bad=list(selected)
        c=bad[0];bad[0]=Card(c.model,(c.attack+1)%256,c.kind,c.physical,c.magical,c.points,c.arrows,c.deck)
        self.assertFalse(economy.validate_selection('account',bad))

    def test_both_humans_gate_early_move(self):
        s,_=service('versus')
        s.handle('alice',ack(0,0))
        self.assertEqual(s.handle('alice',put(0,0,0,0)),[])
        self.assertIsNone(s.board[0])
        self.assertEqual(s.handle('alice',put(0,0,0,0)),[])
        out=s.handle('bob',ack(1,0))
        self.assertEqual([decode(x.payload).subtype for x in out],[10,10,9,9])
        self.assertEqual(s.sequence,1)
        self.assertEqual(len(s.players[0].hand),4)

    def test_duplicate_and_stale_ack_do_not_replay(self):
        s,_=service();s.handle('alice',ack(0,0))
        self.assertEqual(s.handle('alice',ack(0,0)),[])
        s.handle('alice',put(0,0,0,0))
        self.assertEqual(s.handle('alice',ack(0,0)),[])
        self.assertEqual(s.sequence,1)
        out=s.handle('alice',ack(0,1))
        self.assertEqual([decode(x.payload).subtype for x in out],[10,9])
        self.assertEqual(s.sequence,2)

    def test_invalid_turn_and_board_inputs_are_rejected(self):
        s,_=service('versus')
        before=s.snapshot()
        for who,data in [('alice',put(0,7,0,0)),('bob',put(1,0,0,0)),('alice',put(0,0,5,0)),('alice',put(0,0,0,16)),('alice',put(0,0,0,255))]:
            with self.assertRaises(MatchError):s.handle(who,data)
        self.assertEqual(before,s.snapshot())

    def test_nonwrapping_arrows(self):
        s=MatchService()
        self.assertEqual(dict(s.neighbors(0)),{2:1,3:5,4:4})
        self.assertNotIn(4,dict(s.neighbors(3)).values())

    def board_case(self, arrows=4):
        s,_=service()
        s.players[0].hand[0]=Card(9,50,0,40,30,1,arrows)
        s.acks.add('alice')
        return s

    def test_one_way_capture_needs_no_battle(self):
        s=self.board_case()
        s.board[6]=BoardCard(CARDS[0],1,1)
        out=s.handle('alice',put(0,0,0,5))
        self.assertEqual(s.board[6].owner,0)
        self.assertEqual([decode(d.payload).subtype for d in out],[10,9])

    def test_single_target_resolves_without_selection(self):
        s=self.board_case()
        s.board[6]=BoardCard(Card(6,50,0,40,30,1,64),1,1)
        s.battle_values=lambda a,d:((50,30,1,1),(40,5,4,1))
        out=s.handle('alice',put(0,0,0,5))
        self.assertEqual([decode(d.payload).subtype for d in out],[10,12,9])
        self.assertEqual(s.board[6].owner,0)
        self.assertIn(b'/A=5|50|30|1|1/D=6|40|5|4|1',out[1].payload)

    def test_multiple_targets_wait_and_continue_without_extra_request(self):
        s=self.board_case(4|16)
        s.board[6]=BoardCard(Card(6,50,0,40,30,1,64),1,1)
        s.board[9]=BoardCard(Card(7,50,0,40,30,1,1),1,1)
        s.battle_values=lambda a,d:((50,30,1,1),(40,5,4,1))
        out=s.handle('alice',put(0,0,0,5))
        self.assertEqual([decode(d.payload).subtype for d in out],[10])
        self.assertEqual(s.targets,[6,9])
        with self.assertRaises(MatchError):s.handle('alice',packet(0x43,11,0,'@BattleSelect=/B=0'))
        out=s.handle('alice',packet(0x43,11,0,'@BattleSelect=/B=6'))
        self.assertEqual([decode(d.payload).subtype for d in out],[12])
        self.assertTrue(s.pending_work)
        out += s.advance()
        self.assertEqual([decode(d.payload).subtype for d in out],[12,12,9])
        self.assertEqual([s.board[i].owner for i in (5,6,9)],[0,0,0])

    def test_loss_abandons_deferred_capture(self):
        s=self.board_case(4|16)
        s.board[6]=BoardCard(Card(6,50,0,40,30,1,64),1,1)
        s.board[9]=BoardCard(CARDS[0],1,1)
        s.battle_values=lambda a,d:((50,1,1,1),(40,20,4,1))
        s.handle('alice',put(0,0,0,5))
        self.assertEqual([s.board[i].owner for i in (5,6,9)],[1,1,1])

    def test_combo_is_one_hop(self):
        s=self.board_case()
        s.board[5]=BoardCard(Card(5,20,0,10,10,1,4),0,1)
        s.board[6]=BoardCard(Card(6,20,0,10,10,1,4),1,1)
        s.board[7]=BoardCard(CARDS[0],1,1)
        s._combo(5,0,1)
        self.assertEqual([s.board[i].owner for i in (5,6,7)],[0,0,1])

    def test_complete_practice_match_result_and_finish(self):
        s,_=service()
        all_packets=[]
        for _ in range(10):
            if s.phase=='result':break
            out=s.handle('alice',ack(0,s.sequence));all_packets+=out
            if s.phase=='turn' and s.active==0 and s._unlocked():
                f=next(i for i,c in enumerate(s.board) if c is None)
                all_packets+=s.handle('alice',put(0,s.sequence,0,f))
        self.assertEqual(s.phase,'result')
        self.assertEqual(s.sequence,10)
        self.assertEqual(s.snapshot()['hand_counts'],[0,0])
        self.assertEqual(s._scores(),[5,5])
        self.assertEqual(sum(decode(d.payload).subtype==13 for d in all_packets),1)
        out=s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))
        self.assertEqual(decode(out[0].payload).subtype,0x1a)
        self.assertIn(b'/D5=1|',out[0].payload)
        self.assertEqual(s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0')),[])
        out=s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=2/PR=0/Conf=0'))
        self.assertEqual(out[0].payload,packet(0x43,17,2,'@Continue=/Ans=3|3/B=0|0'))
        self.assertEqual(s.phase,'finished')

    def test_rematch_defers_start_until_new_selection(self):
        s,_=service();s.phase='result'
        out=s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
        self.assertEqual([decode(d.payload).subtype for d in out],[17])
        self.assertEqual(s.phase,'selection')
        self.assertFalse(s.players[0].selected)
        self.assertEqual([decode(d.payload).subtype for d in s.handle('alice',select())],[7,8,9])

    def test_two_complete_practice_rounds_keep_native_rules_and_reset_round_state(self):
        s=MatchService(ai_cards=CARDS);s.register('alice',nickname_id=0x123)
        s.handle('alice',packet(0x41,1,body='@GameEN='))
        s.handle('alice',packet(0x14,body=NATIVE_SETTINGS))
        s.admission_notice('alice')
        s.handle('alice',packet(1,body='@Req='))
        s.handle('alice',packet(0x41,8,body='@GameOK='))
        s.handle('alice',select())
        again=packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0')
        ready=packet(0x43,14,10,'@Ready=/Ans=0')
        for round_number in (1,2):
            results=[d.payload for d in finish_arrowless_round(s) if decode(d.payload).subtype==13]
            self.assertEqual(len(results),1)
            self.assertEqual(decode(results[0]).sections['No0']['D'].split('|')[4:6],[str(round_number)]*2)
            self.assertEqual(s.snapshot()['hand_counts'],[0,0])
            final=s.handle('alice',ready)
            self.assertEqual(len(final),1)
            self.assertEqual(decode(final[0].payload).subtype,26)
            self.assertEqual(s.handle('alice',ready),[])
            if round_number == 2:break
            response=s.handle('alice',again)
            self.assertEqual([decode(d.payload).subtype for d in response],[17])
            self.assertEqual(s.snapshot()['hand_counts'],[0,5])
            self.assertEqual(s.players[1].selected,CARDS)
            self.assertEqual(s.phase,'selection')
            self.assertEqual((s.sequence,s.active),(0,0))
            self.assertEqual(s.board,[None]*16)
            self.assertFalse(s.acks or s.ready or s.continues or s.markers or s.targets)
            self.assertIsNone(s.pending_move);self.assertIsNone(s.source)
            self.assertTrue(s.players[0].requested and s.players[0].accepted)
            self.assertEqual(s.rules_body(),NATIVE_SETTINGS)
            self.assertEqual(s.settings_revision,1)
            self.assertEqual(s.handle('alice',again),[])
            self.assertEqual(s.handle('alice',ready),[])
            self.assertEqual(s.handle('alice',ack(0,9)),[])
            new=s.handle('alice',select())
            self.assertEqual([decode(d.payload).subtype for d in new],[7,8,9])
            self.assertEqual(decode(new[0].payload).sections['CardSelect'],{'Ans':'1|1','Ok':'1'})
            self.assertEqual(s.snapshot()['hand_counts'],[5,5])
            self.assertFalse(s._unlocked())
        leave=packet(0x43,17,0,'@Continue=/Ans=2/PR=0/Conf=0')
        self.assertEqual(s.handle('alice',leave)[0].payload,packet(0x43,17,2,'@Continue=/Ans=3|3/B=0|0'))
        self.assertEqual(s.handle('alice',leave),[])
        self.assertEqual(s.handle('alice',ready),[])

    def test_two_human_rematch_waits_for_each_new_choice_and_ack(self):
        s,_=service('versus');finish_arrowless_round(s)
        ready=packet(0x43,14,10,'@Ready=/Ans=0')
        for seat,peer in enumerate(('alice','bob')):
            ready=packet(0x43,14,10,f'@Ready=/Ans={seat}')
            self.assertEqual([d.peer for d in s.handle(peer,ready)],[peer])
            self.assertEqual(s.handle(peer,ready),[])
        a=packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0')
        b=packet(0x43,17,1,'@Continue=/Ans=1/PR=0/Conf=0')
        self.assertEqual(s.handle('alice',a),[])
        self.assertEqual(s.handle('alice',a),[])
        self.assertEqual(s.phase,'result')
        out=s.handle('bob',b)
        self.assertEqual([(d.peer,decode(d.payload).subtype) for d in out],[('alice',17),('bob',17)])
        self.assertEqual(s.snapshot()['hand_counts'],[0,0])
        self.assertEqual(s.handle('alice',a),[]);self.assertEqual(s.handle('bob',b),[])
        first=s.handle('alice',select())
        self.assertEqual([decode(d.payload).sections['CardSelect'] for d in first],[{'Ans':'1|0','Ok':'0'}]*2)
        self.assertEqual(s.phase,'selection')
        start=s.handle('bob',select(seat=1))
        self.assertEqual([decode(d.payload).subtype for d in start],[7,7,8,8,9,9])
        self.assertFalse(s._unlocked())
        s.handle('alice',ack(0,0));self.assertFalse(s._unlocked())
        s.handle('bob',ack(1,0));self.assertTrue(s._unlocked())
        finish_arrowless_round(s)
        self.assertEqual(s.completed_matches,2)

    def test_alternate_continue_rejected_before_mutating_votes(self):
        s,_=service('versus');finish_arrowless_round(s)
        before=s.snapshot()
        with self.assertRaisesRegex(MatchError,'alternate Continue'):
            s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=1'))
        self.assertEqual(s.snapshot(),before)
        self.assertEqual(s.continues,{})
        s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
        stopped=s.handle('bob',packet(0x43,17,1,'@Continue=/Ans=2/PR=0/Conf=0'))
        self.assertEqual([decode(d.payload).argument for d in stopped],[2,2])
        self.assertEqual(s.phase,'finished')

    def test_result_count_saturates_before_native_word_wrap(self):
        s,_=service();s.completed_matches=65535
        for p in s.players:p.result_stats=PracticeStats(count=65535,sequence=65534)
        result=next(d.payload for d in finish_arrowless_round(s) if decode(d.payload).subtype==13)
        self.assertEqual(s.completed_matches,65536)
        for seat in (0,1):
            self.assertEqual(decode(result).sections[f'No{seat}']['D'].split('|')[4:6],['65535']*2)
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from incoming_gameplay_vectors import ReceiveVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        v=ReceiveVM();v.queue(result[5:].decode())
        self.assertEqual(v.receive(13),1)
        self.assertEqual(v.r16(v.player+0x84),65535)

    def test_result_statistics_continue_authenticated_history_and_commit_snapshot(self):
        stats=PracticeStats(rating=234,count=17,sequence=29,max_reward=800,average_reward=70,
                            reward_sum=1200,streak=4,best_streak=8,rn=b'History',
                            rt_points=12345,event_count=61,prize_points=99)
        s=MatchService(ai_cards=CARDS);s.register('alice',result_stats=stats,balance=7654)
        self.assertEqual(s.players[0].rating,234)
        s.begin();s.handle('alice',select())
        packet_result=next(d.payload for d in finish_arrowless_round(s) if decode(d.payload).subtype==13)
        done=s.final_cards('alice')
        self.assertEqual(done.round_id,1)
        self.assertEqual(done.stats,stats.next_result(0))
        self.assertEqual(done.stats.count,18)
        fields=decode(packet_result).sections['No0']
        self.assertEqual(fields,{'D':'0|0|234|0|30|18|1200','T':'0|0|61','RN':b'History'.hex().upper()})
        self.assertEqual(s.players[0].balance,7654)
        for _ in range(3):s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))
        self.assertIs(s.final_cards('alice'),done)
        s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
        s.handle('alice',select(done.updated))
        finish_arrowless_round(s)
        self.assertEqual(s.final_cards('alice').round_id,2)
        self.assertEqual(s.final_cards('alice').stats,stats.next_result(0).next_result(0))

    def test_two_player_result_statistics_use_actual_final_owners(self):
        s,_=service('versus')
        for seat,p in enumerate(s.players):
            p.result_stats=PracticeStats(count=9,sequence=65535,streak=-3 if seat==0 else 4,best_streak=6)
        # Exercise result-only boundary with actual owner scores6/4.
        for cell in range(10):
            original_owner=cell%2;original_slot=cell//2
            s.board[cell]=BoardCard(CARDS[original_slot],0 if cell<6 else 1,original_owner,original_slot)
        s.sequence=9
        out=s._end_turn()
        fields=decode(out[0].payload).sections
        self.assertEqual(fields['No0']['D'],'0|0|300|1|0|10|0')
        self.assertEqual(fields['No1']['D'],'0|0|300|-1|0|10|0')
        for p in s.players:
            self.assertEqual(s.final_cards(p.peer).stats,p.result_stats)
            self.assertEqual(p.result_stats.best_streak,6)

    def test_finished_table_can_be_explicitly_reset(self):
        s,_=service()
        with self.assertRaises(MatchError):s.reset()
        s.phase='finished';s.reset()
        self.assertEqual(s.phase,'lobby')
        self.assertEqual(len(s.players),1)
        self.assertFalse(s.players[0].requested)
        self.assertEqual(len(s.begin()),1)

    def test_full_cards_and_native_combat_domains(self):
        for kind in range(4):
            s=MatchService()
            a=Card(1,10,kind,30,20,1,255);d=Card(2,25,0,15,5,1,255)
            aa,dd=s.battle_values(a,d)
            self.assertEqual(aa[0],30 if kind==3 else 10)
            self.assertEqual(dd[0],15 if kind==0 else 5)
            reference=calculate_battle(a,d,CRTLCG(1))
            self.assertEqual((aa,dd),(reference.attacker.wire(0)[1:],reference.defender.wire(0)[1:]))
        s,_=service()
        self.assertEqual(Card.parse(CARDS[0].full()),CARDS[0])
        for raw in ('250|0|0|0|0|0|0|255','1|0|4|0|0|0|0|255','1|-1|0|0|0|0|0|255'):
            with self.assertRaises(MatchError):Card.parse(raw)

    def test_packet_validation_and_unhandled(self):
        with self.assertRaises(MatchError):packet(67,body='A'*448)
        with self.assertRaises(MatchError):decode(b'GTM0G43000700@X=/A=1/A=2')
        s,_=service()
        self.assertIsNone(s.handle('alice',packet(0xb2,0x15,body='@Get=')))
        with self.assertRaises(MatchError):s.register('bob',2)

    def test_tie_packets_yield_without_owner_or_growth_changes(self):
        s=self.board_case()
        s.board[6]=BoardCard(Card(6,50,0,40,30,1,64),1,1)
        # Exact calculator yields30/30; R0=0 would promote a winner, never a tie.
        s.draw=iter([30,30,30,30]+[0]*8).__next__
        before=s.players[0].updated[0]
        out=s.handle('alice',put(0,0,0,5))
        self.assertEqual([decode(d.payload).subtype for d in out],[10,12])
        self.assertEqual([s.board[i].owner for i in (5,6)],[0,1])
        self.assertEqual((s.sequence,s.phase),(0,'battle'))
        self.assertEqual(s.players[0].updated[0],before)
        self.assertEqual(s.board[5].card.kind,0)
        self.assertTrue(s.pending_work)
        self.assertEqual(s.handle('alice',packet(0x43,11,0,'@BattleSelect=/B=6')),[])
        # Same target/sequence, exact winner promotion, then a fresh turn barrier.
        s.draw=iter([40,40,5,5]+[0]*8+[50]*8).__next__
        more=s.advance()
        self.assertEqual([decode(d.payload).subtype for d in more],[12,9])
        self.assertEqual(decode(more[0].payload).argument,0)
        self.assertTrue(decode(more[0].payload).sections['BattleData']['D'].startswith('6|'))
        self.assertEqual(s.players[0].updated[0].kind,2)
        self.assertFalse(s.pending_work or s._unlocked())
        self.assertEqual(s.advance(),[])

    def test_perpetual_tie_is_bounded_and_abort_discards_continuation(self):
        s=self.board_case();s.board[6]=BoardCard(Card(6,50,0,40,30,1,64),1,1)
        s.draw=lambda:1
        s.handle('alice',put(0,0,0,5))
        for _ in range(8):
            out=s.advance(max_battles=3)
            self.assertEqual([decode(d.payload).subtype for d in out],[12]*3)
            self.assertEqual((s.sequence,s.board[5].owner,s.board[6].owner),(0,0,1))
        with self.assertRaises(ValueError):s.advance(0)
        with self.assertRaises(ValueError):s.advance(17)
        s.handle('alice',packet(0x43,5,10,'@Quit=/No=0'))
        self.assertFalse(s.pending_work)
        self.assertEqual(s.advance(),[])
        self.assertIsNone(s.final_cards('alice'))
        s.reset();self.assertFalse(s.pending_work)

    def test_promoted_card_copy_tracks_original_slot_after_compaction(self):
        cards=tuple(Card(5,20,0,20,20,1,4) for _ in range(5))
        s,_=service(cards=cards);s.acks.add('alice')
        s.handle('alice',put(0,0,1,15)) # Remove original slot1; current H1 is now slot2.
        s.active=0;s.acks.add('alice')
        s.board[6]=BoardCard(Card(6,10,0,10,10,1,64),1,1)
        s.draw=iter([20,20,1,1]+[0]*8+[30]*8).__next__
        s.handle('alice',put(0,1,1,5))
        self.assertEqual(s.board[15].original_slot,1)
        self.assertEqual(s.board[5].original_slot,2)
        self.assertEqual([c.kind for c in s.players[0].updated],[0,0,2,0,0])
        self.assertEqual(s.players[0].selected,cards)
        self.assertEqual(s.players[0].hand_slots,[0,3,4])

    def test_winning_defender_promotes_its_original_copy(self):
        s=self.board_case()
        c=Card(6,50,2,40,30,1,64,9)
        s.players[1].updated[3]=c
        s.board[6]=BoardCard(c,1,1,3)
        s.draw=iter([1,1,30,30]+[0]*8+[30]*8).__next__
        s.handle('alice',put(0,0,0,5))
        self.assertEqual(s.board[5].owner,1)
        self.assertEqual(s.players[1].updated[3].kind,3)
        self.assertEqual(s.players[1].updated[3].deck,9)

    def test_default_computer_uses_original_hand_and_scored_placement(self):
        cards=tuple(Card(5,20,0,20,20,1,4) for _ in range(5))
        s,_=service(cards=cards)
        s.sequence=1;s.active=1
        s.board[6]=BoardCard(CARDS[0],0,0)
        # Original hand gate/choice selects H3; original placement gate and
        # personality draws favor the only one-way capture, field5.
        s.draw=iter([0,3,0,0,0,0]+[50]*8).__next__
        out=s.handle('alice',ack(0,1))
        placed=decode(out[0].payload).sections['PutCard']
        self.assertEqual((placed['H'],placed['F']),('3','5'))
        self.assertEqual(s.board[5].original_slot,3)
        self.assertEqual(s.board[6].owner,1)

    def test_default_computer_target_uses_original_uniform_draw(self):
        s=self.board_case()
        s.active=1
        s.players[1].hand[0]=Card(5,50,0,40,30,1,4|16)
        s.board[6]=BoardCard(Card(6,50,0,40,30,1,64),0,0)
        s.board[9]=BoardCard(Card(7,50,0,40,30,1,1),0,0)
        # Two target draws select second candidate9, then one decisive battle.
        s.draw=iter([0,1,30,30,5,5]+[255]*8).__next__
        out=s._place(0,5)
        battle=decode(out[1].payload).sections['BattleData']
        self.assertTrue(battle['D'].startswith('9|'))
        self.assertEqual(s.board[9].owner,1)
        self.assertEqual(s.board[6].owner,0)
        self.assertTrue(s.pending_work)

    def test_seeded_complete_combat_rounds_preserve_final_copy_contract(self):
        cards=tuple(Card(5+i,20+i,i%4,15+i,10+i,1,255,i) for i in range(5))
        for mode in ('computer','versus'):
            for seed in range(16):
                s=MatchService(mode=mode,seed=seed,ai_cards=cards)
                s.register('alice',1)
                if mode=='versus':s.register('bob',2)
                s.begin();s.handle('alice',select(cards))
                if mode=='versus':s.handle('bob',select(cards,1))
                for _ in range(500):
                    if s.phase=='result':break
                    if s.pending_work:
                        out=s.advance()
                        self.assertLessEqual(sum(decode(x.payload).subtype==12 for x in out),len(s._humans()))
                    elif s.phase=='battle':
                        peer=s.players[s.active].peer
                        self.assertIsNotNone(peer)
                        s.handle(peer,packet(0x43,11,s.sequence,f'@BattleSelect=/B={s.targets[0]}'))
                    else:
                        sequence=s.sequence
                        for seat,p in enumerate(s.players):
                            if p.peer is not None:s.handle(p.peer,ack(seat,sequence))
                        if s.phase=='turn' and s._unlocked() and s.players[s.active].peer is not None:
                            cell=next(i for i,c in enumerate(s.board) if c is None)
                            s.handle(s.players[s.active].peer,put(s.active,s.sequence,0,cell))
                self.assertEqual(s.phase,'result',(mode,seed))
                self.assertEqual(s.sequence,10)
                self.assertFalse(s.pending_work)
                for p in s.players:
                    if p.peer is None:continue
                    final=s.final_cards(p.peer)
                    self.assertEqual(final.original,cards)
                    for old,new in zip(final.original,final.updated):
                        self.assertEqual((old.model,old.arrows,old.deck),(new.model,new.arrows,new.deck))
                        self.assertGreaterEqual(new.kind,old.kind)
                        self.assertLessEqual(sum(getattr(new,k)-getattr(old,k) for k in ('attack','physical','magical')),1)

    def ordered_result(self):
        cards=tuple(Card(5,1,0,1,1,0,0,i) for i in range(5))
        s,_=service('versus',cards)
        hands=(1,0,2,0,0)
        cells=((12,0,10,2,8),(15,1,11,3,9))
        out=[]
        for sequence in range(10):
            for seat,p in enumerate(s.players):s.handle(p.peer,ack(seat,sequence))
            seat=sequence%2;turn=sequence//2
            out=s.handle(s.players[seat].peer,put(seat,sequence,hands[turn],cells[seat][turn]))
        return s,next(d.payload for d in out if decode(d.payload).subtype==13)

    def test_result_growth_uses_rebuilt_hand_but_snapshot_uses_original_slots(self):
        s,result=self.ordered_result()
        shared=list(map(int,decode(result).sections['ResultData']['R'].split('|')))
        for seat,p in enumerate(s.players):
            final=s.final_cards(p.peer)
            self.assertEqual(final.round_id,1)
            self.assertEqual(final.original,p.selected)
            # Ascending board positions, independent of original selection order.
            self.assertEqual(s._final_order[seat],(0,2,3,4,1))
            for hand_slot,original_slot in enumerate(s._final_order[seat]):
                c=final.original[original_slot]
                expected=grow_card(c,s.baselines[c.model],1,shared[(hand_slot-3*seat+2)&7]).card
                self.assertEqual(final.updated[original_slot],Card(*expected.full()))
            replies=s.handle(p.peer,packet(0x43,14,10,f'@Ready=/Ans={seat}'))
            fields=decode(replies[0].payload).sections['TCList']
            for hand_slot,original_slot in enumerate(s._final_order[seat]):
                self.assertEqual(fields[f'D{seat*5+hand_slot}'],f'{seat}|{final.updated[original_slot].reduced()}')
            self.assertIs(s.final_cards(p.peer),final)
            self.assertEqual(s.handle(p.peer,packet(0x43,14,10,f'@Ready=/Ans={seat}')),[])
        old=s.final_cards('alice')
        s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
        s.handle('bob',packet(0x43,17,1,'@Continue=/Ans=1/PR=0/Conf=0'))
        self.assertIs(s.final_cards('alice'),old)
        s.handle('alice',select(old.updated))
        s.handle('bob',select(s.final_cards('bob').updated,1))
        self.assertIsNone(s.final_cards('alice'))

    def test_result_loser_keeps_promotions_but_gets_no_stat_growth(self):
        s,_=self.ordered_result()
        for card in s.board:
            if card is not None:card.owner=0
        original=list(s.players[1].selected)
        promoted=Card(5,1,2,1,1,0,0,3)
        s.players[1].updated=original.copy();s.players[1].updated[3]=promoted
        s._complete_cards((0,)*8)
        updated=s.final_cards('bob').updated
        self.assertEqual([(c.attack,c.physical,c.magical) for c in updated],[(1,1,1)]*5)
        self.assertEqual([c.kind for c in updated],[0,0,0,2,0])

    def test_late_admission_start_and_lobby_exit_cannot_mutate_active_round(self):
        s,_=service()
        before=s.snapshot();identity=s.players[0].nickname_id
        stale=[packet(0x41,1,body='@GameEN=/NN=0000000000000999'),
               packet(0x41,18,body='@GameENC=/NN=0000000000000999'),
               packet(1,body='@Req=/NN=0000000000000999'),
               packet(0x41,3,body='@GameM='),packet(0x41,10,body='@GameReady='),
               packet(0x41,8,body='@GameOK='),packet(0x41,15,body='@GameNG='),
               packet(0x41,5,body='@GameQT='),packet(0x41,13,body='@GameExit=')]
        for raw in stale:
            self.assertEqual(s.handle('alice',raw),[])
            self.assertEqual(s.snapshot(),before)
            self.assertEqual(s.players[0].nickname_id,identity)
        with self.assertRaises(MatchError):s.register('intruder',3)
        self.assertEqual(s.snapshot(),before)

    def test_admission_identity_and_registered_character_are_frozen_atomically(self):
        s=MatchService();s.register('alice',character_id=17,nickname_id=0x111)
        for raw in (packet(0x41,1,body='@Wrong='),
                    packet(0x41,1,body='@GameEN=/HID=2147483648/NN=0000000000000222'),
                    packet(0x41,1,body='@GameEN=/HID=17/NN=not-hex')):
            before=s.snapshot()
            with self.assertRaises(MatchError):s.handle('alice',raw)
            self.assertEqual(s.snapshot(),before)
        s.handle('alice',packet(0x41,1,body='@GameEN=/HID=0/NN=0000000000000222'))
        self.assertEqual(s.players[0].character_id,17)
        self.assertEqual(s.owner_id,0x222)
        with self.assertRaises(MatchError):s.handle('alice',packet(1,body='@Req=/NN=0000000000000333'))
        self.assertFalse(s.players[0].requested)
        self.assertEqual(s.owner_id,0x222)
        s.handle('alice',packet(1,body='@Req=/NN=0000000000000222'))
        self.assertEqual(s.handle('alice',packet(0x41,1,body='@GameEN=/NN=0000000000000333')),[])
        self.assertEqual(s.owner_id,0x222)

    def test_nonowner_offer_and_nonowner_ejection_are_rejected(self):
        s=MatchService(mode='versus');s.register('alice',1,nickname_id=0x111);s.register('bob',2,nickname_id=0x222)
        s.handle('alice',packet(0x41,1,body='@GameEN='))
        before=s.snapshot()
        with self.assertRaises(MatchError):s.handle('bob',packet(0x41,10,body='@GameReady='))
        with self.assertRaises(MatchError):s.handle('bob',packet(0x41,5,body='@GameQT=/ID=0000000000000111'))
        self.assertEqual(s.snapshot(),before)
        offer=s.handle('alice',packet(0x41,10,body='@GameReady='))
        self.assertEqual([d.peer for d in offer],['bob'])
        self.assertEqual(s.handle('alice',packet(0x41,10,body='@GameReady=')),[])
        s.handle('alice',packet(1,body='@Req='));s.handle('alice',packet(0x41,8,body='@GameOK='))
        self.assertEqual(s.handle('alice',packet(1,body='@Req=')),[])

    def test_ready_checks_final_sequence_and_authenticated_seat(self):
        s,_=service('versus');finish_arrowless_round(s)
        before=s.final_cards('alice')
        for peer,raw in [('alice',packet(0x43,14,8,'@Ready=/Ans=0')),
                         ('bob',packet(0x43,14,10,'@Ready=/Ans=0')),
                         ('alice',packet(0x43,14,10,'@Ready='))]:
            with self.assertRaises(MatchError):s.handle(peer,raw)
        self.assertFalse(s.ready)
        self.assertIs(s.final_cards('alice'),before)
        self.assertEqual(len(s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))),1)

    def test_finished_and_rematch_replay_never_regrows_or_restarts(self):
        s,_=service();finish_arrowless_round(s)
        final=s.final_cards('alice');rng=s.rng.state
        leave=packet(0x43,17,0,'@Continue=/Ans=2/PR=0/Conf=0')
        s.handle('alice',leave)
        for raw in (leave,packet(0x43,14,10,'@Ready=/Ans=0'),ack(0,9),
                    packet(0x41,1,body='@GameEN='),packet(1,body='@Req='),
                    packet(0x41,8,body='@GameOK='),packet(0x43,5,10,'@Quit=/No=0')):
            self.assertEqual(s.handle('alice',raw),[])
            self.assertEqual(s.phase,'finished')
            self.assertIs(s.final_cards('alice'),final)
            self.assertEqual(s.rng.state,rng)
        exit_message=packet(0x41,13,body='@GameExit=')
        self.assertEqual(len(s.handle('alice',exit_message)),1)
        self.assertEqual(s.handle('alice',exit_message),[])

    def test_old_quit_scene_is_ignored_during_rematch_selection(self):
        s,_=service();finish_arrowless_round(s)
        old=s.final_cards('alice')
        s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
        before=s.snapshot()
        for raw in (ack(0,9),packet(0x43,5,10,'@Quit=/No=0'),
                    packet(0x43,14,10,'@Ready=/Ans=0'),packet(0x41,15,body='@GameNG=')):
            self.assertEqual(s.handle('alice',raw),[])
            self.assertEqual(s.snapshot(),before)
        self.assertIs(s.final_cards('alice'),old)
        # The current selection's proper major08 quit still releases its wait.
        result=s.handle('alice',packet(0x43,5,8,'@Quit=/No=0'))
        self.assertEqual(decode(result[0].payload).subtype,5)
        self.assertEqual(s.phase,'finished')

    def test_abort_identity_and_major_validation_precedes_mutation(self):
        s,_=service();s.handle('alice',ack(0,0));before=s.snapshot()
        for raw in (packet(0x43,5,10,'@Quit=/No=1'),packet(0x43,5,10,'@Quit='),
                    packet(0x43,39,2,'@Break=/ID=1'),packet(0x43,39,3,'@Break=/ID=2')):
            with self.assertRaises(MatchError):s.handle('alice',raw)
            self.assertEqual(s.snapshot(),before)
        self.assertEqual(s.handle('alice',packet(0x43,5,8,'@Quit=/No=0')),[])
        self.assertEqual(s.snapshot(),before)
        self.assertEqual(s.handle('alice',packet(0x43,39,3,'@Break=/ID=1')),[])
        self.assertEqual(s.phase,'finished')

    def test_invalid_ascii_and_unowned_selection_leave_state_unchanged(self):
        s=MatchService(mode='versus',selected_card_validator=lambda peer,c:peer=='alice')
        s.register('alice',1);s.register('bob',2);s.begin()
        before=s.snapshot()
        with self.assertRaises(MatchError):s.handle('bob',select(seat=1))
        with self.assertRaises(MatchError):s.handle('alice',b'GTM0G43000700@CardSelect=/C=\xff')
        self.assertEqual(s.snapshot(),before)
        self.assertFalse(s.players[1].selected)

    def test_original_tie_loop_consumes_generated_continuation_same_turn(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from combat_sequence_vectors import CombatVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        s=self.board_case();s.board[6]=BoardCard(Card(6,50,0,40,30,1,64),1,1)
        s.draw=iter([30,30,30,30]+[0]*8).__next__
        first=s.handle('alice',put(0,0,0,5))[1].payload
        s.draw=iter([40,40,5,5]+[255]*8+[30]*8).__next__
        second=s.advance()[0].payload
        v=CombatVM();g=v.game
        a=v.card(5,owner=0,arrows=4);d=v.card(6,owner=1,arrows=64)
        v.w8(g+0x17a,6);v.w8(g+0x18d,0)
        for raw in (first,second):v.queue(raw[5:].decode())
        states=[v.step(8)['combat']]
        v.w16(a+0x8e,30);v.w16(d+0x8e,30);v.w8(g+0x16c,1)
        states += [v.step(0x0b)['combat'],v.step()['combat'],v.step()['combat'],v.step()['combat']]
        self.assertEqual(states,[9,12,7,8,9])
        self.assertEqual((v.r16(a+0x8c),v.r16(d+0x8c)),(40,5))
        self.assertFalse(v.sends)
        self.assertEqual(v.r32(0x102b31c4),0)

    def test_original_result_rebuild_growth_and_TCL_preserve_card_decks(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from result_reward_vectors import VM
            from unicorn.x86_const import UC_X86_REG_EAX,UC_X86_REG_ESI,UC_X86_REG_ESP,UC_X86_REG_EIP
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        s,result=self.ordered_result()
        final=s._final_cards('alice')[0].payload
        for local_seat in (0,1):
            v=VM(seat=local_seat);g,p=v.game,v.player
            v.w8(g+0x197,0);v.w8(g+0x196,10)
            for i in range(16):
                c=g+0x344+i*0x9c
                v.w16(c+14,65535)
                for off in (0x1c,0x1d,0x1e):v.w8(c+off,8)
            for cell,bc in enumerate(s.board):
                if bc is None:continue
                original=s.players[bc.original_owner].selected[bc.original_slot]
                address=g+0x344+cell*0x9c
                v.u.mem_write(address+8,bytes((original.attack,original.kind,original.physical,
                                              original.magical,original.points,original.arrows)))
                v.w16(address+14,original.model);v.w8(address+0x16,original.deck)
                for off,owner in ((0x1c,bc.original_owner),(0x1d,bc.owner),(0x1e,bc.owner)):
                    v.w8(address+off,(owner-local_seat)%2)
            for local in range(2):
                v.w8(p+0x19f+local*0x30,0)
                v.w8(p+0x19b+local*0x30,(local+local_seat)%2)
                v.w8(p+0x1a0+local*0x30,5)
            v.queue(result[5:].decode());self.assertEqual(v.receive(13),1)
            v.call(0x100cbd80,this=g)
            v.u.reg_write(UC_X86_REG_ESP,0x200ff000);v.u.reg_write(UC_X86_REG_ESI,g)
            v.u.emu_start(0x100c87e2,0x100c88df,count=4000000)
            self.assertEqual(v.u.reg_read(UC_X86_REG_EIP),0x100c88df)
            for seat in range(2):
                local=(seat-local_seat)%2
                self.assertEqual(v.r8(p+0x19f+local*0x30),5)
                for hand_slot,original_slot in enumerate(s._final_order[seat]):
                    address=p+0x20c+(local*5+hand_slot)*0x9c
                    self.assertEqual(v.r8(address+0x16),original_slot)
                    v.w8(g+0x17a,hand_slot)
                    v.u.reg_write(UC_X86_REG_ESP,0x200ff000)
                    v.u.reg_write(UC_X86_REG_ESI,g);v.u.reg_write(UC_X86_REG_EAX,local)
                    v.u.emu_start(0x100c8f35,0x100c8fb2,count=4000000)
                    self.assertEqual(v.u.reg_read(UC_X86_REG_EIP),0x100c8fb2)
                    expected=s.players[seat].updated[original_slot]
                    self.assertEqual(list(v.u.mem_read(address+8,6)),
                                     [expected.attack,expected.kind,expected.physical,expected.magical,expected.points,expected.arrows])
            v.queue(final[5:].decode());self.assertEqual(v.receive(26),1)
            for seat in range(2):
                local=(seat-local_seat)%2
                for hand_slot,original_slot in enumerate(s._final_order[seat]):
                    address=p+0x20c+(local*5+hand_slot)*0x9c
                    expected=s.final_cards(s.players[seat].peer).updated[original_slot]
                    actual=Card(v.r16(address+14),*list(v.u.mem_read(address+8,6)),v.r8(address+0x16))
                    self.assertEqual(actual,expected)

    def test_original_receiver_accepts_generated_gameplay(self):
        # Optional dependency belongs to the existing reverse-engineering kit.
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from incoming_gameplay_vectors import ReceiveVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        s,out=service()
        vm=ReceiveVM()
        for delivery, expected in zip(out,(2,1,1)):
            msg=decode(delivery.payload)
            vm.queue(delivery.payload[5:].decode())
            self.assertEqual(vm.receive(msg.subtype),expected)
        self.assertEqual(vm.snapshot()['sequence'],0)
        self.assertEqual(vm.sends[-1]['payload'],'43000900@TurnData=/Ans=0')
        s.handle('alice',ack(0,0))
        moves=s.handle('alice',put(0,0,0,5))
        vm.queue(moves[0].payload[5:].decode())
        self.assertEqual(vm.receive(10),1)
        self.assertEqual(vm.snapshot()['staged_card'],[0,50,0,40,30,1,0,255])
        vm.queue(packet(0x43,12,0,'@BattleData=/A=5|50|30|1|1/D=6|40|5|4|1/R=64|65|66|67|68|69|70|71')[5:].decode())
        self.assertEqual(vm.receive(12),1)

    def test_original_receiver_accepts_init_result_final_and_continue(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from incoming_gameplay_vectors import ReceiveVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        for seat in (0,1):
            with self.subTest(seat=seat):
                s=MatchService(mode='versus')
                s.register('alice',1,b'Alice',0x123)
                s.register('bob',2,b'Bob',0x456)
                initial=s.begin()[0].payload
                v=ReceiveVM(seat=seat);v.w32(0x10294bd4,seat+1)
                v.queue(initial[5:].decode());self.assertEqual(v.receive(1),1)
                self.assertEqual(v.r8(v.player+0x19b),seat)
                self.assertEqual(v.r8(v.player+0xe3),2)
                for p in s.players:p.selected=CARDS
                s.sequence=9
                result=s._end_turn()[0].payload
                v.queue(result[5:].decode());self.assertEqual(v.receive(13),1)
                self.assertEqual(v.r16(v.player+0x84),1) # Required nonzero divisor.
                final=s._final_cards('alice')[0].payload
                v.queue(final[5:].decode());self.assertEqual(v.receive(26),1)
                self.assertEqual(v.snapshot()['card_counts'],[5,5])
                for original_seat in (0,1):
                    local=(original_seat-seat)%2
                    for i in range(5):
                        c=v.player+0x20c+(local*5+i)*0x9c
                        self.assertEqual(v.r16(c+14),i)
                        self.assertEqual(v.r8(c+0x1d),local)
                done=packet(0x43,17,2,'@Continue=/Ans=3|3/B=0|0')
                v.queue(done[5:].decode());self.assertEqual(v.receive(17),1)
                self.assertEqual(v.r8(v.player+0xeb),2)
                self.assertEqual(v.r8(v.player+0x1a6),3)

    def test_original_hand_compacts_and_next_H_addresses_shifted_card(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from combat_sequence_vectors import CombatVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        for role in (0,4):
            with self.subTest(role=role):
                v=CombatVM(role=role);g=v.game;p=v.player
                v.w8(p+0x19f,5);v.w8(g+0x17a,2)
                for i in range(5):
                    c=p+0x20c+i*0x9c
                    v.w16(c+14,10+i)
                    for off,value in ((8,50+i),(9,0),(10,40),(11,30),(12,1),(13,0),(0x16,255),(0x1c,0),(0x1d,0),(0x1e,0)):
                        v.w8(c+off,value)
                v.queue('43000A11@PutCard=/A=0/H=2/F=5/D=12|52|0|40|30|1|0|255')
                self.assertEqual(v.receive(10),1)
                v.block(0x100c6fd5,0x100c7781,esi=g)
                self.assertEqual(v.r8(p+0x19f),4)
                actual=[v.r16(p+0x20c+i*0x9c+14) for i in range(5)]
                self.assertEqual(actual,[10,11,13,14,65535] if role==0 else [10,11,12,13,14])
                if role==0:
                    # The next original placement producer uses current H2,
                    # whose compact card is now model13, and serializes H2.
                    v.w8(g+0x17a,2);v.w8(g+0x179,6)
                    v.block(0x100c5e12,0x100c5e99,esi=g,ebp=0)
                    self.assertEqual(v.r16(g+0x33a),13)
                    self.assertEqual(v.sends[-1]['payload'],'43000A11@PutData=/P=0/H=2/F=6')
                    v.queue('43000A11@PutCard=/A=0/H=2/F=6/D=13|53|0|40|30|1|0|255')
                    self.assertEqual(v.receive(10),1)
                    v.block(0x100c6fd5,0x100c7781,esi=g)
                    self.assertEqual([v.r16(p+0x20c+i*0x9c+14) for i in range(5)],[10,11,14,65535,65535])
                    self.assertEqual(v.r8(p+0x19f),3)

    def test_original_F0_start_retains_empty_normal_board(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from combat_sequence_vectors import CombatVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        v=CombatVM(role=0);g=v.game
        for i in range(16):v.card(i,id=0x8003,owner=4,prop=1)
        v.block(0x100bd8fc,0x100bd94e,esi=g)
        for c in v.view()['board']:
            self.assertEqual((c['id'],c['owner']),(65535,8))
            self.assertEqual(v.r8(g+0x344+c['index']*0x9c+0x24),0)
        _,out=service()
        start=next(d.payload for d in out if decode(d.payload).subtype==8)
        v.queue(start[5:].decode());self.assertEqual(v.receive(8),1)
        self.assertEqual(v.r8(g+0x174)+v.r8(g+0x175),0)
        # Exact normal start gate skips all pending-special construction.
        v.block(0x100c2af8,0x100c2e5e,esi=g)
        self.assertTrue(all(c['id']==65535 and c['owner']==8 for c in v.view()['board']))

    def test_original_rematch_flush_discards_early_start_but_preserves_continue(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from combat_sequence_vectors import CombatVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        v=CombatVM(role=0)
        flushed=(2,4,8,9,10,12,13,14,26,15)
        for sub in flushed:v.queue(f'4300{sub:02X}00@Sentinel=')
        v.queue('43001101@Continue=/Ans=1|1/B=0|0')
        self.assertEqual(v.r32(0x102b31c4),11)
        # Unmodified parent event04000054 queue clear. A duplicate Continue
        # survives it, so replaying terminal replies is not safe recovery.
        v.block(0x100bf01e,0x100bf0aa,esi=v.game)
        self.assertEqual(v.r32(0x102b31c4),1)
        self.assertEqual(v.receive(17),1)
        self.assertEqual(v.r32(0x102b31c4),0)
        self.assertEqual(v.r8(v.player+0xeb),1)

    def test_original_rematch_selection_resets_status_before_new_card_select(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from combat_sequence_vectors import CombatVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        v=CombatVM(role=0);p=v.player
        for seat in range(3):
            for off in (0x19d,0x19f,0x1a8):v.w8(p+seat*0x30+off,5)
            for i in range(5):v.w16(p+0x20c+(seat*5+i)*0x9c+14,10+i)
        # Execute the constructor's local state reset and complete remote
        # card/status reset. Skip only local card positioning/render setup.
        v.block(0x100d71cd,0x100d71df,ebx=0)
        v.block(0x100d7228,0x100d7266,ebx=0)
        for seat in range(3):
            self.assertEqual([v.r8(p+seat*0x30+off) for off in (0x19d,0x19f,0x1a8)],[0,0,0])
        for seat in (1,2):
            self.assertEqual([v.r16(p+0x20c+(seat*5+i)*0x9c+14) for i in range(5)],[65535]*5)
        s,_=service();finish_arrowless_round(s)
        s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
        status=s.handle('alice',select())[0].payload
        v.queue(status[5:].decode());self.assertEqual(v.receive(7),2)
        self.assertEqual(v.snapshot()['selection_status'],[1,1])
        self.assertEqual(v.snapshot()['card_counts'],[5,5])

    def test_original_continue_controller_accepts_generated_rematch_and_leave(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from result_reward_vectors import VM
            from unicorn.x86_const import UC_X86_REG_EIP
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        for answer,child_state,expected in ((1,6,10),(2,7,12)):
            with self.subTest(answer=answer):
                s,_=service();finish_arrowless_round(s)
                reply=s.handle('alice',packet(0x43,17,0,f'@Continue=/Ans={answer}/PR=0/Conf=0'))[0].payload
                v=VM();p=v.player;c=0x20150000
                v.w8(p+0xea,2);v.w8(c+0x184,child_state);v.w8(c+0x188,0)
                v.w8(p+0x1a6,answer);v.w32(p+0xc8,1000);v.w32(p+0xdc,100)
                # Existing result harness boundaries: skip drawing and modal
                # animation only; dispatcher, queue and parser remain original.
                v.hooks[0x1010c4ec]=lambda sp:v.u.reg_write(UC_X86_REG_EIP,0x1010c919)
                for address in (0x1018be80,0x1018bfe0,0x1018bf30):v.hooks[address]=lambda sp:v.ret(sp,0,4)
                for address in (0x101951d0,0x10009e40):v.hooks[address]=lambda sp:v.ret(sp)
                v.hooks[0x10195260]=lambda sp:v.ret(sp,0,16)
                v.w16(c+0x2e,500)
                v.queue(reply[5:].decode());v.call(0x1010c480,this=c)
                self.assertEqual(v.r8(c+0x184),expected)
                self.assertEqual(v.r32(0x102b31c4),0)


class SpecialMatchTests(unittest.TestCase):
    def start(self,fields,cards=CARDS,seed=11,allow_cbc=False):
        s=MatchService(mode='versus',start_fields=fields,seed=seed,allow_cbc=allow_cbc)
        s.register('alice',1);s.register('bob',2);s.begin()
        out=s.handle('alice',select(cards))+s.handle('bob',select(cards,1))
        s.handle('alice',ack(0,0));s.handle('bob',ack(1,0))
        return s,out

    def test_configuration_is_explicit_and_default_remains_ordinary(self):
        for fields,mode in (([18]+[0]*15,'computer'),([18]+[0]*15,'versus'),
                            ([1]*7+[0]*9,'versus'),([0]*25,'versus')):
            with self.assertRaises(ValueError):MatchService(mode=mode,start_fields=fields)
        fields=[0]*16
        for i,f in enumerate((1,2,3,4,5,6,9,17)):fields[i]=f
        s,out=self.start(fields)
        start=next(decode(d.payload).sections['StartData'] for d in out if decode(d.payload).subtype==8)
        self.assertEqual(start,{'S':'0','B':'5','E':'3','F':'|'.join(map(str,fields)),'L':'0|0'})
        self.assertEqual(s.special_flags,dict(st=1,cb=1,ca=1))
        self.assertEqual(s._scores(),[0,0])
        self.assertTrue(all(c is None for c in service()[0].board))
        before=s.snapshot()
        with self.assertRaises(MatchError):s.handle('alice',put(0,0,0,0))
        self.assertEqual(s.snapshot(),before)

    def test_placement_consumes_attack_tile_but_preserves_other_low_tiles(self):
        for f,prop in ((3,0),(4,2),(5,3)):
            fields=[0]*16;fields[5]=f
            s,_=self.start(fields)
            s.handle('alice',put(0,0,0,5))
            self.assertEqual((s.board[5].property,s.board[5].original_slot),(prop,0))
            self.assertEqual((s.sequence,s.active),(1,1))
            self.assertEqual(s.players[0].updated[0],CARDS[0])

    def test_current_settings_must_describe_the_configured_board(self):
        fields=[0]*16;fields[5]=3;fields[6]=7;fields[9]=9
        s=MatchService(mode='versus',start_fields=fields)
        s.register('alice',1);s.register('bob',2)
        before=s.rules_body()
        with self.assertRaises(MatchError):s.handle('alice',packet(0x14,body=NATIVE_SETTINGS))
        self.assertEqual(s.rules_body(),before)
        configured=NATIVE_SETTINGS.replace('/st=0/cb=0/ca=0','/st=1/cb=1/ca=1')
        self.assertEqual(s.handle('alice',packet(0x14,body=configured)),[])
        self.assertEqual(s.rules_body(),configured)
        self.assertIn(b'/R=0|1|1|1|0|0|0',s.begin()[0].payload)

    def test_low3_all_arrows_and_low1_low2_battle_multipliers(self):
        fields=[0]*16;fields[5]=5
        s,_=self.start(fields)
        s.board[6]=BoardCard(CARDS[1],1,1)
        out=s.handle('alice',put(0,0,0,5))
        self.assertFalse(any(decode(d.payload).subtype==12 for d in out))
        self.assertEqual((s.board[5].property,s.board[6].owner),(3,0))
        fields[5]=3
        cards=(Card(0,50,0,40,30,1,4),)+CARDS[1:]
        s,_=self.start(fields,cards)
        defender=Card(1,50,0,40,30,1,64)
        s.board[6]=BoardCard(defender,1,1,1,property=2,modifier=10)
        draws=[3,2,20,21,8,9]+[255]*8+[0]*8
        expected=calculate_battle(cards[0],defender,iter(draws).__next__,
            attacker_property=1,defender_property=2,defender_modifier=10)
        s.draw=iter(draws).__next__
        out=s.handle('alice',put(0,0,0,5))
        data=next(decode(d.payload).sections['BattleData'] for d in out if decode(d.payload).subtype==12)
        self.assertEqual(tuple(map(int,data['A'].split('|'))),expected.attacker.wire(5))
        self.assertEqual(tuple(map(int,data['D'].split('|'))),expected.defender.wire(6))
        self.assertEqual((s.board[5].property,s.board[6].property),(0,2))

    def test_strength_tile_modifies_active_cards_without_changing_inventory(self):
        for f,delta in ((6,-10),(7,10)):
            fields=[0]*16;fields[6]=f
            cards=(Card(0,50,0,40,30,1,4),)+CARDS[1:]
            s,_=self.start(fields,cards)
            s.board[0]=BoardCard(CARDS[1],0,0,modifier=125)
            s.board[1]=BoardCard(CARDS[2],1,1,modifier=11)
            out=s.handle('alice',put(0,0,0,5))
            battle=next(decode(d.payload).sections['BattleData'] for d in out if decode(d.payload).subtype==12)
            self.assertEqual([int(battle[x].split('|')[3]) for x in ('A','D')],[0,0])
            self.assertIsNone(s.board[6]);self.assertEqual(s.board[5].modifier,delta)
            self.assertEqual(s.board[0].modifier,((125+delta+128)&255)-128)
            self.assertEqual(s.board[1].modifier,11)
            self.assertEqual(s.players[0].updated[0],cards[0])
            self.assertEqual(s.sequence,1)

    def test_ownership_tile_can_end_attack_and_abandon_direct_capture(self):
        fields=[0]*16;fields[6]=8
        cards=(Card(0,50,0,40,30,1,4|16),)+CARDS[1:]
        s,_=self.start(fields,cards)
        s.board[9]=BoardCard(CARDS[0],1,1)
        # Native calculator then shared R1=255 picks the other player.
        s.draw=iter([10]*4+[0,255,0,0,0,0,0,0]+[0]*8).__next__
        s.handle('alice',put(0,0,0,5))
        self.assertEqual((s.board[5].owner,s.board[9].owner),(1,1))
        self.assertIsNone(s.board[6]);self.assertEqual(s.sequence,1)
        self.assertFalse(s.markers or s.targets or s.pending_work)

    def test_shuffling_retains_stable_copy_identity_and_ends_turn(self):
        fields=[0]*16;fields[6]=17;fields[1]=3
        cards=(Card(0,50,0,40,30,1,4,7),)+CARDS[1:]
        s,_=self.start(fields,cards)
        s.board[14]=BoardCard(Card(4,50,0,40,30,1,0,8),1,1,4,property=2,modifier=-10)
        s.draw=iter([10]*4+list(range(8))+[0]*8).__next__
        s.handle('alice',put(0,0,0,5))
        self.assertEqual(s.sequence,1);self.assertIsNone(s.board[6])
        cards_by_identity={(c.original_owner,c.original_slot):c for c in s.board if c and c.card}
        self.assertEqual(set(cards_by_identity),{(0,0),(1,4)})
        self.assertEqual(cards_by_identity[0,0].card,cards[0])
        moved=cards_by_identity[1,4]
        self.assertEqual((moved.property,moved.modifier,moved.card.deck),(2,-10,8))
        self.assertEqual(s.board[1].tile_id,0x8003)

    def test_complete_configured_rounds_preserve_ten_original_cards_and_growth(self):
        for seed in range(16):
            rng=random.Random(seed)
            fields=[0]*16
            for index,f in zip(rng.sample(range(16),8),(1,3,4,5,6,7,8,9+seed%8)):
                fields[index]=f
            if seed%2:
                fields[next(i for i,f in enumerate(fields) if f==1)]=17
            cards=tuple(Card(i,50,0,40,30,1,rng.randrange(256),i) for i in range(5))
            s,_=self.start(fields,cards,seed=seed)
            for step in range(500):
                if s.phase=='result':break
                if s.pending_work:s.advance();continue
                if s.phase=='battle':
                    s.handle(s.players[s.active].peer,packet(0x43,11,s.sequence,f'@BattleSelect=/B={rng.choice(s.targets)}'))
                    continue
                self.assertEqual(s.phase,'turn')
                for seat,p in enumerate(s.players):s.handle(p.peer,ack(seat,s.sequence))
                vacant=s.legal_fields()
                s.handle(s.players[s.active].peer,put(s.active,s.sequence,
                    rng.randrange(len(s.players[s.active].hand)),rng.choice(vacant)))
            self.assertEqual(s.phase,'result',(seed,s.snapshot()))
            self.assertEqual(s.sequence,10);self.assertEqual(sum(s._scores()),10)
            identities=[(c.original_owner,c.original_slot) for c in s.board if c and c.card]
            self.assertEqual(sorted(identities),[(seat,slot) for seat in range(2) for slot in range(5)])
            for seat,p in enumerate(s.players):
                final=s.final_cards(p.peer)
                self.assertEqual(final.original,cards)
                for original,updated in zip(final.original,final.updated):
                    self.assertEqual((updated.model,updated.arrows,updated.deck),
                                     (original.model,original.arrows,original.deck))
                    self.assertLessEqual(sum(max(0,getattr(updated,k)-getattr(original,k))
                        for k in ('attack','physical','magical')),1)
                self.assertEqual(len(s._final_order[seat]),5)
            if seed==0:
                s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
                s.handle('bob',packet(0x43,17,1,'@Continue=/Ans=1/PR=0/Conf=0'))
                s.handle('alice',select(cards));out=s.handle('bob',select(cards,1))
                self.assertEqual(s._effect_cells(),s.start_board.cells)
                self.assertEqual(decode(out[2].payload).sections['StartData']['F'],'|'.join(map(str,fields)))

    def test_CPU_special_adapter_matches_original_choice_and_random_stream(self):
        from test_native_ai import OriginalAI,Draws
        fields=[0]*16;fields[5]=5;fields[9]=12;fields[6]=7
        s=MatchService(start_fields=fields,seed=812)
        s.register('alice');s.begin();s.handle('alice',select())
        s.handle('alice',ack(0,0));s.handle('alice',put(0,0,0,0))
        self.assertEqual((s.phase,s.active),('turn',1))
        v=OriginalAI();draws=[47,2,22,0,0,15]
        v.seed(s.players[1].hand,(None,)*16,1,draws,1,s.ai_personality)
        for i,c in enumerate(s._effect_cells()):
            v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,prop=c.property,direction=c.direction)
        expected_hand=v.hand(1);expected_field=v.placement()
        stream=Draws(draws+[40]*200);s.draw=stream
        replies=s.handle('alice',ack(0,1))
        placed=next(decode(d.payload).sections['PutCard'] for d in replies if decode(d.payload).subtype==10)
        self.assertEqual((int(placed['H']),int(placed['F'])),(expected_hand,expected_field))
        self.assertEqual(stream.used[:len(v.rng)],v.rng)

    def test_complete_CPU_configured_rounds_and_rematch(self):
        for seed in range(12):
            fields=[0]*16
            for i,f in zip((0,2,5,6,9,10,14),(3,4,5,6,7,8,9+seed%8)):fields[i]=f
            if seed%2:fields[6]=17
            cards=tuple(Card(i,50,0,40,30,1,(seed*23+i*41)&255,i) for i in range(5))
            s=MatchService(start_fields=fields,seed=seed)
            s.register('alice');s.begin();s.handle('alice',select(cards))
            for step in range(500):
                if s.phase=='result':break
                if s.pending_work:s.advance();continue
                if s.phase=='battle':
                    self.assertEqual(s.active,0)
                    s.handle('alice',packet(0x43,11,s.sequence,f'@BattleSelect=/B={s.targets[0]}'));continue
                s.handle('alice',ack(0,s.sequence))
                if s.phase=='turn' and s.active==0 and s._unlocked():
                    s.handle('alice',put(0,s.sequence,0,s.legal_fields()[0]))
            self.assertEqual(s.phase,'result',(seed,s.snapshot()))
            self.assertEqual(sum(s._scores()),10)
            self.assertEqual(s.final_cards('alice').original,cards)
            self.assertEqual([(c.model,c.arrows,c.deck) for c in s.final_cards('alice').updated],
                             [(c.model,c.arrows,c.deck) for c in cards])
            s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
            s.handle('alice',select(cards))
            self.assertEqual(s._effect_cells(),s.start_board.cells)
            self.assertEqual(s.players[1].hand,list(s.ai_cards))

    def test_observer_snapshot_is_immutable_and_reuses_exact_turn_packet(self):
        from dataclasses import FrozenInstanceError
        fields=[0]*16;fields[5]=3;fields[9]=11
        s,out=self.start(fields)
        state=s.rng.state
        snapshot=s.observer_snapshot()
        turn=next(d.payload for d in out if decode(d.payload).subtype==9)
        self.assertEqual(snapshot.turn_packet,turn)
        self.assertEqual((snapshot.phase,snapshot.round_id,snapshot.sequence),('turn',1,0))
        self.assertEqual(snapshot.players[0].remaining,5)
        self.assertEqual(snapshot.board[5].property,1)
        self.assertEqual(snapshot.start_fields,tuple(fields))
        with self.assertRaises(FrozenInstanceError):snapshot.active=1
        with self.assertRaises(FrozenInstanceError):snapshot.board[5].owner=1
        self.assertEqual(s.rng.state,state)
        s.handle('alice',put(0,0,0,5))
        after=s.observer_snapshot()
        self.assertEqual((snapshot.players[0].remaining,snapshot.board[5].model),(5,0x8003))
        self.assertEqual((after.players[0].remaining,after.board[5].identity),(4,(0,0)))
        self.assertEqual(decode(after.turn_packet).argument,1)

    def test_CBC_explicit_gate_generated_packet_and_snapshot_reward(self):
        fields=[0]*16;fields[6]=18
        with self.assertRaises(ValueError):MatchService(start_fields=fields)
        cards=(Card(0,50,0,40,30,1,4),)+CARDS[1:]
        s,_=self.start(fields,cards,allow_cbc=True)
        expected_rng=CRTLCG(s.rng.state)
        calculate_battle(cards[0],Card(0,0,0,0,0,0,0),expected_rng,defender_property=0x50)
        expected_R=tuple(expected_rng()&255 for _ in range(8))
        model=s.catalog.select(75,0,expected_rng)
        expected=Card(*s.catalog.generate(model,expected_rng).full())
        out=s.handle('alice',put(0,0,0,5))
        battle=next(decode(d.payload).sections['BattleData'] for d in out if decode(d.payload).subtype==12)
        self.assertEqual(tuple(map(int,battle['R'].split('|'))),expected_R)
        self.assertEqual(Card.parse(battle['CBC']),expected)
        self.assertEqual((s.board[6].card,s.board[6].original_owner,s.board[6].owner,
                          s.board[6].original_slot),(expected,5,0,None))
        self.assertEqual(expected.deck,255)
        # The generated card may be promoted in later combat, but it never
        # enters the original five-slot growth or TCList result reconstruction.
        s._grow_board(s.board[6],0,0)
        promoted=s.board[6].card
        s.sequence=9;s._end_turn()
        self.assertEqual(s.final_cards('alice').extras,(promoted,))
        self.assertEqual(s.final_cards('bob').extras,())
        self.assertEqual(len(s.final_cards('alice').updated),5)
        self.assertEqual(decode(s._final_cards('alice')[0].payload).sections['TCList']['H'],'5|5')
        final=s.final_cards('alice')
        for _ in range(3):s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))
        self.assertIs(s.final_cards('alice'),final)
        s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/PR=0/Conf=0'))
        s.handle('bob',packet(0x43,17,1,'@Continue=/Ans=1/PR=0/Conf=0'))
        self.assertIs(s.final_cards('alice'),final)

    def test_original_dispatcher_consumes_generated_special_BattleData(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from combat_sequence_vectors import CombatVM
        except ImportError:
            self.skipTest('original-code emulator dependencies unavailable')
        for f in (6,7,8,9,10,11,12,13,14,15,16,17,18):
            with self.subTest(F=f):
                fields=[0]*16;fields[6]=f
                s,_=self.start(fields,allow_cbc=f==18)
                s.source=5;s.phase='battle';s.targets=[6];s.markers={6:1}
                s.board[5]=BoardCard(Card(0,70,0,40,30,1,4,3),0,0,0)
                s.board[2]=BoardCard(CARDS[2],1,1,2)
                before=s._effect_cells()
                s.draw=iter([10]*4+[0,255,0,0,0,0,0,0]+[30]*200).__next__
                out=s._battle(6)
                raw=next(d.payload for d in out if decode(d.payload).subtype==12)
                v=CombatVM();g=v.game
                # Presentation-only jitter consumes local rand after network
                # values have arrived; authoritative shared R stays original.
                v.hooks[0x101e2e44]=lambda sp:v.ret(sp,0)
                v.w8(g+0x18d,0);v.w8(g+0x17a,6);v.w8(g+0x179,5)
                for i,c in enumerate(before):
                    address=v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,prop=c.property,
                                   mark=c.marker,direction=c.direction)
                    v.w8(address+0x1c,c.base_owner);v.w8(address+0x26,c.modifier&255)
                    if c.card:
                        card=c.card
                        v.u.mem_write(address+8,bytes((card.attack,card.kind,card.physical,card.magical,card.points,card.arrows)))
                        v.w8(address+0x16,card.deck)
                v.queue(raw[5:].decode())
                state=v.step(8)
                self.assertEqual(state['combat'],9)
                for _ in range(300):
                    # Ownership17 applies its mutation in elapsed ticks16..31;
                    # coarse presentation skips would jump over that window.
                    state=v.step(elapsed=1 if state['combat']==0x17 else 100)
                    if state['return_value']==1:break
                self.assertEqual(state['return_value'],1,(f,state))
                # Parent turn completion rotates each spinner exactly once.
                v.block(0x100c7474,0x100c756d,esi=g)
                for i,c in enumerate(s._effect_cells()):
                    address=g+0x344+i*0x9c
                    self.assertEqual((v.r16(address+14),v.r8(address+0x1c),v.r8(address+0x1d),
                                      v.r8(address+0x24),v.r8(address+0x26),v.r8(address+13)),
                                     (c.model,c.base_owner,c.owner,c.property,c.modifier&255,c.arrows),(f,i))


class ThreePlayerTests(unittest.TestCase):
    peers=('alice','bob','carol')

    def test_random_three_player_placements_resolve_targets_in_last_two_rows(self):
        cards=tuple(Card(i,30+i*5,0,40,35,1,255) for i in range(5))
        high_targets=set()
        for seed in range(32):
            s,_,_=self.start(cards,seed=seed+1)
            choices=random.Random(seed)
            out=[]
            for _ in range(1000):
                if s.phase=='result':break
                if s.pending_work:out+=s.advance();continue
                if s.phase=='battle':
                    out+=s.handle(self.peers[s.active],packet(0x43,11,s.sequence,f'@BattleSelect=/B={s.targets[0]}'))
                    continue
                sequence=s.sequence
                for seat in (0,1,2):out+=s.handle(self.peers[seat],ack(seat,sequence))
                out+=s.handle(self.peers[s.active],put(s.active,s.sequence,0,choices.choice(s.legal_fields())))
            self.assertEqual((s.phase,s.sequence),('result',15))
            for delivery in out:
                parsed=decode(delivery.payload)
                if parsed.subtype==12:
                    target=int(parsed.sections['BattleData']['D'].split('|')[0])
                    if target>=16:high_targets.add(target)
            self.assertEqual(sum(s._scores()),15)
        self.assertEqual(high_targets,set(range(16,25)))

    def start(self,cards=CARDS,seed=3):
        s=MatchService(mode='versus',player_count=3,seed=seed)
        for i,peer in enumerate(self.peers):s.register(peer,i+1,peer.title().encode(),i+100)
        initial=s.begin()
        out=[]
        for i,peer in enumerate(self.peers):out+=s.handle(peer,select(cards,i))
        return s,initial,out

    def finish(self,s):
        out=[]
        for _ in range(1000):
            if s.phase=='result':return out
            if s.pending_work:out+=s.advance();continue
            if s.phase=='battle':
                out+=s.handle(s.players[s.active].peer,packet(0x43,11,s.sequence,f'@BattleSelect=/B={s.targets[0]}'));continue
            self.assertEqual(s.phase,'turn')
            for seat in (2,0,1):out+=s.handle(self.peers[seat],ack(seat,s.sequence))
            if s.phase=='turn':out+=s.handle(self.peers[s.active],put(s.active,s.sequence,0,s.legal_fields()[0]))
        self.fail('three-player match did not finish')

    def test_shape_admission_and_all_three_acknowledgements(self):
        with self.assertRaises(ValueError):MatchService(player_count=3)
        with self.assertRaises(ValueError):MatchService(mode='versus',player_count=4)
        with self.assertRaises(ValueError):MatchService(mode='versus',player_count=3,start_fields=[0]*16)
        s,initial,out=self.start()
        self.assertEqual((len(s.board),s.last_sequence),(25,14))
        self.assertEqual(dict(s.neighbors(0)),{2:1,3:6,4:5})
        self.assertNotIn(5,dict(s.neighbors(4)).values())
        self.assertEqual(decode(initial[0].payload).sections['VsGameInit']['N'],'3')
        start=next(decode(d.payload).sections['StartData'] for d in out if decode(d.payload).subtype==8)
        self.assertEqual((len(start['F'].split('|')),start['L']),(25,'0|0|0'))
        with self.assertRaises(MatchError):s.register('fourth',4)
        self.assertEqual(s.handle('alice',put(0,0,0,24)),[])
        for seat in (2,0):self.assertEqual(s.handle(self.peers[seat],ack(seat,0)),[])
        self.assertEqual(s.sequence,0)
        out=s.handle('bob',ack(1,0));self.assertEqual(s.sequence,1)
        self.assertEqual([decode(d.payload).subtype for d in out],[10]*3+[9]*3)
        self.assertEqual(s.board[24].original_owner,0)
        state=s.snapshot()
        for peer in self.peers:s.handle(peer,ack(self.peers.index(peer),0))
        self.assertEqual(s.snapshot(),state)

    def test_fifteen_turns_all_final_snapshots_ready_and_rematch(self):
        for arrows in (0,255,85):
            cards=tuple(Card(i,30+i*5,0,40,35,1,arrows) for i in range(5))
            s,_,_=self.start(cards,seed=arrows+100)
            out=self.finish(s)
            self.assertEqual((s.sequence,s.active,s.completed_matches),(15,0,1))
            final=tuple(s.final_cards(p) for p in self.peers)
            self.assertTrue(all(f and len(f.original)==len(f.updated)==5 for f in final))
            for i,peer in enumerate(self.peers):
                replies=s.handle(peer,packet(0x43,14,15,f'@Ready=/Ans={i}'))
                self.assertEqual(decode(replies[0].payload).sections['TCList']['H'],'5|5|5')
                self.assertEqual(s.handle(peer,packet(0x43,14,15,f'@Ready=/Ans={i}')),[])
            for i in (2,0):
                self.assertEqual(s.handle(self.peers[i],packet(0x43,17,i,'@Continue=/Ans=1/Conf=0')),[])
                self.assertEqual(s.phase,'result')
            replies=s.handle('bob',packet(0x43,17,1,'@Continue=/Ans=1/Conf=0'))
            self.assertEqual(decode(replies[0].payload).sections['Continue']['Ans'],'1|1|1')
            self.assertEqual((s.phase,len(s.board),s.active),('selection',25,0))
            self.assertEqual(tuple(s.final_cards(p) for p in self.peers),final)
            for i,peer in enumerate(self.peers):s.handle(peer,select(final[i].updated,i))
            self.finish(s);self.assertEqual(s.completed_matches,2)
            replies=s.handle('carol',packet(0x43,17,2,'@Continue=/Ans=2/Conf=0'))
            self.assertEqual(decode(replies[0].payload).sections['Continue']['Ans'],'3|3|3')
            self.assertEqual(s.phase,'finished')

    def test_three_seat_initialization_and_all_packets_in_original_receiver(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from result_reward_vectors import VM
        s,initial,selected=self.start()
        result_packets=self.finish(s)
        for seat,peer in enumerate(self.peers):
            v=VM(n=3,seat=seat);v.w32(0x10294bd4,seat+1)
            v.queue(initial[seat].payload[5:].decode());self.assertEqual(v.receive(1),1)
            self.assertEqual((v.r8(v.player+0xe5),v.r8(v.player+0x19b)),(3,seat))
            for delivery in selected:
                if delivery.peer!=peer:continue
                msg=decode(delivery.payload);v.queue(delivery.payload[5:].decode())
                self.assertEqual(v.receive(msg.subtype),2 if msg.subtype==7 and msg.sections['CardSelect']['Ok']=='1' else 1)
            # All15 PutCard/TurnData packets, followed by recipient-local stats.
            for delivery in result_packets:
                if delivery.peer!=peer:continue
                msg=decode(delivery.payload);v.queue(delivery.payload[5:].decode())
                self.assertEqual(v.receive(msg.subtype),1)
            self.assertEqual(v.r16(v.player+0x84),1)
            final=s._final_cards(peer)[0].payload;v.queue(final[5:].decode())
            self.assertEqual(v.receive(26),1)
            self.assertEqual(v.snapshot()['card_counts'],[5,5,5])
            for server_seat in range(3):
                local=(server_seat-seat)%3
                for slot in range(5):
                    address=v.player+0x20c+(local*5+slot)*0x9c
                    self.assertEqual((v.r16(address+14),v.r8(address+0x1d)),(slot,local))

    def test_three_player_original_result_ranks_all_fifteen_card_partitions(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from result_reward_vectors import rank
        s=MatchService(mode='versus',player_count=3)
        checked=0
        for a in range(16):
            for b in range(16-a):
                scores=(a,b,15-a-b)
                s.board=[BoardCard(CARDS[0],seat,seat) for seat,count in enumerate(scores) for _ in range(count)]+[None]*10
                expected=s.result_classes()
                row=rank(scores)
                self.assertNotIn('error',row)
                actual=tuple(p[1] for p in row['after']['players'])
                self.assertEqual(actual,expected,scores)
                checked+=1
        self.assertEqual(checked,136)

    def test_maximum_three_seat_packets_stay_in_single_body(self):
        s=MatchService(mode='versus',player_count=3)
        for seat,peer in enumerate(self.peers):
            stats=PracticeStats(rating=0xffffffff,count=65535,sequence=65535,reward_sum=0xffffffff,rn=b'R'*31,event_count=65535)
            s.register(peer,seat+1,b'N'*15,0xffffffffffffffff-seat,points=65535,result_stats=stats)
        self.assertLessEqual(len(s.playack('alice'))-13,447)
        s.begin()
        extreme=tuple(Card(249,255,3,255,255,255,255) for _ in range(5))
        for p in s.players:s._choose_cards(p,extreme)
        s.sequence=14;out=s._end_turn()
        self.assertEqual(len(out),3)
        for delivery in out:
            self.assertLessEqual(len(delivery.payload)-13,447)
            parsed=decode(delivery.payload)
            self.assertEqual(parsed.sections['ResultData']['P'],'3')
            self.assertEqual(sum('RN' in parsed.sections[f'No{i}'] for i in range(3)),1)
        for peer in self.peers:self.assertLessEqual(len(s._final_cards(peer)[0].payload)-13,447)


class OriginalComputerTests(unittest.TestCase):
    def menu(self,seed=20):
        s=MatchService(computer_menu=True,seed=seed)
        s.register('alice',nickname_id=0x123,points=222,collection_size=10)
        entered=s.handle('alice',packet(0x41,0x12,body='@GameENC=/NN=0000000000000123/HID=1'))
        self.assertEqual(decode(entered[0].payload).subtype,0x13)
        out=s.handle('alice',packet(1,body='@Req=/NN=0000000000000123/HID=1'))
        self.assertEqual([(decode(d.payload).primary,decode(d.payload).subtype) for d in out],[(2,0),(0x43,2)])
        return s,out

    @staticmethod
    def choose(npc=1,argument=2,rule='0|0|0|0|2|0|0|1'):
        return packet(0x43,6,argument,'@ComGame=/Rule='+rule+(f'/Com={npc}' if argument else ''))

    def finish(self,s):
        for _ in range(1000):
            if s.phase=='result':return
            if s.pending_work:s.advance();continue
            if s.phase=='battle':
                s.handle('alice',packet(0x43,11,s.sequence,f'@BattleSelect=/B={s.targets[0]}'));continue
            self.assertEqual(s.phase,'turn')
            s.handle('alice',ack(0,s.sequence))
            if s.phase=='turn' and s.active==0 and s._unlocked():
                s.handle('alice',put(0,s.sequence,0,s.legal_fields()[0]))
        self.fail('original computer policy did not finish')

    def test_original_menu_requires_correct_admission_and_never_uses_VS_names(self):
        with self.assertRaises(ValueError):MatchService(mode='versus',computer_menu=True)
        s=MatchService(computer_menu=True);s.register('alice')
        before=s.snapshot()
        for request in (packet(1,body='@Req='),packet(0x41,1,body='@GameEN=/NN=0000000000000033')):
            with self.assertRaises(MatchError):s.handle('alice',request)
            self.assertEqual(s.snapshot(),before)
            self.assertEqual(s.players[0].nickname_id,1)
        s,out=self.menu()
        self.assertEqual((s.phase,len(s.players)),('computer_menu',1))
        self.assertIn(b'/S=10/',out[1].payload)
        self.assertEqual(decode(out[0].payload).sections['GameML']['Num'],'1')
        self.assertEqual(s.handle('alice',packet(0x41,8,body='@GameOK=')),[])
        state=s.rng.state
        self.assertEqual(s.handle('alice',self.choose(argument=0,rule='0|0|0|0|4|0|2')),[])
        self.assertEqual((s.phase,s.computer_view,s.rng.state),('computer_menu',2,state))
        self.assertEqual(s.handle('alice',self.choose()),[])
        self.assertEqual((s.phase,s.npc_id),('selection',1))
        self.assertEqual(s.players[1].name,b'Flower Girl Natasha')
        with self.assertRaises(MatchError):s.register('third',2)
        out=s.handle('alice',select())
        self.assertEqual([decode(d.payload).subtype for d in out],[7,8,9])
        self.assertFalse(any(b'VsGameInit' in d.payload or b'/CN=' in d.payload for d in out))

    def test_original_computer_configuration_sender_and_init_receiver(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from incoming_gameplay_vectors import ReceiveVM
        s,out=self.menu()
        v=ReceiveVM();table=0x20110000;v.w32(0x102bb630,table)
        v.queue(out[1].payload[5:].decode());self.assertEqual(v.receive(2),1)
        self.assertEqual((v.r16(v.player),v.r16(v.player+0xec)),(222,10))
        from native_opponents import OPPONENTS
        self.assertEqual([v.r32(table+24+i*20) for i in range(30)],[n.rating for n in OPPONENTS])
        # Execute original06 serialization with the actual menu option bytes.
        v.w32(v.player+0xdc,0)
        for off,value in ((0xe0,0),(0xe1,0),(0xe2,0),(0xe3,2),(0xe4,0),(0xd9,2),(0xe9,1),(0x1ca,21)):
            v.w8(v.player+off,value)
        v.call(0x10106e30,[6,2,0])
        request=b'GTM0G'+v.sends[-1]['payload'].encode()
        self.assertEqual(request,self.choose(21,rule='0|0|0|0|2|0|2|1'))
        self.assertEqual(s.handle('alice',request),[])
        self.assertEqual((s.npc_id,s.players[1].name),(21,b'Revolutionary Carol'))

    def test_invalid_computer_choices_do_not_mutate_or_draw(self):
        s,_=self.menu()
        before=(s.snapshot(),s.computer_view,s.rng.state)
        invalid=[self.choose(0),self.choose(31),self.choose(argument=3),self.choose(rule='1|0|0|0|2|0|0|1'),
                 self.choose(rule='0|2|0|0|2|0|0|1'),self.choose(rule='0|0|0|0|6|0|0|1'),
                 self.choose(rule='0|0|0|0|2|1|0|1'),self.choose(rule='0|0|0|0|2|0|3|1'),
                 self.choose(rule='0|0|0|0|2|0|0|3')]
        for request in invalid:
            with self.assertRaises(MatchError):s.handle('alice',request)
            self.assertEqual((s.snapshot(),s.computer_view,s.rng.state),before)

    def test_complete_original_NPC_round_rematch_and_menu_reconfiguration(self):
        from native_opponents import prepare_opponent
        for npc in (1,3,30):
            s,_=self.menu(seed=20+npc)
            expected=prepare_opponent(npc,s.catalog,CRTLCG(s.rng.state))
            s.handle('alice',self.choose(npc))
            self.assertEqual([c.full() for c in s.ai_cards],['|'.join(map(str,c.full())) for c in expected.hand])
            s.handle('alice',select());self.finish(s)
            final=s.final_cards('alice');self.assertEqual(final.round_id,1)
            old=s.ai_cards
            s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/Conf=0'))
            self.assertEqual(s.phase,'selection');self.assertNotEqual(s.ai_cards,old)
            self.assertIs(s.final_cards('alice'),final)
            s.handle('alice',select(final.updated));self.finish(s)
            final=s.final_cards('alice');self.assertEqual(final.round_id,2)
            out=s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/Conf=1'))
            self.assertEqual([decode(d.payload).subtype for d in out],[4])
            self.assertEqual(s.phase,'computer_menu');self.assertIs(s.final_cards('alice'),final)
            self.assertEqual(s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/Conf=1')),[])
            s.handle('alice',self.choose(2));self.assertEqual(s.npc_id,2)
            self.assertIs(s.final_cards('alice'),final)
            s.handle('alice',select(final.updated));self.assertIsNone(s.final_cards('alice'))
            self.finish(s);self.assertEqual(s.final_cards('alice').round_id,3)




class CardPrizeIntegrationTests(unittest.TestCase):
    def result_fixture(self,owners,*,cbc=False):
        n=len(owners)
        fields=[0]*16
        if cbc:fields[15]=18
        s=MatchService(mode='versus',player_count=n,card_prizes=True,
                       allow_cbc=cbc,start_fields=fields if cbc else None,seed=53)
        for seat in range(n):s.register(f'p{seat}',character_id=seat+1,nickname_id=seat+1)
        s.begin()
        cards=[tuple(Card(seat*5+i,10,0,10,10,1,0,i) for i in range(5)) for seat in range(n)]
        for seat in range(n):s.handle(f'p{seat}',select(cards[seat],seat))
        slots=(4,1,3,0,2)
        for seat,hand in enumerate(owners):
            for j,owner in enumerate(hand):
                slot=slots[j]
                s.board[seat*5+j]=BoardCard(cards[seat][slot],owner,seat,slot)
        if cbc:s.board[15]=BoardCard(Card(20,10,0,10,10,1,0),0,5,None)
        s.sequence=s.last_sequence;s.source=None
        return s,s._end_turn()

    def ready(self,s):
        out=[]
        for seat,p in enumerate(s.players):
            out+=s.handle(p.peer,packet(0x43,14,s.sequence,f'@Ready=/Ans={seat}'))
        return out

    def test_auto_awards_hold_result_until_durable_confirmation(self):
        for owners,awards in [([[0]*5,[0,1,1,1,1]],1),
                              ([[0]*5,[0]*5],5),
                              ([[0]*5,[1]*5],0),
                              ([[1]*5,[1]*5,[1]*5],10)]:
            s,out=self.result_fixture(owners)
            self.assertEqual(out,[])
            self.assertTrue(s.prize_commit_required)
            final=s.final_prizes()
            self.assertEqual(len(final.settlement.transfers),awards)
            self.assertEqual(final.round_id,1)
            self.assertTrue(all(s.final_cards(p.peer) is not None for p in s.players))
            with self.assertRaises(MatchError):s.confirm_prize_commit(0)
            for ans in (1,2):
                with self.assertRaises(MatchError):s.handle('p0',packet(0x43,17,0,f'@Continue=/Ans={ans}/Conf=0'))
            out=s.confirm_prize_commit(1)
            self.assertEqual([decode(d.payload).subtype for d in out],[13]*len(owners))
            self.assertEqual(s.confirm_prize_commit(1),[])
            self.assertFalse(s.prize_commit_required)
            self.assertEqual(len(self.ready(s)),len(owners))
            self.assertIs(s.final_prizes(),final)

    def test_ambiguous_choice_holds_only_last_reply_and_native_consumes_wire(self):
        s,out=self.result_fixture([[0]*5,[0,0,1,1,1]])
        self.assertEqual([decode(d.payload).subtype for d in out],[13,13])
        self.assertFalse(s.prize_commit_required);self.assertIsNone(s.final_prizes())
        choice=s.prize_choice
        with self.assertRaises(MatchError):s.handle('p0',packet(0x43,15,0,'@GetSelect=/H=0'))
        tc=self.ready(s)
        initial={d.peer:d.payload for d in tc}
        for bad_peer,header,h in [('p1',1,0),('p0',1,0),('p0',0,4)]:
            with self.assertRaises(MatchError):s.handle(bad_peer,packet(0x43,15,header,f'@GetSelect=/H={h}'))
            self.assertEqual(s.prize_choice,choice)
        chosen=s.handle('p0',packet(0x43,15,0,'@GetSelect=/H=1'))
        self.assertEqual(chosen,[]);self.assertTrue(s.prize_commit_required)
        final=s.final_prizes();award=final.settlement.transfers[0]
        self.assertEqual((award.winner,award.loser,award.hand_index,award.original_slot),(0,1,1,1))
        self.assertEqual(award.source_card,s.final_cards('p1').updated[1])
        self.assertEqual(s.handle('p0',packet(0x43,15,0,'@GetSelect=/H=1')),[])
        self.assertEqual(s.handle('p0',packet(0x43,14,10,'@Ready=/Ans=0')),[])
        replies=s.confirm_prize_commit(1)
        self.assertEqual([d.payload for d in replies],[packet(0x43,15,0,'@GetSelect=/E=0/H=1')]*2)
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from result_reward_vectors import VM
        for seat in range(2):
            v=VM(2,seat);v.w8(v.game+0x18d,s.last_sequence)
            v.queue(initial[f'p{seat}'][5:].decode());self.assertEqual(v.receive(26),1)
            for server in range(2):
                local=(server-seat)%2
                for j in range(5):
                    owner=0 if server==0 or j<2 else 1
                    self.assertEqual(v.r8(v.player+0x229+(local*5+j)*0x9c),(owner-seat)%2)
            v.queue(replies[seat].payload[5:].decode());self.assertEqual(v.receive(15),1)
            self.assertEqual(v.r8(v.game+0x1b2),1)

    def test_three_player_choices_compact_hands_and_preserve_cached_tclist(self):
        s,out=self.result_fixture([[0]*5,[0,0,2,2,1],[2]*5])
        self.assertEqual(s._scores(),[7,1,7])
        # All three learn original capture owners, even if a later recipient
        # asks Ready after an earlier winner choice mutated the server ledger.
        first=s.handle('p0',packet(0x43,14,15,'@Ready=/Ans=0'))[0].payload
        out=s.handle('p0',packet(0x43,15,0,'@GetSelect=/H=1'))
        self.assertEqual([decode(d.payload).subtype for d in out],[15]*3)
        second=s.handle('p1',packet(0x43,14,15,'@Ready=/Ans=1'))[0].payload
        self.assertEqual(first,second)
        s.handle('p2',packet(0x43,14,15,'@Ready=/Ans=2'))
        self.assertEqual((s.prize_choice.winner,s.prize_choice.loser),(2,1))
        self.assertEqual([o.hand_index for o in s.prize_choice.options],[1,2])
        self.assertEqual(s.handle('p2',packet(0x43,15,2,'@GetSelect=/H=2')),[])
        final=s.final_prizes()
        self.assertEqual([(a.loser,a.original_slot) for a in final.settlement.transfers],[(1,1),(1,0)])
        self.assertEqual(len(s.confirm_prize_commit(1)),3)
        for seat in range(3):s.handle(f'p{seat}',packet(0x43,17,seat,'@Continue=/Ans=1/Conf=0'))
        self.assertEqual(s.phase,'selection');self.assertIs(s.final_prizes(),final)
        for seat in range(3):s.handle(f'p{seat}',select(s.final_cards(f'p{seat}').updated,seat))
        self.assertIsNone(s.final_prizes());self.assertIsNone(s.prize_choice)
        with self.assertRaises(MatchError):s.confirm_prize_commit(1)

    def test_cbc_is_separate_from_ordinary_award_snapshot(self):
        s,_=self.result_fixture([[0]*5,[0,1,1,1,1]],cbc=True)
        final=s.final_prizes()
        self.assertEqual(len(final.settlement.transfers),1)
        self.assertEqual([c.model for c in s.final_cards('p0').extras],[20])
        self.assertNotIn(20,[a.card.model for a in final.settlement.transfers])
        self.assertTrue(s.prize_commit_required)

    def test_opt_in_guards_abort_and_full_round_default_behavior(self):
        self.assertTrue(MatchService(card_prizes=True).card_prizes)
        with self.assertRaises(ValueError):MatchService(mode='versus',card_prizes=1)
        s,out=service('versus');self.assertFalse(s.card_prizes)
        self.assertIsNone(s.handle('alice',packet(0x43,15,0,'@GetSelect=/H=0')))
        self.assertIsNone(s.final_prizes())
        s=MatchService(mode='versus',card_prizes=True)
        s.register('alice',1);s.register('bob',2);s.begin()
        s.handle('alice',select());s.handle('bob',select(seat=1))
        out=finish_arrowless_round(s)
        self.assertFalse(any(decode(d.payload).subtype==13 for d in out))
        self.assertTrue(s.prize_commit_required)
        s.handle('alice',packet(0x43,5,11,'@Quit=/No=0'))
        self.assertEqual(s.phase,'finished')
        with self.assertRaises(MatchError):s.confirm_prize_commit(1)
        s.reset();self.assertIsNone(s.final_prizes())
        self.assertFalse(s.prize_commit_required)


class GeneratedBoardMatchTests(unittest.TestCase):
    def make(self,n,flags,seed,mode='versus',menu=False):
        s=MatchService(mode=mode,player_count=n,seed=seed,ai_cards=CARDS,computer_menu=menu)
        for seat in range(n if mode=='versus' else 1):s.register(f'p{seat}',seat+1)
        if menu:
            s.handle('p0',packet(0x41,0x12,body='@GameENC='))
            s.handle('p0',packet(1,body='@Req='))
            s.handle('p0',OriginalComputerTests.choose(rule='0|'+'|'.join(map(str,flags))+'|2|0|0|1'))
        else:
            settings=NATIVE_SETTINGS.replace('/st=0/cb=0/ca=0',f'/st={flags[0]}/cb={flags[1]}/ca={flags[2]}')
            s.handle('p0',packet(0x14,body=settings));s.begin()
        return s

    @staticmethod
    def selections(s,cards=CARDS):
        out=[]
        for seat,p in enumerate(s.players):
            if p.peer is not None:out+=s.handle(p.peer,select(cards,seat))
        return out

    def finish(self,s,rng):
        output=[]
        for _ in range(4000):
            if s.phase=='result':return output
            if s.pending_work:output+=s.advance();continue
            if s.phase=='battle':
                output+=s.handle(s.players[s.active].peer,packet(0x43,11,s.sequence,f'@BattleSelect=/B={rng.choice(s.targets)}'))
                continue
            self.assertEqual(s.phase,'turn')
            seq=s.sequence
            for seat,p in enumerate(s.players):
                if p.peer is not None:output+=s.handle(p.peer,ack(seat,seq))
            if s.phase=='turn' and s.players[s.active].peer is not None and s._unlocked():
                output+=s.handle(s.players[s.active].peer,put(s.active,s.sequence,0,rng.choice(s.legal_fields())))
        self.fail('generated board did not finish within bounded continuation work')

    def test_generated_StartData_and_first_TurnData_match_original_helper(self):
        from native_board_generation import generate_board
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from board_generation_vectors import BoardVM
        v=BoardVM()
        for n in (2,3):
            for flags in ((1,0,0),(0,1,0),(0,0,1),(1,1,1)):
                s=self.make(n,flags,37)
                state=s.rng.state
                self.assertEqual(state,37)  # Settings/initialization never draw.
                oracle=v.generate(n,state,flags)
                out=self.selections(s)
                start=next(decode(d.payload).sections['StartData'] for d in out if decode(d.payload).subtype==8)
                turn=next(decode(d.payload).sections['TurnData'] for d in out if decode(d.payload).subtype==9)
                self.assertEqual([int(x) for x in start['F'].split('|')],oracle['fields'])
                self.assertEqual((int(start['S']),int(start['B']),int(start['E'])),(oracle['active'],oracle['blocks'],oracle['effects']))
                self.assertEqual(int(turn['A']),oracle['active'])
                self.assertEqual([int(x) for x in turn['R'].split('|')],oracle['shared'])
                self.assertEqual(s.rng.state,oracle['rng_state'])
                self.assertGreaterEqual(len(s.legal_fields()),5*n)
                from incoming_gameplay_vectors import ReceiveVM
                initial_packets=[d.payload for d in out if d.peer=='p0' and decode(d.payload).subtype in (8,9)]
                for seat in range(n):
                    client=ReceiveVM(n=n,seat=seat)
                    for payload in initial_packets:
                        client.queue(payload[5:].decode())
                        self.assertEqual(client.receive(decode(payload).subtype),1)
                    self.assertEqual(client.r8(client.game+0x178),(n-seat+oracle['active'])%n)
                    self.assertEqual(list(client.u.mem_read(client.game+0x1a8,8)),oracle['shared'])

    def test_every_toggle_combination_completes_two_and_three_human_rounds(self):
        import itertools
        cards=tuple(Card(i,70,0,60,50,1,255) for i in range(5))
        seen=set();completed=0
        for n in (2,3):
            for flags in itertools.product((0,1),repeat=3):
                for seed in range(4):
                    s=self.make(n,flags,seed)
                    self.selections(s,cards);seen.update(s.start_board.fields)
                    initial=s.active
                    self.finish(s,random.Random(seed))
                    self.assertEqual((s.sequence,s.active,s.completed_matches),(5*n,initial,1))
                    self.assertEqual(sum(s._scores()),5*n)
                    for p in s.players:self.assertEqual(len(s.final_cards(p.peer).updated),5)
                    completed+=1
        self.assertEqual(completed,64)
        self.assertTrue({3,4,5,6,7,8,17}<=seen,seen)
        self.assertTrue(any(9<=f<=16 for f in seen))

    def test_computer_special_generation_and_rematch_refresh(self):
        for menu in (False,True):
            for seed in (1,21,42):
                s=self.make(2,(1,1,1),seed,mode='computer',menu=menu)
                self.selections(s)
                first=s.start_board.fields
                self.finish(s,random.Random(seed))
                final=s.final_cards('p0')
                s.handle('p0',packet(0x43,17,0,'@Continue=/Ans=1/Conf=0'))
                self.selections(s,final.updated)
                self.assertNotEqual(s.start_board.fields,first)
                self.finish(s,random.Random(seed+1))
                self.assertEqual(s.completed_matches,2)

    def test_empty_default_and_explicit_override_preserve_their_draw_schedule(self):
        for flags in ((0,0,0),):
            s=self.make(2,flags,1)
            expected=CRTLCG(1);shared=[expected()&255 for _ in range(8)]
            out=self.selections(s)
            self.assertEqual((s.start_board.fields,s.active),((0,)*16,0))
            turn=next(decode(d.payload).sections['TurnData'] for d in out if decode(d.payload).subtype==9)
            self.assertEqual([int(x) for x in turn['R'].split('|')],shared)
            self.assertEqual(s.rng.state,expected.state)
        s=MatchService(mode='versus',seed=1,start_fields=[3]+[0]*15)
        s.register('p0',1);s.register('p1',2);s.begin();self.selections(s)
        self.assertEqual(s.start_board.fields,(3,)+(0,)*15)
        self.assertEqual(s.active,0)


class ComputerPrizeTests(unittest.TestCase):
    def result_fixture(self,owners,*,menu=False,draws=None):
        s=MatchService(card_prizes=True,computer_menu=menu,ai_cards=CARDS,seed=42)
        s.register('alice',character_id=1,nickname_id=1)
        if menu:
            s.handle('alice',packet(0x41,0x12,body='@GameENC='))
            s.handle('alice',packet(1,body='@Req='))
            s.handle('alice',OriginalComputerTests.choose())
        else:s.begin()
        s._choose_cards(s.players[1],CARDS)
        s.handle('alice',select())
        slots=(4,1,3,0,2)
        for seat,hand in enumerate(owners):
            for index,owner in enumerate(hand):
                slot=slots[index]
                s.board[seat*5+index]=BoardCard(CARDS[slot],owner,seat,slot)
        if draws is not None:s.draw=draws
        s.sequence=9
        return s,s._end_turn()

    def test_computer_ambiguous_choice_commits_before_result_and_follows_TCList(self):
        owners=[[1,0,1,1,0],[1]*5]
        for menu in (False,True):
            draws=iter([255]*8+[123,1])
            s,out=self.result_fixture(owners,menu=menu,draws=lambda:next(draws))
            self.assertEqual(out,[])
            self.assertIsNone(s.prize_choice)
            self.assertTrue(s.prize_commit_required)
            final=s.final_prizes();award=final.settlement.transfers[0]
            self.assertEqual((award.winner,award.loser,award.hand_index,award.original_slot),(1,0,2,3))
            self.assertEqual(award.source_card,s.final_cards('alice').updated[3])
            result=s.confirm_prize_commit(1)
            self.assertEqual([decode(d.payload).subtype for d in result],[13])
            self.assertEqual(s.confirm_prize_commit(1),[])
            listed=s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))
            self.assertEqual([decode(d.payload).subtype for d in listed],[26,15])
            self.assertEqual(decode(listed[1].payload).argument,1)
            self.assertEqual(decode(listed[1].payload).sections,{'GetSelect':{'E':'0','H':'2'}})
            self.assertEqual(s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0')),[])
            self.assertEqual(s.handle('alice',packet(0x43,15,1,'@GetSelect=/H=0')),[])
            self.assertIs(s.final_prizes(),final)
            # Fully original39 consume followed by original0F wait. TCList
            # removes only itself from the queue; the NPC choice survives.
            sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
            from wager_transfer_vectors import WagerVM,seed,classify
            v=WagerVM();seed(v,[2,8],owners)
            v.w8(v.game+0x172,1)
            for d in listed:v.queue(d.payload[5:].decode())
            v.block(0x100c90a8,0x100cb49d,esi=v.game,edi=0)
            self.assertEqual((v.r8(v.game+0x16f),v.r32(0x102b31c4)),(0x3a,1))
            self.assertEqual(classify(v),(1,0,[0,2,3]))
            v.block(0x100ca46c,0x100ca675,esi=v.game,edi=0)
            self.assertEqual((v.r8(v.game+0x1b2),v.r32(0x102b31c4)),(2,0))

    def test_computer_sole_perfect_and_draw_prizes_need_no_choice_packets_or_draws(self):
        for owners,count in (([[1,0,0,0,0],[1]*5],1),([[1]*5,[1]*5],5),([[0]*5,[1]*5],0)):
            draws=iter([255]*8)
            s,out=self.result_fixture(owners,menu=True,draws=lambda:next(draws))
            self.assertEqual(out,[])
            self.assertEqual(len(s.final_prizes().settlement.transfers),count)
            s.confirm_prize_commit(1)
            out=s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))
            self.assertEqual([decode(d.payload).subtype for d in out],[26])

    def test_human_computer_win_uses_existing_human_choice_commit_barrier(self):
        draws=iter([255]*8)
        s,out=self.result_fixture([[0]*5,[0,1,0,0,1]],menu=True,draws=lambda:next(draws))
        self.assertEqual([decode(d.payload).subtype for d in out],[13])
        choice=s.prize_choice;self.assertEqual((choice.winner,choice.loser),(0,1))
        self.assertFalse(s.prize_commit_required)
        out=s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))
        self.assertEqual([decode(d.payload).subtype for d in out],[26])
        self.assertEqual(s.handle('alice',packet(0x43,15,0,'@GetSelect=/H=2')),[])
        final=s.final_prizes();award=final.settlement.transfers[0]
        self.assertEqual((award.winner,award.loser,award.original_slot),(0,1,3))
        self.assertEqual(award.source_card,s.players[1].updated[3])
        self.assertTrue(s.prize_commit_required)
        out=s.confirm_prize_commit(1)
        self.assertEqual([decode(d.payload).subtype for d in out],[15])
        self.assertIsNone(s.final_cards(None))

    def test_CPU_result_choice_does_not_leak_across_menu_or_rematch(self):
        s,_=self.result_fixture([[1,0,1,1,0],[1]*5],menu=True)
        s.confirm_prize_commit(1)
        s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))
        previous=s.final_prizes()
        s.handle('alice',packet(0x43,17,0,'@Continue=/Ans=1/Conf=1'))
        self.assertIs(s.final_prizes(),previous)
        s.handle('alice',OriginalComputerTests.choose(npc=2))
        self.assertIs(s.final_prizes(),previous)
        s._choose_cards(s.players[1],CARDS)
        s.handle('alice',select());self.assertIsNone(s.final_prizes())
        finish_arrowless_round(s)
        self.assertEqual(s.final_prizes().round_id,2)
        self.assertEqual(s.final_prizes().settlement.transfers,())
        s.confirm_prize_commit(2)
        out=s.handle('alice',packet(0x43,14,10,'@Ready=/Ans=0'))
        self.assertEqual([decode(d.payload).subtype for d in out],[26])


class WagerHookTests(unittest.TestCase):
    class Bank:
        def __init__(self,payouts=None):
            self.starts=[];self.quotes=[];self.payouts=payouts;self.fail=False

        def authorize(self,s,round_id,stake):
            if self.fail:raise MatchError('durable admission rejected')
            self.starts.append((round_id,stake,s.phase,tuple(p.balance for p in s.players if p.peer is not None)))
            return {i:p.balance-stake for i,p in enumerate(s.players) if p.peer is not None}

        def quote(self,s,round_id,scores,ratings):
            self.quotes.append((round_id,scores,ratings))
            return {i:(self.payouts or {}).get(i,s.round_stake) for i,p in enumerate(s.players) if p.peer is not None}

    def make(self,*,mode='versus',n=2,balance=10000,bank=None,computer_menu=False,**kw):
        bank=bank or self.Bank()
        s=MatchService(mode=mode,player_count=n,ai_cards=CARDS,computer_menu=computer_menu,
                       allow_wagers=True,round_authorizer=bank.authorize,payout_provider=bank.quote,**kw)
        for seat in range(n if mode=='versus' else 1):
            s.register(f'p{seat}',seat+1,nickname_id=seat+1,balance=balance)
        return s,bank

    @staticmethod
    def settings(s,stake=100,double=1):
        return s.handle('p0',packet(0x14,body=NATIVE_SETTINGS.replace('/bm=0',f'/bm={stake}').replace('/du=0',f'/du={double}')))

    @staticmethod
    def selections(s):
        for seat,p in enumerate(s.players):
            if p.peer is not None:s.handle(p.peer,select(CARDS,seat))

    @staticmethod
    def yes(s,seat=0,conf=0):
        return s.handle(f'p{seat}',packet(0x43,17,seat,f'@Continue=/Ans=1/Conf={conf}'))

    def test_defaults_and_incomplete_economic_configuration_remain_disabled(self):
        for args in ({'allow_wagers':1},{'allow_wagers':True},{'allow_wagers':True,'round_authorizer':lambda *x:{}},
                     {'round_authorizer':1},{'payout_provider':False}):
            with self.assertRaises(ValueError):MatchService(**args)
        s=MatchService();s.register('p0')
        with self.assertRaises(MatchError):self.settings(s)
        for balance in (-1,True,100000000):
            with self.assertRaises(MatchError):MatchService().register('p0',balance=balance)

    def test_latest_owner_settings_are_debited_once_before_VsInit(self):
        s,b=self.make()
        owner=s.handle('p0',packet(0x41,1,body='@GameEN='))
        self.assertEqual(decode(owner[0].payload).sections['GameEA']['EN'],'1')
        self.settings(s,100);self.settings(s,275)
        joiner=s.handle('p1',packet(0x41,1,body='@GameEN='))
        self.assertEqual(decode(joiner[0].payload).sections['GameEA']['EN'],'2')
        self.assertEqual(s.owner_peer,'p0')
        self.assertEqual(b.starts,[])
        before_rng=s.rng.state
        out=s.begin()
        self.assertEqual(b.starts,[(1,275,'lobby',(10000,10000))])
        self.assertEqual((s.authorized_round_id,s.round_stake,s.rng.state),(1,275,before_rng))
        for d in out:
            f=decode(d.payload).sections
            self.assertEqual(f['VsGameInit']['R'],'275|0|0|0|0|1|0')
            self.assertEqual((f['No0']['M'],f['No1']['M']),('9725','9725'))
        self.assertEqual(s.begin(),[])
        self.assertEqual(s.handle('p0',packet(0x41,8,body='@GameOK=')),[])
        self.assertEqual(len(b.starts),1)
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from incoming_gameplay_vectors import ReceiveVM
        for seat in (0,1):
            v=ReceiveVM(seat=seat);v.w32(0x10294bd4,seat+1)
            v.queue(out[seat].payload[5:].decode());self.assertEqual(v.receive(1),1)
            self.assertEqual(v.r32(v.player+0xc8),9725)
            self.assertEqual(v.r32(v.player+0xdc),275)

    def test_affordability_and_authorizer_failures_precede_round_transition(self):
        s,b=self.make(balance=99);self.settings(s,100)
        before=s.snapshot()
        with self.assertRaisesRegex(MatchError,'afford'):s.begin()
        self.assertEqual((s.snapshot(),b.starts),(before,[]))
        s.players[0].balance=s.players[1].balance=1000;b.fail=True
        before=s.snapshot()
        with self.assertRaisesRegex(MatchError,'durable'):s.begin()
        self.assertEqual((s.snapshot(),b.starts),(before,[]))
        b.fail=False;s.begin();self.assertEqual(len(b.starts),1)

    def test_callback_return_contract_rejects_foreign_missing_or_invalid_balances(self):
        for balances in ({0:900},{0:900,1:900,2:900},{0:-1,1:900},{0:True,1:900},[900,900]):
            s,_=self.make();self.settings(s)
            s.round_authorizer=lambda *args:balances
            before=s.snapshot()
            with self.assertRaisesRegex(MatchError,'every human'):s.begin()
            self.assertEqual(s.snapshot(),before)
            self.assertEqual([p.balance for p in s.players],[10000,10000])

    def test_double_up_requires_every_vote_and_uses_original_cap(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from wager_transfer_vectors import WagerVM
        v=WagerVM()
        for stake,double in ((0,1),(1,1),(999,1),(1000,1),(1001,1),(2000,1),(100,0)):
            s,b=self.make();self.settings(s,stake,double);s.begin();self.selections(s)
            finish_arrowless_round(s)
            final=s.final_cards('p0')
            v.w8(v.player+0xe4,double);v.w32(v.player+0xdc,stake)
            v.block(0x1010bc4b,0x1010bc77)
            expected=v.r32(v.player+0xdc)
            self.assertEqual(self.yes(s),[]);self.assertEqual(self.yes(s),[])
            self.assertEqual(len(b.starts),1)
            out=self.yes(s,1)
            self.assertEqual([decode(d.payload).argument for d in out],[1,1])
            self.assertEqual(b.starts[-1][:2],(2,expected))
            self.assertEqual(s.game_rules['bm'],expected)
            self.assertEqual([p.balance for p in s.players],[10000-expected]*2)
            self.assertIs(s.final_cards('p0'),final)
            self.assertEqual(self.yes(s,1),[]);self.assertEqual(len(b.starts),2)

    def test_decline_and_failed_rematch_never_charge_or_change_next_rules(self):
        s,b=self.make();self.settings(s,1000);s.begin();self.selections(s);finish_arrowless_round(s)
        self.yes(s)
        b.fail=True;before=s.snapshot();votes=dict(s.continues)
        with self.assertRaisesRegex(MatchError,'durable'):self.yes(s,1)
        self.assertEqual((s.snapshot(),s.continues),(before,votes))
        self.assertEqual(len(b.starts),1)
        out=s.handle('p1',packet(0x43,17,1,'@Continue=/Ans=2/Conf=0'))
        self.assertEqual(s.phase,'finished')
        self.assertEqual([decode(d.payload).argument for d in out],[2,2])
        self.assertEqual(len(b.starts),1)

    def test_three_player_middle_finish_credits_are_not_classified_as_tied_refunds(self):
        s,b=self.make(n=3,bank=self.Bank({0:500,1:150,2:0}))
        self.settings(s,100,0);s.begin();self.selections(s)
        for index in range(15):
            owner=0 if index<7 else 1 if index<12 else 2
            original,slot=divmod(index,5)
            s.board[index]=BoardCard(CARDS[slot],owner,original,slot)
        s.sequence=14;out=s._end_turn()
        self.assertEqual(b.quotes,[(1,(7,5,3),(300,300,300))])
        self.assertEqual([p.balance for p in s.players],[10400,10050,9900])
        for seat,d in enumerate(out):
            fields=decode(d.payload).sections[f'No{seat}']['D'].split('|')
            self.assertEqual(int(fields[1]),[500,150,0][seat])
            stats=s.final_cards(f'p{seat}').stats
            self.assertEqual((stats.reward_sum,stats.max_reward,stats.current_reward),tuple([500,150,0][seat] for _ in range(3)))
        self.assertEqual(s.players[1].result_stats.streak,0)
        final=s.final_cards('p1')
        for _ in range(3):s.handle('p1',packet(0x43,14,15,'@Ready=/Ans=1'))
        self.assertEqual(len(b.quotes),1);self.assertIs(s.final_cards('p1'),final)

    def test_invalid_payouts_reject_before_stats_advance(self):
        for payouts in ({0:1},{0:1,1:1,2:1},{0:-1,1:1},{0:True,1:1},{0:99999999,1:1},[0,0]):
            s,_=self.make();self.settings(s);s.begin();self.selections(s)
            s.sequence=9;s.payout_provider=lambda *args:payouts
            before=(s.snapshot(),tuple(p.result_stats for p in s.players),tuple(p.balance for p in s.players))
            with self.assertRaisesRegex(MatchError,'bounded credits'):s._end_turn()
            self.assertEqual((s.snapshot(),tuple(p.result_stats for p in s.players),tuple(p.balance for p in s.players)),before)
            self.assertIsNone(s.final_cards('p0'))

    def test_refund_D1_consumed_by_original_and_does_not_grow_reward_sum(self):
        s,b=self.make();self.settings(s);out=s.begin();self.selections(s)
        result=next(d.payload for d in finish_arrowless_round(s) if decode(d.payload).subtype==13)
        self.assertEqual([p.balance for p in s.players],[10000,10000])
        self.assertEqual(s.final_cards('p0').stats.current_reward,100)
        self.assertEqual(s.final_cards('p0').stats.reward_sum,0)
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from incoming_gameplay_vectors import ReceiveVM
        for seat in (0,1):
            v=ReceiveVM(seat=seat);v.w32(0x10294bd4,seat+1)
            v.queue(out[seat].payload[5:].decode());self.assertEqual(v.receive(1),1)
            v.queue(result[5:].decode());self.assertEqual(v.receive(13),1)
            self.assertEqual(v.r32(v.player+0xcc),100)
            self.assertEqual(v.r32(v.player+0xc8),9900)  # Animation applies D1 later.

    def test_computer_menu_debits_only_final_choice_and_Conf1_defers_next_debit(self):
        s,b=self.make(mode='computer',computer_menu=True)
        s.handle('p0',packet(0x41,0x12,body='@GameENC='))
        init=s.handle('p0',packet(1,body='@Req='))[-1]
        self.assertEqual(decode(init.payload).sections['ComGameInit']['M'],'10000')
        self.assertEqual(b.starts,[])
        s.handle('p0',OriginalComputerTests.choose(argument=0,rule='100|0|0|0|2|1|0'))
        self.assertEqual((s.game_rules['bm'],b.starts),(100,[]))
        bad=OriginalComputerTests.choose(npc=31,rule='200|0|0|0|2|1|0|1')
        with self.assertRaises(MatchError):s.handle('p0',bad)
        self.assertEqual(b.starts,[])
        b.fail=True;before=(s.snapshot(),s.rng.state)
        choose=OriginalComputerTests.choose(rule='200|0|0|0|2|1|0|1')
        with self.assertRaises(MatchError):s.handle('p0',choose)
        self.assertEqual((s.snapshot(),s.rng.state),before)
        b.fail=False;s.handle('p0',choose)
        self.assertEqual(b.starts,[(1,200,'computer_menu',(10000,))])
        with self.assertRaises(MatchError):s.handle('p0',choose)
        self.assertEqual(len(b.starts),1)
        # Remove arrows only in this pacing fixture; NPC generation is tested
        # separately against the original. Execute the public full-round path.
        s._choose_cards(s.players[1],CARDS);self.selections(s);finish_arrowless_round(s)
        out=self.yes(s,conf=1)
        self.assertEqual((s.phase,s.game_rules['bm'],len(b.starts)),('computer_menu',400,1))
        self.assertEqual(decode(out[0].payload).subtype,4)
        s.handle('p0',OriginalComputerTests.choose(npc=2,rule='75|0|0|0|2|0|0|1'))
        self.assertEqual(b.starts[-1][:2],(2,75))
        self.assertEqual(s.players[0].balance,9925)
        s._choose_cards(s.players[1],CARDS);self.selections(s);finish_arrowless_round(s)
        out=self.yes(s)
        self.assertEqual((s.phase,s.game_rules['bm'],len(b.starts)),('selection',75,3))
        self.assertEqual(b.starts[-1][:2],(3,75))
        self.assertEqual(decode(out[0].payload).argument,1)


class DepartureMatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))

    def start(self,n=2,rule=0,**kw):
        kw.setdefault('allow_departures',True)
        s=MatchService(mode='versus',player_count=n,seed=412,**kw)
        for i in range(n):s.register(str(i),i+1,name='Player'+str(i))
        s.game_rules['gs']=rule;s.begin()
        for i in range(n):s.handle(str(i),select(CARDS,i))
        return s

    def finish(self,s):
        out=[]
        for _ in range(150):
            if s.phase in ('result','finished'):return out
            if s.pending_work:out+=s.advance();continue
            seq=s.sequence
            for i,p in enumerate(s.players):
                if p.peer is not None and not p.departed:out+=s.handle(p.peer,ack(i,seq))
            if s.phase=='turn' and s._unlocked() and not s._controlled(s.players[s.active]):
                out+=s.handle(s.players[s.active].peer,put(s.active,s.sequence,0,s.legal_fields()[0]))
            elif s.phase=='battle' and not s.pending_work:
                out+=s.handle(s.players[s.active].peer,packet(0x43,11,s.sequence,f'@BattleSelect=/B={s.targets[0]}'))
        self.fail('departure round stalled')

    def test_substitute_retains_account_slots_and_completes_two_and_three_players(self):
        from disconnect_recovery_vectors import RecoveryVM
        for n in (2,3):
            s=self.start(n);before=s.players[0].selected
            out=s.depart('0',replacement=3)
            self.assertEqual(s.players[0].peer,'0');self.assertEqual(s.departure_seats,(0,))
            self.assertEqual([decode(x.payload).subtype for x in out],[0x23]*(n-1)+[0x24]*(n-1))
            v=RecoveryVM(n=n,seat=1,rule=0)
            for d in out:
                if d.peer=='1':v.queue(d.payload.decode()[5:])
            v.worker();local=(n-1)%n
            self.assertEqual(v.r8(v.player+0x19a+local*0x30),3)
            self.assertEqual(s.depart('0'),[])
            self.assertEqual(s.handle('0',put(0,0,0,0)),[])
            self.finish(s);self.assertEqual(s.phase,'result')
            self.assertEqual(s.sequence,5*n);self.assertEqual(sum(s._scores()),5*n)
            self.assertEqual(s.final_cards('0').original,before)
            self.assertEqual(len(s.final_cards('0').updated),5)
            reply=s.handle('1',packet(0x43,14,s.sequence,'@Ready=/Ans=1'))
            self.assertEqual(decode(reply[0].payload).sections['TCList']['H'],'|'.join(['5']*n))
            self.assertEqual(s.handle('1',packet(0x43,17,1,'@Continue=/Ans=1/Conf=0'))[0].payload[11:13],b'02')
            self.assertEqual(s.phase,'finished')

    def test_invalid_two_player_cancels_without_result_or_growth(self):
        s=self.start(rule=1);out=s.depart('1')
        self.assertEqual(s.phase,'finished');self.assertEqual(s.completed_matches,0)
        self.assertEqual([decode(d.payload).subtype for d in out],[0x23])
        self.assertIsNone(s.final_cards('0'))
        self.assertEqual(s.players[1].selected,CARDS)
        self.assertEqual(s.depart('1'),[])

    def test_invalid_three_player_skips_only_departed_seat_and_preserves_card_identity(self):
        s=self.start(3,1)
        for i in range(3):s.handle(str(i),ack(i,0))
        s.handle('0',put(0,0,0,0))
        s.depart('0')
        c=s.board[0];self.assertEqual((c.original_owner,c.original_slot,c.base_owner),(0,0,3))
        self.assertEqual(s._effect_cells()[0].base_owner,3)
        self.assertEqual(s._effect_cells()[0].identity,(0,0))
        self.finish(s);self.assertEqual(s.phase,'result')
        self.assertGreaterEqual(s.sequence,15)
        self.assertEqual(s._final_order[0],())
        self.assertEqual(len(s.final_cards('0').updated),5)
        self.assertEqual(s.final_cards('0').updated,CARDS)
        self.assertEqual(len(s._final_order[1]),5)

    def test_loss_active_departure_uses_ack_gated_no_placement_and_partial_tclist(self):
        from result_reward_vectors import VM
        s=self.start(rule=2)
        out=s.depart('0');self.assertEqual(s.phase,'turn')
        self.assertEqual([decode(d.payload).subtype for d in out],[0x23])
        out=s.handle('1',ack(1,0))
        self.assertEqual([decode(d.payload).subtype for d in out],[10,13])
        self.assertEqual(decode(out[0].payload).sections['PutCard']['F'],'255')
        self.assertEqual((s.phase,s.sequence),('result',1))
        self.assertEqual(s.result_classes(),(3,1))
        self.assertEqual(s._final_order,((),(0,1,2,3,4)))
        self.assertEqual(s.final_cards('0').updated,CARDS)
        ready=s.handle('1',packet(0x43,14,1,'@Ready=/Ans=1'))[0].payload
        self.assertEqual(decode(ready).sections['TCList']['H'],'0|5')
        v=VM(2,1);v.queue(ready.decode()[5:]);self.assertEqual(v.receive(26),1)
        self.assertEqual((v.r8(v.player+0x19f),v.r8(v.player+0x1cf)),(5,0))

    def test_loss_three_player_departure_order_and_hand_rebuild(self):
        s=self.start(3,2)
        for _ in range(4):
            seq=s.sequence
            for i in range(3):s.handle(str(i),ack(i,seq))
            s.handle(str(s.active),put(s.active,s.sequence,0,s.legal_fields()[0]))
        s.depart('2');self.assertEqual(s.phase,'turn')
        s.depart('1');self.finish(s)
        self.assertEqual(s.phase,'result')
        self.assertEqual(s.departure_seats,(2,1))
        self.assertEqual(s.result_classes(),(0,2,4))
        self.assertEqual(s._final_order[0],(2,3,4,0,1))
        self.assertEqual(s._final_order[1],(0,));self.assertEqual(s._final_order[2],(0,))
        self.assertEqual(len(s.final_cards('2').updated),5)
        self.assertEqual(s.final_cards('2').updated[1:],CARDS[1:])

    def test_unsupported_departures_and_bad_replacement_reject_before_mutation(self):
        for opts in ({'allow_departures':False},{'card_prizes':True},{'rating_provider':lambda *a:None},
                     {'allow_wagers':True,'round_authorizer':lambda s,n,b:{i:p.balance for i,p in enumerate(s.players)},
                      'payout_provider':lambda s,*a:{i:0 for i in range(s.player_count)}}):
            s=self.start(**opts);before=s.snapshot()
            self.assertFalse(s.departure_supported('0'))
            with self.assertRaises(MatchError):s.depart('0')
            self.assertEqual(s.snapshot(),before)
        s=self.start();before=s.snapshot()
        with self.assertRaises(ValueError):s.depart('0',replacement=0)
        self.assertEqual(s.snapshot(),before)

    def test_departure_during_target_choice_and_tie_keeps_bounded_combat_order(self):
        for rule in (0,2):
            s=self.start(rule=rule)
            source=Card(5,50,0,40,30,1,4|16)
            s.players[0].hand[0]=source
            s.players[0].selected=(source,)+s.players[0].selected[1:]
            s.players[0].updated[0]=source
            for index,arrows in ((6,64),(9,1)):
                p=s.players[1];slot=p.hand_slots.pop(0);p.hand.pop(0)
                c=Card(6+slot,50,0,40,30,1,arrows)
                p.updated[slot]=c
                s.board[index]=BoardCard(c,1,1,slot)
            for i in range(2):s.handle(str(i),ack(i,0))
            s.handle('0',put(0,0,0,5))
            self.assertEqual((s.phase,s.targets,s.pending_work),('battle',[6,9],False))
            out=s.depart('0')
            self.assertTrue(s.pending_work)
            self.assertEqual([decode(d.payload).subtype for d in out],[35,36] if rule==0 else [35])
            s.battle_values=lambda a,d:((50,10,1,1),(40,10,1,1))
            tie=s.advance(max_battles=1)
            self.assertEqual([decode(d.payload).subtype for d in tie],[12])
            self.assertEqual((s.sequence,s.phase),(0,'battle'))
            self.assertEqual([s.board[i].owner for i in (5,6,9)],[0,1,1])
            self.assertEqual(s.depart('0'),[])
            self.assertEqual(s.handle('0',packet(0x43,11,0,'@BattleSelect=/B=9')),[])
            s.battle_values=lambda a,d:((50,30,1,1),(40,5,4,1))
            resolved=[]
            while s.pending_work:resolved+=s.advance(max_battles=1)
            self.assertEqual([decode(d.payload).subtype for d in resolved],[12,12,9 if rule==0 else 13])
            self.assertTrue(all(d.peer=='1' for d in tie+resolved))
            self.assertEqual([s.board[i].owner for i in (5,6,9)],[0,0,0])
            if rule==0:self.assertEqual(s.phase,'turn')
            else:self.assertEqual((s.phase,len(s.final_cards('0').updated)),('result',5))

    def test_pass_rotates_spinner_without_consuming_unplaced_low_tile(self):
        fields=[0]*16;fields[5]=9;fields[6]=3
        s=self.start(rule=2,start_fields=fields)
        s.depart('0');s.handle('1',ack(1,0))
        cells=s._effect_cells()
        self.assertEqual((cells[5].direction,cells[5].arrows),(1,2))
        self.assertEqual((cells[6].model,cells[6].property),(0x8003,1))

    def test_voluntary_loss_quitter_receives_result_without_rejoining_turn_barrier(self):
        from wager_transfer_vectors import WagerVM
        for n in (2,3):
            for unlocked in (False,True):
                s=self.start(n,2)
                if unlocked:
                    for i in range(n):s.handle(str(i),ack(i,0))
                out=s.handle('0',packet(0x43,5,10,'@Quit=/No=0'))
                if n==3:out+=s.depart('2')
                out+=self.finish(s)
                mine=[d.payload for d in out if d.peer=='0']
                self.assertEqual([decode(d).subtype for d in mine],[5,13])
                self.assertNotIn('0',s._humans());self.assertNotIn('0',s.acks)
                self.assertEqual(s.handle('0',ack(0,s.sequence)),[])
                self.assertEqual(s.handle('0',packet(0x43,14,s.sequence,'@Ready=/Ans=0')),[])
                # Original gs2 menu waits13 before it can consume an earlier Quit5.
                v=WagerVM(n);c=0x20150000
                v.hooks[0x101951d0]=lambda sp:v.ret(sp)
                v.w8(c+0x150,8)
                for payload in mine:v.queue(payload.decode()[5:])
                v.block(0x1010f63e,0x1010f6c4,esi=c,ebx=0)
                self.assertEqual((v.r8(c+0x150),v.r32(0x102b31c4)),(9,1))
                self.assertEqual(v.receive(5),1)
                s.handle('1',packet(0x43,17,1,'@Continue=/Ans=2/Conf=0'))
                s.reset();self.assertEqual(s._departure_result_peers,set())

    def test_original_advanced_ready_header_is_accepted_and_old_turn_is_rejected(self):
        from wager_transfer_vectors import WagerVM
        for n in (2,3):
            s=self.start(n,1);self.finish(s)
            v=WagerVM(n);v.w8(v.game+0x18d,s.sequence)
            v.call(0x10106e30,[14,0,0])
            payload=('GTM0G'+v.sends[-1]['payload']).encode()
            self.assertEqual(decode(payload).argument,5*n)
            with self.assertRaises(MatchError):s.handle('0',packet(0x43,14,5*n-1,'@Ready=/Ans=0'))
            self.assertEqual(decode(s.handle('0',payload)[0].payload).subtype,26)

    def test_rating_quote_is_validated_before_terminal_stats_and_reflected_in_result(self):
        from local_rating_history import LocalRatingHistory
        history=LocalRatingHistory();history.enroll('0');history.enroll('1');calls=[]
        def quote(s,n,scores):
            calls.append((n,scores));return {i:history.quote(str(i),'round:'+str(n),scores,i) for i in range(2)}
        s=self.start(rating_provider=quote);self.finish(s)
        self.assertEqual(len(calls),1)
        self.assertEqual([p.rating for p in s.players],[200,200])
        self.assertEqual([s.final_cards(str(i)).stats.rating for i in range(2)],[200,200])
        s=self.start(rating_provider=lambda *a:{})
        s.sequence=9;before=s.snapshot();stats=[p.result_stats for p in s.players]
        with self.assertRaises(MatchError):s._end_turn()
        self.assertEqual(s.snapshot(),before);self.assertEqual([p.result_stats for p in s.players],stats)

    def test_maximum_supported_playack_and_oversized_settings_reject_atomically(self):
        s=MatchService(mode='versus',player_count=3)
        for i in range(3):s.register(str(i),i+1,name=b'Z'*15,nickname_id=(1<<64)-1-i,rating=300,points=65535)
        game=dict(s.game_rules,bm=2000,du=1,st=1,cb=1,ca=1,gs=2,tl=5)
        table=dict(s.table_rules,lu=99999,ll=99999,pa=1,pw='41'*8)
        data=s._check_playack(s.players,game,table)
        self.assertLessEqual(len(data)-13,447)
        for i in range(3):s.players[i].rating=(1<<32)-1
        before=s.snapshot()
        text=NATIVE_SETTINGS.replace('/pa=0','/pa=1/pw='+'41'*8)
        with self.assertRaises(MatchError):s.handle('0',packet(0x14,body=text))
        self.assertEqual(s.snapshot(),before)
        with self.assertRaises(MatchError):MatchService().register('bad',points=65536)


if __name__=='__main__':unittest.main()
