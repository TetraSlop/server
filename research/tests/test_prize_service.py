"""Prize points, award replay, card ownership and original-client flow checks."""
from dataclasses import replace
import json
from pathlib import Path
import struct
import sys
import unittest

from economy_service import Card, Catalog, EconomyError, EconomyService, PROFILE_SIZE, named, parse_named
from prize_service import PrizeService, PrizeError


class PrizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.catalog=Catalog()

    def setUp(self):
        self.e=EconomyService(seed=123,catalog=self.catalog)
        template=bytearray(PROFILE_SIZE);struct.pack_into('<I',template,0x134,1234)
        for key,nn in [('one',1),('two',2)]:
            self.e.provision(key,nn=nn,player_id=nn,name=key.encode(),balance=5678,profile_template=bytes(template))
        self.p=PrizeService(self.e,seed=9)
        self.session={}

    def entry(self,key='one'):
        view=self.e.account_view(key)
        payload=named(0x70,0,0,f'@CvReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN={view.name.hex()}')
        replies=self.p.handle(key,payload)
        self.session[key]=int(parse_named(replies[1])[3]['ExcInit']['N'])
        return replies

    def send(self,subtype,body,key='one',session=None):
        return self.p.handle(key,named(0xB2,subtype,self.session[key] if session is None else session,body))

    def get(self,key='one'):
        return self.send(0x15,f'@Get=/No={self.session[key]}',key)

    def quit(self,key='one'):
        return self.send(0x17,f'@Quit=/No={self.session[key]}',key)

    def state(self):return {'economy':self.e.snapshot(),'prize':self.p.snapshot()}

    def restart(self):
        data=json.loads(json.dumps(self.state()))
        self.e=EconomyService.restore(data['economy'],catalog=self.catalog)
        self.p=PrizeService.restore(self.e,data['prize'],seed=9)

    @staticmethod
    def card(reply):
        fields=parse_named(reply)[3]
        return Card(*map(int,fields['N0']['D'].split('|')))

    @staticmethod
    def edits(token,cards,mode=None):
        return '@'+token+'='+(f'/Mode={mode}' if mode is not None else '')+f'/C={len(cards)}'+''.join(
            f'@{i}=/D='+'|'.join(map(str,c.reduced()+((c.deck,) if token=='Decks' else ()))) for i,c in enumerate(cards))

    def test_zero_awards_default_and_identity_binding(self):
        before=self.state()
        with self.assertRaises(PrizeError):self.p.handle('one',named(0x70,0,0,'@CvReq=/NN=0000000000000002/HID=0/Dm=1/Vol=1/CN=6F6E65'))
        self.assertEqual(self.state(),before)
        replies=self.entry();fields=parse_named(replies[1])[3]['ExcInit']
        self.assertEqual(parse_named(replies[0])[3],{'CvEnter':{'EN':'1'}})
        self.assertEqual(fields,{'N':'1','PP':'1234|0|0|0','LC':'65535|65535|65535'})
        self.assertEqual(parse_named(replies[1])[2],0)
        self.assertEqual(self.e.prize_points('one'),1234)
        self.assertEqual(self.e.balance('one'),5678)

    def test_awards_redeem_before_init_once_across_retry_disconnect_and_restart(self):
        self.assertTrue(self.p.grant_award('one','week.1',top30=12,lucky=23,rookies=34,lucky_cards=(11,22,33)))
        self.assertFalse(self.p.grant_award('one','week.1',top30=999))
        self.restart();replies=self.entry()
        self.assertEqual(parse_named(replies[1])[3]['ExcInit']['PP'],'1234|12|23|34')
        self.assertEqual(self.e.prize_points('one'),1303)
        self.assertEqual(self.entry(),replies)
        self.assertEqual(self.e.prize_points('one'),1303)
        self.p.disconnect('one');self.restart()
        self.assertEqual(parse_named(self.entry()[1])[3]['ExcInit']['PP'],'1303|0|0|0')
        self.assertFalse(self.p.grant_award('one','week.1',top30=12))
        self.assertEqual(self.e.prize_points('two'),1234)

    def test_award_cap_and_pending_bundle_validation(self):
        self.p.grant_award('one','large',top30=65535,lucky=65535,rookies=65535)
        with self.assertRaises(PrizeError):self.p.grant_award('one','other',top30=1)
        self.entry();self.assertEqual(self.e.prize_points('one'),99999)
        for kwargs in ({'top30':65536},{'lucky':-1},{'rookies':True},{'lucky_cards':(1,2)}, {'lucky_cards':(1,2,250)}):
            with self.subTest(kwargs=kwargs),self.assertRaises(PrizeError):self.p.grant_award('two','bad',**kwargs)
        with self.assertRaises(PrizeError):self.p.grant_award('two','with spaces',top30=1)

    def test_award_queued_during_active_visit_waits_for_next_visit(self):
        first=self.entry();self.p.grant_award('one','later',lucky=20)
        self.assertEqual(self.entry(),first)
        self.assertEqual(self.e.prize_points('one'),1234)
        self.quit();self.entry();self.assertEqual(self.e.prize_points('one'),1254)

    def test_purchase_stages_points_and_card_until_get(self):
        self.entry();before=self.state();cards=self.e.inventory('one')
        reply=self.send(0x14,'@Buy=/No=45')[0];card=self.card(reply)
        self.assertEqual(card.model,198)
        self.assertEqual(self.e.prize_points('one'),1234)
        self.assertEqual(self.e.inventory('one'),cards)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.send(0x14,'@Buy=/No=45'),[reply])
        self.assertEqual(parse_named(self.send(0x14,'@Buy=/No=46')[0])[3],{'Card':{'S':'0'}})
        with self.assertRaises(PrizeError):self.quit()
        self.get();self.assertEqual(self.e.prize_points('one'),1184)
        self.assertEqual(self.e.inventory('one'),cards+(card,))
        self.assertEqual(self.e.balance('one'),5678)
        self.get();self.assertEqual(self.e.prize_points('one'),1184)
        self.assertEqual(parse_named(self.entry()[1])[3]['ExcInit']['PP'],'1184|0|0|0')
        reply=self.quit()[0];self.assertEqual(parse_named(reply)[3],{'Quit':{'N':'1','D':'0','B':'0'}})
        self.assertEqual(self.quit(),[reply])

    def test_unaccepted_purchase_rolls_back_and_accepted_purchase_survives_reload(self):
        self.entry();self.send(0x14,'@Buy=/No=45');self.p.disconnect('one');self.restart()
        self.assertEqual((self.e.prize_points('one'),len(self.e.inventory('one'))),(1234,10))
        self.entry();reply=self.send(0x14,'@Buy=/No=45')[0];self.get();self.restart()
        self.assertEqual((self.e.prize_points('one'),len(self.e.inventory('one'))),(1184,11))
        self.assertEqual(self.e.inventory('one')[-1],self.card(reply))
        self.entry();self.assertEqual(self.session['one'],3)

    def test_invalid_and_unaffordable_packs_return_nonpositive_card(self):
        self.entry();before=self.state()
        for pack in (0,44,70,74,75,255):
            self.assertEqual(parse_named(self.send(0x14,f'@Buy=/No={pack}')[0])[3],{'Card':{'S':'0'}})
        self.assertEqual(self.state(),before)

    def test_all_thirty_prizes_use_original_catalogue_model_and_price(self):
        self.p.grant_award('one','catalogue',top30=65535)
        self.entry();points=self.e.prize_points('one')
        for pack_id in range(45,75):
            reply=self.send(0x14,f'@Buy=/No={pack_id}')[0]
            card=self.card(reply);pack=self.catalog.packs[pack_id]
            self.assertEqual(card.model,pack['explicit_models'][0]['model_id'])
            self.catalog.validate(card)
            self.get();points-=pack['price']
            self.assertEqual(self.e.prize_points('one'),points)
            self.assertEqual(self.e.inventory('one')[-1],card)
        self.assertEqual(self.e.balance('one'),5678)

    def test_stale_session_bad_body_and_forged_edits_cannot_commit(self):
        self.entry();self.send(0x14,'@Buy=/No=45');before=self.state()
        with self.assertRaises(PrizeError):self.send(0x15,'@Get=/No=2',session=2)
        with self.assertRaises(PrizeError):self.send(0x15,'@Get=/No=2')
        with self.assertRaises(PrizeError):self.entry()
        with self.assertRaises(PrizeError):self.send(0x16,'@Sell=/C=0')
        c=self.e.inventory('one')[0]
        with self.assertRaises(PrizeError):self.send(0x10,self.edits('Erase',[replace(c,arrows=c.arrows^1)],1))
        self.assertEqual(self.state(),before)
        self.get();self.assertEqual(len(self.e.inventory('one')),11)

    def test_overflow_requires_discard_before_point_and_card_commit(self):
        c=self.e.inventory('one')[0];template=self.e.profile_bytes('one')
        self.e.provision('full',nn=3,player_id=3,name=b'full',cards=[c]*1000,profile_template=template)
        self.entry('full');reply=self.send(0x14,'@Buy=/No=45','full')[0]
        with self.assertRaises(EconomyError):self.get('full')
        self.assertEqual((self.e.prize_points('full'),len(self.e.inventory('full'))),(1234,1000))
        self.send(0x10,self.edits('Erase',[c],1),'full')
        self.send(0x10,'@Erase=/Mode=2','full')
        self.get('full')
        self.assertEqual((self.e.prize_points('full'),len(self.e.inventory('full'))),(1184,1000))
        self.assertEqual(self.e.inventory('full')[-1],self.card(reply))

    def test_discard_purchased_card_still_charges_points_and_disconnect_discards_edits(self):
        self.entry();reply=self.send(0x14,'@Buy=/No=45')[0]
        self.send(0x10,self.edits('Erase',[self.card(reply)],1));self.get()
        self.assertEqual((self.e.prize_points('one'),len(self.e.inventory('one'))),(1184,10))
        self.send(0x14,'@Buy=/No=46');c=self.e.inventory('one')[0]
        self.send(0x10,self.edits('Erase',[c],1));self.p.disconnect('one')
        self.assertEqual((self.e.prize_points('one'),len(self.e.inventory('one'))),(1184,10))

    def test_decks_names_options_and_late_deck_edit_preserve_currency(self):
        self.entry();reply=self.send(0x14,'@Buy=/No=45')[0];card=replace(self.card(reply),deck=3)
        self.send(0x22,self.edits('Decks',[card])+'@DN1=/S='+b'Prize deck'.hex())
        self.e.save_options('one',dict(Ar=1,Cu=0,Vi=0,Se=3,Bgm=3,Per=1,Ran=0,CMD=1,CAD=1,CL=8,CT=60,HNSS=1))
        self.get()
        self.assertEqual(self.e.inventory('one')[-1],card)
        self.assertEqual(dict(self.e.account_view('one').deck_names)[1],b'Prize deck')
        self.assertEqual(self.e.profile_bytes('one')[0x126],1)
        self.send(0x22,self.edits('Decks',[replace(card,deck=4)]))
        self.assertEqual(self.e.inventory('one')[-1].deck,4)
        self.assertEqual((self.e.balance('one'),self.e.prize_points('one')),(5678,1184))

    def test_busy_accounts_exclude_shop_auction_match_and_trade(self):
        self.entry()
        for call in (lambda:self.e.reserve_auction('one'),lambda:self.e.reserve_match('one'),
                     lambda:self.e.reserve_exchange(('one','two')),
                     lambda:self.e.handle('one',named(0xB0,0,0,'@ShReq='))):
            with self.assertRaises(EconomyError):call()
        self.assertFalse(self.e.validate_selection('one',self.e.inventory('one')[:5]))
        self.quit();self.assertFalse(self.e.is_reserved('one'))
        token=self.e.reserve_match('one')
        with self.assertRaises(EconomyError):self.entry()
        self.e.release_match(token)

    def test_public_prize_boundary_validates_points_inventory_and_capabilities(self):
        t=self.e.reserve_prize('one');before=self.e.snapshot();c=t.account.cards[0]
        for kw in ({'debit':1235},{'credit':200000},{'debit':1,'credit':1},
                   {'cards':t.account.cards+(c,)},{'cards':t.account.cards[1:]}):
            with self.subTest(kw=kw),self.assertRaises(EconomyError):self.e.commit_prize(t,**kw)
            self.assertEqual(self.e.snapshot(),before)
        new=self.e.commit_prize(t,debit=50)
        with self.assertRaises(EconomyError):self.e.commit_prize(t,debit=50)
        self.assertFalse(self.e.release_prize(t));self.assertTrue(self.e.release_prize(new))
        self.assertEqual(self.e.balance('one'),5678)

    def test_snapshot_validation_legacy_and_sessions_wrap(self):
        self.p.grant_award('one','first',lucky=10);snapshot=self.p.snapshot()
        for mutate in (lambda x:x['accounts']['one'].update(last_session=256),
                       lambda x:x['accounts']['one'].update(known_awards=[]),
                       lambda x:x['accounts']['one']['pending'].update(top30=65536)):
            bad=json.loads(json.dumps(snapshot));mutate(bad)
            with self.assertRaises(PrizeError):PrizeService.restore(self.e,bad)
        snapshot['accounts']['one']['last_session']=255
        self.p=PrizeService.restore(self.e,snapshot);self.entry()
        self.assertEqual(self.session['one'],1)
        self.assertEqual(PrizeService.restore(self.e).snapshot(),{'version':1,'accounts':{}})

    def test_original_init_status_awards_purchase_and_get_agree(self):
        pm=Path(__file__).resolve().parents[1]/'protocol_map'
        if str(pm) not in sys.path:sys.path.insert(0,str(pm))
        from auction_rank_vectors import Machine,SCENE,BUF
        self.p.grant_award('one','original',top30=12,lucky=23,rookies=34,lucky_cards=(11,22,33))
        init=self.entry()[1]
        for header,want in ((0,1),(1,0xFFFFFFFF),(2,1),(255,1)):
            m=Machine();payload=init[5:11]+f'{header:02X}'.encode()+init[13:]
            m.queue(payload.decode());self.assertEqual(m.receive(0x13)&0xFFFFFFFF,want)
            self.assertEqual(m.r8(m.player+0x30),1)
        m=Machine();m.queue(init[5:].decode());self.assertEqual(m.receive(0x13),1)
        m.u.mem_write(BUF,(pm/'decoded_PackPrm.BIN').read_bytes()[16:]);m.w32(0x102b6360,BUF)
        for kind in (0,1):
            m.w32(SCENE+0x148,kind);m.block(0x10178ec1,0x10178f41,esi=SCENE,ebx=0)
        self.assertEqual(m.r32(m.player+0xb4),self.e.prize_points('one'))
        reply=self.send(0x14,'@Buy=/No=45')[0]
        m.w32(SCENE+0x148,0);m.w32(SCENE+0x14c,10);m.queue(reply[5:].decode())
        m.block(0x101792ca,0x101792f6,esi=SCENE)
        self.assertEqual(m.r32(SCENE+0x14c),20)
        m.w32(m.player+0xc8,5678);m.block(0x10179338,0x1017936b,esi=SCENE)
        m.u.mem_write(m.player+0xec,struct.pack('<H',10));m.block(0x1017961d,0x10179666,esi=SCENE)
        self.assertEqual(m.sends[-1]['body'],'@Get=/No=1')
        self.get()
        self.assertEqual(m.r32(m.player+0xb4),self.e.prize_points('one'))
        self.assertEqual(m.r32(m.player+0xc8),self.e.balance('one'))
        self.assertEqual(m.r16(m.player+0xec),len(self.e.inventory('one')))
        self.assertEqual(m.r16(m.player+0xb30+10*16+6),self.e.inventory('one')[-1].model)

    def test_original_cost_debits_for_every_catalogue_prize(self):
        pm=Path(__file__).resolve().parents[1]/'protocol_map'
        if str(pm) not in sys.path:sys.path.insert(0,str(pm))
        from prize_center_vectors import points_deduction
        for index in range(30):
            result=points_deduction(index,10000)
            self.assertEqual(result['cost'],self.catalog.packs[45+index]['price'])
            self.assertEqual(result['balance_after'],10000-self.catalog.packs[45+index]['price'])
            self.assertEqual(result['money_unchanged'],5678)


if __name__=='__main__':unittest.main()
