"""Configured room rules stay coherent through native settings and gameplay."""

import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import Mock, patch

from tests.game.test_match_service import NATIVE_SETTINGS, select
from tests.game.test_three_player_computer import finish, menu, unchanged_state
from tmserver.game.models import DEFAULT_GAME_RULES, DEFAULT_TABLE_RULES
from tmserver.game.errors import MatchError
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, packet
from tmserver.game.boards import generate_board
from tmserver.rooms.rules import RoomRules


def settings(rules, **table):
    return (
        "@Tet="
        + "".join(f"/{k}={v}" for k, v in rules.as_dict().items())
        + "@Tab="
        + "".join(f"/{k}={v}" for k, v in dict(DEFAULT_TABLE_RULES, **table).items())
    )


def computer(rules, *, count=2, observation=0):
    values = [getattr(rules, k) for k in ("bm", "st", "cb", "ca", "tl", "du")]
    values.append(observation)
    if count:
        values.append(rules.gs)
    body = "@ComGame=/Rule=" + "|".join(map(str, values))
    if count:
        body += "/Com=" + "|".join(map(str, (1, 30)[: count - 1]))
    return packet(0x43, 6, count, body)


class RoomRulesValueTests(unittest.TestCase):
    def test_defaults_are_immutable_and_match_existing_engine(self):
        rules = RoomRules()
        self.assertEqual(rules.as_dict(), DEFAULT_GAME_RULES)
        self.assertEqual(rules.packed(), b"AAAAACB")
        changed = rules.as_dict()
        changed["st"] = 1
        self.assertEqual(rules.st, 0)
        with self.assertRaises(FrozenInstanceError):
            rules.st = 1

    def test_native_packed_rule_vectors(self):
        # Original1003BD30 fixtures in DIRECTORY_UI_CONTRACT.md: no native launch.
        cases = (
            (RoomRules(gs=0, tl=0), b"AAAAAAA"),
            (RoomRules(gs=1, tl=0), b"AAAAAAB"),
            (RoomRules(gs=2, tl=0), b"AAAAAAC"),
            (RoomRules(du=1, gs=0, tl=0), b"AAAABAA"),
            (RoomRules(st=1, gs=0, tl=0), b"AAAACAA"),
            (RoomRules(cb=1, gs=0, tl=0), b"AAAAEAA"),
            (RoomRules(ca=1, gs=0, tl=0), b"AAAAIAA"),
            (RoomRules(bm=2000, du=1, st=1, cb=1, ca=1, tl=3), b"AHNAPDB"),
        )
        for rules, encoded in cases:
            with self.subTest(rules=rules):
                self.assertEqual(rules.packed(), encoded)

    def test_invalid_known_fields_and_unknown_fields_are_rejected(self):
        limits = dict(bm=2000, du=1, st=1, cb=1, ca=1, gs=2, tl=5)
        for key, maximum in limits.items():
            for value in (-1, maximum + 1, True, 0.0, "0", None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    RoomRules(**{key: value})
        with self.assertRaises(TypeError):
            RoomRules(unknown=0)


class FixedRoomMatchTests(unittest.TestCase):
    def test_fresh_engines_queries_init_and_generated_board_share_the_preset(self):
        rules = RoomRules(st=1, cb=1, ca=1, gs=2, tl=4)
        for _ in range(2):  # OrdinaryTable reset constructs another engine from this preset.
            service = MatchService(mode="versus", room_rules=rules)
            self.assertEqual(service.game_rules, rules.as_dict())
            self.assertEqual(service.special_flags, dict(st=1, cb=1, ca=1))
            service.register("alice", character_id=1)
            service.register("bob", character_id=2)
            query = decode(service.handle("alice", packet(0x12, body="@Rule="))[0].payload)
            self.assertEqual({k: int(v) for k, v in query.sections["Tet"].items()}, rules.as_dict())
            service.handle("alice", packet(0x14, body=settings(rules)))
            ack = decode(service.playack("alice"))
            self.assertEqual({k: int(v) for k, v in ack.sections["Tet"].items()}, rules.as_dict())
            init = decode(service.begin()[0].payload)
            self.assertEqual(init.sections["VsGameInit"]["R"], "0|1|1|1|4|0|2")
            with patch("tmserver.game.match.generate_board", wraps=generate_board) as generated:
                service.handle("alice", select())
                service.handle("bob", select(seat=1))
            generated.assert_called_once()
            self.assertEqual(generated.call_args.kwargs, dict(st=1, cb=1, ca=1))

    def test_incompatible_economic_or_explicit_board_configuration_is_rejected(self):
        for value in ({}, DEFAULT_GAME_RULES, "fixed"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "RoomRules"):
                MatchService(room_rules=value)
        for rules in (RoomRules(bm=1), RoomRules(du=1)):
            with self.assertRaisesRegex(ValueError, "economic"):
                MatchService(room_rules=rules)
        fields = (3, 6, 9) + (0,) * 13
        with self.assertRaisesRegex(ValueError, "explicitly configured board"):
            MatchService(room_rules=RoomRules(), start_fields=fields)
        with self.assertRaisesRegex(ValueError, "explicitly configured board"):
            MatchService(room_rules=RoomRules(st=1), start_fields=(0,) * 16)
        service = MatchService(room_rules=RoomRules(st=1, cb=1, ca=1), start_fields=fields)
        self.assertEqual(service.special_flags, dict(st=1, cb=1, ca=1))

    def test_conflicting_apply_is_atomic_but_table_settings_remain_editable(self):
        rules = RoomRules(st=1, gs=2, tl=4)
        service = MatchService(mode="versus", room_rules=rules)
        service.register("alice")
        service.handle("alice", packet(0x14, body=settings(rules)))
        before = unchanged_state(service)
        for key, value in dict(bm=1, du=1, st=0, cb=1, ca=1, gs=1, tl=2).items():
            with self.subTest(key=key), self.assertRaisesRegex(MatchError, "room rules"):
                service.handle("alice", packet(0x14, body=settings(replace(rules, **{key: value}))))
            self.assertEqual(unchanged_state(service), before)
        service.handle(
            "alice", packet(0x14, body=settings(rules, **{"in": 2, "pa": 1, "pw": "4142"}))
        )
        self.assertEqual(service.game_rules, rules.as_dict())
        self.assertEqual((service.table_rules["in"], service.table_rules["pw"]), (2, "4142"))

    def test_saved_defaults_do_not_override_fixed_rules_when_applied_later(self):
        rules = RoomRules(st=1, tl=3)
        saved = RoomRules(cb=1, tl=5)
        service = MatchService(room_rules=rules)
        service.register("alice")
        service.handle("alice", packet(0x24, body="@Save=/Tbl=1/Game=1" + settings(saved)))
        self.assertEqual(service.saved_preferences["alice"]["Tet"], saved.as_dict())
        self.assertEqual(service.game_rules, rules.as_dict())
        self.assertEqual(service.settings_revision, 0)
        before = unchanged_state(service)
        with self.assertRaisesRegex(MatchError, "room rules"):
            service.handle("alice", packet(0x14, body=settings(saved)))
        self.assertEqual(unchanged_state(service), before)

    def test_unconfigured_settings_behavior_is_unchanged(self):
        service = MatchService()
        service.register("alice")
        service.handle("alice", packet(0x14, body=NATIVE_SETTINGS))
        self.assertEqual((service.game_rules["gs"], service.game_rules["tl"]), (0, 0))

    def test_computer_conflicts_reject_before_any_authorization_randomness_or_mutation(self):
        rules = RoomRules(bm=100, st=1, cb=1, ca=1, gs=0, tl=3)
        authorize = Mock(
            side_effect=lambda service, *_args, **_kw: {0: service.players[0].balance - 100}
        )
        service = menu(
            room_rules=rules,
            allow_wagers=True,
            round_authorizer=authorize,
            payout_provider=lambda *_: {0: 0},
        )
        before = unchanged_state(service)
        for count in (0, 2, 3):
            for key, value in dict(bm=99, du=1, st=0, cb=0, ca=0, gs=1, tl=2).items():
                if count == 0 and key == "gs":
                    continue  # The seven-field menu preference packet has no departure field.
                with (
                    self.subTest(count=count, key=key),
                    self.assertRaisesRegex(MatchError, "room rules"),
                ):
                    service.handle("alice", computer(replace(rules, **{key: value}), count=count))
                self.assertEqual(unchanged_state(service), before)
        authorize.assert_not_called()
        service.handle("alice", computer(rules, count=0, observation=2))
        self.assertEqual((service.phase, service.table_rules["in"]), ("computer_menu", 2))
        authorize.assert_not_called()
        service.handle("alice", computer(rules, count=3, observation=2))
        self.assertEqual(service.game_rules, rules.as_dict())
        self.assertEqual((service.phase, service.player_count), ("selection", 3))
        authorize.assert_called_once()

    def test_double_up_reconfiguration_accepts_only_the_server_selected_next_stake(self):
        rules = RoomRules(bm=100, du=1, gs=0)
        stakes = []

        def authorize(service, _round, stake, **_kwargs):
            stakes.append(stake)
            return {0: service.players[0].balance - stake}

        service = menu(
            room_rules=rules,
            allow_wagers=True,
            round_authorizer=authorize,
            payout_provider=lambda *_: {0: 0},
        )
        service.handle("alice", computer(rules))
        service.handle("alice", select())
        finish(service)
        service.handle("alice", packet(0x43, 17, 0, "@Continue=/Ans=1/Conf=1"))
        self.assertEqual(service.game_rules["bm"], 200)
        before = unchanged_state(service)
        with self.assertRaisesRegex(MatchError, "room rules"):
            service.handle("alice", computer(rules))
        self.assertEqual(unchanged_state(service), before)
        service.handle("alice", computer(replace(rules, bm=200)))
        self.assertEqual(stakes, [100, 200])
        self.assertEqual(rules.bm, 100)  # The directory continues to describe the starting stake.


if __name__ == "__main__":
    unittest.main()
