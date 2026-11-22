"""Opt-in departures through encrypted TCP and reentrant failed-send cleanup."""

import asyncio
import unittest
from contextlib import asynccontextmanager

from tests.events.test_event_rooms import config, presence
from tests.support.irc import Client
from tests.rooms.harness import serve
from tmserver.events.models import EventTableConfig
from tmserver.protocol.identities import (
    GAME_KEY,
    SHOP_RAW,
    TABLE_RAW,
    game_id,
    player_id,
    service_nick,
)
from tmserver.game.packets import decode, packet
from tmserver.rooms.watchdog import MatchWatchdog
from tmserver.rooms.config import OrdinaryRoomConfig
from tmserver.rooms.packets import TABLE_CHANNEL
from tmserver.network.server import Server


class DepartureIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.live = set()
        self.received = {}

    async def start(
        self, *, count=2, accounts=None, rule=0, enabled=True, watchdog=None, stake=0, **kwargs
    ):
        await serve(
            self,
            clients=count,
            match_mode="versus",
            account_count=accounts or count,
            match_player_count=count,
            allow_departures=enabled,
            watchdog=watchdog,
            **kwargs,
        )
        self.live.update(range(count))
        self.received.update((side, []) for side in range(count))
        self.keys = [c.nick.decode() for c in self.clients]
        self.peers = [self.server.nicks[c.nick] for c in self.clients]
        self.initial_accounts = self.server.economy.snapshot()
        for side in range(count):
            await self.send(
                side, packet(0x41, 1, body=f"@GameEN=/NN={player_id(self.clients[side].nick):016X}")
            )
        await self.send(
            0,
            packet(
                0x14,
                body=f"@Tet=/bm={stake}/du=0/st=0/cb=0/ca=0/gs={rule}/tl=0@Tab=/in=0/lu=100/ll=0/au=300/al=100/co=0/pa=0",
            ),
        )
        for side in range(count):
            await self.send(
                side, packet(1, body=f"@Req=/NN={player_id(self.clients[side].nick):016X}")
            )
            await self.send(side, packet(0x41, 8, body="@GameOK="))
        for side in range(count):
            cards = self.server.economy.inventory(self.keys[side])[:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
            )
            await self.send(side, packet(0x43, 7, side, body))
        self.assertEqual(self.server.default_table.match.phase, "turn")
        self.original = self.server.default_table.match
        self.before = self.server.economy.snapshot()

    async def settle(self):
        # Wait only for explicitly tracked finite work; no timing assumptions.
        for _ in range(100):
            tasks = [
                t
                for t in (
                    self.server.default_table.departures.task,
                    self.server.default_table.match_task,
                )
                if t is not None and not t.done()
            ]
            if not tasks:
                return
            await asyncio.wait_for(asyncio.gather(*(asyncio.shield(t) for t in tasks)), 3)
        self.fail("departure/continuation work did not settle")

    async def flush(self, side):
        client = self.clients[side]
        result = []
        for _ in range(2):
            await client.send(b"PING :departure-barrier")
            for _ in range(200):
                line = await client.read()
                self.assertTrue(line, "healthy connection was closed")
                if line == b"PONG departure-barrier":
                    break
                if b" NOTICE " in line and b" :GTM0G" in line:
                    payload = line.split(b" :", 1)[1]
                    result.append(payload)
                    self.received[side].append(payload)
            else:
                self.fail("response limit exceeded")
            await self.settle()
        return result

    async def flush_all(self):
        return {side: await self.flush(side) for side in sorted(self.live)}

    async def send(self, side, payload):
        await self.clients[side].send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + payload)
        return await self.flush_all()

    async def drop(self, side):
        self.live.remove(side)
        await self.clients[side].close()
        await self.flush_all()
        await self.settle()

    def notices(self, side, subtype):
        return [
            decode(p)
            for p in self.received[side]
            if decode(p).primary == 0x43 and decode(p).subtype == subtype
        ]

    async def finish(self):
        match = self.server.default_table.match
        for _ in range(50):
            if match.phase == "result":
                return
            self.assertIn(match.phase, ("turn", "battle"))
            if match.phase == "turn":
                seq = match.sequence
                for side in sorted(self.live):
                    if not match.players[side].departed and match.sequence == seq:
                        await self.send(side, packet(0x43, 9, seq, f"@TurnData=/Ans={side}"))
                if match.phase != "turn" or match.sequence != seq:
                    continue
                side = match.active
                self.assertFalse(match.players[side].departed, "CPU move did not advance")
                await self.send(
                    side,
                    packet(0x43, 10, seq, f"@PutData=/P={side}/H=0/F={match.legal_fields()[0]}"),
                )
            else:
                side = match.active
                self.assertFalse(match.players[side].departed, "CPU combat did not advance")
                await self.send(
                    side, packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}")
                )
        self.fail("match did not finish")

    async def continue_leave(self, side):
        match = self.server.default_table.match
        await self.send(side, packet(0x43, 14, match.sequence, f"@Ready=/Ans={side}"))
        await self.send(side, packet(0x43, 17, side, "@Continue=/Ans=2/PR=0/Conf=0"))
        self.assertEqual(match.phase, "finished")
        self.assertFalse(self.server.default_table.match_reservations)
        for key in self.keys:
            self.assertFalse(self.server.economy.is_reserved(key))

    async def test_two_player_disconnect_substitutes_retains_then_settles_and_reconnects(self):
        await self.start(rule=0)
        await self.drop(0)
        self.assertIs(self.server.default_table.match, self.original)
        self.assertEqual(self.original.departure_seats, (0,))
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        self.assertEqual(len(self.notices(1, 0x23)), 1)
        self.assertEqual(len(self.notices(1, 0x24)), 1)
        retry = Client()
        self.clients.append(retry)
        await retry.open(self.port, account=self.peers[0].nick)
        self.assertEqual(await retry.read(), b"")
        await self.finish()
        self.assertFalse(self.server.result_rewards.has_pending(self.keys[0]))
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        await self.continue_leave(1)
        reconnect = Client()
        self.clients.append(reconnect)
        await reconnect.open(self.port, account=self.peers[0].nick)
        self.assertIn(b" 422 ", await reconnect.read())
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
        finally:
            await restored.stop()

    async def test_two_player_invalid_rule_finishes_without_growth_or_loss_result(self):
        await self.start(rule=1)
        await self.drop(1)
        self.assertEqual(self.original.phase, "finished")
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.server.economy.snapshot(), self.before)
        self.assertEqual(len(self.notices(0, 0x23)), 1)
        self.assertEqual(self.notices(0, 0x24), [])
        self.assertEqual(self.notices(0, 0x0D), [])

    async def test_three_player_loss_rule_finishes_partial_round_and_all_profiles(self):
        await self.start(count=3, rule=2)
        await self.drop(1)
        self.assertEqual(self.original.departure_seats, (1,))
        await self.finish()
        self.assertEqual(len(self.notices(0, 0x0D)), 1)
        self.assertEqual(len(self.notices(2, 0x0D)), 1)
        for peer, key in zip(self.peers, self.keys):
            self.assertEqual(len(self.original.final_cards(peer).updated), 5)
            self.assertEqual(len(self.server.economy.inventory(key)), 10)
        self.assertFalse(self.server.result_rewards.has_pending(self.keys[1]))
        await self.continue_leave(0)

    async def test_explicit_quit_then_part_emits_departure_only_once(self):
        await self.start(rule=0)
        await self.clients[0].send(b"JOIN " + TABLE_CHANNEL)
        await self.flush_all()
        await self.send(0, packet(0x43, 5, 10, "@Quit=/No=0"))
        self.assertEqual(len(self.notices(0, 5)), 1)
        await self.clients[0].send(b"PART " + TABLE_CHANNEL)
        await self.flush_all()
        self.assertEqual(self.original.departure_seats, (0,))
        self.assertEqual(len(self.notices(1, 0x23)), 1)
        self.assertEqual(len(self.notices(1, 0x24)), 1)
        self.assertTrue(self.server.economy.is_reserved(self.keys[0]))
        await self.finish()
        await self.continue_leave(1)

    async def test_live_loss_quitter_receives_terminal_result_without_ready_barrier(self):
        await self.start(rule=2)
        await self.send(1, packet(0x43, 5, 10, "@Quit=/No=1"))
        self.assertFalse(self.peers[1].closed)
        self.assertEqual(len(self.notices(1, 5)), 1)
        self.assertEqual(self.original.departure_seats, (1,))
        await self.finish()
        self.assertEqual(len(self.notices(1, 0x0D)), 1)
        self.assertEqual(self.notices(1, 0x1A), [])
        self.assertFalse(self.server.result_rewards.has_pending(self.keys[1]))
        await self.continue_leave(0)
        self.assertFalse(self.peers[1].closed)
        self.assertEqual(len(self.notices(1, 0x0D)), 1)

    async def test_part_without_quit_uses_same_departure_path(self):
        await self.start(count=3, rule=0)
        await self.clients[2].send(b"JOIN " + TABLE_CHANNEL)
        await self.flush_all()
        await self.clients[2].send(b"PART " + TABLE_CHANNEL)
        await self.flush_all()
        self.assertEqual(self.original.departure_seats, (2,))
        self.assertFalse(self.peers[2].closed)
        self.assertTrue(self.server.economy.is_reserved(self.keys[2]))

    async def test_failed_send_inside_locked_publish_does_not_deadlock_or_close_requester(self):
        await self.start(count=3, rule=0)
        original_send = self.peers[1].send

        async def fail(message):
            if b"@PutCard=" in message:
                raise OSError("injected recipient failure")
            await original_send(message)

        self.peers[1].send = fail
        for side in range(3):
            await self.send(side, packet(0x43, 9, 0, f"@TurnData=/Ans={side}"))
        self.live.remove(1)
        await asyncio.wait_for(
            self.send(
                0, packet(0x43, 10, 0, f"@PutData=/P=0/H=0/F={self.original.legal_fields()[0]}")
            ),
            3,
        )
        self.assertTrue(self.peers[1].closed)
        self.assertFalse(self.peers[0].closed)
        self.assertFalse(self.peers[2].closed)
        self.assertEqual(self.original.departure_seats, (1,))
        self.assertEqual(len(self.notices(0, 0x23)), 1)
        self.assertEqual(len(self.notices(2, 0x23)), 1)

    async def test_all_connections_gone_and_stop_release_every_capability(self):
        await self.start(count=3, rule=0)
        await self.drop(0)
        for side in (1, 2):
            await self.clients[side].close()
        # Tracked accept tasks finish only after their close path detached peers.
        await asyncio.wait_for(asyncio.gather(*list(self.server.tasks)), 3)
        await self.settle()
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertIsNot(self.server.default_table.match, self.original)
        for key in self.keys:
            self.assertFalse(self.server.economy.is_reserved(key))
        await self.server.stop()

    async def test_opted_in_timeout_still_aborts_instead_of_substituting(self):
        now = [0]
        await self.start(watchdog=MatchWatchdog(clock=lambda: now[0]))
        now[0] = 120
        timeout = await self.server.default_table.check_match_timeout()
        self.assertEqual(timeout.reason, "turn_ack")
        await self.settle()
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.original.departure_seats, ())
        self.assertTrue(all(p.closed for p in self.peers))

    async def test_two_player_loss_rule_ends_after_survivor_move(self):
        await self.start(rule=2)
        await self.drop(1)
        await self.finish()
        self.assertLess(self.original.sequence, 10)
        self.assertEqual(len(self.notices(0, 0x0D)), 1)
        self.assertEqual(self.original._final_order[1], ())
        self.assertEqual(len(self.original.final_cards(self.peers[1]).updated), 5)
        await self.continue_leave(0)

    async def test_three_player_invalid_rule_continues_two_survivors(self):
        await self.start(count=3, rule=1)
        await self.drop(2)
        self.assertEqual(self.original.phase, "turn")
        await self.finish()
        self.assertEqual(self.original._final_order[2], ())
        self.assertEqual(len(self.original.final_cards(self.peers[2]).updated), 5)
        await self.continue_leave(0)

    async def test_stop_with_retained_account_aborts_without_partial_growth(self):
        await self.start(rule=0)
        await self.drop(0)
        await asyncio.wait_for(self.server.stop(), 3)
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.server.economy.snapshot(), self.before)
        for key in self.keys:
            self.assertFalse(self.server.economy.is_reserved(key))

    async def test_rated_departure_remains_abort_even_when_opted_in(self):
        await self.start(rating_mode="local-history")
        await self.clients[0].close()
        self.assertEqual(await self.clients[1].read(), b"")
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.original.departure_seats, ())
        self.assertEqual(self.server.economy.snapshot(), self.before)

    async def test_watchdog_waits_only_for_survivors_after_departure(self):
        now = [0]
        await self.start(watchdog=MatchWatchdog(clock=lambda: now[0]))
        await self.drop(1)
        wait = self.server.default_table.watchdog.observe(self.original, self.original)
        self.assertEqual(wait.missing_peers, (self.peers[0],))
        await self.finish()
        wait = self.server.default_table.watchdog.observe(self.original, self.original)
        self.assertEqual(wait.missing_peers, (self.peers[0],))
        await self.send(0, packet(0x43, 14, self.original.sequence, "@Ready=/Ans=0"))
        wait = self.server.default_table.watchdog.observe(self.original, self.original)
        self.assertEqual(wait.reason, "result_continue")
        self.assertEqual(wait.missing_peers, (self.peers[0],))

    async def read_until_closed(self, side):
        messages = []
        for _ in range(100):
            line = await self.clients[side].read(timeout=3)
            if not line:
                return messages
            if b" :GTM0G" in line:
                messages.append(decode(line.split(b" :", 1)[1]))
        self.fail("aborted participant did not disconnect")

    async def assert_explicit_abort(self, payload):
        await self.clients[0].send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + payload)
        messages = await asyncio.gather(*(self.read_until_closed(s) for s in sorted(self.live)))
        self.assertTrue(all(peer.closed for peer in self.peers))
        self.assertEqual(self.original.phase, "finished")
        self.assertEqual(self.original.departure_seats, ())
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertIsNot(self.server.default_table.match, self.original)
        self.assertEqual(self.server.default_table.match.players, [])
        self.assertIsNone(
            self.server.default_table.watchdog.observe(
                self.server.default_table.match, self.server.default_table.match
            )
        )
        self.assertEqual(self.server.economy.snapshot(), self.initial_accounts)
        self.assertTrue(all(not self.server.economy.is_busy(key) for key in self.keys))
        self.assertFalse(any(m.subtype == 0x0D for rows in messages for m in rows))
        return messages

    async def test_default_explicit_quit_disconnects_every_waiting_participant(self):
        await self.start(count=3, enabled=False)
        for side in range(3):
            await self.send(side, packet(0x43, 9, 0, f"@TurnData=/Ans={side}"))
        messages = await self.assert_explicit_abort(packet(0x43, 5, 10, "@Quit=/No=0"))
        self.assertEqual([m.subtype for m in messages[0]], [5])

    async def test_rated_explicit_quit_aborts_even_with_departures_enabled(self):
        await self.start(rating_mode="local-history")
        ratings = self.server.ratings.snapshot()
        await self.assert_explicit_abort(packet(0x43, 5, 10, "@Quit=/No=0"))
        self.assertEqual(self.server.ratings.snapshot(), ratings)

    async def test_economic_quit_refunds_once_and_restores_without_reservations(self):
        await self.start(allow_wagers=True, card_prizes=True, stake=137)
        self.assertTrue(all(self.server.economy.balance(key) == 9863 for key in self.keys))
        await self.assert_explicit_abort(packet(0x43, 5, 10, "@Quit=/No=0"))
        ledger = self.server.match_economy.snapshot()
        self.assertEqual(ledger["rounds"][0]["money"], "refunded")
        self.assertTrue(ledger["rounds"][0]["closed"])
        for _ in range(2):
            restored = Server(state_path=self.state_path)
            try:
                self.assertEqual(restored.economy.snapshot(), self.initial_accounts)
                self.assertEqual(restored.match_economy.snapshot(), ledger)
            finally:
                await restored.stop()

    async def test_card_prize_break_aborts_without_awarding_or_losing_cards(self):
        await self.start(card_prizes=True)
        character = self.server.economy.account_view(self.keys[0]).player_id
        await self.assert_explicit_abort(packet(0x43, 0x27, 3, f"@Break=/ID={character}"))
        self.assertEqual(self.server.result_rewards.snapshot()["pending"], [])

    @asynccontextmanager
    async def paused_abort_cleanup(self):
        """Let the survivor enter another service between the two closes."""
        first_closed, finish_close = asyncio.Event(), asyncio.Event()
        closing_tasks = []
        close = self.peers[0].close

        async def pause_after_close():
            closing_tasks.append(asyncio.current_task())
            await close()
            first_closed.set()
            await finish_close.wait()

        self.peers[0].close = pause_after_close
        try:
            await self.clients[0].send(
                b"NOTICE " + service_nick(TABLE_RAW) + b" :" + packet(0x43, 5, 10, "@Quit=/No=0")
            )
            await asyncio.wait_for(first_closed.wait(), 3)
            self.live.remove(0)
            yield
        finally:
            finish_close.set()
            if closing_tasks:
                await asyncio.wait_for(asyncio.shield(closing_tasks[0]), 3)

    async def test_abort_cleanup_does_not_close_survivor_rebound_to_another_table(self):
        room = OrdinaryRoomConfig(b"Other Room", 0x101, 0x201, b"#ROOM2", b"#TABLE2")
        await self.start(enabled=False, ordinary_rooms=(room,))
        async with self.paused_abort_cleanup():
            await self.clients[1].send(
                b"NOTICE "
                + service_nick(room.table_raw)
                + b" :"
                + packet(0x41, 1, body=f"@GameEN=/NN={player_id(self.clients[1].nick):016X}")
            )
            await self.flush(1)
            owner = self.server.table_for_peer(self.peers[1])
            self.assertIsNotNone(owner)
            self.assertEqual(owner.table_raw, room.table_raw)
        await self.flush(1)
        self.assertFalse(self.peers[1].closed)
        self.assertIs(self.server.table_for_peer(self.peers[1]), owner)
        self.assertTrue(self.server.economy.is_reserved(self.keys[1]))

    async def test_abort_cleanup_preserves_survivors_new_event_admission(self):
        event = config(match_enabled=True, card_service_enabled=True)
        table = EventTableConfig(event.id, game_id(0x1003), b"#EVTABLE001")
        await self.start(
            enabled=False, event_configs=(event,), event_tables=(table,), event_clock=lambda: 200
        )
        async with self.paused_abort_cleanup():
            view = self.server.economy.account_view(self.keys[1])
            messages = (
                (event.room_id, presence(event.room_id, view.nn, view.player_id, view.name)),
                (event.room_id, packet(0xD1, 4, body=f"@EventTimeReq=/NN={view.nn:016X}")),
                (
                    event.init_id,
                    packet(
                        0x98,
                        body=f"@EInitReq=/NN={view.nn:016X}/HID=0/CN={view.name.hex()}"
                        f"/Dm=1/Vol=1/GID={view.player_id:016X}",
                    ),
                ),
            )
            for target, payload in messages:
                await self.clients[1].send(
                    b"NOTICE " + service_nick(target ^ GAME_KEY) + b" :" + payload
                )
                await self.flush(1)
            coordinator = self.server.event_matches.coordinator
            self.assertTrue(coordinator.participant(self.peers[1]))
            self.assertIsNone(self.server.table_for_peer(self.peers[1]))
        await self.flush(1)
        self.assertFalse(self.peers[1].closed)
        self.assertTrue(coordinator.participant(self.peers[1]))
        self.assertTrue(self.server.economy.is_reserved(self.keys[1]))

    async def test_abort_cleanup_preserves_survivors_new_shop_session(self):
        await self.start(enabled=False)
        async with self.paused_abort_cleanup():
            view = self.server.economy.account_view(self.keys[1])
            await self.clients[1].send(
                b"NOTICE "
                + service_nick(SHOP_RAW)
                + b" :"
                + packet(
                    0xB0,
                    body=f"@ShReq=/NN={view.nn:016X}/HID=0/Dm=0/Vol=0/CN={view.name.hex()}",
                )
            )
            messages = await self.flush(1)
            self.assertTrue(any(b"@ShEnter=/EN=1" in p for p in messages))
            self.assertIsNone(self.server.table_for_peer(self.peers[1]))
        await self.flush(1)
        self.assertFalse(self.peers[1].closed)
        self.assertTrue(self.server.economy.is_busy(self.keys[1]))


if __name__ == "__main__":
    unittest.main()
