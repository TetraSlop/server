"""Portable comparisons against original combined pool/hand instruction fixtures."""

import hashlib
import json
import struct
import unittest
from collections import Counter
from pathlib import Path

from tests import FIXTURES
from tmserver.cards.catalog import Catalog
from tmserver.game.opponents import OpponentError, prepare_opponent, prepare_opponents
from tmserver.game.rules import CRTLCG


def card_bytes(card):
    """Every server Card field in the original compact layout, excluding bookkeeping."""
    return struct.pack(
        "<6BHB",
        card.attack,
        card.kind,
        card.physical,
        card.magical,
        card.points,
        card.arrows,
        card.model,
        card.deck,
    )


def original_card_bytes(compact):
    raw = bytes.fromhex(compact)
    return raw[:8] + raw[14:15]


class OpponentBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()
        cls.fixture = json.loads(
            (FIXTURES / "native_opponent_batches.json").read_text()
        )

    def test_complete_original_hands_unused_cards_and_random_stream_match(self):
        self.assertEqual(len(self.fixture["cases"]), 8)
        for case in self.fixture["cases"]:
            with self.subTest(seed=case["seed"], npcs=case["npc_ids"], guild=case["guild"]):
                rng = CRTLCG(case["seed"])
                draws = []

                def draw():
                    value = rng()
                    draws.append(value)
                    return value

                prepared = prepare_opponents(case["npc_ids"], self.catalog, draw, case["guild"])
                self.assertEqual([p.opponent.npc_id for p in prepared], case["npc_ids"])
                for seat, player in enumerate(prepared):
                    hand = tuple(map(card_bytes, player.hand))
                    unused = tuple(map(card_bytes, player.unused))
                    original_hand = tuple(map(original_card_bytes, case["hands_compact16"][seat]))
                    original_unused = tuple(
                        map(original_card_bytes, case["unused_compact16"][seat])
                    )
                    original_pool = tuple(map(original_card_bytes, case["pools_compact16"][seat]))
                    self.assertEqual(hand, original_hand)
                    self.assertEqual(unused, original_unused)
                    self.assertEqual((len(hand), len(unused)), (5, 5))
                    self.assertEqual(hand[0], original_pool[0])
                    self.assertEqual(Counter(hand + unused), Counter(original_pool))
                self.assertEqual(rng.state, case["rng_final_state"])
                self.assertEqual(len(draws), case["total_draws"])
                self.assertEqual(len(draws) - case["generation_draws"], 4 * len(prepared))
                self.assertEqual(
                    hashlib.sha256(struct.pack(f"<{len(draws)}H", *draws)).hexdigest(),
                    case["rng_draws_sha256"],
                )

    def test_single_opponent_API_keeps_original_frozen_hands_and_stream(self):
        singles = [case for case in self.fixture["cases"] if len(case["npc_ids"]) == 1]
        self.assertEqual(len(singles), 2)
        for case in singles:
            with self.subTest(npc=case["npc_ids"][0], guild=case["guild"]):
                rng = CRTLCG(case["seed"])
                player = prepare_opponent(case["npc_ids"][0], self.catalog, rng, case["guild"])
                self.assertEqual(
                    tuple(map(card_bytes, player.hand)),
                    tuple(map(original_card_bytes, case["hands_compact16"][0])),
                )
                self.assertEqual(
                    tuple(map(card_bytes, player.unused)),
                    tuple(map(original_card_bytes, case["unused_compact16"][0])),
                )
                self.assertEqual(rng.state, case["rng_final_state"])

    def test_invalid_batch_and_guild_do_not_generate_cards_or_consume_randomness(self):
        def unexpected_draw():
            self.fail("invalid input consumed randomness")

        for ids in (None, 1, (), (1, 3, 30), (1, 1), (0,), (1, 31), (1, True), (1, "3")):
            with self.subTest(ids=ids), self.assertRaises(OpponentError):
                prepare_opponents(ids, self.catalog, unexpected_draw)
        for guild in (-1, 4, True, "0"):
            with self.subTest(guild=guild), self.assertRaises(OpponentError):
                prepare_opponents((1, 3), self.catalog, unexpected_draw, guild)

    def test_draw_contract_is_preserved_by_single_and_batch_helpers(self):
        for bad in (-1, 32768, True, 0.0, None):
            with self.subTest(draw=bad):
                with self.assertRaisesRegex(OpponentError, "CRT draw"):
                    prepare_opponent(1, self.catalog, lambda: bad)
                with self.assertRaisesRegex(OpponentError, "CRT draw"):
                    prepare_opponents((1, 3), self.catalog, lambda: bad)


if __name__ == "__main__":
    unittest.main()
