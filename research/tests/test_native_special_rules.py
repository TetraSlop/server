"""Original x86 board-effect comparisons; no game process or desktop control."""
from dataclasses import replace
from pathlib import Path
import random
import struct
import sys
import unittest
from native_rules import Card
from native_special_rules import (Cell,SpecialRulesError,start_board,place,classify,
    combo,direct_captures,ownership_beneficiary,modify_strength,adjacent_ownership,
    shuffle,spinner_chain,apply_special,finish_turn,special_placement_scores,
    poll_special_placement,choose_special_move)

BASE=Path(__file__).resolve().parents[1]
CARD=Card(5,20,0,30,40,1,129,3)


class SpecialAPITests(unittest.TestCase):
    def test_start_counts_placement_and_low_property_lifetime(self):
        fields=[0]*16
        for i,f in enumerate((1,3,4,5,6,9,17,18)):fields[i]=f
        initial=start_board(fields)
        self.assertEqual((initial.blocks,initial.effects),(5,3))
        placed=place(initial.cells,1,CARD,0,identity=('p',3))
        self.assertEqual((placed[1].model,placed[1].property,placed[1].identity),(5,1,('p',3)))
        self.assertEqual(initial.cells[1].model,0x8003)
        with self.assertRaises(SpecialRulesError):place(initial.cells,0,CARD,0)
        done=finish_turn(placed,1)
        self.assertEqual(done[1].property,0)
        self.assertEqual((done[5].direction,done[5].arrows),(1,2))
        for prop in (2,3,0x11):
            board=list(placed);board[1]=replace(board[1],property=prop)
            self.assertEqual(finish_turn(board,1)[1].property,prop)

    def test_CBC_is_explicit_and_generated_ownership_is_not_inventory(self):
        fields=[0]*16;fields[5]=18
        b=list(start_board(fields).cells);b[9]=Cell.ordinary(CARD,0,identity=7)
        with self.assertRaises(SpecialRulesError):apply_special(b,9,5,0,(0,)*8)
        result=apply_special(b,9,5,0,(0,)*8,replacement=CARD)
        c=result.cells[5]
        self.assertEqual((c.base_owner,c.owner,c.card,c.identity),(5,0,CARD,None))
        self.assertTrue(result.continue_combat)

    def test_input_boundaries(self):
        for fields in ([0]*15,[19]+[0]*15,[True]+[0]*15):
            with self.assertRaises(SpecialRulesError):start_board(fields)
        b=list(start_board([0]*16).cells);b[5]=Cell(model=0x8009,base_owner=4,owner=4,arrows=16,direction=4)
        for power in (-1,32768):
            with self.assertRaises(SpecialRulesError):spinner_chain(b,5,0,power)
        with self.assertRaises(SpecialRulesError):shuffle(b,(1,2))

    def test_special_AI_is_pure_and_includes_bare_low_tiles(self):
        fields=[0]*16;fields[5]=5;fields[6]=9
        b=list(start_board(fields).cells);b[1]=Cell.ordinary(replace(CARD,arrows=0),1)
        before=tuple(b)
        scores=dict(special_placement_scores(replace(CARD,arrows=0),b,0))
        self.assertIn(5,scores);self.assertNotIn(6,scores)
        self.assertEqual(scores[5],1)
        self.assertEqual(tuple(b),before)
        self.assertEqual(special_placement_scores(CARD,start_board([0]*25).cells,0),tuple((i,0) for i in range(25)))

    def test_special_AI_adapter_no_move_and_validation_boundaries(self):
        from native_ai import Personality
        from test_native_ai import Draws
        empty=start_board([0]*16).cells
        self.assertIsNone(choose_special_move((),empty,0,Draws([])))
        self.assertIsNone(choose_special_move((CARD,),start_board([1]*16).cells,0,Draws([])))
        with self.assertRaises(SpecialRulesError):choose_special_move((CARD,)*6,empty,0,Draws([]))
        for denominator in (0,-1,True,0x80000000):
            with self.assertRaises(SpecialRulesError):poll_special_placement(CARD,empty,0,Draws([]),denominator=denominator)
        for raw in (-1,True,32768):
            with self.assertRaises(SpecialRulesError):poll_special_placement(CARD,empty,0,Draws([raw]))
        move=choose_special_move((CARD,),empty,0,Draws([0,0,0,0,0,5]),Personality())
        self.assertEqual((move.hand_index,move.field),(0,5))


class OriginalSpecialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(BASE/'protocol_map'))
        try:
            from special_card_effect_vectors import Effects,G
            from combat_sequence_vectors import CombatVM
        except ImportError:
            raise unittest.SkipTest('original-code emulator dependencies unavailable')
        cls.Effects=Effects;cls.G=G;cls.CombatVM=CombatVM
        cls.machines={n:Effects(mode=n-2,n=n) for n in (2,3)}
        cls.count=0

    @classmethod
    def tearDownClass(cls):
        print('Original special-rule comparisons:',cls.count)

    def seed(self,board,active=0):
        n=2 if len(board)==16 else 3;m=self.machines[n];g=self.G
        m.u.mem_write(g,bytes(0x3000));m.byte(g+0x178,active)
        for i,c in enumerate(board):
            address=m.card(i,id=c.model,owner=c.owner,prop=c.property,mod=c.modifier,arrows=c.arrows)
            m.byte(address+0x1c,c.base_owner);m.byte(address+0x20,c.marker);m.byte(address+0x28,c.direction)
            if c.card is not None:
                d=c.card
                m.u.mem_write(address+8,bytes((d.attack,d.kind,d.physical,d.magical,d.points,c.arrows)))
                m.byte(address+0x16,d.deck)
        return m

    def compare(self,m,expected,*,markers=True):
        for i,c in enumerate(expected):
            actual=m.state(i)
            self.assertEqual((actual['id'],actual['base_owner'],actual['owner'],actual['property'],actual['modifier'],actual['arrows']),
                             (c.model,c.base_owner,c.owner,c.property,c.modifier,c.arrows),(i,actual,c))
            if markers:self.assertEqual(actual['target_class'],c.marker,(i,actual,c))
        type(self).count+=1

    def test_all_StartData_F_mappings_and_counters(self):
        for size in (16,25):
            for index in (5,size-1):
                for f in range(1,19):
                    fields=[0]*size;fields[index]=f
                    expected=start_board(fields)
                    m=self.seed(start_board([0]*size).cells);g=self.G
                    m.byte(g+0x179,index);m.byte(g+0x1f5+index,f)
                    m.byte(g+0x174,expected.blocks);m.byte(g+0x175,expected.effects)
                    m.block(0x100c1920,0x100c1ba3,eax=index,esi=g,ebp=0)
                    self.compare(m,expected.cells)
                    self.assertEqual((m.b(g+0x174),m.b(g+0x175)),(0,0))
                    if 9<=f<=16:self.assertEqual(m.b(g+0x344+index*0x9c+0x28),f-9)

    def test_classifier_full_property_marker_and_owner_domains(self):
        rng=random.Random(1976)
        for n in (2,3):
            size=(n+2)**2
            for _ in range(100):
                active=rng.randrange(n);source=rng.randrange(size);board=[]
                for i in range(size):
                    owner=rng.choice(tuple(range(n))+(4,8))
                    model=0xffff if owner==8 else 0x8009 if owner==4 else 5
                    board.append(Cell(model=model,owner=owner,base_owner=owner,arrows=rng.randrange(256),
                                      property=rng.choice((0,1,2,3,0x13)),marker=rng.randrange(3)))
                board[source]=Cell.ordinary(CARD,active,property=rng.choice((0,1,2,3)))
                m=self.seed(board,active);m.call(0x100d3760,source,this=self.G)
                self.compare(m,classify(board,source,active))

    def test_every_signed_modifier_wrap(self):
        for delta,start,end in ((-10,0x100d0b58,0x100d0bc4),(10,0x100d0ea2,0x100d0ef2)):
            for value in range(-128,128):
                b=list(start_board([0]*16).cells)
                b[2]=Cell.ordinary(CARD,0,modifier=value);b[3]=Cell.ordinary(CARD,1,modifier=value)
                m=self.seed(b);m.block(start,end)
                self.compare(m,modify_strength(b,0,delta))

    def test_all_shared_R1_beneficiary_thresholds(self):
        for n in (2,3):
            for active in range(n):
                for value in range(256):
                    b=list(start_board([0]*((n+2)**2)).cells)
                    b[5]=Cell(model=0x8008,base_owner=4,owner=4,property=0x30,arrows=255)
                    m=self.seed(b,active);m.byte(self.G+0x17a,5);m.byte(self.G+0x1a9,value)
                    m.block(0x100d07d2,0x100d3116)
                    self.assertEqual(m.state(5)['owner'],ownership_beneficiary(active,n,value))
                    type(self).count+=1

    def test_shuffle_original_order_exclusions_properties_and_identity(self):
        rng=random.Random(733)
        for n in (2,3):
            size=(n+2)**2
            for _ in range(35):
                b=list(start_board([0]*size).cells)
                b[5]=Cell(model=0x800a,base_owner=4,owner=4,property=0x40,arrows=255)
                b[6]=Cell(model=0x8003,base_owner=4,owner=8,property=1)
                slots=rng.sample([i for i in range(size) if i not in (5,6)],7)
                for j,index in enumerate(slots):
                    card=replace(CARD,model=20+j,arrows=rng.randrange(256))
                    b[index]=Cell.ordinary(card,j%n,identity=j,property=j%4,modifier=j*10-30)
                shared=tuple(rng.randrange(256) for _ in range(8))
                m=self.seed(b);m.u.mem_write(self.G+0x1a8,bytes(shared))
                m.block(0x100d1925,0x100d1af4,ecx=0x200a1000,esi=0)
                expected=shuffle(b,shared);self.compare(m,expected)
                self.assertEqual(sorted(c.identity for c in expected if c.card is not None),list(range(7)))

    def test_CBC_copy_and_exact_low1_cleanup(self):
        b=list(start_board([0]*16).cells);b[5]=Cell(model=0x800b,base_owner=4,owner=4,property=0x50,arrows=255)
        b[9]=Cell.ordinary(CARD,1)
        m=self.seed(b,1);g=self.G;m.byte(g+0x17a,5)
        compact=bytes((CARD.attack,CARD.kind,CARD.physical,CARD.magical,CARD.points,CARD.arrows))+struct.pack('<HI',CARD.model,0)+bytes((0,0,CARD.deck,0))
        m.u.mem_write(g+0x334,compact);m.block(0x100d220c,0x100d22ad,esi=0)
        self.compare(m,apply_special(b,9,5,1,(0,)*8,replacement=CARD).cells)
        for prop in (0,1,2,3,0x11,0x21,0x31):
            b=list(start_board([0]*16).cells);b[5]=Cell.ordinary(CARD,0,property=prop)
            m=self.seed(b);m.block(0x100d30ee,0x100d3104,ecx=5*39)
            self.compare(m,finish_turn(b,5))

    def test_adjacent_ownership_every_neighbor_and_combo_low3(self):
        rng=random.Random(891)
        for n in (2,3):
            size=(n+2)**2
            for case in range(60):
                active=case%n;source=rng.randrange(size)
                b=[Cell.ordinary(CARD,rng.randrange(n),marker=rng.randrange(3))
                   if rng.randrange(4) else Cell() for _ in range(size)]
                b[source]=Cell(model=0x8008,base_owner=4,owner=active,property=0x30,arrows=255)
                m=self.seed(b,active);m.byte(self.G+0x17a,source);m.byte(self.G+0x16c,1)
                for direction in range(8):
                    m.block(0x100d15fc,0x100d1786,esi=direction,ebx=128)
                self.compare(m,adjacent_ownership(b,source,active))
                b[source]=Cell.ordinary(CARD,active,property=3 if case%2 else 0)
                loser=(active+1)%n
                m=self.seed(b,active);m.call(0x100d3590,source,active,loser,this=self.G)
                self.compare(m,combo(b,source,active,loser))

    def test_spinner_chains_match_full_original_dispatcher(self):
        rng=random.Random(20260926)
        for n in (2,3):
            size=(n+2)**2
            for case in range(48):
                source,target=rng.sample(range(size),2);active=case%n
                b=[]
                for i in range(size):
                    typ=rng.randrange(5)
                    if typ==0:b.append(Cell())
                    elif typ==1:b.append(Cell(model=0x8001,base_owner=4,owner=4))
                    elif typ==2:
                        direction=rng.randrange(8)
                        b.append(Cell(model=0x8009,base_owner=4,owner=4,arrows=1<<direction,direction=direction))
                    else:b.append(Cell.ordinary(CARD,rng.randrange(n),marker=rng.randrange(3)))
                direction=rng.randrange(8);power=rng.choice((0,29,30,59,60,255))
                b[source]=Cell.ordinary(CARD,active)
                b[target]=Cell(model=0x8009,base_owner=4,owner=4,arrows=1<<direction,direction=direction,marker=3)
                v=self.CombatVM(n=n);g=v.game
                v.w8(g+0x178,active);v.w8(g+0x179,source)
                for i,c in enumerate(b):
                    v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,direction=c.direction,mark=c.marker)
                v.w16(g+0x344+source*0x9c+0x8e,power)
                for step in range(300):
                    state=v.step(0x1e if step==0 else None)
                    if state['combat']==2:break
                self.assertEqual(state['combat'],2)
                expected=spinner_chain(b,target,active,power)
                self.assertEqual([(c['owner'],c['mark']) for c in state['board']],
                                 [(c.owner,c.marker) for c in expected],(n,case))
                type(self).count+=1

    def test_special_AI_scorer_matches_original_all_preferences(self):
        from test_native_ai import OriginalAI
        v=OriginalAI();rng=random.Random(92718)
        comparisons=0
        for case in range(40):
            fields=[rng.choice((0,0,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17)) for _ in range(16)]
            board=list(start_board(fields).cells)
            for index in range(16):
                if rng.randrange(3)==0:
                    card=replace(CARD,model=rng.randrange(250),arrows=rng.randrange(256))
                    board[index]=Cell.ordinary(card,rng.randrange(2),property=rng.choice((0,1,2,3)))
            board[case%16]=Cell(model=0x8005,base_owner=4,owner=8,property=3)
            card=replace(CARD,arrows=(0,255,1,16,170)[case%5]);active=case%2
            v.seed((card,),(None,)*16,active);v.w8(v.game+0x17a,0)
            for i,c in enumerate(board):
                v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,prop=c.property,direction=c.direction)
            for preference in (0,1,2,3,4,5,255):
                for field,value in special_placement_scores(card,board,active,preference):
                    self.assertEqual(value,v.score(field,preference),(case,preference,field))
                    comparisons+=1
        self.assertGreater(comparisons,900)
        type(self).count+=comparisons

    def test_complete_special_AI_choices_and_consumed_draws_match_original(self):
        from test_native_ai import OriginalAI,Draws
        from native_ai import Personality,Move
        v=OriginalAI();rng=random.Random(6791)
        personalities=(Personality(),Personality(0,0,0),Personality(49,3,51),
                       Personality(100,1,100),Personality(100,2,100),Personality(100,4,100))
        for case in range(30):
            fields=[rng.randrange(18) if rng.randrange(2) else 0 for _ in range(16)]
            board=list(start_board(fields).cells)
            for index in range(16):
                if rng.randrange(3)==0:
                    board[index]=Cell.ordinary(replace(CARD,model=rng.randrange(250),arrows=rng.randrange(256)),
                        rng.randrange(2),property=rng.randrange(4))
            board[case%16]=Cell(model=0x8005,base_owner=4,owner=8,property=3)
            hand=tuple(replace(CARD,model=rng.randrange(250),arrows=rng.randrange(256)) for _ in range(case%5+1))
            owner=case%2
            for personality in personalities:
                draws=[rng.randrange(32768) for _ in range(6)]
                v.seed(hand,(None,)*16,owner,draws,1,personality)
                for i,c in enumerate(board):v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,prop=c.property,direction=c.direction)
                index=v.hand(owner);field=v.placement()
                stream=Draws(draws)
                self.assertEqual(choose_special_move(hand,board,owner,stream,personality),Move(index,field))
                self.assertEqual(stream.used,v.rng)
                type(self).count+=1
            for trigger in (0,1,2,59,60,61):
                draws=[trigger,case%100,(case*17)%100,case]
                personality=Personality(49,3,51)
                v.seed(hand,(None,)*16,owner,draws,60,personality)
                v.w8(v.game+0x17a,0)
                for i,c in enumerate(board):v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,prop=c.property,direction=c.direction)
                field=v.placement();stream=Draws(draws)
                self.assertEqual(poll_special_placement(hand[0],board,owner,stream,personality),field)
                self.assertEqual(stream.used,v.rng)
                type(self).count+=1

    def test_spinner_full_dispatcher_directions_range_chains_and_rotation(self):
        for n in (2,3):
            size=(n+2)**2
            for direction in range(8):
                for power in (0,29,30,59,60,255):
                    v=self.CombatVM(n=n);g=v.game
                    source=9 if n==2 else 11;target=5 if n==2 else 6
                    b=list(start_board([0]*size).cells)
                    for i in range(size):
                        if i not in (source,target):b[i]=Cell.ordinary(CARD,1)
                    b[source]=Cell.ordinary(CARD,0)
                    b[target]=Cell(model=0x8009,base_owner=4,owner=4,arrows=1<<direction,direction=direction,marker=3)
                    v.w8(g+0x179,source)
                    for i,c in enumerate(b):v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,direction=c.direction,mark=c.marker)
                    v.w16(g+0x344+source*0x9c+0x8e,power)
                    for step in range(100):
                        state=v.step(0x1e if step==0 else None)
                        if state['combat']==2:break
                    self.assertEqual(state['combat'],2)
                    expected=spinner_chain(b,target,0,power)
                    self.assertEqual([(c['owner'],c['mark']) for c in state['board']],[(c.owner,c.marker) for c in expected])
                    type(self).count+=1
        for direction in range(8):
            v=self.CombatVM();g=v.game
            v.card(5,id=0x8009,owner=4,arrows=1<<direction,direction=direction)
            v.block(0x100c7474,0x100c756d,esi=g)
            b=list(start_board([0]*16).cells);b[5]=Cell(model=0x8009,base_owner=4,owner=4,arrows=1<<direction,direction=direction)
            expected=finish_turn(b,None)[5];actual=v.view()['board'][5]
            self.assertEqual((actual['direction'],actual['arrows']),(expected.direction,expected.arrows))
            type(self).count+=1


if __name__=='__main__':unittest.main()
