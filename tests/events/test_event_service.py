"""Event file builders against original consumer instructions, without UI."""

import copy
import json
import struct
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tmserver.cards.catalog import Catalog
from tmserver.economy.service import EconomyService
from tmserver.economy.packets import named, parse_named
from tmserver.events.packets import (
    SIZES,
    announcement,
    event_data,
    event_list,
    event_members,
    time_reply,
)
from tmserver.events.models import EventConfig, EventError, EventListing, EventMember, PrizeDisplay
from tmserver.events.service import EventHost



class EventFileTests(unittest.TestCase):
    def test_empty_directory_has_complete_extents_and_no_events(self):
        service = EventHost(EconomyService())
        for path, size in SIZES.items():
            self.assertEqual(len(service.file(path)), size)
        self.assertEqual(struct.unpack_from("<I", service.file("b/g/TM0EventList"), 4)[0], 0)
        self.assertEqual(struct.unpack_from("<I", service.file("b/g/TM0EventMemberList"), 4)[0], 0)
        self.assertIsNone(service.file("b/g/unknown"))

    def test_client_array_and_string_bounds_are_enforced(self):
        bad = [
            lambda: event_list([EventListing(b"R" * 32, b"E", b"")]),
            lambda: event_list([EventListing(b"R", b"E", b"")] * 33),
            lambda: event_list([EventListing(b"R", b"E", b"")] * 2),
            lambda: event_members([EventMember(1, b"X" * 16)]),
            lambda: event_members([EventMember(1, b"A")] * 2),
            lambda: event_members([EventMember(1, b"A", 0x80000000)]),
            lambda: event_data(classes=(b"A", b"B"), steps_per_class=0),
            lambda: event_data(missions={0: 1, 1: 1, 2: 1, 3: 1}),
            lambda: event_data(missions={7: 0}),
            lambda: event_data(prizes=(PrizeDisplay(description=b"X" * 40),)),
            lambda: announcement(2147483647, b"x" * 1991, 2147483647),
        ]
        for index, function in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(ValueError):
                function()


if __name__ == "__main__":
    unittest.main()
