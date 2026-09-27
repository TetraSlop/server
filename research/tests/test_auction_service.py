"""Auction wire/file policy and original x86 consumer compatibility."""
from dataclasses import replace
from pathlib import Path
import json
import struct
import sys
import unittest

from auction_economy import AuctionEconomy
from auction_service import (AuctionService, actor_record, actor_wire, descriptor,
                             listing_record, listing_reply, bid_record,
                             EXHIBIT_PATH, BID_PATH, METADATA_PATH, PENDING_PATHS)
from economy_service import EconomyService, EconomyError, named, parse_named, MAX_MONEY
from identities import game_id
from tm_protocol import Command, encode, parse


def fixture():
    e=EconomyService(seed=37)
    for i,key in enumerate(('seller','buyer','third'),1):
        e.provision(key,nn=game_id(i),player_id=i,name=key.encode())
    return e,AuctionEconomy(e)


def actor(e,key):
    a=e.account_view(key)
    return b'\x06'.join([f'0x{a.nn:016X}'.encode(),str(a.player_id).encode(),b'1',b'1',b'1',a.name,*([b'0']*6)])


def request(e,key,operation,*fields):
    return b'GTM0A'+encode([Command(operation,actor(e,key)),*fields])


def exhibit_request(e,key='seller',opening=100,hours=1,relists=0):
    c=e.inventory(key)[0];a=e.account_view(key)
    ii=(c.model,e.catalog.sale_value(c),a.rating,0,0,1,0,0,
        c.attack,c.physical,c.kind,c.magical,c.points,c.arrows,0,0)
    return request(e,key,b'ER',Command(b'SP',str(opening).encode()),Command(b'BI',b'20'),
                   Command(b'ET',str(hours).encode()),Command(b'AM',str(relists).encode()),
                   Command(b'II',b'\x06'.join(str(v).encode() for v in ii)))


def bid_request(e,key,auction_id,amount,revision):
    return request(e,key,b'BR',Command(b'AI',str(auction_id).encode()),
                   Command(b'BM',str(amount).encode()),Command(b'BC',str(revision).encode()))


def collection_request(e,key):
    a=e.account_view(key)
    return named(0xB0,1,0,f'@ShReq=/NN={a.nn:016X}/HID=1/Dm=1/Vol=1/CN={a.name.hex()}')


class AuctionServiceTests(unittest.TestCase):
    def setUp(self):
        self.e,self.a=fixture();self.now=1700000000
        self.s=AuctionService(self.a,clock=lambda:self.now)

    def exhibit(self,**kwargs):
        reply=self.s.handle('seller',exhibit_request(self.e,**kwargs))[0]
        self.assertEqual(parse(reply[5:])[0].token,b'ES')
        return self.a.listings()[-1],reply

    def query(self,kind=b'SI',key='seller',fields=()):
        reply=self.s.handle(key,request(self.e,key,kind,*fields))[0]
        commands=parse(reply[5:]);self.assertEqual(commands[0].token,b'SS' if kind==b'SI' else b'HS')
        path=commands[0].payload.decode();data=self.s.file(key,path)
        self.assertEqual(len(data),int(commands[1].payload)*(0xC0 if kind==b'SI' else 0x48))
        return path,data,reply

    def sold(self):
        item,_=self.exhibit()
        self.s.handle('buyer',bid_request(self.e,'buyer',item.id,100,0))
        self.now=item.expires;self.a.advance(self.now)
        return self.a.listing(item.id)

    def enter(self,key):
        replies=self.s.handle(key,collection_request(self.e,key))
        session=int(parse_named(replies[0])[3]['Init']['N'])
        return session,replies

    def get(self,key,session):
        return self.s.handle(key,named(0xB2,0x15,session,f'@Get=/No={session}'))

    def test_exhibit_bid_and_readonly_search_match_files(self):
        item,reply=self.exhibit();self.assertEqual(len(self.e.inventory('seller')),9)
        self.assertEqual(dict((c.token,c.payload) for c in parse(reply[5:]))[b'BC'],b'0')
        path,data,_=self.query();self.assertEqual(data,listing_record(item))
        before=self.e.snapshot();self.query(key='buyer');self.assertEqual(self.e.snapshot(),before)
        reply=self.s.handle('buyer',bid_request(self.e,'buyer',item.id,120,0))[0]
        self.assertEqual(parse(reply[5:])[0].token,b'BS')
        self.assertEqual(self.e.balance('buyer'),9880)
        self.assertEqual(self.s.file('seller',path),data)
        _,current,_=self.query();self.assertNotEqual(current,data)
        self.assertEqual(struct.unpack_from('<3I',current,0x90),(120,self.now,1))
        _,history,_=self.query(b'BH',fields=(Command(b'AI',b'1'),))
        self.assertEqual(history,bid_record(self.a.history(1)[0]))

    def test_original_search_filters_and_repeated_card_selectors(self):
        item,_=self.exhibit(opening=1200)
        fields=(Command(b'IO',b'seller'),Command(b'AI',b'1'),Command(b'MP',b'1001\x062000'),
                Command(b'LT',b'1\x062'),Command(b'IC',f'0\x06{item.card.model}\x06{item.card.model}'.encode()),
                Command(b'IC',f'10\x06{item.card.kind}\x06{item.card.kind}'.encode()))
        self.assertEqual(len(self.query(fields=fields)[1]),0xC0)
        self.assertEqual(self.query(fields=(Command(b'IO',b'buyer'),))[1],b'')
        self.s.handle('buyer',bid_request(self.e,'buyer',1,1220,0))
        self.assertEqual(len(self.query(fields=(Command(b'IB',b'buyer'),))[1]),0xC0)
        self.assertEqual(len(self.query(b'BH',fields=(Command(b'IB',b'buyer'),))[1]),0x48)
        for fields in ((Command(b'IC',b'11\x060\x061'),),(Command(b'MP',b'2\x061'),),
                       (Command(b'AI',b'1'),Command(b'AI',b'2')),(Command(b'BD',b'1'),)):
            with self.subTest(fields=fields),self.assertRaises(EconomyError):
                self.s.handle('seller',request(self.e,'seller',b'SI',*fields))

    def test_actor_authentication_and_width_failures_are_atomic(self):
        before=self.e.snapshot();ledger=self.a.snapshot()
        raw=exhibit_request(self.e)
        malformed=[raw.replace(actor(self.e,'seller'),actor(self.e,'buyer')),
                   raw.replace(b'\x06seller\x06',b'\x06wrong\x06'),
                   raw.replace(b'<ET>\x071\x07',b'<ET>\x07256\x07'),
                   raw.replace(b'<SP>',b'<MP>')]
        for payload in malformed:
            with self.subTest(payload=payload),self.assertRaises(EconomyError):
                self.s.handle('seller',payload)
            self.assertEqual(self.e.snapshot(),before);self.assertEqual(self.a.snapshot(),ledger)

    def test_mapped_failures_do_not_debit_or_remove_again(self):
        item,_=self.exhibit();before=self.e.snapshot()
        cases=[(99,0,b'-8611'),(100,1,b'-680'),(MAX_MONEY,0,b'-8610')]
        for amount,revision,error in cases:
            reply=self.s.handle('buyer',bid_request(self.e,'buyer',item.id,amount,revision))[0]
            self.assertEqual(parse(reply[5:]),[Command(b'BF',error)])
            self.assertEqual(self.e.snapshot(),before)
        self.assertEqual(parse(self.s.handle('buyer',bid_request(self.e,'buyer',999,100,0))[0][5:]),
                         [Command(b'BF',b'-8620')])

    def test_metadata_is_twenty_bytes_and_empty_query_still_has_file(self):
        self.assertEqual(self.s.file('seller',METADATA_PATH),bytes(20))
        self.assertEqual(self.query()[1],b'')
        for price in (1000,1001,2000,2001,5001):
            self.exhibit(opening=price)
        self.assertEqual(struct.unpack('<5I',self.s.file('seller',METADATA_PATH)),(5,1,2,1,1))

    def test_snapshot_capacity_and_account_scoped_cleanup(self):
        self.s=AuctionService(self.a,clock=lambda:self.now,max_snapshots=2)
        first,old,_=self.query();second,_,_=self.query(b'BH')
        other,_,_=self.query(key='buyer')
        failure=self.s.handle('seller',request(self.e,'seller',b'SI'))[0]
        self.assertEqual(parse(failure[5:]),[Command(b'SF',b'-8621')])
        self.assertEqual(self.s.file('seller',first),old)
        self.assertIsNone(self.s.file('buyer',first))
        self.assertTrue(self.s.delete('seller',EXHIBIT_PATH))
        self.assertIsNone(self.s.file('seller',first));self.assertIsNotNone(self.s.file('seller',second))
        self.assertIsNotNone(self.s.file('buyer',other))
        self.assertTrue(self.s.delete('seller',second));self.assertTrue(self.s.delete('seller',second))
        self.assertFalse(self.s.delete('seller',PENDING_PATHS[0]))

    def test_collection_all_stages_even_empty_and_terminal_quit_is_proactive(self):
        item=self.sold();before=self.e.balance('seller')
        for path in PENDING_PATHS:self.assertEqual(self.s.file('seller',path),b'\0')
        session,first=self.enter('seller')
        self.assertEqual([parse_named(x)[1] for x in first],[0x13,0x20,0x21])
        self.assertEqual(parse_named(first[1])[3],{'Data':{'M':'100'}})
        self.assertEqual(parse_named(first[2])[3],{'Card':{'E':'0','S':'0','C':'0'}})
        self.assertEqual(self.e.balance('seller'),before+100)
        self.assertTrue(self.e.is_reserved('seller'))
        self.assertEqual(parse_named(self.get('seller',session)[0])[3],{'Card':{'E':'0','S':'0','C':'0'}})
        final=self.get('seller',session)
        self.assertEqual([parse_named(x)[1] for x in final],[0x20,0x17])
        self.assertEqual(parse_named(final[1])[3],{'Quit':{'N':str(session),'D':'0','B':'0'}})
        self.assertFalse(self.s.collection_active('seller'));self.assertFalse(self.e.is_reserved('seller'))
        for path in PENDING_PATHS:self.assertIsNone(self.s.file('seller',path))
        session,first=self.enter('buyer');self.assertEqual(len(self.e.inventory('buyer')),10)
        won=self.get('buyer',session)
        self.assertEqual(parse_named(won[0])[3]['N0']['D'],'|'.join(map(str,item.card.reduced())))
        self.assertEqual(len(self.e.inventory('buyer')),10)
        self.get('buyer',session);self.assertEqual(len(self.e.inventory('buyer')),11)

    def test_disconnect_preserves_unaccepted_items_but_never_recredits_payment(self):
        self.sold();session,_=self.enter('seller');balance=self.e.balance('seller')
        self.s.disconnect('seller');self.assertFalse(self.e.is_reserved('seller'))
        later,replies=self.enter('seller');self.assertNotEqual(session,later)
        self.assertEqual(parse_named(replies[1])[3],{'Data':{'M':'0'}})
        self.assertEqual(self.e.balance('seller'),balance)
        self.s.disconnect('seller')
        session,_=self.enter('buyer');self.get('buyer',session);self.s.disconnect('buyer')
        self.assertEqual(len(self.a.pending('buyer')),1);self.assertEqual(len(self.e.inventory('buyer')),10)
        session,_=self.enter('buyer');self.get('buyer',session);self.get('buyer',session)
        self.assertEqual(len(self.e.inventory('buyer')),11)

    def test_stale_or_invalid_collection_control_cannot_commit(self):
        self.sold();session,_=self.enter('buyer');before=self.e.snapshot()
        for payload in (named(0xB2,0x15,session+1,f'@Get=/No={session+1}'),
                        named(0xB2,0x15,session,'@Get=/No=99'),
                        named(0xB2,0x17,session,f'@Quit=/No={session}'),collection_request(self.e,'buyer')):
            with self.subTest(payload=payload),self.assertRaises(EconomyError):self.s.handle('buyer',payload)
            self.assertEqual(self.e.snapshot(),before)
        self.assertTrue(self.s.collection_active('buyer'))
        self.assertIsNone(self.s.handle('seller',named(0xB0,0,0,'@ShReq=')))

    def test_collection_overflow_erase_deck_edits_commit_only_on_get(self):
        item=self.sold();base=self.e.inventory('buyer')[0]
        self.e.provision('full',nn=game_id(4),player_id=4,name=b'full',cards=[base]*1000)
        # A trusted ledger sale creates one pending card for the full inventory.
        extra,_=self.exhibit();self.a.bid('full',extra.id,100,0,now=self.now)
        self.now=extra.expires;self.a.advance(self.now)
        session,_=self.enter('full');self.get('full',session)
        with self.assertRaises(EconomyError):self.get('full',session)
        self.assertEqual(len(self.e.inventory('full')),1000)
        values='|'.join(map(str,base.reduced()))
        self.s.handle('full',named(0xB2,0x10,session,f'@Erase=/Mode=1/C=1@0=/D={values}'))
        self.s.handle('full',named(0xB2,0x22,session,f'@Decks=/C=1@0=/D={values}|0@DN1=/S=4E6577'))
        self.assertEqual(dict(self.e.account_view('full').deck_names)[1],b'Deck One')
        self.get('full',session)
        self.assertEqual(len(self.e.inventory('full')),1000)
        self.assertEqual(dict(self.e.account_view('full').deck_names)[1],b'New')
        self.assertEqual(sum(c.deck==0 for c in self.e.inventory('full')),1)

    def test_queries_are_available_while_collection_owns_account(self):
        self.sold();session,_=self.enter('seller');before=self.e.snapshot()
        self.query();self.query(b'BH');self.assertEqual(self.e.snapshot(),before)
        self.assertTrue(self.e.is_reserved('seller'));self.get('seller',session);self.get('seller',session)

    def test_pages_fit_native_budget_and_restart_conserves_pending(self):
        card=self.e.inventory('seller')[0]
        pages=AuctionService._card_messages(255,[card]*30)
        self.assertGreater(len(pages),1)
        start=0
        for page in pages:
            self.assertLessEqual(len(page),460)
            head=parse_named(page)[3]['Card'];self.assertEqual(int(head['S']),start)
            start+=int(head['C']);self.assertEqual(int(head['E']),30)
        self.assertEqual(start,30)
        self.sold();old=self.a.snapshot()
        restored=EconomyService.restore(json.loads(json.dumps(self.e.snapshot())),catalog=self.e.catalog)
        ledger=AuctionEconomy.restore(restored,json.loads(json.dumps(old)))
        service=AuctionService(ledger,clock=lambda:self.now)
        self.assertEqual(service.file('buyer',PENDING_PATHS[0]),b'\0')
        self.assertEqual(service.file('seller',METADATA_PATH),self.s.file('seller',METADATA_PATH))


class NativeAuctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        try:
            from auction_rank_vectors import Machine,OUT,BUF
            from profile_contract_vectors import ProfileVM
            from auction_collection_vectors import VM
        except ImportError as exc:
            raise unittest.SkipTest('original-code emulator unavailable: '+str(exc))
        cls.Machine,cls.ProfileVM,cls.VM,cls.OUT,cls.BUF=Machine,ProfileVM,VM,OUT,BUF

    def setUp(self):
        self.e,self.a=fixture();self.s=AuctionService(self.a,clock=lambda:1700000000)

    def test_original_actor_and_ii_serializers_match_service_record_projection(self):
        item=self.a.exhibit('seller',self.e.inventory('seller')[0],100,20,1,0,now=1700000000)
        m=self.ProfileVM();source=0x20090000;out=0x20091000
        m.u.mem_write(source,actor_record(item.seller))
        m.call(0x101A5720,out,source,0x33)
        self.assertEqual(m.c(out),encode([Command(b'EI',actor_wire(item.seller))]))
        m.u.mem_write(source,struct.pack('<4I4H8B',*descriptor(item)))
        m.call(0x101A58D0,out,source,0x30)
        self.assertEqual(m.c(out),encode([Command(b'II',b'\x06'.join(str(x).encode() for x in descriptor(item)))]))

    def test_original_rich_ack_parsers_equal_binary_search_records(self):
        er=self.s.handle('seller',exhibit_request(self.e))[0]
        for payload,fn,kind,state,destination in [(er,0x101A4C30,20,0x10323CC8,0x10323CD8),
                (self.s.handle('buyer',bid_request(self.e,'buyer',1,120,0))[0],0x101A4D00,22,0x10323D98,0x10323DA8)]:
            with self.subTest(kind=kind):
                m=self.Machine();m.w32(state,1)
                # The shared Machine seeds decimal-only CRT classification;
                # actor identity parsing also uses alphabet bits and toupper.
                m.u.mem_write(0x20360000,b''.join(struct.pack('<H',4 if 48<=i<=57 else 1 if 65<=i<=90 else 2 if 97<=i<=122 else 0) for i in range(256)))
                m.hooks[0x101E43D8]=lambda sp:m.ret(sp,ord(chr(m.r32(sp+4)).upper()))
                m.callback(fn,kind,payload[5:].decode('ascii'))
                self.assertEqual(m.r32(state),2)
                decoded=bytes(m.u.mem_read(destination,0xC0))
                original=self.a.listing(1)
                if kind==20:original=replace(original,bidder=None,amount=0,bid_at=0,revision=0)
                self.assertEqual(decoded,listing_record(original))

    def test_original_search_callbacks_poll_and_read_generated_file_slices(self):
        self.s.handle('seller',exhibit_request(self.e))
        self.s.handle('buyer',bid_request(self.e,'buyer',1,120,0))
        for op,fn,kind,state,pathaddr,countaddr,poll,read,stride in [
            (b'SI',0x101A4DD0,24,0x10323E68,0x10323E78,0x10323E74,0x101A45A0,0x101A46A0,0xC0),
            (b'BH',0x101A4EB0,26,0x10323EBC,0x10323ECC,0x10323EC8,0x101A4970,0x101A4A70,0x48)]:
            m=self.Machine();m.w32(state,1);m.w32(state+8,self.OUT)
            reply=self.s.handle('seller',request(self.e,'seller',op))[0]
            m.callback(fn,kind,reply[5:].decode());self.assertEqual(m.r32(state),2)
            self.assertEqual(m.call(poll),1);self.assertEqual(m.r32(self.OUT),1)
            m.call(read,[self.OUT+0x100,1,0]);capture=m.files[-1]
            data=self.s.file('seller',m.text(pathaddr))
            self.assertEqual((capture['size'],capture['offset'],len(data)),(stride,0,stride))

    def test_original_collection_entry_init_payment_and_empty_cards_consume_service_messages(self):
        replies=self.s.handle('seller',collection_request(self.e,'seller'))
        v=self.VM();session=int(parse_named(replies[0])[3]['Init']['N'])
        # Entry learns source identity while leaving Init for the next consumer.
        for payload in replies:v.queue(payload[5:].decode())
        self.assertEqual(v.call(0x10088D60,this=1),1)
        self.assertEqual(v.r32(0x102B31C4),3)
        v.w32(v.obj+0x17C,1);v.call(0x101333D0,this=v.obj)
        self.assertEqual(v.r32(v.obj+0x17C),2);self.assertEqual(v.r8(v.player+0x30),session)
        self.assertEqual(v.r32(v.player+0x28),0x12345678)
        # Whole original empty child flows produce exactly one Get for cards.
        v.w32(v.obj+0x148,0)
        for _ in range(3):v.call(0x101348F0,this=v.obj)
        self.assertEqual(v.r32(0x102EF340)&1,1)
        v.w32(v.obj+0x148,0)
        for _ in range(3):v.call(0x10134F00,this=v.obj)
        self.assertEqual(v.r32(0x102EF340)&2,2)
        self.assertEqual(len(v.sends),1)

    def test_original_init_header_one_is_failure_independent_of_body_session(self):
        for header,expected in ((0,1),(1,-1),(2,1),(17,1),(255,1)):
            with self.subTest(header=header):
                v=self.VM()
                v.queue(named(0xB2,0x13,header,'@Init=/N=93/RA=0/M=0/CP=0/S=0/SP=0/SN=/CMP=0/HD=0')[5:].decode())
                self.assertEqual(v.receive(0x13),expected)
                self.assertEqual(v.r8(v.player+0x30),93)

    def test_original_collection_multi_page_card_decoder_and_terminal(self):
        card=self.e.inventory('seller')[0];pages=self.s._card_messages(17,[card]*30)
        v=self.VM()
        for page in pages:v.queue(page[5:].decode())
        # Run just the original returned child until it has accepted all pages;
        # nonempty presentation is deliberately not simulated as acknowledgement.
        for _ in range(len(pages)+1):v.call(0x10134F00,this=v.obj)
        self.assertEqual(v.r16(v.player+0xF0),30)
        self.assertEqual(v.r16(v.player+0x4B96),card.model)
        self.assertEqual(list(v.u.mem_read(v.player+0x4B90,6)),
                         [card.attack,card.kind,card.physical,card.magical,card.points,card.arrows])
        self.assertEqual(len(v.sends),0)
        v.w32(v.obj+0x17C,12)
        v.queue(named(0xB2,0x17,17,'@Quit=/N=17/D=0/B=0')[5:].decode())
        v.call(0x101333D0,this=v.obj)
        self.assertEqual(v.r32(v.obj+0x17C),13)


if __name__=='__main__':unittest.main()
