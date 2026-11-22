"""Two simultaneous borrowed-hand matches in one event room."""

import asyncio
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tests.events import test_event_matches_integration as base
from tests.events.test_event_rooms import presence
from tests.support.irc import Client
from tmserver.economy.errors import EconomyError
from tmserver.protocol.identities import GAME_KEY, game_id, service_nick
from tmserver.game.errors import MatchError
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server
from tmserver.rooms.spectators import SpectatorService


class EventMultiTableHelpers(base.EventMatchHelpers):
    """Four players on two tables of one event, with optional observers."""

    async def asyncTearDown(self):
        for client in getattr(self, "observers", ()):
            await client.close()
        await base.EventMatchHelpers.asyncTearDown(self)

    async def start(self, observer=False):
        self.tables = (
            self.table,
            replace(self.table, table_id=game_id(0x1004), channel=b"#EVTABLE002"),
        )
        self.server = Server(
            store=self.store,
            account_count=4 + int(observer),
            event_configs=(self.cfg,),
            event_tables=self.tables,
            event_clock=lambda: 200,
            event_progress_policy="rounds",
            spectator=SpectatorService(
                enabled=observer, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        port = await self.server.start(0)
        self.clients = [Client() for _ in range(4)]
        self.port = port
        self.observers = []
        self.inbox = [[] for _ in self.clients]
        self.closed = set()
        for client, nick in zip(self.clients, self.server.accounts):
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]
        self.before = [self.server.economy.profile_bytes(k) for k in self.keys]

    async def drain(self):
        for _ in range(80):
            await asyncio.sleep(0.015)
            if not self.server.event_matches.coordinator.pending():
                break
        for side, client in enumerate(self.clients):
            if side not in self.closed:
                self.inbox[side] += base.notices(await base.barrier(self, client, b"multi-event"))

    def round(self, index):
        return self.server.event_matches.coordinator._rounds[self.tables[index].table_id]

    async def admit_all(self):
        for side in range(4):
            await self.enter_selection(side)
        for _ in range(20):
            if all(any(b"@MuchMake=/TblNo=" in p for _, p in b) for b in self.inbox):
                break
            await self.drain()
        for side, box in enumerate(self.inbox):
            fields = next(
                decode(p).sections["MuchMake"] for _, p in box if b"@MuchMake=/TblNo=" in p
            )
            index = side // 2
            table = self.tables[index]
            self.assertEqual(fields, {"TblNo": str(index + 1), "TblId": f"{table.table_id:016X}"})
            await self.send(
                side,
                self.cfg.room_id,
                packet(
                    0xD3, body=f"@MuchMakeAns=/Ans=1/TblNo={index+1}/TblId={table.table_id:016X}"
                ),
            )
        for side, client in enumerate(self.clients):
            table = self.tables[side // 2]
            await self.send(
                side, table.table_id, packet(0x41, 1, body=self.identity(side, "GameEN"))
            )
            await client.send(b"JOIN " + table.channel)
            await self.drain()
            await self.send(side, table.table_id, packet(1, body=self.identity(side, "Req")))
            await self.send(side, table.table_id, packet(0x41, 8, body="@GameOK="))
        self.assertEqual([self.round(i).engine.phase for i in range(2)], ["turn", "turn"])
        self.assertEqual(
            [t.table_state for t in self.server.event_rooms.rooms[0].room.tables], [2, 2]
        )
        self.assertNotEqual(self.round(0).epoch, self.round(1).epoch)

    async def tick(self, index):
        engine = self.round(index).engine
        target = self.tables[index].table_id
        if engine.phase == "result":
            return
        if engine.pending_work:
            await self.drain()
            return
        if engine.phase == "turn":
            sequence = engine.sequence
            for seat in (0, 1):
                await self.send(
                    index * 2 + seat, target, packet(0x43, 9, sequence, f"@TurnData=/Ans={seat}")
                )
            if engine.phase == "turn":
                await self.send(
                    index * 2 + engine.active,
                    target,
                    packet(
                        0x43,
                        10,
                        engine.sequence,
                        f"@PutData=/P={engine.active}/H=0/F={engine._combat.legal_fields()[0]}",
                    ),
                )
        elif engine.phase == "battle":
            await self.send(
                index * 2 + engine.active,
                target,
                packet(
                    0x43,
                    11,
                    engine.sequence,
                    "@BattleSelect=/B=" + str(engine.snapshot()["battle_targets"][0]),
                ),
            )
        else:
            self.fail(engine.phase)

    async def finish(self, indices=(0, 1)):
        for _ in range(200):
            if all(self.round(i).engine.phase == "result" for i in indices):
                return
            for index in indices:
                await self.tick(index)
        self.fail("parallel event matches did not finish")

    async def release(self, index):
        target = self.tables[index].table_id
        for seat in (0, 1):
            await self.send(index * 2 + seat, target, packet(0x43, 14, 10, f"@Ready=/Ans={seat}"))


class EventMultiTable(EventMultiTableHelpers, unittest.IsolatedAsyncioTestCase):
    async def test_two_matches_settle_once_and_persist_independent_progress(self):
        await self.start()
        await self.admit_all()
        await self.finish()
        self.assertEqual(len(self.server.event_results.snapshot()["rounds"]), 2)
        for side, box in enumerate(self.inbox):
            source = service_nick(self.tables[side // 2].table_id ^ GAME_KEY)
            results = [(s, p) for s, p in box if b"@EventResult=" in p]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0], source)
            self.assertEqual(self.server.events.member(self.keys[side], "festival").steps, 1)
        await self.release(0)
        self.assertNotIn(self.tables[0].table_id, self.server.event_matches.coordinator._rounds)
        self.assertTrue(all(self.server.economy.is_reserved(k) for k in self.keys[2:]))
        self.assertEqual(
            [t.table_count for t in self.server.event_rooms.rooms[0].room.tables], [0, 2]
        )
        await self.release(1)
        self.assertFalse(self.server.event_matches.coordinator._rounds)
        self.assertEqual([self.server.economy.profile_bytes(k) for k in self.keys], self.before)
        restored = Server(
            store=self.store,
            account_count=4,
            event_configs=(self.cfg,),
            event_tables=self.tables,
            event_clock=lambda: 200,
            event_progress_policy="rounds",
        )
        try:
            self.assertEqual(
                restored.event_results.snapshot(), self.server.event_results.snapshot()
            )
            self.assertEqual(restored.events.snapshot(), self.server.events.snapshot())
        finally:
            await restored.stop()

    async def test_one_cancel_does_not_release_or_close_other_event_table(self):
        await self.start()
        await self.admit_all()
        before = self.round(1).engine.snapshot()
        self.closed = {0, 1}
        await self.clients[0].close()
        for _ in range(100):
            if not await self.clients[1].read():
                break
        else:
            self.fail("cancelled partner not closed")
        await self.drain()
        self.assertEqual(self.round(1).engine.snapshot(), before)
        self.assertTrue(all(self.server.economy.is_reserved(k) for k in self.keys[2:]))
        self.assertTrue(all(not self.server.economy.is_reserved(k) for k in self.keys[:2]))
        await self.finish((1,))
        await self.release(1)
        self.assertEqual(len(self.server.event_results.snapshot()["rounds"]), 1)
        self.assertEqual(
            [self.server.events.member(k, "festival").steps for k in self.keys], [0, 0, 1, 1]
        )

    async def test_other_table_target_and_proposal_number_are_rejected(self):
        await self.start()
        await self.admit_all()
        c = self.server.event_matches.coordinator
        before = [c.snapshot("festival", table_id=t.table_id) for t in self.tables]
        peer = self.server.nicks[self.clients[0].nick]
        for target, payload in (
            (self.tables[1].table_id, packet(0x43, 9, 0, "@TurnData=/Ans=0")),
            (
                self.cfg.room_id,
                packet(
                    0xD3, body=f"@MuchMakeAns=/Ans=1/TblNo=2/TblId={self.tables[0].table_id:016X}"
                ),
            ),
        ):
            with self.assertRaises(EconomyError):
                await self.server.event_matches.dispatch(peer, target, payload)
            self.assertEqual(
                [c.snapshot("festival", table_id=t.table_id) for t in self.tables], before
            )

    async def test_shutdown_with_two_active_event_tables_releases_without_deadlock(self):
        await self.start()
        await self.admit_all()
        for client in self.clients:
            await client.close()
        await asyncio.wait_for(self.server.stop(), 5)
        self.assertFalse(self.server.sessions)
        self.assertFalse(any(self.server.economy.is_reserved(key) for key in self.keys))

    async def test_reused_table_has_fresh_session_and_epoch_while_neighbor_plays(self):
        await self.start()
        await self.admit_all()
        coordinator = self.server.event_matches.coordinator
        old_epoch = self.round(0).epoch
        peer = self.server.nicks[self.clients[0].nick]
        old_session = coordinator.session(peer)
        other = self.round(1).engine.snapshot()
        await self.finish((0,))
        await self.release(0)
        for side in (0, 1):
            self.inbox[side].clear()
            await self.enter_selection(side)
        for _ in range(20):
            if all(any(b"@MuchMake=/TblNo=" in p for _, p in self.inbox[side]) for side in (0, 1)):
                break
            await self.drain()
        self.assertNotEqual(self.round(0).epoch, old_epoch)
        self.assertNotEqual(coordinator.session(peer), old_session)
        before = coordinator.snapshot("festival", table_id=self.table.table_id)
        self.assertEqual(coordinator.cancel("festival", old_epoch), [])
        self.assertEqual(coordinator.advance("festival", old_epoch), [])
        with self.assertRaises(EconomyError):
            await self.server.event_matches.dispatch(
                peer, self.cfg.init_id, packet(0xB2, 0x17, old_session, f"@Quit=/No={old_session}")
            )
        self.assertEqual(coordinator.snapshot("festival", table_id=self.table.table_id), before)
        for side in (0, 1):
            fields = next(
                decode(p).sections["MuchMake"]
                for _, p in self.inbox[side]
                if b"@MuchMake=/TblNo=" in p
            )
            self.assertEqual(fields, {"TblNo": "1", "TblId": f"{self.table.table_id:016X}"})
            await self.send(
                side,
                self.cfg.room_id,
                packet(0xD3, body=f"@MuchMakeAns=/Ans=1/TblNo=1/TblId={self.table.table_id:016X}"),
            )
        for side in (0, 1):
            await self.send(
                side, self.table.table_id, packet(0x41, 1, body=self.identity(side, "GameEN"))
            )
            await self.send(side, self.table.table_id, packet(1, body=self.identity(side, "Req")))
            await self.send(side, self.table.table_id, packet(0x41, 8, body="@GameOK="))
        with self.assertRaises(MatchError):
            await self.server.event_matches.dispatch(
                peer, self.table.table_id, packet(0x43, 14, 10, "@Ready=/Ans=0")
            )
        self.assertEqual(self.round(0).engine.snapshot()["released_count"], 0)
        self.assertEqual(self.round(1).engine.snapshot(), other)
        await self.finish()
        await self.release(0)
        await self.release(1)
        self.assertEqual(len(self.server.event_results.snapshot()["rounds"]), 3)
        self.assertEqual(
            [self.server.events.member(k, "festival").steps for k in self.keys], [2, 2, 1, 1]
        )
        self.assertEqual([self.server.economy.profile_bytes(k) for k in self.keys], self.before)

    async def test_second_table_observer_survives_first_table_abort(self):
        await self.start(observer=True)
        await self.admit_all()
        observer = Client()
        self.observers.append(observer)
        await observer.open(self.port, account=list(self.server.accounts)[4])
        self.assertIn(b" 422 ", await observer.read())
        key = observer.nick.decode()
        view = self.server.economy.account_view(key)
        before = self.server.economy.profile_bytes(key)
        table = self.tables[1]
        source = service_nick(table.table_id ^ GAME_KEY)

        async def send(target, payload):
            await observer.send(b"NOTICE " + service_nick(target ^ GAME_KEY) + b" :" + payload)

        async def read_table():
            for _ in range(200):
                line = await observer.read()
                self.assertTrue(line, "neighbor abort closed observer")
                if line.startswith(b":" + source + b"!p@local NOTICE "):
                    return line.split(b" :", 1)[1]
                self.assertFalse(
                    line.startswith(
                        b":" + service_nick(self.table.table_id ^ GAME_KEY) + b"!p@local NOTICE "
                    )
                )
            self.fail("observer response limit")

        await send(self.cfg.room_id, presence(self.cfg.room_id, view.nn, view.player_id, view.name))
        await send(table.table_id, packet(0x2D, body="@CheckJoinTable="))
        self.assertEqual(decode(await read_table()).sections["CheckJoinTable_Ans"]["Join"], "1")
        await observer.send(b"JOIN " + table.channel)
        await send(table.table_id, packet(1, body=f"@Req=/NN={view.nn:016X}/HID={view.player_id}"))
        self.assertEqual(decode(await read_table()).primary, 2)
        await send(table.table_id, packet(0x43, 0x25, 0, "@Data=/Watch=0"))
        self.assertEqual([decode(await read_table()).subtype for _ in range(3)], [0x25, 0x26, 9])
        self.closed = {0, 1}
        await self.clients[0].close()
        for _ in range(100):
            if not await self.clients[1].read():
                break
        else:
            self.fail("aborted partner not closed")
        await self.drain()
        await self.finish((1,))
        received = []
        for _ in range(200):
            raw = await read_table()
            received.append(raw)
            if b"@EventResult=" in raw:
                break
        else:
            self.fail("missing second-table observer result")
        self.assertEqual(
            [decode(p).argument for p in received if decode(p).subtype == 9], list(range(1, 10))
        )
        self.assertEqual(
            [decode(p).argument for p in received if decode(p).subtype == 10], list(range(10))
        )
        self.assertFalse(any(b"@EventCard=" in p or b"@TCList=" in p for p in received))
        await self.release(1)
        self.assertEqual(self.server.economy.profile_bytes(key), before)
        self.assertFalse(self.server.nicks[observer.nick].closed)


if __name__ == "__main__":
    unittest.main()
