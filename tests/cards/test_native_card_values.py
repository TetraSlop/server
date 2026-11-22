"""Catalogue points and sale value against the original routines."""

import ctypes
import random
import struct
import unittest

from tmserver.cards.catalog import Catalog
from tmserver.cards.model import Card as CatalogCard
from tmserver.game.original.native import _card_record
from tmserver.game.original.runtime import default_rules
from tmserver.game.rules import Card, GrowthBaseline

# Addresses in the image the bridge maps at 0x10000000.
_STORED_CARD_SALE = (
    0x100B9700  # recomputes a stored card's sale; shop sell and auction exhibit use it
)
_CARD_COUNT, _CARDS, _PRICES = 0x102FF4FC, 0x102B6364, 0x102B6368
_PC_24, _MCW_PC = 0x00020000, 0x00030000


class NativeCardValueTests(unittest.TestCase):
    def test_points_match_original_growth(self):
        catalog = Catalog()
        rng = random.Random(3)
        for model, row in enumerate(catalog.cards):
            baseline = GrowthBaseline.from_catalog(row, catalog.prices)
            for mode in range(4):
                stats = [
                    rng.randrange(1, row[f] + 1)
                    for f in ("attack", "physical_defense", "magical_defense")
                ]
                card = Card(model, stats[0], rng.randrange(4), *stats[1:], 0, 1)
                grown = default_rules().grow_card(card, baseline, mode, rng.randrange(256)).card
                with self.subTest(card=card, mode=mode):
                    self.assertEqual(
                        grown.points,
                        catalog.points(model, grown.attack, grown.physical, grown.magical),
                    )

    def test_sale_value_matches_client_for_every_reachable_card(self):
        # Every stat a generated or grown card can hold (half to full baseline)
        # and every type, through the client's routine at its 24-bit precision.
        catalog = Catalog()
        default_rules()  # maps the image
        rows = b"".join(map(_card_record, catalog.cards))
        prices = struct.pack(f"<{len(catalog.prices)}I", *catalog.prices)
        rows_buffer = ctypes.create_string_buffer(rows)
        prices_buffer = ctypes.create_string_buffer(prices)
        # The bridge restores the image data before each of its own calls.
        for address, value in (
            (_CARD_COUNT, len(catalog.cards)),
            (_CARDS, ctypes.addressof(rows_buffer)),
            (_PRICES, ctypes.addressof(prices_buffer)),
        ):
            ctypes.c_uint32.from_address(address).value = value
        sale = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(_STORED_CARD_SALE)
        stored = (ctypes.c_uint8 * 16)()
        address = ctypes.addressof(stored)
        controlfp = ctypes.CDLL("ucrtbase")._controlfp
        controlfp.restype = ctypes.c_uint
        controlfp.argtypes = (ctypes.c_uint, ctypes.c_uint)
        previous = controlfp(0, 0)
        controlfp(_PC_24, _MCW_PC)
        differ = []
        try:
            for model, b in enumerate(catalog.cards):
                stored[6:8] = struct.pack("<H", model)
                for a in range(b["attack"] // 2, b["attack"] + 1):
                    stored[0] = a
                    for p in range(b["physical_defense"] // 2, b["physical_defense"] + 1):
                        stored[2] = p
                        for m in range(b["magical_defense"] // 2, b["magical_defense"] + 1):
                            stored[3] = m
                            for kind in range(4):
                                stored[1] = kind
                                card = CatalogCard(model, a, kind, p, m, 0, 1)
                                if sale(address) != catalog.sale_value(card):
                                    differ.append(card)
        finally:
            controlfp(previous, _MCW_PC)
        self.assertEqual(differ[:10], [])


if __name__ == "__main__":
    unittest.main()
