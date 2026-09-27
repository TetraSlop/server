"""Execute original existing-table eligibility against production metadata."""
from pathlib import Path
import sys
import unittest


class OriginalTableEligibility(unittest.TestCase):
    def test_owned_cards_occupancy_state_money_and_level_boundaries(self):
        sys.path.insert(0,str(Path(__file__).parents[1]/'protocol_map'))
        from table_eligibility_vectors import cases
        self.assertEqual(len(cases()),21)


if __name__=='__main__':unittest.main()
