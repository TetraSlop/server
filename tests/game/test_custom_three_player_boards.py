"""Explicit25-cell layouts through original consumers and encrypted matches."""

import contextlib
import io
import unittest
from dataclasses import replace

from tests.events import test_event_matches_integration as events
from tests.game import test_generated_boards_integration as generated
from tests.support.memory_store import DATABASE
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, packet
from tmserver.game.rules import CRTLCG
from tmserver.game.special_rules import start_board
from tmserver.cli.serve import arguments


def layout(entries):
    fields = [0] * 25
    for index, tile in entries.items():
        fields[index] = tile
    return tuple(fields)


LAYOUTS = (
    layout({0: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8, 11: 9, 12: 10, 24: 17}),
    layout({0: 11, 1: 12, 2: 13, 3: 14, 4: 15, 5: 16, 10: 3, 15: 4, 20: 5, 24: 17}),
)


class CustomThreePlayerBoards(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = generated.GeneratedBoardsIntegrationTests.asyncSetUp
    asyncTearDown = generated.GeneratedBoardsIntegrationTests.asyncTearDown
    scenario = generated.GeneratedBoardsIntegrationTests.scenario
    flush = generated.GeneratedBoardsIntegrationTests.flush
    send = generated.GeneratedBoardsIntegrationTests.send
    admit = generated.GeneratedBoardsIntegrationTests.admit
    finish_round = generated.GeneratedBoardsIntegrationTests.finish_round
    assert_persisted_result = generated.GeneratedBoardsIntegrationTests.assert_persisted_result

    async def selection(self, fields):
        match = self.server.default_table.match
        rng = CRTLCG(match.rng.state)
        shared = [rng() & 255 for _ in range(8)]
        for seat, key in enumerate(self.keys):
            cards = self.server.economy.inventory(key)[:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
            )
            replies = await self.send(seat, packet(0x43, 7, seat, body))
        initial = start_board(fields)
        for messages in replies:
            starts = [decode(p).sections["StartData"] for p in messages if b"@StartData=" in p]
            turns = [decode(p).sections["TurnData"] for p in messages if b"@TurnData=" in p]
            self.assertEqual(len(starts), 1)
            self.assertEqual(len(turns), 1)
            self.assertEqual(tuple(map(int, starts[0]["F"].split("|"))), fields)
            self.assertEqual(
                (int(starts[0]["B"]), int(starts[0]["E"])), (initial.blocks, initial.effects)
            )
            self.assertEqual(list(map(int, turns[0]["R"].split("|"))), shared)
        self.assertEqual(match.rng.state, rng.state, "explicit board consumes no generator draws")

    async def test_all_non_cbc_tiles_complete_paid_rounds_and_rematches(self):
        for fields in LAYOUTS:
            async with self.scenario(3, money=True, start_fields=fields):
                flags = tuple(
                    self.server.default_table.match.special_flags[k] for k in ("st", "cb", "ca")
                )
                await self.admit(flags, stake=137)
                for number in (1, 2):
                    await self.selection(fields)
                    await self.finish_round(seed=number + 4)
                    await self.assert_persisted_result(count=number)
                    self.assertEqual(sum(self.server.economy.balance(k) for k in self.keys), 30000)
                    for seat in range(3):
                        await self.send(
                            seat,
                            packet(
                                0x43,
                                17,
                                seat,
                                f"@Continue=/Ans={1 if number==1 else 2}/PR=0/Conf=0",
                            ),
                        )
                self.assertEqual(self.server.default_table.match.phase, "finished")
                self.assertFalse(self.server.default_table.match_reservations)


class CustomEventBoard(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = events.EventMatchIntegrationTests.asyncSetUp
    asyncTearDown = events.EventMatchIntegrationTests.asyncTearDown
    start = events.EventMatchIntegrationTests.start
    drain = events.EventMatchIntegrationTests.drain
    send = events.EventMatchIntegrationTests.send
    identity = events.EventMatchIntegrationTests.identity
    enter_selection = events.EventMatchIntegrationTests.enter_selection
    admit = events.EventMatchIntegrationTests.admit
    engine = events.EventMatchIntegrationTests.engine
    play = events.EventMatchIntegrationTests.play
    complete = events.EventMatchIntegrationTests.complete

    async def test_configured_three_player_event_layout_round_and_restart(self):
        self.table = replace(self.table, player_count=3, start_fields=LAYOUTS[0])
        await self.complete(3, "none")
        for box in self.inbox:
            starts = [decode(p).sections["StartData"] for _, p in box if b"@StartData=" in p]
            self.assertEqual(len(starts), 1)
            self.assertEqual(tuple(map(int, starts[0]["F"].split("|"))), LAYOUTS[0])


class BoardConfiguration(unittest.TestCase):
    def test_runner_rejects_shape_mismatch_and_retains_cbc_reward_gate(self):
        valid = ["--mode", "versus", "--players", "3", "--board", ",".join(map(str, LAYOUTS[0]))]
        args = arguments([*DATABASE, *valid])
        self.assertEqual((args.players, args.accounts, args.board), (3, 3, LAYOUTS[0]))
        invalid = (
            ["--board", ",".join(["0"] * 25)],
            ["--mode", "versus", "--players", "3", "--board", ",".join(["0"] * 16)],
            ["--no-allow-cbc", "--board", ",".join(["18"] + ["0"] * 15)],
            ["--no-allow-cbc", "--mode", "versus", "--players", "3", "--board", ",".join(["18"] + ["0"] * 24)],
        )
        for command in invalid:
            with (
                self.subTest(command=command),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                arguments([*DATABASE, *command])
        with self.assertRaisesRegex(ValueError, "CBC materialization"):
            MatchService(mode="versus", player_count=3, start_fields=(18,) + (0,) * 24)
        self.assertTrue(
            MatchService(
                mode="versus", player_count=3, start_fields=(18,) + (0,) * 24, allow_cbc=True
            ).allow_cbc
        )


if __name__ == "__main__":
    unittest.main()
