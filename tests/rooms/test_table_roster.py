"""Native roster barriers over original code and encrypted room/table routes."""

import asyncio
import unittest
from dataclasses import replace

from tests.events.test_event_rooms import presence
from tests.game.test_match_service import NATIVE_SETTINGS
from tests.rooms.test_ordinary_rooms import EXTRA
from tests.rooms.harness import serve
from tmserver.network import dispatch as services
from tmserver.protocol.identities import GAME_KEY, service_nick
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, packet
from tmserver.rooms.config import OrdinaryTableConfig
from tmserver.rooms.roster import TableRoster
from tmserver.protocol.commands import Command, encode, parse


class RosterUnits(unittest.TestCase):

    def test_readiness_retry_dedup_and_generation_cleanup(self):
        m = MatchService(mode="versus")
        r = TableRoster()
        for i in range(2):
            m.register(i, character_id=i + 1, nickname_id=i + 1, name=f"Player{i}")
        self.assertEqual(r.publish(m, []), [])
        r.ready.add(0)
        out = r.publish(m, [])
        self.assertEqual([d.peer for d in out], [0])
        self.assertNotIn("ID", decode(out[0].payload).sections["GameML"])
        self.assertEqual(r.publish(m, []), [])
        r.admission(0)
        self.assertEqual(r.publish(m, []), [])
        r.ready.add(0)
        self.assertEqual(len(r.publish(m, [])), 1)
        m.players = m.players[1:]
        self.assertEqual(r.publish(m, []), [])
        self.assertFalse(r.ready)
        self.assertFalse(r.sent)
        r.ready.add(1)
        m.phase = "selection"
        self.assertEqual(r.publish(m, []), [])
        self.assertFalse(r.ready)


class RosterIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        extra = replace(EXTRA, tables=(OrdinaryTableConfig(0x202, b"#ROSTER3", "versus"),))
        await serve(
            self,
            match_mode="versus",
            match_player_count=3,
            account_count=4,
            ordinary_rooms=(extra,),
        )
        self.peers = [self.server.nicks[c.nick] for c in self.clients]

    async def send(self, side, target, payload):
        await self.clients[side].send(
            b"NOTICE " + service_nick(target ^ GAME_KEY) + b" :" + payload
        )
        return await self.drain(side)

    async def drain(self, side=0):
        out = [[] for c in self.clients]
        for i in (side, *(j for j in range(4) if j != side)):
            await self.clients[i].send(b"PING :roster-barrier")
            for _ in range(100):
                line = await self.clients[i].read()
                self.assertTrue(line)
                if line == b"PONG roster-barrier":
                    break
                self.assertIn(b" NOTICE ", line)
                out[i].append((line.split(b"!", 1)[0][1:], line.split(b" :", 1)[1]))
            else:
                self.fail("roster delivery limit")
        return out

    async def table(self, side, payload, table=None):
        return await self.send(side, (table or self.server.default_table).room.table_id, payload)

    async def enter(self, side, table=None, token=b"DE", ready=False):
        table = table or self.server.default_table
        view = self.server.economy.account_view(self.clients[side].nick.decode())
        payload = presence(table.room.room_id, view.nn, view.player_id, view.name, token)
        if ready:
            cmd = parse(payload[5:])[0]
            fields = cmd.payload.split(b"\x06")
            info = bytearray(fields[-1])
            info[23:25] = b"DB"
            fields[-1] = bytes(info)
            payload = b"GTM0L" + encode([Command(token, b"\x06".join(fields))])
        return await self.send(side, table.room.room_id, payload)

    def rosters(self, out):
        return [
            [
                (source, decode(payload).sections["GameML"])
                for source, payload in rows
                if payload.startswith(b"GTM0G") and "GameML" in decode(payload).sections
            ]
            for rows in out
        ]

    def assert_rosters(self, out, counts, table=None):
        source = service_nick((table or self.server.default_table).table_raw)
        self.assertEqual([[int(f["Num"]) for s, f in rows] for rows in self.rosters(out)], counts)
        for rows in self.rosters(out):
            for s, fields in rows:
                self.assertEqual(s, source)
                self.assertNotIn("ID", fields)

    async def admit(self, side, table=None):
        return await self.table(side, packet(65, 1, body="@GameEN="), table)

    async def test_native_two_then_three_player_admission_pushes_after_each_barrier(self):
        await self.enter(0)
        await self.admit(0)
        self.assert_rosters(
            await self.table(0, packet(20, body=NATIVE_SETTINGS)), [[1], [], [], []]
        )
        await self.enter(1)
        self.assert_rosters(await self.admit(1), [[2], [], [], []])
        self.assert_rosters(await self.enter(1, token=b"PD"), [[], [], [], []])
        self.assert_rosters(await self.enter(1, token=b"PD", ready=True), [[], [2], [], []])
        self.assert_rosters(await self.enter(1, token=b"PD", ready=True), [[], [], [], []])
        await self.enter(2)
        self.assert_rosters(await self.admit(2), [[3], [3], [], []])
        self.assert_rosters(await self.enter(2, token=b"PD", ready=True), [[], [], [3], []])
        out = await self.table(0, packet(65, 10, body="@GameReady="))
        self.assertTrue(
            all(any(p.startswith(b"GTM0G410007") for s, p in rows) for rows in out[1:3])
        )
        self.assertFalse(out[0])
        self.assertFalse(out[3])
        for side in (0, 1, 2):
            await self.table(side, packet(1, body="@Req="))
        for side in (0, 1, 2):
            await self.table(side, packet(65, 8, body="@GameOK="))
        self.assertEqual(self.server.default_table.match.phase, "selection")

    async def test_explicit_retry_waits_for_fresh_presence_and_refreshes_same_roster(self):
        for side in (0, 1):
            await self.enter(side)
            await self.admit(side)
        await self.table(0, packet(20, body=NATIVE_SETTINGS))
        await self.enter(1, token=b"PD", ready=True)
        self.assert_rosters(await self.admit(1), [[], [], [], []])
        self.assertNotIn(self.peers[1], self.server.default_table.table_roster.ready)
        self.assert_rosters(await self.enter(1, token=b"PD", ready=True), [[], [2], [], []])
        self.assert_rosters(await self.enter(1, token=b"PD", ready=True), [[], [], [], []])

    async def test_presence_before_admission_or_from_unseated_member_cannot_enable_roster(self):
        await self.enter(0, ready=True)
        self.assert_rosters(await self.enter(0, token=b"PD", ready=True), [[], [], [], []])
        self.assertFalse(self.server.default_table.match.players)
        self.assert_rosters(await self.admit(0), [[], [], [], []])
        await self.enter(1)
        await self.admit(1)
        self.assert_rosters(await self.enter(1, token=b"PD", ready=True), [[], [2], [], []])
        self.assertNotIn(self.peers[0], self.server.default_table.table_roster.ready)
        self.assert_rosters(
            await self.table(0, packet(20, body=NATIVE_SETTINGS)), [[2], [], [], []]
        )
        await self.enter(2)
        self.assert_rosters(await self.enter(2, token=b"PD", ready=True), [[], [], [], []])
        self.assertEqual(len(self.server.default_table.match.players), 2)
        bad = presence(self.server.room.room_id, 999, 3, token=b"PD")
        with self.assertRaises(ValueError):
            await services.dispatch(
                self.peers[2], service_nick(self.server.room.room_id ^ GAME_KEY), bad
            )
        self.assertEqual(self.server.default_table.table_roster.ready, set(self.peers[:2]))

    async def test_shared_room_roster_notices_use_only_the_members_actual_table(self):
        tables = self.server.ordinary_tables[1:]
        for owner, table in zip((0, 2), tables):
            await self.enter(owner, table)
            await self.admit(owner, table)
            await self.table(owner, packet(20, body=NATIVE_SETTINGS), table)
        for guest, table, counts in (
            (1, tables[0], [[2], [], [], []]),
            (3, tables[1], [[], [], [2], []]),
        ):
            await self.enter(guest, table)
            self.assert_rosters(await self.admit(guest, table), counts, table)
        self.assert_rosters(
            await self.enter(1, tables[0], b"PD", True), [[], [2], [], []], tables[0]
        )
        self.assert_rosters(
            await self.enter(3, tables[1], b"PD", True), [[], [], [], [2]], tables[1]
        )
        self.assertEqual([len(t.table_roster.ready) for t in tables], [2, 2])
        self.assertFalse(self.server.default_table.table_roster.ready)

    async def test_last_seat_race_rejects_without_disconnect_then_allows_retry(self):
        for side in (0, 1, 2):
            await self.admit(side)
        before = self.server.default_table.match.snapshot()
        tokens = dict(self.server.default_table.match_reservations)
        state = self.server.economy.snapshot()
        requests = [
            (packet(65, 1, body="@GameEN="), "GameEA"),
            (packet(65, 18, body="@GameENC="), "GameECA"),
            (packet(1, body="@Req="), "PLAYACK"),
        ]
        for payload, section in requests:
            out = await self.table(3, payload)
            self.assertEqual([len(rows) for rows in out], [0, 0, 0, 1])
            self.assertEqual(
                decode(out[3][0][1]).sections,
                {section: {"EN": "-32871" if section == "GameECA" else "-32870"}},
            )
            self.assertEqual(self.server.default_table.match.snapshot(), before)
            self.assertEqual(self.server.default_table.match_reservations, tokens)
            self.assertEqual(self.server.economy.snapshot(), state)
            self.assertFalse(self.server.economy.is_reserved(self.clients[3].nick.decode()))
        identity = self.server.economy.account_view(self.clients[1].nick.decode()).nn
        await self.table(0, packet(65, 5, body=f"@GameQT=/ID={identity:016X}"))
        out = await self.admit(3)
        self.assertEqual(decode(out[3][-1][1]).sections, {"GameEA": {"EN": "2"}})
        self.assertEqual(len(self.server.default_table.match.players), 3)
        self.assertTrue(self.server.economy.is_reserved(self.clients[3].nick.decode()))

    async def test_busy_match_rejection_preserves_active_players_and_authenticated_identity(self):
        for side in (0, 1, 2):
            await self.admit(side)
            await self.table(side, packet(1, body="@Req="))
        for side in (0, 1, 2):
            await self.table(side, packet(65, 8, body="@GameOK="))
        before = self.server.default_table.match.snapshot()
        tokens = dict(self.server.default_table.match_reservations)
        for request, section in (
            (packet(65, 1, body="@GameEN="), "GameEA"),
            (packet(1, body="@Req="), "PLAYACK"),
        ):
            out = await self.table(3, request)
            self.assertEqual([len(rows) for rows in out], [0, 0, 0, 1])
            self.assertEqual(
                decode(out[3][0][1]).sections,
                {section: {"EN": "-32869" if section == "PLAYACK" else "-32871"}},
            )
            self.assertEqual(self.server.default_table.match.snapshot(), before)
            self.assertEqual(self.server.default_table.match_reservations, tokens)
        for body in ("@GameEN=/NN=0000000000000000", "@GameEN=/HID=2147483648"):
            with self.assertRaises(ValueError):
                await self.server.default_table.dispatch_table(
                    self.peers[3], packet(65, 1, body=body)
                )
        self.assertEqual(self.server.default_table.match.snapshot(), before)

    async def test_concurrent_requests_for_last_seat_have_one_winner_and_no_disconnect(self):
        for side in (0, 1):
            await self.admit(side)
        target = service_nick(self.server.default_table.table_raw)
        request = b"NOTICE " + target + b" :" + packet(65, 1, body="@GameEN=")
        await asyncio.gather(*(self.clients[side].send(request) for side in (2, 3)))
        out = await self.drain(2)
        answers = [decode(out[side][0][1]).sections["GameEA"]["EN"] for side in (2, 3)]
        self.assertEqual(sorted(answers), ["-32870", "2"])
        winner = 2 + answers.index("2")
        loser = 5 - winner
        self.assertEqual(len(self.server.default_table.match.players), 3)
        self.assertEqual(
            set(self.server.default_table.match_reservations),
            {self.peers[0], self.peers[1], self.peers[winner]},
        )
        self.assertFalse(self.server.economy.is_reserved(self.clients[loser].nick.decode()))
        self.assertFalse(self.peers[loser].closed)


if __name__ == "__main__":
    unittest.main()
