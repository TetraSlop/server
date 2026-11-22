"""Departure helper comparisons against original instructions; no native UI."""

import unittest

from tmserver.game.departure_rules import DepartureError, next_turn, result_slots


class OriginalDepartureTests(unittest.TestCase):

    def test_invalid_inputs_and_selected_provenance(self):
        for args in ((4, 0, ()), (2, 3, ()), (2, 1, (1, 1))):
            with self.assertRaises(DepartureError):
                next_turn(*args, 0, 0)
        with self.assertRaises(DepartureError):
            next_turn(2, 2, (0, 1), 0, 0)
        self.assertEqual(result_slots((0, 3), (4, 2, 1), departed=True, rule=1), ())
        self.assertEqual(result_slots((0, 3), (4, 2, 1), departed=True, rule=2), (4, 2, 1))
        self.assertEqual(result_slots((0, 3), (4, 2, 1)), (0, 3, 4, 2, 1))


if __name__ == "__main__":
    unittest.main()
