"""Departure helper comparisons against original instructions; no native UI."""

import unittest

from tmserver.game.departure_rules import next_turn, result_slots
from tmserver.game.errors import MatchError


class OriginalDepartureTests(unittest.TestCase):

    def test_no_active_seat_and_selected_provenance(self):
        with self.assertRaises(MatchError):
            next_turn(2, 2, (0, 1), 0, 0)
        self.assertEqual(result_slots((0, 3), (4, 2, 1), departed=True, rule=1), ())
        self.assertEqual(result_slots((0, 3), (4, 2, 1), departed=True, rule=2), (4, 2, 1))
        self.assertEqual(result_slots((0, 3), (4, 2, 1)), (0, 3, 4, 2, 1))


if __name__ == "__main__":
    unittest.main()
