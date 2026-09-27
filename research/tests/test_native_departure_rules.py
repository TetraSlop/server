"""Departure helper comparisons against original instructions; no native UI."""
from pathlib import Path
import random,sys,unittest
from dataclasses import replace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
from departure_rules_vectors import ranking_vectors,turn_vectors,rebuild_vectors
from native_departure_rules import *
from test_native_ai import OriginalAI,Draws
from native_ai import Personality,Move
from native_special_rules import Cell,start_board,special_placement_scores,choose_special_move
from match_service import Card


class OriginalDepartureTests(unittest.TestCase):
    def test_ranking_and_partial_hand_rebuild_original(self):
        self.assertEqual(len(ranking_vectors()),1080)
        self.assertEqual(len(turn_vectors()),324)
        self.assertEqual(len(rebuild_vectors()),72)

    def test_menu_inventory_reconciliation_original(self):
        from departure_inventory_vectors import vectors
        self.assertEqual(len(vectors()),72)

    def test_three_player_substitute_AI_original(self):
        v=OriginalAI();rng=random.Random(783291);comparisons=0
        for case in range(32):
            active=case%3
            card=Card(5,10,0,10,10,1,rng.randrange(256))
            board=list(start_board(tuple(rng.randrange(18) if rng.randrange(3)==0 else 0 for _ in range(25))).cells)
            for i in range(25):
                if rng.randrange(3)==0:
                    c=replace(card,model=rng.randrange(250),arrows=rng.randrange(256))
                    board[i]=Cell.ordinary(c,rng.randrange(3),property=rng.randrange(4))
            board[case%25]=Cell()
            v.w8(v.player+0xe5,3);v.w8(0x20300000,1)
            v.seed((card,),(None,)*25,active);v.w8(v.game+0x17a,0)
            for i,c in enumerate(board):v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,prop=c.property,direction=c.direction)
            for preference in range(5):
                for field,value in special_placement_scores(card,board,active,preference):
                    v.u.mem_write(v.game+0x227,bytes(25))
                    self.assertEqual(value,v.score(field,preference),(case,preference,field))
                    comparisons+=1
            for personality in (Personality(),Personality(100,1,100),Personality(100,3,100),Personality(0,0,0)):
                draws=[rng.randrange(32768) for _ in range(6)]
                hand=tuple(replace(card,model=i,arrows=rng.randrange(256)) for i in range(case%5+1))
                v.seed(hand,(None,)*25,active,draws,1,personality)
                for i,c in enumerate(board):v.card(i,id=c.model,owner=c.owner,arrows=c.arrows,prop=c.property,direction=c.direction)
                index=v.hand(active);field=v.placement();stream=Draws(draws)
                self.assertEqual(choose_special_move(hand,board,active,stream,personality),Move(index,field))
                self.assertEqual(stream.used,v.rng);comparisons+=1
        self.assertGreater(comparisons,1200)
        print('Original three-player substitute AI comparisons:',comparisons)

    def test_invalid_inputs_and_selected_provenance(self):
        for args in ((4,0,()),(2,3,()),(2,1,(1,1))):
            with self.assertRaises(DepartureError):next_turn(*args,0,0)
        with self.assertRaises(DepartureError):next_turn(2,2,(0,1),0,0)
        self.assertEqual(result_slots((0,3),(4,2,1),departed=True,rule=1),())
        self.assertEqual(result_slots((0,3),(4,2,1),departed=True,rule=2),(4,2,1))
        self.assertEqual(result_slots((0,3),(4,2,1)),(0,3,4,2,1))


if __name__=='__main__':unittest.main()
