"""Explicit event configuration reaches the same validated wire/file builders."""

import copy
import unittest
from dataclasses import replace

from tmserver.economy.service import EconomyService
from tmserver.events.config import parse_event_configs, parse_event_setup
from tmserver.events.matches import EventMatchRound, EventParticipant
from tmserver.events.models import EventTableConfig
from tmserver.events.rooms import EventRooms
from tmserver.events.service import EventHost
from tmserver.events.models import PrizeDisplay
from tmserver.events.packets import event_data
from tmserver.protocol.identities import game_id

from tests.events.test_event_matches import CARDS, finish

DOCUMENT = {
    "events": [
        {
            "id": "festival",
            "listing": {
                "key_region": "Local Lobby",
                "key_event": "Festival",
                "description": "Local fixture",
                "scalar": 4096,
            },
            "data": {
                "classes": ["First", "Next"],
                "steps_per_class": 4,
                "missions": {"0": 3},
                "prizes": [{"cards": [5, 9], "money": 250, "description": "Local award display"}],
            },
            "start": 100,
            "end": 500,
            "room_raw": 4096,
            "init_raw": 4097,
            "card_raw": 4098,
            "card_service_enabled": True,
        }
    ]
}


class EventConfigurationTests(unittest.TestCase):
    def test_unplayable_boards_fail_during_configuration(self):
        for players in (2, 3):
            size = (players + 2) ** 2
            # Blocks, arrow tiles and spinners all consume placement cells.
            for tile in (1, 2, 6, 8, 9, 16, 17):
                blocked = size - 5 * players + 1
                fields = [tile] * blocked + [0] * (size - blocked)
                document = copy.deepcopy(DOCUMENT)
                document["events"][0]["match_enabled"] = True
                document["tables"] = [
                    dict(
                        event_id="festival",
                        table_raw=4099,
                        channel="#EVTABLE001",
                        player_count=players,
                        start_fields=fields,
                    )
                ]
                with (
                    self.subTest(players=players, tile=tile),
                    self.assertRaisesRegex(ValueError, "five placement cells per player"),
                ):
                    parse_event_setup(document)

    def test_direct_table_configuration_checks_board_and_seed_domains(self):
        table = EventTableConfig("festival", game_id(4099), b"#EVTABLE001")
        for fields in ([0] * 25, [0] * 15, [18] + [0] * 15, [True] + [0] * 15):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(table, start_fields=fields)
        for seed in (-1, 0x100000000, True, 1.5):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                replace(table, seed=seed)

    def test_exact_capacity_boards_are_frozen_and_complete_rounds(self):
        for players, cpu in ((2, False), (3, False), (2, True)):
            with self.subTest(players=players, cpu=cpu):
                blocked = (players + 2) ** 2 - 5 * players
                fields = [1] * blocked + [0] * (5 * players)
                original = tuple(fields)
                table = EventTableConfig(
                    "festival",
                    game_id(4099),
                    b"#EVTABLE001",
                    player_count=players,
                    cpu_cards=CARDS if cpu else None,
                    start_fields=fields,
                    seed=0xFFFFFFFF,
                )
                fields[:] = [1] * len(fields)
                self.assertEqual(table.start_fields, original)
                participants = tuple(
                    EventParticipant(str(i), i + 1, f"Player{i}".encode(), game_id(i + 1), CARDS)
                    for i in range(1 if cpu else players)
                )
                round = EventMatchRound(
                    participants,
                    cpu_cards=table.cpu_cards,
                    start_fields=table.start_fields,
                    seed=table.seed,
                )
                round.begin()
                finish(round)
                self.assertEqual(round.sequence, 5 * players)
                self.assertEqual(sum(round.outcome.scores), 5 * players)
                self.assertFalse(round.outcome.aborted)

    def test_placeable_effect_tiles_do_not_reduce_board_capacity(self):
        for players in (2, 3):
            blocked = (players + 2) ** 2 - 5 * players
            for effect in (3, 4, 5):
                with self.subTest(players=players, effect=effect):
                    fields = (1,) * blocked + (effect,) * (5 * players)
                    table = EventTableConfig(
                        "festival",
                        game_id(4099),
                        b"#EVTABLE001",
                        player_count=players,
                        start_fields=fields,
                        seed=0,
                    )
                    self.assertEqual(table.start_fields, fields)

    def test_distinct_tables_can_share_one_enabled_event(self):
        item = copy.deepcopy(DOCUMENT)
        item["events"][0]["match_enabled"] = True
        item["tables"] = [
            dict(event_id="festival", table_raw=4099 + i, channel=f"#EVTABLE00{i + 1}")
            for i in range(2)
        ]
        _, tables, _ = parse_event_setup(item)
        self.assertEqual([t.event_id for t in tables], ["festival", "festival"])
        self.assertEqual(len({t.table_id for t in tables}), 2)

    def test_match_setup_requires_an_explicit_unique_table_and_policy(self):
        item = copy.deepcopy(DOCUMENT)
        item["events"][0]["match_enabled"] = True
        with self.assertRaises(ValueError):
            parse_event_setup(item)
        table = dict(event_id="festival", table_raw=4099, channel="#EVTABLE001", player_count=3)
        item["tables"] = [table]
        item["progress_policy"] = "wins"
        events, tables, policy = parse_event_setup(item)
        self.assertTrue(events[0].match_enabled)
        self.assertEqual(
            (tables[0].table_id, tables[0].player_count, policy), (game_id(4099), 3, "wins")
        )
        for key, value in (
            ("table_raw", True),
            ("seed", -1),
            ("cpu_cards", [[1] * 7] * 5),
            ("start_fields", [0] * 16),
        ):
            invalid = copy.deepcopy(item)
            invalid["tables"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_event_setup(invalid)
        item["tables"] *= 2
        with self.assertRaises(ValueError):
            parse_event_setup(item)

    def test_json_values_project_without_assigning_opaque_fields(self):
        configs = parse_event_configs(DOCUMENT)
        c = configs[0]
        self.assertEqual(c.room_id, game_id(4096))
        self.assertEqual(
            c.data,
            event_data(
                classes=(b"First", b"Next"),
                steps_per_class=4,
                missions={0: 3},
                prizes=(PrizeDisplay((5, 9), 250, b"Local award display"),),
            ),
        )
        rooms = EventRooms(EventHost(EconomyService(), configs))
        self.assertEqual(len(rooms.rooms), 1)
        self.assertEqual(len(c.service_records()), 1)
        self.assertEqual(c.service_records()[0][0x28], ord("F"))
        self.assertEqual(parse_event_configs({"events": []}), ())

    def test_malformed_unknown_and_unrepresentable_settings_fail(self):
        invalid = []
        for field, value in (
            ("room_raw", True),
            ("room_raw", 1 << 41),
            ("surprise", 1),
            ("init_channel", "long channel with spaces"),
        ):
            item = copy.deepcopy(DOCUMENT)
            item["events"][0][field] = value
            invalid.append(item)
        item = copy.deepcopy(DOCUMENT)
        item["events"][0]["data"]["missions"] = {"8": 2}
        invalid.append(item)
        item = copy.deepcopy(DOCUMENT)
        item["events"][0]["listing"]["key_event"] = "雪"
        invalid.append(item)
        invalid.extend(({"events": "text"}, {"events": [], "unknown": 1}, []))
        for document in invalid:
            with self.subTest(document=document), self.assertRaises((ValueError, TypeError)):
                parse_event_configs(document)


if __name__ == "__main__":
    unittest.main()
