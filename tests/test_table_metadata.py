"""Encoder validation; original x86 round trips live in table_metadata_vectors.py."""

import unittest

from tmserver.table_metadata import TableMetadataError, table_info

GAME = dict(bm=0, du=0, st=0, cb=0, ca=0, gs=0, tl=0)
TABLE = {"in": 0, "lu": 100, "ll": 0, "au": 300, "al": 100, "co": 0, "pa": 0}
OWNER = 0xB62DC8D0E4BCBB91


class TableMetadataTests(unittest.TestCase):
    def encode(self, game=None, table=None, **identity):
        return table_info(
            identity.get("number", 1),
            identity.get("count", 2),
            identity.get("owner", OWNER),
            GAME if game is None else game,
            TABLE if table is None else table,
        )

    def test_observed_native_settings_golden(self):
        self.assertEqual(self.encode(), b"AAAAAC********LGCNMINAOELMLLJBAAAAAAAAAAAAGEAAAAA")
        self.assertEqual(len(self.encode()), 49)

    def test_all_fields_nondefault_golden(self):
        game = dict(bm=2000, du=1, st=0, cb=1, ca=1, gs=2, tl=5)
        table = dict(TABLE, lu=99999, ll=12345, co=8, pa=1, pw="6162632A2A2A2A2A")
        table["in"] = 2
        self.assertEqual(
            self.encode(game, table, number=128, count=255, owner=0x0123456789ABCDEF),
            b"AAHPPPabc*****ABCDEFGHIJKLMNOPAHNANFCGIBIGJPADADJ",
        )

    def test_password_bytes_and_padding(self):
        for password, raw in [
            ("", b"********"),
            ("616263", b"abc*****"),
            ("6162632a2a2a2a2a", b"abc*****"),
            ("3132333435363738", b"12345678"),
        ]:
            with self.subTest(password=password):
                self.assertEqual(self.encode(table=dict(TABLE, pa=1, pw=password))[6:14], raw)

    def test_invalid_passwords_rejected(self):
        for password in (
            None,
            b"abc",
            "0",
            "GG",
            "61 62",
            "61" * 9,
            "612a62",
            "00",
            "06",
            "07",
            "0a",
            "0d",
        ):
            with self.subTest(password=password), self.assertRaises(TableMetadataError):
                self.encode(table=dict(TABLE, pa=1, pw=password))
        with self.assertRaises(TableMetadataError):
            self.encode(table=dict(TABLE, pa=1))
        with self.assertRaises(TableMetadataError):
            self.encode(table=dict(TABLE, pw="2A" * 8))

    def test_rank_bounds_cannot_be_silently_changed(self):
        for fields in ({"au": 99999, "al": 0}, {"au": 301}, {"al": 99}):
            with (
                self.subTest(fields=fields),
                self.assertRaisesRegex(TableMetadataError, "cannot encode au/al"),
            ):
                self.encode(table=dict(TABLE, **fields))

    def test_range_and_integer_validation(self):
        for key, maximum in [
            ("bm", 65535),
            ("du", 1),
            ("st", 1),
            ("cb", 1),
            ("ca", 1),
            ("gs", 2),
            ("tl", 5),
        ]:
            for value in (-1, maximum + 1, 1.0, "1", None):
                with self.subTest(key=key, value=value), self.assertRaises(TableMetadataError):
                    self.encode(game=dict(GAME, **{key: value}))
        for key, maximum in [("in", 2), ("lu", 1048575), ("ll", 1048575), ("co", 8), ("pa", 1)]:
            for value in (-1, maximum + 1, 1.0, "1", None):
                with self.subTest(key=key, value=value), self.assertRaises(TableMetadataError):
                    self.encode(table=dict(TABLE, **{key: value}))
        for key, minimum, maximum in [
            ("number", 1, 4096),
            ("count", 0, 255),
            ("owner", 0, 2**64 - 1),
        ]:
            for value in (minimum - 1, maximum + 1, 1.0, "1", None, True):
                with self.subTest(key=key, value=value), self.assertRaises(TableMetadataError):
                    self.encode(**{key: value})
        with self.assertRaises(TableMetadataError):
            self.encode(table=dict(TABLE, ll=101))

    def test_boolean_objects_only_for_boolean_fields(self):
        self.assertEqual(
            self.encode(game=dict(GAME, du=True, st=False)),
            self.encode(game=dict(GAME, du=1, st=0)),
        )
        self.assertEqual(
            self.encode(table=dict(TABLE, pa=True, pw="")),
            self.encode(table=dict(TABLE, pa=1, pw="")),
        )
        for key in ("bm", "gs", "tl"):
            with self.subTest(key=key), self.assertRaises(TableMetadataError):
                self.encode(game=dict(GAME, **{key: True}))
        for key in ("in", "lu", "ll", "au", "al", "co"):
            with self.subTest(key=key), self.assertRaises(TableMetadataError):
                self.encode(table=dict(TABLE, **{key: False}))

    def test_missing_and_unknown_fields_rejected(self):
        for label, rules in [("game", GAME), ("table", TABLE)]:
            for key in rules:
                missing = dict(rules)
                missing.pop(key)
                with self.subTest(label=label, key=key), self.assertRaises(TableMetadataError):
                    self.encode(**{label: missing})
            for value in ([], {}, dict(rules, unknown=0)):
                with self.subTest(label=label, value=value), self.assertRaises(TableMetadataError):
                    self.encode(**{label: value})

    def test_format_maxima_and_inputs_unchanged(self):
        game = dict(GAME, bm=65535)
        table = dict(TABLE, lu=1048575, ll=1048575)
        before = game.copy(), table.copy()
        value = self.encode(game, table, number=4096, count=255, owner=2**64 - 1)
        self.assertEqual(value[:6], b"APPPPP")
        self.assertEqual(value[14:34], b"P" * 20)
        self.assertEqual(value[39:49], b"P" * 10)
        self.assertEqual((game, table), before)


if __name__ == "__main__":
    unittest.main()
