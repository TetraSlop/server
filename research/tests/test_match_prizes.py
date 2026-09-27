"""Prize ledger invariants and original-x86 result/compaction comparisons."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'protocol_map'))
from match_service import Card
from match_prizes import (PrizeCard, PrizeError, PrizeLedger, PrizeChoice, PrizeOption,
                         poll_computer_prize, choose_computer_prize)
from wager_transfer_vectors import WagerVM, transfer_vectors


def hands(owners, slots=(0,1,2,3,4)):
    return [[PrizeCard(slots[j],Card(i*5+j,10,0,10,10,1,0,j),owner)
             for j,owner in enumerate(hand)] for i,hand in enumerate(owners)]


class PrizeTests(unittest.TestCase):
    def test_original_computer_prize_rate_gate_candidate_order_and_draws(self):
        from computer_prize_vectors import choice_vectors
        rows=choice_vectors();self.assertEqual(len(rows),419)
        for row in rows:
            if 'candidates' not in row:continue
            options=tuple(PrizeOption(i,4-i,Card(i,10,0,10,10,1,0)) for i in row['candidates'])
            choice=PrizeChoice(1,row['winner'],1-row['winner'],options)
            values=list(row['input_draws']);used=[]
            def draw():
                value=values.pop(0);used.append(value);return value
            before=repr(choice)
            self.assertEqual(poll_computer_prize(choice,draw,row['denominator']),row['selected'])
            self.assertEqual(used,row['draws']);self.assertEqual(repr(choice),before)
        ledger=PrizeLedger((7,3),hands([[0]*5,[0,1,0,1,1]]))
        choice=ledger.pending;values=iter((32767,1))
        self.assertEqual(choose_computer_prize(choice,lambda:next(values)),2)
        for denominator in (0,-1,True,0x80000000):
            with self.assertRaises(PrizeError):poll_computer_prize(choice,lambda:0,denominator)
        for value in (-1,32768,True):
            with self.assertRaises(PrizeError):choose_computer_prize(choice,lambda:value)

    def test_original_complete_order_compaction_all_seats(self):
        cases=transfer_vectors()
        self.assertEqual(len(cases),500)
        slots=(4,1,3,0,2)
        for row in cases:
            n,seat=row['n'],row['seat']
            original=hands(row['owners'],slots)
            rotated=[None]*n;scores=[0]*n
            for local in range(n):
                wire=(local+seat)%n
                rotated[wire]=tuple(PrizeCard(c.original_slot,c.card,(c.current_owner+seat)%n) for c in original[local])
                scores[wire]=row['scores'][local]
            ledger=PrizeLedger(scores,rotated)
            while not ledger.complete:
                p=ledger.pending;expected=row['awards'][len(ledger.transfers)]
                self.assertEqual((p.winner,p.loser),((expected[0]+seat)%n,(expected[1]+seat)%n))
                self.assertEqual([o.hand_index for o in p.options],expected[2])
                ledger.select(p.winner,expected[3],choice_id=p.choice_id)
            result=ledger.settlement()
            got=[[(a.winner-seat)%n,(a.loser-seat)%n,a.hand_index,a.source_card.model] for a in result.transfers]
            self.assertEqual(got,[[r[0],r[1],r[3],r[4]] for r in row['awards']])
            for local in range(n):
                wire=(local+seat)%n
                self.assertEqual([c.card.model for c in result.surviving[wire]],row['remaining'][local])
            original_ids=sorted(c.card.model for hand in original for c in hand)
            settled_ids=sorted([c.card.model for hand in result.surviving for c in hand]+[a.card.model for a in result.transfers])
            self.assertEqual(settled_ids,original_ids)
            for award in result.transfers:
                self.assertEqual(award.original_slot,slots[award.source_card.model%5])
                self.assertEqual(award.card.deck,255)
                self.assertEqual(award.source_card.deck,award.source_card.model%5)
            self.assertIs(result,ledger.settlement())
        print('Original prize-order/compaction comparisons:',len(cases))

    def test_choice_authority_eligibility_and_generation_are_atomic(self):
        ledger=PrizeLedger((6,3,6),hands([[0]*5,[0,0,2,2,1],[2]*5]))
        p=ledger.pending;self.assertIsNotNone(p)
        before=(ledger.pending,ledger.transfers)
        for winner,index,generation in ((1,0,p.choice_id),(p.winner,4,p.choice_id),
                                         (p.winner,0,p.choice_id+1),(p.winner,True,p.choice_id)):
            with self.assertRaises(PrizeError):ledger.select(winner,index,choice_id=generation)
            self.assertEqual((ledger.pending,ledger.transfers),before)
        a=ledger.select(p.winner,1,choice_id=p.choice_id)
        self.assertEqual((a.loser,a.original_slot),(1,1))
        self.assertIsNotNone(ledger.pending) # tied top seat2 can also win from1
        next_before=(ledger.pending,ledger.transfers)
        with self.assertRaises(PrizeError):ledger.select(ledger.pending.winner,2,choice_id=p.choice_id)
        self.assertEqual((ledger.pending,ledger.transfers),next_before)
        p=ledger.pending
        ledger.select(p.winner,p.options[0].hand_index,choice_id=p.choice_id)
        result=ledger.settlement()
        self.assertEqual(len(result.transfers),2)
        with self.assertRaises(PrizeError):ledger.select(0,0,choice_id=1)
        self.assertIs(ledger.settlement(),result)

    def test_perfect_is_automatic_and_all_original_identities_survive(self):
        ledger=PrizeLedger((0,15,0),hands([[1]*5,[1]*5,[1]*5]))
        self.assertTrue(ledger.complete);self.assertIsNone(ledger.pending)
        result=ledger.settlement()
        self.assertEqual([len(x) for x in result.surviving],[0,5,0])
        self.assertEqual([len(x) for x in result.awards],[0,10,0])
        self.assertEqual([(a.loser,a.hand_index) for a in result.transfers],[(0,0)]*5+[(2,0)]*5)
        self.assertEqual(sorted(c.card.model for h in result.surviving for c in h)+
                         sorted(a.card.model for a in result.transfers),list(range(5,10))+list(range(5))+list(range(10,15)))

    def test_draw_and_absent_captures_never_transfer(self):
        for scores,owners in [((5,5),[[1]*5,[0]*5]),
                              ((6,4),[[0]*5,[1]*5]),
                              ((5,5,5),[[2]*5,[0]*5,[1]*5])]:
            ledger=PrizeLedger(scores,hands(owners))
            self.assertTrue(ledger.complete)
            self.assertFalse(ledger.settlement().transfers)
            self.assertEqual([len(x) for x in ledger.settlement().surviving],[5]*len(scores))

    def test_original_received_prize_clears_deck_association(self):
        for deck in (0,7,49,255):
            v=WagerVM();p,g=v.player,v.game;a=g+0x2a8
            v.u.mem_write(a+8,bytes([10,0,10,10,1,0,5,0]))
            v.w8(a+0x16,deck);v.w8(g+0x174,0);v.w8(g+0x178,0);v.w16(p+0xf0,0)
            v.block(0x100cadb2,0x100caea0,esi=g,ecx=0)
            self.assertEqual(v.r8(a+0x16),255)
            self.assertEqual(v.r16(p+0xf0),1)
            self.assertEqual(v.r8(p+0x4b90+14),255)

    def test_invalid_input_and_no_partial_settlement(self):
        valid=hands([[0]*5,[1]*5])
        cases=[((5,),valid),((0,0),valid),((True,9),valid),
               ((5,5),[valid[0]]),((5,5),[valid[0][:-1],valid[1]]),
               ((5,5),[[valid[0][0]]*5,valid[1]]),
               ((5,5),[[PrizeCard(c.original_slot,c.card,2) for c in valid[0]],valid[1]])]
        for scores,hand in cases:
            with self.assertRaises(PrizeError):PrizeLedger(scores,hand)
        ledger=PrizeLedger((7,3),hands([[0]*5,[0,0,1,1,1]]))
        with self.assertRaises(PrizeError):ledger.settlement()


if __name__=='__main__':unittest.main()
