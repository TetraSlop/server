"""GTM0G envelope codec against the original builder and receive-parser vectors."""

import json
import unittest

from tests import FIXTURES
from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import named, parse_named
from tmserver.game.errors import MatchError
from tmserver.game.packets import decode, packet
from tmserver.protocol.envelope import MAX_BODY, Envelope

VECTORS = FIXTURES / "protocol"


class EnvelopeTests(unittest.TestCase):
    def test_original_builder_vectors(self):
        rows = json.loads((VECTORS / "envelope_vectors.json").read_text())
        for row in rows:
            header, body = (row["op"], row["sub"], row["sequence"]), row["payload"][8:]
            with self.subTest(length=row["body_length"], fragment=row["fragment"]):
                if row["body_length"] > MAX_BODY:
                    # The original splits these; the server sends one body only.
                    for encode, error in ((named, EconomyError), (packet, MatchError)):
                        with self.assertRaises(error):
                            encode(*header, "A" * row["body_length"])
                    continue
                expected = b"GTM0G" + row["payload"].encode()
                self.assertEqual(named(*header, body), expected)
                self.assertEqual(packet(*header, body), expected)
        put = Envelope(67, 10, 17, {"PutData": {"P": "1", "H": "2", "F": "3"}})
        payload = b"GTM0G" + rows[0]["payload"].encode()
        self.assertEqual((parse_named(payload), decode(payload)), (put, put))

    def test_original_receive_parser_vectors(self):
        rows = json.loads((VECTORS / "envelope_review_vectors.json").read_text())
        for row in rows:
            raw = b"GTM0G" + row["raw"].encode()
            with self.subTest(raw=row["raw"][:40]):
                if not row["token_found"] or len(row["raw"]) - 8 > MAX_BODY:
                    with self.assertRaises(MatchError):
                        decode(raw)
                    continue
                message = decode(raw)
                self.assertEqual(
                    (message.primary, message.subtype, message.argument), tuple(row["metadata"][:3])
                )
                self.assertIn(row["token"][1:], message.sections)

    def test_dialects_differ_only_where_documented(self):
        for payload, strict, lenient in (
            (b"GTM0G43FF0A11@X=/A=1", EconomyError, {"X": {"A": "1"}}),
            (b"GTM0G43000A11", EconomyError, {}),
            (b"GTM0G43000A11@X=/A=\x01", EconomyError, {"X": {"A": "\x01"}}),
            (b"GTM0G43000A11@a b=/c d=1", EconomyError, {"a b": {"c d": "1"}}),
            (b"GTM0G43000A11@X=/A=1@X=/B=2", EconomyError, MatchError),
            (b"GTM0G43000A11@X=v", EconomyError, MatchError),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(strict):
                    parse_named(payload)
                if isinstance(lenient, dict):
                    self.assertEqual(decode(payload), Envelope(67, 10, 17, lenient))
                else:
                    with self.assertRaises(lenient):
                        decode(payload)
        self.assertIsNone(decode(b"GTM0G43000A11@X=/A=1\r\n"))
        self.assertEqual(packet(67, 10, 17, "@X=/A=\x01"), b"GTM0G43000A11@X=/A=\x01")
        with self.assertRaises(EconomyError):
            named(67, 10, 17, "@X=/A=\x01")


if __name__ == "__main__":
    unittest.main()
