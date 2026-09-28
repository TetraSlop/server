"""Whole service replies, including native sender framing, fit one frame."""

import unittest

from tmserver.protocol.commands import Command, encode_service, parse
from tmserver.protocol.messages import service_notice


class ServiceMessageTests(unittest.TestCase):
    def test_bel_families_include_the_prefix_in_the_budget(self):
        for family in (b"A", b"L", b"P", b"R"):
            with self.subTest(family=family):
                commands = [Command(b"PO", b"x" * 458)]
                payload = encode_service(family, commands)
                self.assertEqual(len(payload), 469)
                self.assertEqual(parse(payload[5:]), commands)
                line = service_notice(b"UUIRT71SR", b"UUIRT71SS", payload)
                self.assertTrue(line.startswith(b":UUIRT71SR!p@local NOTICE UUIRT71SS :"))
                self.assertEqual(len(line) + 6, 512)
                with self.assertRaisesRegex(ValueError, "frame budget"):
                    encode_service(family, [Command(b"PO", b"x" * 459)])

    def test_notice_validates_names_and_complete_payload(self):
        for nick in (b"", b"x" * 10, b"x y", b"x:y", b"x!y", b"x\ny", b"x\0y"):
            for source, recipient in ((nick, b"UUIRT71SS"), (b"UUIRT71SR", nick)):
                with self.subTest(source=source, recipient=recipient), self.assertRaises(ValueError):
                    service_notice(source, recipient, b"")
        for payload in (b"x" * 470, b"x\ny", b"x\ry", b"x\0y"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                service_notice(b"short", b"short", payload)
        self.assertEqual(service_notice(b"a", b"b", b"x"), b":a!p@local NOTICE b :x")
