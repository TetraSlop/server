"""Exact arithmetic comparisons with original x86; no native process or I/O."""
from dataclasses import replace
from itertools import product
from pathlib import Path
import json
import random
import struct
import sys
import unittest

from native_rules import (BattleSide, Card, CRTLCG, GrowthBaseline, RulesError,
                          calculate_battle, grow_card)

BASE=Path(__file__).resolve().parents[1]


class RulesAPITests(unittest.TestCase):
    def test_rng_is_explicit_restorable_and_uint32(self):
        rng=CRTLCG(1)
        self.assertEqual([rng() for _ in range(5)],[41,18467,6334,26500,19169])
        state=rng.state;expected=[rng() for _ in range(20)]
        rng.state=state
        self.assertEqual([rng() for _ in range(20)],expected)
        self.assertEqual(CRTLCG(-1).state,0xffffffff)
        self.assertEqual(CRTLCG(0x100000001).state,1)

    def test_ties_are_preserved_not_returned_as_defender_wins(self):
        c=Card(0,10,0,10,10,1,0)
        result=calculate_battle(c,c,iter([8,8,8,8]).__next__)
        self.assertTrue(result.tied)
        self.assertEqual(result.comparison,0)
        self.assertEqual(result.calculator_return,2)
        self.assertEqual(result.raw_draws,(8,8,8,8))
        self.assertEqual(result.attacker.wire(5),(5,10,8,1,1))

    def test_zero_fallback_occurs_once_even_if_still_tied(self):
        c=Card(0,0,0,0,0,0,0)
        result=calculate_battle(c,c,iter([0,0,0,0,2,4]).__next__)
        self.assertEqual(result.attacker.start,1)
        self.assertTrue(result.tied)
        self.assertEqual(result.raw_draws,(0,0,0,0,2,4))

    def test_stat_selector_ties_and_full_property_bytes(self):
        c=Card(0,50,3,50,50,1,0)
        result=calculate_battle(c,c,iter([3,2,1,2,3,4]).__next__,
                                attacker_property=0x51,defender_property=0x32)
        self.assertEqual((result.attacker.selector,result.defender.selector),(8,8))
        self.assertEqual((result.attacker.multiplier,result.defender.multiplier),(5,4))
        self.assertEqual(len(result.raw_draws),6)

    def test_growth_normalizes_bytes_and_recomputes_even_without_increment(self):
        c=Card(7,100,3,80,60,0,129,4);b=GrowthBaseline(100,80,60,20,100)
        result=grow_card(c,b,257,256)
        self.assertEqual(result.card,replace(c,points=20))
        self.assertIsNone(result.changed_field)
        self.assertEqual(result.sale_value,112)
        self.assertEqual(c.points,0)  # Immutable input.
        self.assertEqual(grow_card(replace(c,kind=0),b,256,259).card.kind,2)

    def test_growth_preserves_over_cap_stats_arrows_and_deck(self):
        c=Card(0,255,1,255,255,1,255,49);b=GrowthBaseline(10,20,30,255,17500)
        for r in range(3):
            result=grow_card(c,b,1,r)
            self.assertEqual((result.card.attack,result.card.physical,result.card.magical),(255,255,255))
            self.assertEqual((result.card.arrows,result.card.deck),(255,49))
            self.assertIsNone(result.changed_field)
            self.assertEqual(result.card.points,(765*255//60)&255)

    def test_invalid_domains_are_explicit(self):
        c=Card(0,1,0,1,1,1,0)
        with self.assertRaises(RulesError):calculate_battle(c,c,lambda:0,attacker_modifier=128)
        with self.assertRaises(RulesError):calculate_battle(c,c,lambda:-1)
        with self.assertRaises(RulesError):calculate_battle(c,c,lambda:0,defender_property=256)
        with self.assertRaises(RulesError):GrowthBaseline(0,1,1,1,10)
        with self.assertRaises(RulesError):Card(65535,1,0,1,1,1,0)
        with self.assertRaises(RulesError):CRTLCG(True)

    def test_existing_card_adapters(self):
        from match_service import Card as MatchCard
        from economy_service import Card as EconomyCard
        values=(5,20,1,30,40,2,129,4)
        m,e=MatchCard(*values),EconomyCard(*values)
        b=GrowthBaseline(100,80,60,20,100)
        expected=grow_card(m,b,1,0)
        self.assertEqual(grow_card(e,b,1,0),expected)
        self.assertEqual(MatchCard(*expected.card.full()).attack,21)
        self.assertEqual(EconomyCard(*expected.card.full()).deck,4)
        self.assertEqual(calculate_battle(m,e,iter([1,2,3,4]).__next__).attacker.selector,1)


class OriginalRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(BASE/'protocol_map'))
        try:
            from result_reward_vectors import VM
        except ImportError:
            raise unittest.SkipTest('existing original-code emulator dependencies unavailable')
        cls.vm=VM()
        cls.catalog=json.loads((BASE/'protocol_map/economy_catalog.json').read_text(encoding='utf-8'))
        cls.counts={'battle':0,'growth':0,'rand':0}

    @classmethod
    def tearDownClass(cls):
        print('Original-code comparisons: '+json.dumps(cls.counts,sort_keys=True))

    def original_battle(self,a,d,draws,am=0,dm=0,ap=0,dp=0):
        v=self.vm;ca,cd=0x20130000,0x20130100
        for address,c,modifier,prop in ((ca,a,am,ap),(cd,d,dm,dp)):
            v.u.mem_write(address,bytes(0x9c))
            v.u.mem_write(address+8,bytes((c.attack,c.kind,c.physical,c.magical,c.points,c.arrows)))
            v.w16(address+14,c.model);v.w8(address+0x26,modifier);v.w8(address+0x24,prop)
        consumed=[]
        def raw(sp):
            value=draws[len(consumed)];consumed.append(value);v.ret(sp,value)
        v.hooks[0x101e2e44]=raw
        ret=v.call(0x100d31d0,[ca,cd])
        sides=tuple(BattleSide(v.r16(c+0x8e),v.r16(c+0x8c),v.r8(c+0x29),v.r8(c+0x2a)) for c in (ca,cd))
        actual=calculate_battle(a,d,iter(draws).__next__,attacker_modifier=am,defender_modifier=dm,
                                attacker_property=ap,defender_property=dp)
        expected=(sides,tuple(consumed),ret)
        got=((actual.attacker,actual.defender),actual.raw_draws,actual.calculator_return)
        self.assertEqual(got,expected,(a,d,am,dm,ap,dp,draws))
        self.assertEqual(actual.comparison,(sides[0].final>sides[1].final)-(sides[0].final<sides[1].final))
        self.counts['battle']+=1
        return actual

    def original_growth(self,card,mode,r,precision=64):
        v=self.vm;c=0x20130000
        v.u.mem_write(c,bytes(0x9c))
        v.u.mem_write(c+8,bytes((card.attack,card.kind,card.physical,card.magical,card.points,card.arrows)))
        v.w16(c+14,card.model);v.w8(c+0x16,card.deck)
        b=GrowthBaseline.from_catalog(self.catalog['cards'][card.model],self.catalog['price_tiers'])
        v.call(0x100b3630,[mode,r,v.game],this=c)
        after=Card(v.r16(c+14),v.r8(c+8),v.r8(c+9),v.r8(c+10),v.r8(c+11),v.r8(c+12),v.r8(c+13),v.r8(c+0x16))
        result=grow_card(card,b,mode,r,x87_precision=precision)
        self.assertEqual((result.card,result.sale_value),(after,v.r32(c+0x10)),(card,b,mode,r))
        changes=[name for name in ('attack','kind','physical','magical') if getattr(card,name)!=getattr(after,name)]
        self.assertEqual(result.changed_field,changes[0] if changes else None)
        self.counts['growth']+=1

    def test_original_CRT_sequences(self):
        v=self.vm;tls=0x20160000
        v.hooks.pop(0x101e2e44,None)
        v.hooks[0x101e6161]=lambda sp:v.ret(sp,tls)
        for seed in (0,1,0x7fffffff,0x80000000,0xffffffff):
            v.w32(tls+0x14,seed);rng=CRTLCG(seed)
            for _ in range(256):
                expected=v.call(0x101e2e44)
                self.assertEqual(rng(),expected)
                self.assertEqual(rng.state,v.r32(tls+0x14))
                self.counts['rand']+=1

    def test_original_battle_exhaustive_stat_order_and_extremes(self):
        # Every comparison/tie ordering among three raw stats, including0/255.
        stats=tuple(product((0,7,255),repeat=3))
        for kind in range(4):
            for a,d in product(stats,repeat=2):
                self.original_battle(Card(0,a[0],kind,a[1],a[2],0,0),
                                     Card(0,d[0],0,d[1],d[2],0,0),
                                     [32767,65535,65536,2147483647,1,0])

    def test_original_battle_multipliers_and_zero_fallback(self):
        for kind,ar,dr in product(range(4),range(4),range(4)):
            a=Card(0,255,kind,254,253,0,0);d=Card(0,252,0,251,250,0,0)
            self.original_battle(a,d,[ar,dr,32767,32766,32765,32764,1,0],127,-128,0x31,0xa2)
        for kind,ap,dp,aend,dend in product(range(4),(0,1),(0,2),range(2),range(2)):
            c=Card(0,0,kind,0,0,0,0)
            draws=([3] if ap else [])+([2] if dp else [])+[0,0,0,0,aend,dend]
            result=self.original_battle(c,c,draws,-128,-128,ap,dp)
            self.assertEqual((result.attacker.final,result.defender.final),(aend,dend))

    def test_original_battle_randomized_full_valid_domain(self):
        rng=random.Random(20260926)
        for _ in range(1200):
            a=Card(0,rng.randrange(256),rng.randrange(4),rng.randrange(256),rng.randrange(256),0,0)
            d=Card(0,rng.randrange(256),rng.randrange(4),rng.randrange(256),rng.randrange(256),0,0)
            self.original_battle(a,d,[rng.randrange(32768) for _ in range(8)],
                                 rng.randrange(-128,128),rng.randrange(-128,128),rng.randrange(256),rng.randrange(256))

    def test_original_growth_exhaustive_random_bytes_and_types(self):
        for kind,mode,r in product(range(4),range(2),range(256)):
            self.original_growth(Card(5,10,kind,10,10,250,129,7),mode,r)

    def test_original_growth_every_catalogue_model_and_caps(self):
        for model,row in enumerate(self.catalog['cards']):
            base=(row['attack'],row['physical_defense'],row['magical_defense'])
            for stats,r in product(((0,0,0),tuple(x//2 for x in base),base,tuple(min(255,x+1) for x in base)),range(3)):
                self.original_growth(Card(model,stats[0],row['type'],stats[1],stats[2],255,255,49),1,r)

    def test_original_growth_randomized_and_argument_narrowing(self):
        rng=random.Random(731992)
        for _ in range(600):
            c=Card(rng.randrange(250),rng.randrange(256),rng.randrange(4),rng.randrange(256),rng.randrange(256),
                   rng.randrange(256),rng.randrange(256),rng.randrange(256))
            self.original_growth(c,rng.choice((0,1,2,255,256,257,-1)),rng.randrange(-0x80000000,0x100000000))

    def test_original_growth_with_53_bit_x87_precision(self):
        from unicorn.x86_const import UC_X86_REG_FPCW
        rng=random.Random(532026)
        self.vm.u.reg_write(UC_X86_REG_FPCW,0x27f)
        try:
            for _ in range(600):
                c=Card(rng.randrange(250),rng.randrange(256),rng.randrange(4),rng.randrange(256),rng.randrange(256),0,0)
                self.original_growth(c,rng.randrange(2),rng.randrange(256),precision=53)
        finally:
            self.vm.u.reg_write(UC_X86_REG_FPCW,0x37f)


if __name__=='__main__':unittest.main()
