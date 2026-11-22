"""Original computer-menu messages with two NPC seats; no sockets."""

import copy
import json
import unittest
from unittest.mock import patch

from tests import FIXTURES
from tmserver.game.models import BoardCard
from tmserver.game.errors import MatchError
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, packet
from tmserver.game.opponents import opponent, prepare_opponents
from tmserver.game.rules import CRTLCG

from tests.game.test_match_service import CARDS, ack, put, select


def choose(npcs=(1, 30), *, rules="0|0|0|0|2|0|0|1"):
    return packet(0x43, 6, len(npcs) + 1, f"@ComGame=/Rule={rules}/Com=" + "|".join(map(str, npcs)))


def menu(**kwargs):
    service = MatchService(computer_menu=True, seed=57, **kwargs)
    service.register("alice", character_id=3, nickname_id=0x123, points=222, collection_size=5)
    service.handle("alice", packet(0x41, 0x12, body="@GameENC=/NN=0000000000000123/HID=3"))
    service.handle("alice", packet(1, body="@Req=/NN=0000000000000123/HID=3"))
    return service


def finish(service):
    deliveries = []
    for _ in range(1000):
        if service.phase == "result":
            return deliveries
        if service.pending_work:
            deliveries += service.advance()
        elif service.phase == "battle":
            deliveries += service.handle(
                "alice",
                packet(0x43, 11, service.sequence, f"@BattleSelect=/B={service.targets[0]}"),
            )
        elif service.phase == "turn":
            deliveries += service.handle("alice", ack(0, service.sequence))
            if service.phase == "turn" and service.active == 0 and service._unlocked():
                deliveries += service.handle(
                    "alice", put(0, service.sequence, 0, service.legal_fields()[0])
                )
        else:
            raise AssertionError(f"unexpected phase: {service.phase}")
    raise AssertionError("computer match did not finish")


def unchanged_state(service):
    return copy.deepcopy(
        (
            service.snapshot(),
            service.players,
            service.rng.state,
            service.start_board,
            service.player_count,
            service.board_width,
            service.board_size,
            service.last_sequence,
            service.authorized_round_id,
            service.round_stake,
            service.ai_cards,
        )
    )


class ThreePlayerComputerTests(unittest.TestCase):
    def test_original_sender_vectors_select_distinct_NPC_records_and_hands(self):
        fixture = json.loads(
            (FIXTURES / "three_player_computer_sender.json").read_text()
        )
        self.assertEqual(fixture["entry"], "0x10106E30")
        self.assertEqual(len(fixture["image_sha256"]), 64)
        for vector in fixture["vectors"]:
            with self.subTest(npcs=vector["npc_ids"]):
                service = menu(npc_nickname_id=0x888, second_npc_nickname_id=0x999)
                expected_rng = CRTLCG(service.rng.state)
                expected = prepare_opponents(vector["npc_ids"], service.catalog, expected_rng)
                _, primary, subtype, argument, reserved = vector["builder_args"]
                self.assertEqual(
                    (primary, subtype, argument, reserved), (67, 6, len(expected) + 1, 0)
                )
                self.assertEqual(
                    service.handle("alice", packet(primary, subtype, argument, vector["body"])), []
                )
                self.assertEqual(service.player_count, vector["player_count"])
                self.assertEqual(service.rng.state, expected_rng.state)
                self.assertEqual(service.seat_capacity, 1)
                self.assertEqual(len({p.character_id for p in service.players}), len(expected) + 1)
                for player, prepared in zip(service.players[1:], expected, strict=True):
                    self.assertEqual(player.npc_id, prepared.opponent.npc_id)
                    self.assertEqual(player.name, prepared.opponent.name)
                    self.assertEqual(player.ai_personality, prepared.opponent.personality)
                    self.assertEqual(
                        [c.full() for c in player.selected],
                        ["|".join(map(str, c.full())) for c in prepared.hand],
                    )
                self.assertEqual(service.ai_cards, service.players[1].selected)
                self.assertEqual(service.npc_id, vector["npc_ids"][0])
                self.assertEqual(service.ai_personality, expected[0].opponent.personality)
                observed = service.observer_snapshot()
                self.assertEqual([p.npc_id for p in observed.players], [0, *vector["npc_ids"]])
                self.assertEqual(
                    [p.nickname_id for p in observed.players],
                    [0x123, 0x888, 0x999][: service.player_count],
                )

    def test_three_seat_game_has_fifteen_moves_and_uses_each_NPC_personality(self):
        service = menu()
        service.handle("alice", choose())
        selection = service.handle("alice", select())
        self.assertEqual(decode(selection[0].payload).sections["CardSelect"]["Ans"], "1|1|1")
        start = decode(selection[1].payload).sections["StartData"]
        self.assertEqual(len(start["F"].split("|")), 25)
        with patch.object(
            service.rules_backend, "choose_special_move", wraps=service.rules_backend.choose_special_move
        ) as chooser:
            out = finish(service)
        self.assertEqual(
            {(call.args[2], call.args[4]) for call in chooser.call_args_list},
            {(1, opponent(1).personality), (2, opponent(30).personality)},
        )
        moves = [decode(d.payload) for d in out if decode(d.payload).subtype == 10]
        self.assertEqual([m.argument for m in moves], list(range(15)))
        self.assertEqual([int(m.sections["PutCard"]["A"]) for m in moves], [0, 1, 2] * 5)
        self.assertEqual(sum(service._scores()), 15)
        self.assertEqual(service.sequence, 15)
        self.assertEqual(decode(out[-1].payload).sections["ResultData"]["P"], "3")
        cards = service.handle("alice", packet(0x43, 14, 15, "@Ready=/Ans=0"))
        fields = decode(cards[0].payload).sections["TCList"]
        self.assertEqual(fields["H"], "5|5|5")
        self.assertIn("D14", fields)
        self.assertTrue(all(len(d.payload) - 13 <= 447 for d in selection + out + cards))

    def test_rematch_regenerates_both_hands_and_reconfiguration_changes_geometry(self):
        service = menu()
        service.handle("alice", choose())
        service.handle("alice", select())
        finish(service)
        final = service.final_cards("alice")
        previous = [p.selected for p in service.players[1:]]
        continued = service.handle("alice", packet(0x43, 17, 0, "@Continue=/Ans=1/Conf=0"))
        self.assertEqual(decode(continued[0].payload).sections["Continue"]["Ans"], "1|1|1")
        self.assertEqual([p.npc_id for p in service.players[1:]], [1, 30])
        for old, new in zip(previous, service.players[1:], strict=True):
            self.assertNotEqual(old, new.selected)
        self.assertIs(service.final_cards("alice"), final)
        service.handle("alice", select(final.updated))
        finish(service)
        for round_id, npcs in ((3, (3,)), (4, (30, 1))):
            final = service.final_cards("alice")
            out = service.handle("alice", packet(0x43, 17, 0, "@Continue=/Ans=1/Conf=1"))
            self.assertEqual([decode(d.payload).subtype for d in out], [4])
            service.handle("alice", choose(npcs))
            n = len(npcs) + 1
            self.assertEqual(
                (
                    service.player_count,
                    service.board_width,
                    service.board_size,
                    service.last_sequence,
                ),
                (n, n + 2, (n + 2) ** 2, 5 * n - 1),
            )
            self.assertEqual([p.npc_id for p in service.players[1:]], list(npcs))
            self.assertIs(service.final_cards("alice"), final)
            service.handle("alice", select(final.updated))
            self.assertIsNone(service.final_cards("alice"))
            finish(service)
            self.assertEqual(service.final_cards("alice").round_id, round_id)

    def test_invalid_choices_and_mismatched_explicit_boards_do_not_debit_draw_or_mutate(self):
        authorizations = []

        def authorize(service, round_id, stake, **kwargs):
            authorizations.append((round_id, stake, kwargs))
            return {0: service.players[0].balance - stake}

        invalid = [
            choose((1, 1)),
            choose((0, 3)),
            choose((1, 31)),
            packet(0x43, 6, 3, "@ComGame=/Rule=0|0|0|0|2|0|0|1/Com=1"),
            packet(0x43, 6, 2, "@ComGame=/Rule=0|0|0|0|2|0|0|1/Com=1|3"),
            choose((1, 3, 30)),
        ]
        service = menu(round_authorizer=authorize)
        before = unchanged_state(service)
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(MatchError):
                    service.handle("alice", payload)
                self.assertEqual(unchanged_state(service), before)
        for initial, target in ((2, (1, 3)), (3, (1,))):
            service = menu(
                player_count=initial,
                start_fields=[0] * (initial + 2) ** 2,
                round_authorizer=authorize,
                allow_wagers=True,
                payout_provider=lambda *_: {0: 0},
            )
            before = unchanged_state(service)
            with self.assertRaisesRegex(MatchError, "explicitly configured board"):
                service.handle("alice", choose(target, rules="100|0|0|0|2|0|0|1"))
            self.assertEqual(unchanged_state(service), before)
        self.assertEqual(authorizations, [])

    def test_resizing_authorizer_receives_target_count_before_any_state_change(self):
        calls = []
        fail = True

        def authorize(service, round_id, stake, **kwargs):
            calls.append((service.player_count, round_id, stake, kwargs, service.rng.state))
            if fail:
                raise OSError("synthetic durable save failure")
            return {0: service.players[0].balance - stake}

        service = menu(
            round_authorizer=authorize, allow_wagers=True, payout_provider=lambda *_: {0: 0}
        )
        before = unchanged_state(service)
        payload = choose(rules="100|0|0|0|2|0|0|1")
        with self.assertRaises(OSError):
            service.handle("alice", payload)
        self.assertEqual(unchanged_state(service), before)
        fail = False
        service.handle("alice", payload)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1][:4], (2, 1, 100, {"player_count": 3}))
        self.assertEqual(service.players[0].balance, 9900)
        service.handle("alice", select())
        finish(service)
        service.handle("alice", packet(0x43, 17, 0, "@Continue=/Ans=1/Conf=0"))
        self.assertEqual(calls[-1][:4], (3, 2, 100, {}))

    def test_generated_and_explicit_three_player_special_boards(self):
        fields = [0] * 25
        fields[24] = 3
        for kwargs in ({}, {"player_count": 3, "start_fields": fields}):
            with self.subTest(explicit=bool(kwargs)):
                service = menu(**kwargs)
                service.handle("alice", choose(rules="0|1|0|0|2|0|0|1"))
                out = service.handle("alice", select())
                self.assertEqual(
                    len(decode(out[1].payload).sections["StartData"]["F"].split("|")), 25
                )
                if kwargs:
                    self.assertEqual(service.start_board.fields, tuple(fields))
                self.assertLessEqual(service.start_board.blocks, 10)
                finish(service)
                self.assertEqual(service.completed_matches, 1)

    def test_original_fixed_computer_and_human_modes_keep_their_seat_rules(self):
        with self.assertRaises(ValueError):
            MatchService(player_count=3)
        with self.assertRaises(ValueError):
            MatchService(mode="versus", computer_menu=True)
        for kwargs in ({"second_npc_nickname_id": 0}, {"second_npc_nickname_id": 0x200}):
            with self.assertRaises(ValueError):
                menu(**kwargs)
        service = MatchService(npc_nickname_id=0x201)
        service.register("alice")
        service.begin()
        self.assertEqual(len(service.players), 2)
        self.assertEqual(service.players[1].nickname_id, 0x201)
        self.assertEqual(service.players[1].npc_id, 0)
        self.assertIsNone(service.players[1].ai_personality)


class ThreePlayerComputerPrizeTests(unittest.TestCase):
    def result_fixture(self, owners):
        service = menu(card_prizes=True)
        service.handle("alice", choose((1, 3)))
        for player in service.players[1:]:
            service._choose_cards(player, CARDS)
        service.handle("alice", select())
        for seat, hand in enumerate(owners):
            for slot, owner in enumerate(hand):
                service.board[seat * 5 + slot] = BoardCard(CARDS[slot], owner, seat, slot)
        service.sequence = service.last_sequence
        service.source = None
        return service, service._end_turn()

    def test_CPU_prize_after_human_choice_finishes_behind_durable_commit_barrier(self):
        # Human and NPC2 tie for first; both captured multiple NPC1 cards.
        service, out = self.result_fixture([[0] * 5, [0, 0, 2, 2, 1], [2] * 5])
        self.assertEqual([decode(d.payload).subtype for d in out], [13])
        self.assertEqual((service.prize_choice.winner, service.prize_choice.loser), (0, 1))
        out = service.handle("alice", packet(0x43, 14, 15, "@Ready=/Ans=0"))
        self.assertEqual([decode(d.payload).subtype for d in out], [26])
        service.draw = lambda: 0
        self.assertEqual(service.handle("alice", packet(0x43, 15, 0, "@GetSelect=/H=0")), [])
        self.assertIsNone(service.prize_choice)
        self.assertTrue(service.prize_commit_required)
        settlement = service.final_prizes().settlement
        self.assertEqual([(a.winner, a.loser) for a in settlement.transfers], [(0, 1), (2, 1)])
        with self.assertRaises(MatchError):
            service.handle("alice", packet(0x43, 17, 0, "@Continue=/Ans=1/Conf=0"))
        released = service.confirm_prize_commit(1)
        self.assertEqual([decode(d.payload).argument for d in released], [0, 2])
        self.assertEqual([decode(d.payload).subtype for d in released], [15, 15])
        self.assertEqual(service.confirm_prize_commit(1), [])
        self.assertEqual(service.handle("alice", packet(0x43, 15, 0, "@GetSelect=/H=0")), [])
        self.assertIs(service.final_prizes().settlement, settlement)

    def test_multiple_CPU_choices_are_queued_after_TClist_before_human_continues(self):
        # Both CPU winners have multiple captured cards in the human hand.
        service, out = self.result_fixture([[1, 1, 2, 2, 0], [1] * 5, [2] * 5])
        self.assertEqual(out, [])
        self.assertIsNone(service.prize_choice)
        settlement = service.final_prizes().settlement
        self.assertEqual([(a.winner, a.loser) for a in settlement.transfers], [(1, 0), (2, 0)])
        self.assertEqual([decode(d.payload).subtype for d in service.confirm_prize_commit(1)], [13])
        out = service.handle("alice", packet(0x43, 14, 15, "@Ready=/Ans=0"))
        self.assertEqual([decode(d.payload).subtype for d in out], [26, 15, 15])
        self.assertEqual([decode(d.payload).argument for d in out[1:]], [1, 2])
        service.handle("alice", packet(0x43, 17, 0, "@Continue=/Ans=1/Conf=0"))
        self.assertEqual((service.phase, len(service.players)), ("selection", 3))


if __name__ == "__main__":
    unittest.main()
