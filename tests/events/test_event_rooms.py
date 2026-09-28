"""Configured event directory, presence and original client routing tests."""

import struct
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tmserver.cards.catalog import Catalog
from tmserver.economy.service import EconomyService
from tmserver.economy.packets import parse_named
from tmserver.events.rooms import EventRooms
from tmserver.events.models import EventConfig, EventError, EventListing
from tmserver.events.service import EventHost
from tmserver.events.packets import event_data
from tmserver.protocol.identities import game_id
from tmserver.rooms.service import RoomService
from tmserver.rooms.packets import directory_files
from tmserver.protocol.commands import Command, encode, parse


def config(identifier="festival", raw=0x1000, region=b"Local Lobby", name=b"Festival", **kwargs):
    return EventConfig(
        identifier,
        EventListing(region, name, b"Explicit local event", scalar=raw & 0x7FFFFFFF),
        event_data(classes=(name, b"Next"), steps_per_class=4),
        100,
        500,
        game_id(raw),
        game_id(raw + 1),
        game_id(raw + 2),
        init_channel=("#E" + identifier).encode(),
        card_channel=("#F" + identifier).encode(),
        **kwargs,
    )


def presence(room_id, nickname, character, name=b"Alice", token=b"DE"):
    fields = (
        f"0x{nickname:016X}".encode(),
        str(character).encode(),
        b"2",
        b"0",
        name,
        b"0",
        b"0",
        b"0",
        b"0",
        f"0x{room_id:016X}".encode(),
        b"A" * 31,
    )
    return b"GTM0L" + encode([Command(token, b"\x06".join(fields))])


class EventRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.economy = EconomyService(seed=99, catalog=self.catalog)
        self.economy.provision("one", nn=game_id(1), player_id=1, name=b"Alice")
        self.economy.provision("two", nn=game_id(2), player_id=2, name=b"Bob")
        self.configs = (
            config(card_service_enabled=True),
            config("second", 0x1100, b"Event Zone", b"Festival", card_service_enabled=True),
        )
        self.host = EventHost(self.economy, self.configs, clock=lambda: 200)
        self.events = EventRooms(self.host)

    def admit(self, binding, peer="one", account="one"):
        view = self.economy.account_view(account)
        responses = binding.room.handle(
            peer,
            presence(binding.room_id, view.nn, view.player_id, view.name),
            authenticated_identity=(view.nn, view.player_id),
        )
        replies = self.host.admit_room(account, binding.event_id)
        self.assertEqual(parse_named(replies[0])[3]["EventEn"]["Ans"], "1")
        return responses, replies

    def test_default_files_and_room_are_unchanged(self):
        events = EventRooms(EventHost(self.economy))
        self.assertEqual(events.directory_files(), directory_files())
        self.assertEqual(events.rooms, ())
        self.assertIsNone(events.profile_file(0x1000, "b/g/PTL"))
        room = RoomService()
        self.assertEqual(struct.unpack_from("<I", room.snapshot(), 0x48)[0], 2)
        self.assertEqual(room.snapshot()[0x58B8 + 0x28], ord("D"))

    def test_exact_keys_are_routable_stable_and_do_not_advertise_disabled_e(self):
        files = self.events.directory_files()
        self.assertEqual(struct.unpack_from("<I", files["b/g/ZL"], 0x40)[0], 2)
        self.assertEqual(files["b/g/ZL"][0x99:0xB4].split(b"\0")[0], b"Event Zone")
        reverse = EventRooms(EventHost(self.economy, reversed(self.configs)))
        for c, binding in zip(self.configs, self.events.rooms):
            self.assertIs(self.events.room_for_service(c.room_id), binding)
            self.assertIs(self.events.room_for_service(c.card_id), binding)
            self.assertIs(self.events.room_for_channel(binding.room_channel.lower()), binding)
            self.assertIs(self.events.room_for_channel(c.card_channel), binding)
            self.assertEqual(reverse.room_for_service(c.room_id).room_channel, binding.room_channel)
            self.assertLessEqual(len(binding.room_channel), 12)
            snapshot = self.events.profile_file(binding.raw_owner, "b/g/PTL")
            self.assertEqual(struct.unpack_from("<I", snapshot, 0x48)[0], 1)
            self.assertEqual(snapshot[0x5878], ord("F"))
            self.assertEqual(struct.unpack_from("<Q", snapshot, 0x5850)[0], c.card_id)
            self.assertIsNone(binding.room.table_id)
            with self.assertRaises(ValueError):
                binding.room.set_table(1, 1, game_id(1))

    def test_authenticated_de_ds_and_d0_are_room_scoped_and_disconnect_is_transient(self):
        first, second = self.events.rooms
        before = first.room.snapshot(), self.host.snapshot()
        view = self.economy.account_view("one")
        with self.assertRaises(ValueError):
            first.room.handle(
                "one",
                presence(second.room_id, view.nn, view.player_id),
                authenticated_identity=(view.nn, view.player_id),
            )
        self.assertEqual((first.room.snapshot(), self.host.snapshot()), before)
        responses, d0 = self.admit(first)
        self.assertEqual(
            parse(responses[0][1][5:]), [Command(b"DS", f"0x{first.room_id:016X}".encode())]
        )
        self.assertTrue(d0[0].startswith(b"GTM0GD0"))
        self.admit(first, "two", "two")
        self.host.update_progress("one", first.event_id, "round1", steps_delta=3)
        durable = self.host.snapshot()
        groups = self.events.disconnect("one", "one")
        self.assertEqual(len(groups), 1)
        self.assertIs(groups[0][0], first)
        self.assertEqual(groups[0][1][0][0], "two")
        self.assertEqual(
            parse(groups[0][1][0][1][5:])[-1], Command(b"PC", f"0x{game_id(1):016X}".encode())
        )
        self.assertEqual(self.host.snapshot(), durable)
        self.assertEqual(self.events.disconnect("one", "one"), [])
        self.assertEqual(second.room.members, {})

    def test_profile_owner_never_falls_back_to_another_event(self):
        first, second = self.events.rooms
        self.admit(first)
        for binding, c in zip(self.events.rooms, self.configs):
            self.assertEqual(
                self.events.profile_file(binding.raw_owner, "b/g/TM0EventDataList"), c.data
            )
            members = self.events.profile_file(binding.raw_owner, "b/g/TM0EventMemberList")
            self.assertEqual(struct.unpack_from("<I", members, 4)[0], int(binding is first))
        for owner in (0, 0x100, 0x1001, 0x1102, -1, True, 1 << 41, None):
            for path in ("b/g/PTL", "b/g/TM0EventDataList", "b/g/TM0EventMemberList"):
                self.assertIsNone(self.events.profile_file(owner, path))
        self.assertEqual(
            self.events.profile_file(0, "b/g/TM0EventList"), self.host.file("b/g/TM0EventList")
        )

    def test_directory_tracks_live_presence_without_overstating_tables(self):
        first = self.events.rooms[0]
        self.admit(first)
        files = self.events.directory_files()
        record = files["b/g/RL000"][0x48 + 0xC8 :]
        self.assertEqual(struct.unpack_from("<I", record, 0x10)[0], 1)
        self.assertEqual(record[0x6A:0x70], b"AAAABA")
        self.assertEqual(record[0x28], ord("B"))
        self.assertEqual(struct.unpack_from("<II", files["b/g/ZL"], 0x48), (1, 2))

    def test_invalid_directory_names_and_routes_are_rejected(self):
        source = self.configs[0]
        bad = [
            replace(source, listing=replace(source.listing, key_region=b"R" * 27)),
            replace(source, listing=replace(source.listing, key_event=b"E" * 31)),
            replace(source, listing=replace(source.listing, key_event=b"Bad*Name")),
            replace(source, listing=replace(source.listing, key_event=b"Local Room")),
            replace(source, listing=replace(source.listing, scalar=0xFFFFFFFF)),
            replace(source, room_id=123),
            replace(source, room_id=game_id(0x100)),
            replace(source, card_channel=b"#localroom01"),
        ]
        for c in bad:
            with self.subTest(config=c), self.assertRaises(EventError):
                EventRooms(EventHost(self.economy, (c,)))
        duplicate = replace(self.configs[1], card_channel=source.card_channel.lower())
        with self.assertRaises(EventError):
            EventRooms(EventHost(self.economy, (source, duplicate)))

    def test_boundaries_and_custom_room_advertisements(self):
        last = config("last", (1 << 41) - 3, b"R" * 26, b"N" * 30)
        events = EventRooms(EventHost(self.economy, (last,)))
        self.assertLessEqual(len(events.rooms[0].room_channel), 12)
        self.assertEqual(struct.unpack_from("<I", events.rooms[0].room.snapshot(), 0x48)[0], 0)
        for kwargs in (
            {"room_channel": b"#" + b"R" * 12},
            {"service_records": (b"bad",)},
            {"table_id": game_id(0x100)},
            {"service_records": (source := self.configs[0].service_records()[0], source)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RoomService(**kwargs)


if __name__ == "__main__":
    unittest.main()
