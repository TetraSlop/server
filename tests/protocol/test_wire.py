"""Round trips for the PlayOnline IRC wire encoders."""

import math
import os
import unittest

from tmserver.protocol.wire import (
    MD5_IV,
    SqBlowfish,
    b64dec,
    b64enc,
    crypt64,
    frame_line,
    md5_iv,
    polid_from_string,
    polid_scramble,
    polid_unscramble,
    rsa_wrap_session_key,
)


def _prime128():
    while True:
        x = int.from_bytes(os.urandom(16), "big") | 1 << 127 | 1
        if all(pow(a, x - 1, x) == 1 for a in (2, 3, 5, 7, 11)) and math.gcd(0xFFFF, x - 1) == 1:
            return x


class WireTests(unittest.TestCase):
    def test_polid_nick_round_trip(self):
        for value in (0, 1, 12345678901, 0x1FFFFFFFFFF):
            nick = polid_scramble(value)
            self.assertEqual(len(nick), 9)
            self.assertEqual(polid_unscramble(nick), value)
        self.assertEqual(polid_scramble(polid_from_string("EEEEEEEE")), polid_scramble(0))

    def test_base64_round_trip(self):
        blob = os.urandom(0x48)
        self.assertEqual(b64dec(b64enc(blob)), blob)
        self.assertEqual(len(b64enc(blob)), 96)

    def test_md5_with_standard_iv(self):
        self.assertEqual(md5_iv(b"abc", MD5_IV).hex(), "900150983cd24fb0d6963f7d28e17f72")

    def test_line_cipher_round_trip_and_framing(self):
        bf = SqBlowfish(os.urandom(8))
        line = b":srv 422 U00000000 :no motd" + b"\x0a\x0d" * 3
        encrypted = crypt64(bf, 0x1122334455667788, line)
        self.assertEqual(crypt64(bf, 0x1122334455667788, encrypted), line)
        framed = frame_line(b"NOTICE x :y", bf, 42)
        self.assertTrue(framed.endswith(b"\r\n"))
        self.assertNotIn(b"\n", framed[:-1])

    def test_rsa_session_key_wrap(self):
        p, q = _prime128(), _prime128()
        n, d = p * q, pow(0xFFFF, -1, (p - 1) * (q - 1))
        key = os.urandom(8)
        wrapped = b64dec(rsa_wrap_session_key(n.to_bytes(32, "little"), key))[:32]
        block = pow(int.from_bytes(wrapped, "big"), d, n).to_bytes(32, "big")
        self.assertEqual(block[:2], b"\0\x02")
        self.assertEqual(block[-8:][::-1], key)
