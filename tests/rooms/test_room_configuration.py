"""The JSON boundary stays strict while preserving the dataclass configuration API."""

import copy
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from tmserver.rooms.config import OrdinaryRoomConfig, OrdinaryTableConfig, load_ordinary_rooms
from tmserver.rooms.rules import RoomRules


DOCUMENT = {
    "rooms": [
        {
            "name": "Mixed",
            "room_raw": 257,
            "table_raw": 513,
            "room_channel": "#MIXED",
            "table_channel": "#HUMAN",
            "players": "auto",
            "rules": {"gs": 0},
            "tables": [{"table_raw": 514, "table_channel": "#CPU", "mode": "computer"}],
        }
    ],
}


class RoomConfigurationTests(unittest.TestCase):
    def load(self, document):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rooms.json"
            path.write_text(json.dumps(document), encoding="utf8")
            return load_ordinary_rooms(path)

    def test_json_shorthand_nested_tables_and_dataclass_api(self):
        (room,) = self.load(DOCUMENT)
        self.assertEqual(
            room,
            OrdinaryRoomConfig(
                b"Mixed",
                257,
                513,
                b"#MIXED",
                b"#HUMAN",
                players=3,
                flexible_seats=True,
                rules=RoomRules(gs=0),
                tables=(OrdinaryTableConfig(514, b"#CPU", "computer"),),
            ),
        )
        self.assertEqual(replace(room, name=b"Renamed").name, b"Renamed")
        with self.assertRaises(FrozenInstanceError):
            room.name = b"Changed"
        document = copy.deepcopy(DOCUMENT)
        document["rooms"][0]["tables"][0].update(mode="versus", players="auto")
        table = self.load(document)[0].tables[0]
        self.assertEqual((table.players, table.flexible_seats), (3, True))

    def test_json_rejects_coercion_unknown_fields_and_internal_flags(self):
        cases = {
            "room": [
                ("room_raw", True),
                ("table_raw", "513"),
                ("players", 2.0),
                ("name", 123),
                ("name", "Café"),
                ("flexible_seats", True),
                ("surprise", 1),
                ("tables", {}),
                ("rules", None),
            ],
            "table": [
                ("table_raw", False),
                ("table_raw", 1 << 41),
                ("players", "2"),
                ("table_channel", None),
                ("flexible_seats", True),
                ("surprise", 1),
            ],
            "rules": [("gs", False), ("bm", "100"), ("tl", 6), ("surprise", 1)],
        }
        for target, values in cases.items():
            for key, value in values:
                document = copy.deepcopy(DOCUMENT)
                room = document["rooms"][0]
                row = {"room": room, "table": room["tables"][0], "rules": room["rules"]}[target]
                row[key] = value
                with (
                    self.subTest(target=target, key=key, value=value),
                    self.assertRaises(ValueError),
                ):
                    self.load(document)
        for document in ({}, [], {"rooms": {}}, {"rooms": [], "surprise": 1}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.load(document)

    def test_python_callers_remain_strict_and_do_not_accept_json_shorthand(self):
        (room,) = self.load(DOCUMENT)
        for values in (
            {"players": "auto"},
            {"room_raw": True},
            {"table_raw": 513.0},
            {"name": "Mixed"},
            {"tables": list(room.tables)},
            {"rules": {"gs": 0}},
            {"flexible_seats": 1},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(room, **values)
        for values in ({"table_raw": True}, {"players": "auto"}, {"flexible_seats": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(room.tables[0], **values)


if __name__ == "__main__":
    unittest.main()
