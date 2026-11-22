"""Native minimum-two Start, frozen membership and two/three-player boards."""

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.economy import test_match_prizes_integration as prizes
from tests.game.test_match_service import NATIVE_SETTINGS, finish_arrowless_round, select
from tests.support.irc import Client
from tests.support.memory_store import DATABASE, MemoryStore
from tmserver.protocol.identities import TABLE_RAW, player_id
from tmserver.game.errors import MatchError
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, packet
from tmserver.rooms.config import load_ordinary_rooms
from tmserver.rankings.stats import PracticeStats
from tmserver.cli.serve import arguments
from tmserver.network.server import Server


def seated(count):
    m = MatchService(mode="versus", player_count=3, flexible_seats=True)
    for i in range(count):
        m.register(i, character_id=i + 1, nickname_id=i + 1, name=f"Player{i}")
        m.handle(i, packet(0x41, 1, body=f"@GameEN=/NN={i+1:016X}"))
    m.handle(0, packet(0x14, body=NATIVE_SETTINGS))
    return m


class FlexibleSeatUnits(unittest.TestCase):
    def test_owner_start_freezes_two_or_three_and_rejects_late_admission(self):
        for n in (2, 3):
            m = seated(n)
            self.assertTrue(m.ready_to_start)
            before = m.snapshot()
            with self.assertRaises(MatchError):
                m.handle(1, packet(65, 10, body="@GameReady="))
            self.assertEqual(m.snapshot(), before)
            out = m.handle(0, packet(65, 10, body="@GameReady="))
            self.assertEqual([d.peer for d in out], list(range(1, n)))
            self.assertEqual(
                (m.player_count, m.board_size, m.last_sequence), (n, (n + 2) ** 2, 5 * n - 1)
            )
            self.assertFalse(m.accepting_players)
            with self.assertRaises(MatchError):
                m.register(99, character_id=100)
            self.assertEqual(m.handle(0, packet(65, 10, body="@GameReady=")), [])
            for i in range(n):
                m.handle(i, packet(1, body="@Req="))
            for i in range(n):
                out = m.handle(i, packet(65, 8, body="@GameOK="))
            self.assertEqual(m.phase, "selection")
            self.assertEqual(
                [
                    decode(d.payload).sections["VsGameInit"]["N"]
                    for d in out
                    if b"@VsGameInit=" in d.payload
                ],
                [str(n)] * n,
            )

    def test_two_rounds_keep_frozen_shape_and_counter_reset_reopens_capacity(self):
        for n in (2, 3):
            m = seated(n)
            m.handle(0, packet(65, 10, body="@GameReady="))
            for i in range(n):
                m.handle(i, packet(1, body="@Req="))
            for i in range(n):
                m.handle(i, packet(65, 8, body="@GameOK="))
            for round_number in (1, 2):
                for i in range(n):
                    m.handle(i, select(seat=i))
                finish_arrowless_round(m)
                self.assertTrue(
                    all(p.result_stats.vs_player_games == round_number for p in m.players)
                )
                for i in range(n):
                    m.handle(i, packet(0x43, 14, m.sequence, f"@Ready=/Ans={i}"))
                for i in range(n):
                    m.handle(i, packet(0x43, 17, i, "@Continue=/Ans=1/PR=0/Conf=0"))
                self.assertEqual(
                    (m.phase, m.player_count, m.board_size), ("selection", n, (n + 2) ** 2)
                )
            m.phase = "finished"
            m.reset()
            self.assertEqual((m.player_count, m.board_size), (3, 25))
            if n == 2:
                self.assertTrue(m.accepting_players)
                m.register(2, character_id=3, nickname_id=3)
                m.handle(0, packet(65, 10, body="@GameReady="))
                self.assertEqual(m.player_count, 3)

    def test_direct_requested_roster_freezes_at_first_acceptance(self):
        m = seated(2)
        for i in range(2):
            m.handle(i, packet(1, body="@Req="))
        m.handle(0, packet(65, 8, body="@GameOK="))
        self.assertEqual(m.player_count, 2)
        self.assertFalse(m.accepting_players)
        m.handle(1, packet(65, 8, body="@GameOK="))
        self.assertEqual(m.phase, "selection")

    def test_generated_special_boards_use_frozen_shape(self):
        for n in (2, 3):
            m = seated(n)
            m.handle(
                0, packet(20, body=NATIVE_SETTINGS.replace("/st=0/cb=0/ca=0", "/st=1/cb=1/ca=1"))
            )
            m.begin()
            for i in range(n):
                messages = m.handle(i, select(seat=i))
            start = next(decode(d.payload) for d in messages if b"@StartData=" in d.payload)
            self.assertEqual(len(start.sections["StartData"]["F"].split("|")), (n + 2) ** 2)
            self.assertEqual(len(m.board), (n + 2) ** 2)

    def test_one_player_cannot_freeze_and_fixed_three_still_requires_three(self):
        m = seated(1)
        self.assertFalse(m.ready_to_start)
        with self.assertRaises(MatchError):
            m.handle(0, packet(65, 10, body="@GameReady="))
        self.assertTrue(m.accepting_players)
        self.assertFalse(m.start_pending)
        m = MatchService(mode="versus", player_count=3)
        for i in range(2):
            m.register(i, character_id=i + 1)
        m.settings_applied = True
        self.assertFalse(m.ready_to_start)

    def test_configuration_is_explicit_and_rejects_fixed_layout(self):
        args = arguments([*DATABASE, "--mode", "versus", "--players", "auto"])
        self.assertEqual((args.players, args.accounts, args.flexible_seats), (3, 3, True))
        self.assertFalse(arguments([*DATABASE, "--mode", "versus", "--players", "3"]).flexible_seats)
        for opts in (
            ["--players", "auto"],
            ["--mode", "versus", "--players", "auto", "--board", ",".join(["0"] * 25)],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                arguments([*DATABASE, *opts])
        for kw in (
            {"flexible_seats": 1},
            {"mode": "computer"},
            {"player_count": 2},
            {"start_fields": (0,) * 25},
        ):
            options = dict(mode="versus", player_count=3, flexible_seats=True)
            options.update(kw)
            with self.assertRaises(ValueError):
                MatchService(**options)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "rooms.json"
            f.write_text(
                json.dumps(
                    {
                        "rooms": [
                            dict(
                                name="Auto Room",
                                room_raw=0x101,
                                table_raw=0x201,
                                room_channel="#AUTO1",
                                table_channel="#AUTO2",
                                players="auto",
                                tables=[
                                    dict(table_raw=0x202, table_channel="#AUTO3", players="auto")
                                ],
                            )
                        ]
                    }
                )
            )
            room = load_ordinary_rooms(f)[0]
            self.assertTrue(room.flexible_seats and room.tables[0].flexible_seats)


class FlexibleSeatNetwork(unittest.IsolatedAsyncioTestCase):
    player_count = 2
    flush = prizes.PrizeIntegrationTests.flush
    send = prizes.PrizeIntegrationTests.send
    play = prizes.PrizeIntegrationTests.play
    choose = prizes.PrizeIntegrationTests.choose
    asyncTearDown = prizes.PrizeIntegrationTests.asyncTearDown

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.store = MemoryStore()
        self.server = Server(
            store=self.store,
            match_mode="versus",
            match_player_count=3,
            account_count=3,
            flexible_seats=True,
            card_prizes=True,
            allow_wagers=True,
            rating_mode="local-history",
        )
        self.port = await self.server.start(0)
        self.clients = []
        for nick in list(self.server.accounts)[: self.player_count]:
            c = Client()
            self.clients.append(c)
            await c.open(self.port, account=nick)
            self.assertIn(b" 422 ", await c.read())
        self.keys = [c.nick.decode() for c in self.clients]

    async def test_native_start_paid_prize_rematch_and_restart(self):
        for side, c in enumerate(self.clients):
            await self.send(side, packet(65, 1, body=f"@GameEN=/NN={player_id(c.nick):016X}"))
        await self.send(0, packet(20, body=NATIVE_SETTINGS.replace("/bm=0", "/bm=200")))
        await self.send(0, packet(65, 10, body="@GameReady="))
        self.assertEqual(self.server.default_table.match.player_count, self.player_count)
        for side in range(self.player_count):
            await self.send(side, packet(1, body="@Req="))
        for side in range(self.player_count):
            await self.send(side, packet(65, 8, body="@GameOK="))
        for number in (1, 2):
            await self.play()
            await self.choose()
            for key in self.keys:
                self.assertEqual(
                    PracticeStats.from_profile(
                        self.server.economy.profile_bytes(key)
                    ).vs_player_games,
                    number,
                )
            for side in range(self.player_count):
                await self.send(side, packet(67, 17, side, "@Continue=/Ans=1/PR=0/Conf=0"))
            self.assertEqual(self.server.default_table.match.player_count, self.player_count)
        restored = Server(store=self.store, account_count=3, rating_mode="local-history")
        try:
            for key in self.keys:
                self.assertEqual(
                    PracticeStats.from_profile(restored.economy.profile_bytes(key)).vs_player_games,
                    2,
                )
            self.assertEqual(
                sum(restored.economy.balance(k) for k in self.keys), 10000 * self.player_count
            )
        finally:
            await restored.stop()


class ThreeFlexibleSeatNetwork(FlexibleSeatNetwork):
    player_count = 3


class FlexibleSeatAdmissionRace(unittest.IsolatedAsyncioTestCase):
    player_count = 2
    asyncSetUp = FlexibleSeatNetwork.asyncSetUp
    asyncTearDown = FlexibleSeatNetwork.asyncTearDown
    flush = FlexibleSeatNetwork.flush
    send = FlexibleSeatNetwork.send

    async def test_late_native_join_rejected_without_disconnect_or_reservation(self):
        for side, c in enumerate(self.clients):
            await self.send(side, packet(65, 1, body=f"@GameEN=/NN={player_id(c.nick):016X}"))
        await self.send(0, packet(20, body=NATIVE_SETTINGS))
        await self.send(0, packet(65, 10, body="@GameReady="))
        late = Client()
        await late.open(self.port, account=list(self.server.accounts)[2])
        try:
            self.assertIn(b" 422 ", await late.read())
            from tmserver.protocol.identities import service_nick

            for request, section in (
                (packet(45, body="@CheckJoinTable="), "CheckJoinTable_Ans"),
                (packet(65, 1, body=f"@GameEN=/NN={player_id(late.nick):016X}"), "GameEA"),
            ):
                await late.send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + request)
                reply = decode((await late.read()).split(b" :", 1)[1])
                if section == "GameEA":
                    self.assertEqual(reply.sections[section]["EN"], "-32871")
                else:
                    self.assertEqual(reply.sections[section], {"Join": "0", "Mem": "0"})
            await late.send(b"PING :still-open")
            self.assertEqual(await late.read(), b"PONG still-open")
            self.assertEqual(len(self.server.default_table.match.players), 2)
            self.assertFalse(self.server.economy.is_reserved(late.nick.decode()))
        finally:
            await late.close()


if __name__ == "__main__":
    unittest.main()
