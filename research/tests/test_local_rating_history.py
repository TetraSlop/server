"""Original-instruction comparisons and durable exactly-once policy tests."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import unittest

from local_rating_history import HistoryState, LocalRatingHistory, RatingHistoryError, classify_result


class LocalRatingHistoryTests(unittest.TestCase):
    def test_original_constructor_and_all_arithmetic_vectors(self):
        fixture = json.loads((Path(__file__).parents[1]/'protocol_map/rating_history_vectors.json').read_text())
        self.assertEqual(fixture['case_count'], 594)
        service = LocalRatingHistory()
        initial = service.enroll('first')
        native = fixture['reset'][0]['after']
        self.assertEqual((initial.entries, initial.sequence, initial.rating),
                         (tuple(native['entries']), native['sequence'], native['rating']))
        for row in fixture['arithmetic']:
            with self.subTest(sequence=row['sequence'], result_class=row['result_class']):
                state = HistoryState(tuple(row['entries']), row['sequence']).next(row['result_class'])
                self.assertEqual((list(state.entries), state.sequence, state.rating),
                                 (row['after']['entries'], row['after']['sequence'], row['after']['rating']))

    def test_every_original_two_three_player_rotation_tie_and_perfect_class(self):
        fixture = json.loads((Path(__file__).parents[1]/'protocol_map/rating_history_vectors.json').read_text())
        classes = set()
        for row in fixture['classifications']:
            service = LocalRatingHistory()
            service.enroll('human', history=[i%5 for i in range(32)], sequence=row['sequence'])
            quoted = service.quote('human', 'round', row['scores'], row['seat'])
            self.assertEqual(quoted.result_class, row['result_class'])
            self.assertEqual((quoted.d2, quoted.after.sequence, list(quoted.after.entries)),
                             (row['after']['rating'], row['after']['sequence'], row['after']['entries']))
            classes.add(quoted.result_class)
        self.assertEqual(classes, set(range(5)))

    def test_enrollment_is_explicit_and_cannot_overwrite_existing_history(self):
        service = LocalRatingHistory()
        self.assertFalse(service.enrolled('existing300'))
        with self.assertRaises(RatingHistoryError):
            service.quote('existing300', 'one', [7, 3], 0)
        self.assertEqual(service.snapshot()['accounts'], {})
        self.assertEqual(service.enroll('fresh').rating, 200)
        with self.assertRaises(RatingHistoryError):
            service.enroll('fresh', history=[4]*32)
        self.assertEqual(service.state('fresh').rating, 200)

    def test_quote_has_no_side_effect_commit_replay_and_restart_are_once(self):
        service = LocalRatingHistory()
        service.enroll('one', sequence=65535)
        before = service.snapshot()
        quote = service.quote('one', 'table-uuid:round1', [10, 0], 0)
        self.assertEqual(service.snapshot(), before)
        self.assertEqual((quote.d2, quote.after.sequence), (198, 0))
        self.assertEqual(service.commit(quote), quote.after)
        saved = json.loads(json.dumps(service.snapshot()))
        restored = LocalRatingHistory.restore(saved)
        self.assertEqual(restored.commit(quote), quote.after)
        self.assertEqual(restored.snapshot(), saved)
        next_quote = restored.quote('one', 'table-uuid:round2', [0, 10], 0)
        restored.commit(next_quote)
        self.assertEqual(restored.quote('one', quote.match_id, [10, 0], 0), quote)
        self.assertEqual(restored.commit(quote), quote.after)
        self.assertEqual(restored.state('one'), next_quote.after)
        with self.assertRaises(RatingHistoryError):
            restored.quote('one', quote.match_id, [0, 10], 0)
        with self.assertRaises(RatingHistoryError):
            restored.quote('one', quote.match_id, [10, 0], 1)

    def test_competing_quotes_and_forged_state_rejected_without_changes(self):
        service = LocalRatingHistory()
        service.enroll('one')
        a = service.quote('one', 'a', [5, 5], 0)
        stale = service.quote('one', 'b', [5, 5], 0)
        with self.assertRaises(RatingHistoryError):
            service.commit(replace(a, after=replace(a.after, entries=(4,)*32)))
        self.assertEqual(service.state('one').revision, 0)
        service.commit(a)
        before = service.snapshot()
        with self.assertRaises(RatingHistoryError):
            service.commit(stale)
        self.assertEqual(service.snapshot(), before)

    def test_batch_stale_last_participant_is_atomic(self):
        service = LocalRatingHistory()
        for key in ('a', 'b', 'c'):
            service.enroll(key)
        quotes = [service.quote(key, 'round', [6, 6, 3], seat)
                  for seat, key in enumerate(('a', 'b', 'c'))]
        service.commit(service.quote('c', 'intervening', [0, 15], 0))
        before = service.snapshot()
        with self.assertRaises(RatingHistoryError):
            service.commit_many(quotes)
        self.assertEqual(service.snapshot(), before)
        quotes[2] = service.quote('c', 'round', [6, 6, 3], 2)
        result = service.commit_many(quotes)
        self.assertEqual({k: s.rating for k, s in result.items()}, {'a': 198, 'b': 198, 'c': 204})
        saved = service.snapshot()
        self.assertEqual(service.commit_many(quotes), result)
        self.assertEqual(service.snapshot(), saved)

    def test_batch_rejects_same_seat_different_order_or_match(self):
        service = LocalRatingHistory()
        for key in ('a', 'b'):
            service.enroll(key)
        a = service.quote('a', 'round', [7, 3], 0)
        for b in (service.quote('b', 'round', [7, 3], 0),
                  service.quote('b', 'round', [3, 7], 1),
                  service.quote('b', 'other', [7, 3], 1), a):
            before = service.snapshot()
            with self.assertRaises(RatingHistoryError):
                service.commit_many([a, b])
            self.assertEqual(service.snapshot(), before)

    def test_full_ring_replacement_and_lower_is_better(self):
        service = LocalRatingHistory()
        service.enroll('a')
        for number in range(32):
            service.commit(service.quote('a', f'loss:{number}', [0, 5, 10], 0))
        self.assertEqual(service.state('a').rating, 300)
        for number in range(32):
            service.commit(service.quote('a', f'win:{number}', [15, 0, 0], 0))
        self.assertEqual(service.state('a').rating, 100)
        self.assertEqual(service.state('a').sequence, 64)
        self.assertEqual(service.state('a').revision, 64)
        self.assertEqual(LocalRatingHistory.restore(service.snapshot()).state('a'), service.state('a'))

    def test_native_online_bypasses_ring_and_authoritative_rating(self):
        fixture = json.loads((Path(__file__).parents[1]/'protocol_map/rating_history_vectors.json').read_text())
        for row in fixture['online_boundary']:
            self.assertEqual(row['before'], row['after'])
        self.assertEqual(fixture['profile_header'][0]['after'],
                         dict(sequence=77, rating=300, entries=list(range(32))))

    def test_invalid_extents_types_seats_and_history(self):
        for scores, seat in (([0, 0], 0), ([1], 0), ([1, 2, 3, 4], 0), ([16, 1], 0),
                             ([25, 0, 1], 0), ([1, -1], 0), ([True, 1], 0),
                             ([5, 5], -1), ([5, 5], 2), ([5, 5], True), ('55', 0)):
            with self.subTest(scores=scores, seat=seat), self.assertRaises(RatingHistoryError):
                classify_result(scores, seat)
        for history, sequence in (([2]*31, 0), ([2]*33, 0), ([5]*32, 0), ([True]*32, 0),
                                  ([2]*32, -1), ([2]*32, 65536), ([2]*32, True)):
            with self.assertRaises(RatingHistoryError):
                LocalRatingHistory().enroll('one', history=history, sequence=sequence)

    def test_snapshot_validation_rejects_duplicate_corrupt_or_ambiguous_records(self):
        service = LocalRatingHistory()
        service.enroll('one')
        service.commit(service.quote('one', 'round', [7, 3], 0))
        saved = service.snapshot()
        variants = []
        x = copy.deepcopy(saved); x['accounts']['one']['results'] *= 2; variants.append(x)
        x = copy.deepcopy(saved); x['accounts']['one']['initial']['entries'] = None; variants.append(x)
        x = copy.deepcopy(saved); x['accounts']['one']['initial']['entries'][0] = 255; variants.append(x)
        x = copy.deepcopy(saved); x['accounts']['one']['results'][0]['seat'] = 2; variants.append(x)
        x = copy.deepcopy(saved); x['accounts']['one']['rating'] = 123; variants.append(x)
        x = copy.deepcopy(saved); x['version'] = True; variants.append(x)
        x = copy.deepcopy(saved); x['policy'] = 'elo'; variants.append(x)
        for variant in variants:
            with self.assertRaises(RatingHistoryError):
                LocalRatingHistory.restore(variant)
        self.assertEqual(LocalRatingHistory.restore(None).snapshot()['accounts'], {})
        self.assertEqual(service.snapshot(), saved)


if __name__ == '__main__':
    unittest.main()
