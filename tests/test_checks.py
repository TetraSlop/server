"""The shared bounds checks accept and reject exactly what the services relied on."""

import unittest
from dataclasses import replace

from tmserver.cards.catalog import Catalog
from tmserver.checks import decimal, exact_keys, hex_bytes, stable_id, uint
from tmserver.economy.errors import EconomyError


class Rejected(ValueError):
    pass


class ChecksTests(unittest.TestCase):
    def test_uint(self):
        self.assertEqual(uint(5, 5, error=Rejected), 5)
        for value, maximum, minimum in ((True, 1, 0), (6, 5, 0), (0, 5, 1), ("1", 5, 0), (1.0, 5, 0)):
            with self.subTest(value=value), self.assertRaises(Rejected):
                uint(value, maximum, minimum, error=Rejected)

    def test_decimal(self):
        cases = (
            ("0", {}, 0),
            ("0000000007", {}, 7),
            (b"255", {"maximum": 255}, 255),
            ("-0", {"signed": True}, 0),
            ("-5", {"signed": True, "minimum": -5}, -5),
        )
        for text, options, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(decimal(text, error=Rejected, **options), expected)
        for text, options in (
            ("-0", {}),
            ("256", {"maximum": 255}),
            ("-1", {"signed": True}),
            ("12345678901", {}),
            ("", {}),
            ("+1", {}),
            (None, {}),
            ("١", {}),
        ):
            with self.subTest(text=text), self.assertRaises(Rejected):
                decimal(text, error=Rejected, **options)

    def test_stable_id_and_exact_keys(self):
        self.assertEqual(stable_id("a" * 128, error=Rejected), "a" * 128)
        self.assertEqual(stable_id("_-.:AZaz09", error=Rejected), "_-.:AZaz09")
        for value in ("", "a" * 129, "a b", "a/b", 1, None):
            with self.subTest(value=value), self.assertRaises(Rejected):
                stable_id(value, error=Rejected)
        exact_keys({"a": 1, "b": 2}, ("b", "a"), error=Rejected)
        for mapping in ({"a": 1}, {"a": 1, "b": 2, "c": 3}):
            with self.subTest(mapping=mapping), self.assertRaises(Rejected):
                exact_keys(mapping, ("a", "b"), error=Rejected)

    def test_hex_bytes(self):
        self.assertEqual(hex_bytes("4142", 2, error=Rejected), b"AB")
        for text in ("414", "414243", "zz", "4100"):
            with self.subTest(text=text), self.assertRaises(Rejected):
                hex_bytes(text, 2, error=Rejected)

    def test_catalog_batch(self):
        catalog = Catalog()
        card = catalog.generate(1, lambda: 20)
        self.assertEqual(catalog.validate_batch([card] * 30, error=Rejected), (card,) * 30)
        with self.assertRaises(Rejected):
            catalog.validate_batch([card] * 31, error=Rejected)
        with self.assertRaises(Rejected):
            catalog.validate_batch([replace(card, deck=3)], error=Rejected)
        with self.assertRaises(EconomyError):
            catalog.validate_batch([replace(card, points=(card.points + 1) & 255)], error=Rejected)


if __name__ == "__main__":
    unittest.main()
