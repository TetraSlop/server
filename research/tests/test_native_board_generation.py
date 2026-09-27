"""Exact local board-generation oracle and bounded pure API checks."""
from pathlib import Path
import sys,unittest
from native_board_generation import generate_board,BoardGenerationError
from native_rules import CRTLCG


class BoardGenerationTests(unittest.TestCase):
    def test_original_generation_and_every_raw_draw_all_rule_combinations(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'protocol_map'))
        from board_generation_vectors import run
        rows=run();self.assertEqual(len(rows),1024)
        self.assertEqual({f for row in rows for f in row['fields']},set(range(18)))
        for row in rows:
            self.assertLessEqual(row['blocks'],len(row['fields'])-5*row['players'])
            self.assertEqual(sum(f!=0 for f in row['fields']),row['blocks']+row['effects'])
            self.assertEqual(sum(3<=f<=5 for f in row['fields']),row['effects'])
            self.assertEqual(sum(9<=f<=16 for f in row['fields']),row['spinners'])
        self.assertEqual({row['active'] for row in rows if row['players']==3},{0,1,2})

    def test_bad_inputs_validate_before_randomness_and_default_original_has_walls(self):
        def forbidden():raise AssertionError('unexpected random draw')
        for n in (0,1,4,True):
            with self.assertRaises(BoardGenerationError):generate_board(n,forbidden)
        for field in ('st','cb','ca'):
            for value in (-1,2,True):
                with self.assertRaises(BoardGenerationError):generate_board(2,forbidden,**{field:value})
        for value in (-1,32768,True):
            with self.assertRaises(BoardGenerationError):generate_board(2,lambda:value)
        generated=generate_board(2,CRTLCG(1))
        self.assertGreater(generated.blocks,0)
        self.assertEqual(generated.effects,0)
        self.assertTrue(set(generated.fields)<={0,1,2})


if __name__=='__main__':unittest.main()
