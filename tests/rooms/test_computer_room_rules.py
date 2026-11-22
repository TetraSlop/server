"""Configured Computer rooms must be representable by the original menu."""

import json
import tempfile
import unittest
from pathlib import Path

from tests import FIXTURES
from tests.game import test_computer_menu_integration as computer
from tests.protocol.test_admission_identity import native_identity
from tmserver.game.packets import decode, packet
from tmserver.rooms.config import OrdinaryRoomConfig, OrdinaryTableConfig, load_ordinary_rooms
from tmserver.rooms.rules import RoomRules
from tests.support.profiles import read_preferences
from tests.rooms.harness import serve


def room(*, mode="versus", rules=None, tables=()):
    return OrdinaryRoomConfig(
        b"Rule Room", 0x101, 0x201, b"#RULES", b"#RULETABLE", mode=mode, rules=rules, tables=tables
    )


class ComputerRoomConfiguration(unittest.TestCase):
    def test_fixed_computer_tables_reject_unrepresentable_departure_rules(self):
        cpu = OrdinaryTableConfig(0x202, b"#RULECPU", mode="computer")
        for gs in (1, 2):
            for options in ({"mode": "computer"}, {"tables": (cpu,)}):
                with (
                    self.subTest(gs=gs, options=options),
                    self.assertRaisesRegex(ValueError, "Computer.*gs=0"),
                ):
                    room(rules=RoomRules(gs=gs), **options)
        for options in ({"mode": "computer"}, {"tables": (cpu,)}):
            self.assertIsNone(room(**options).rules)
            self.assertEqual(room(rules=RoomRules(gs=0), **options).rules.gs, 0)
        for gs in (0, 1, 2):
            self.assertEqual(room(rules=RoomRules(gs=gs)).rules.gs, gs)

    def test_json_fixed_cpu_rules_require_explicit_native_departure_value(self):
        config = {
            "name": "Fixed CPU",
            "room_raw": 257,
            "table_raw": 513,
            "room_channel": "#FIXEDCPU",
            "table_channel": "#CPUTABLE",
            "mode": "computer",
            "rules": {"st": 1},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rooms.json"
            path.write_text(json.dumps({"rooms": [config]}))
            with self.assertRaisesRegex(ValueError, "Computer.*gs=0"):
                load_ordinary_rooms(path)
            config["rules"]["gs"] = 0
            path.write_text(json.dumps({"rooms": [config]}))
            self.assertEqual(load_ordinary_rooms(path)[0].rules, RoomRules(st=1, gs=0))


class NativeComputerRoomIntegration(unittest.IsolatedAsyncioTestCase):
    request = computer.ComputerMenuIntegrationTests.request

    async def asyncSetUp(self):
        evidence = json.loads(
            (FIXTURES / "computer_room_rules.json").read_text()
        )
        self.case = next(c for c in evidence["cases"] if c["name"] == "fixed_gs0_saved2")
        self.rules = RoomRules(**self.case["preset"])
        (self.client,) = await serve(
            self, clients=1, ordinary_rooms=(room(mode="computer", rules=self.rules),)
        )
        self.table = self.server.ordinary_tables[1]
        self.key = self.client.nick.decode()
        with self.server.commerce.edit_account(self.key) as economy:
            economy.save_table_preferences(self.key, game=self.case["saved_tet"])

    async def test_original_computer_start_reaches_selection_with_fixed_rules(self):
        view = self.server.economy.account_view(self.key)
        await self.request(
            self.table.table_raw, packet(0x41, 0x12, body=native_identity(view, "GameENC"))
        )
        replies = await self.request(
            self.table.table_raw, packet(1, body=native_identity(view, "Req"))
        )
        received = next(decode(r) for r in replies if b"@ComGameInit=" in r)
        original = decode(self.case["production_init"].encode("ascii"))
        self.assertEqual(
            received.sections["ComGameInit"]["R"], original.sections["ComGameInit"]["R"]
        )
        self.assertEqual(
            await self.request(self.table.table_raw, self.case["initial_start"].encode("ascii")),
            [],
        )
        self.assertEqual(self.table.match.phase, "selection")
        self.assertEqual(self.table.match.game_rules, self.rules.as_dict())
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(view.cards[:5])
        )
        replies = await self.request(self.table.table_raw, packet(0x43, 7, 0, body))
        start = next(decode(r).sections["StartData"] for r in replies if b"@StartData=" in r)
        self.assertEqual(len(start["F"].split("|")), 16)
        self.assertEqual(self.table.match.phase, "turn")
        self.assertEqual(self.table.room.table_metadata[30:37], self.rules.packed())
        self.assertEqual(
            read_preferences(self.server.economy.profile_bytes(self.key))["Tet"],
            self.case["saved_tet"],
        )


if __name__ == "__main__":
    unittest.main()
