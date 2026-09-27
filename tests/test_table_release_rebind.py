"""Old PART/disconnect publication cannot close a survivor's new admission."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import patch

from tests.test_admission_identity import native_identity
from tests.test_event_rooms import config, presence
from tests.test_ordinary_rooms import EXTRA
from tests.test_session_recovery import Writer
from tmserver import services
from tmserver.event_matchmaking import EventTableConfig
from tmserver.identities import GAME_KEY, SHOP_RAW, game_id, service_nick
from tmserver.match_service import packet
from tmserver.ordinary_rooms import OrdinaryTableConfig
from tmserver.server import Server, Session
from tmserver.spectator_service import SpectatorService


class TableReleaseRebindTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def lobby(self):
        extra = replace(EXTRA, tables=(OrdinaryTableConfig(0x202, b"#TABLE3", "computer"),))
        self.event = config(match_enabled=True, card_service_enabled=True)
        self.event_table = EventTableConfig(self.event.id, game_id(0x1003), b"#EVTABLE001")
        self.server = Server(
            match_mode="versus",
            account_count=5,
            ordinary_rooms=(extra,),
            event_configs=(self.event,),
            event_tables=(self.event_table,),
            event_clock=lambda: 200,
            spectator=SpectatorService(enabled=True),
        )
        self.peers = []
        try:
            for index, nick in enumerate(self.server.accounts):
                peer = Session(self.server, None, Writer(), index + 1)
                peer.nick = nick
                self.server.ensure_account(nick)
                self.server.trade.bind(peer, nick.decode("ascii"))
                self.server.nicks[nick] = peer
                self.server.sessions.add(peer)
                self.peers.append(peer)
            for peer in self.peers[:2]:
                await peer.command(b"JOIN", [self.server.default_table.table_channel])
                await self.server.default_table.dispatch_table(peer, packet(65, 1, body="@GameEN="))
                await self.server.default_table.dispatch_table(peer, packet(1, body="@Req="))
            for peer in self.peers[:2]:
                await self.server.default_table.dispatch_table(peer, packet(65, 8, body="@GameOK="))
            self.assertEqual(self.server.default_table.match.phase, "selection")
            self.original = self.server.default_table.match
            self.sibling, self.unrelated = self.server.ordinary_tables[1:]
            await self.unrelated.dispatch_table(self.peers[2], packet(65, 1, body="@GameEN="))
            self.unrelated_before = (
                self.unrelated.match.snapshot(),
                dict(self.unrelated.match_reservations),
            )
            yield
        finally:
            await self.server.stop()

    @asynccontextmanager
    async def paused_cleanup(self, trigger):
        entered, resume = asyncio.Event(), asyncio.Event()
        publish = self.server.default_table.publish_room

        async def pause_first_publication(deliveries):
            if not entered.is_set():
                entered.set()
                await resume.wait()
            await publish(deliveries)

        with patch.object(
            self.server.default_table, "publish_room", side_effect=pause_first_publication
        ):
            owner = self.peers[0]
            task = asyncio.create_task(
                owner.part(self.server.default_table.table_channel)
                if trigger == "part"
                else owner.close()
            )
            try:
                await asyncio.wait_for(entered.wait(), 10)
                self.assertIsNot(self.server.default_table.match, self.original)
                self.assertFalse(self.server.default_table.match_reservations)
                yield
            finally:
                resume.set()
                await asyncio.wait_for(task, 10)
        self.assertEqual(
            (self.unrelated.match.snapshot(), self.unrelated.match_reservations),
            self.unrelated_before,
        )
        self.assertFalse(self.peers[2].closed)

    def assert_live(self, peer):
        self.assertFalse(peer.closed)
        self.assertFalse(peer.writer.closed)
        self.assertIs(self.server.nicks[peer.nick], peer)

    async def dispatch(self, peer, target, payload):
        self.assertTrue(await services.dispatch(peer, service_nick(target ^ GAME_KEY), payload))

    async def enter_event(self, peer, *, select=False):
        view = self.server.economy.account_view(peer.nick.decode("ascii"))
        await self.dispatch(
            peer,
            self.event.room_id,
            presence(self.event.room_id, view.nn, view.player_id, view.name),
        )
        await self.dispatch(
            peer, self.event.room_id, packet(0xD1, 4, body=f"@EventTimeReq=/NN={view.nn:016X}")
        )
        await self.dispatch(
            peer,
            self.event.init_id,
            packet(
                0x98,
                body=f"@EInitReq=/NN={view.nn:016X}/HID=0/CN={view.name.hex()}"
                f"/Dm=1/Vol=1/GID={view.player_id:016X}",
            ),
        )
        if select:
            session = self.server.event_matches.coordinator._entries[peer].session
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, card.full()))
                for i, card in enumerate(view.cards[:5])
            )
            await self.dispatch(peer, self.event.init_id, packet(0xB2, 7, session, body))

    async def start_watched_event(self):
        participants = self.peers[3:]
        for peer in participants:
            await self.enter_event(peer, select=True)
        adapter = self.server.event_matches
        async with adapter.lock:
            done = adapter._queue(adapter.coordinator.poll())
        await adapter._wait_delivery(done)
        for peer in participants:
            await self.dispatch(
                peer,
                self.event.room_id,
                packet(
                    0xD3,
                    body=f"@MuchMakeAns=/Ans=1/TblNo=1/TblId={self.event_table.table_id:016X}",
                ),
            )
        for peer in participants:
            view = self.server.economy.account_view(peer.nick.decode("ascii"))
            await self.dispatch(
                peer, self.event_table.table_id, packet(65, 1, body=native_identity(view, "GameEN"))
            )
            await peer.command(b"JOIN", [self.event_table.channel])
            await self.dispatch(
                peer, self.event_table.table_id, packet(1, body=native_identity(view, "Req"))
            )
            await self.dispatch(peer, self.event_table.table_id, packet(65, 8, body="@GameOK="))
        engine = adapter.coordinator.observer_context(self.event_table.table_id)[-1]
        self.assertEqual(engine.phase, "turn")

    async def test_survivor_can_enter_same_or_sibling_table_while_cleanup_waits(self):
        for trigger in ("part", "disconnect"):
            for target in ("same", "sibling"):
                with self.subTest(trigger=trigger, target=target):
                    async with self.lobby():
                        peer = self.peers[1]
                        table = self.server.default_table if target == "same" else self.sibling
                        async with self.paused_cleanup(trigger):
                            await table.dispatch_table(peer, packet(65, 1, body="@GameEN="))
                            current = table.match
                            reservation = table.match_reservations[peer]
                        self.assert_live(peer)
                        self.assertIs(table.match, current)
                        self.assertIs(self.server.table_for_peer(peer), table)
                        self.assertEqual(table.match_reservations[peer], reservation)

    async def test_new_event_participant_is_preserved(self):
        for trigger in ("part", "disconnect"):
            with self.subTest(trigger=trigger):
                async with self.lobby():
                    peer = self.peers[1]
                    async with self.paused_cleanup(trigger):
                        await self.enter_event(peer)
                        self.assertIsNone(self.server.table_for_peer(peer))
                    self.assert_live(peer)
                    self.assertTrue(self.server.event_matches.coordinator.participant(peer))
                    self.assertTrue(self.server.economy.is_reserved(peer.nick.decode("ascii")))

    async def test_new_event_observer_without_reservation_is_preserved(self):
        for trigger in ("part", "disconnect"):
            with self.subTest(trigger=trigger):
                async with self.lobby():
                    await self.start_watched_event()
                    peer = self.peers[1]
                    async with self.paused_cleanup(trigger):
                        view = self.server.economy.account_view(peer.nick.decode("ascii"))
                        await self.dispatch(
                            peer,
                            self.event.room_id,
                            presence(self.event.room_id, view.nn, view.player_id, view.name),
                        )
                        await peer.command(b"JOIN", [self.event_table.channel])
                        await self.dispatch(
                            peer,
                            self.event_table.table_id,
                            packet(1, body=native_identity(view, "Req")),
                        )
                        self.assertFalse(self.server.economy.is_busy(peer.nick.decode("ascii")))
                    self.assert_live(peer)
                    self.assertIn(peer, self.server.event_matches.spectators.admissions)
                    self.assertFalse(self.server.event_matches.coordinator.participant(peer))

    async def test_new_shop_session_is_preserved(self):
        for trigger in ("part", "disconnect"):
            with self.subTest(trigger=trigger):
                async with self.lobby():
                    peer = self.peers[1]
                    async with self.paused_cleanup(trigger):
                        view = self.server.economy.account_view(peer.nick.decode("ascii"))
                        await self.dispatch(
                            peer,
                            game_id(SHOP_RAW),
                            packet(
                                0xB0,
                                body=f"@ShReq=/NN={view.nn:016X}/HID=0/Dm=0/Vol=0"
                                f"/CN={view.name.hex()}",
                            ),
                        )
                        self.assertIsNone(self.server.table_for_peer(peer))
                    self.assert_live(peer)
                    self.assertTrue(self.server.economy.is_busy(peer.nick.decode("ascii")))

    async def test_idle_survivor_still_reconnects_and_accounts_are_unchanged(self):
        for trigger in ("part", "disconnect"):
            with self.subTest(trigger=trigger):
                async with self.lobby():
                    before = self.server.economy.snapshot()
                    async with self.paused_cleanup(trigger):
                        pass
                    self.assertTrue(self.peers[1].closed)
                    self.assertFalse(self.server.default_table.match_reservations)
                    self.assertFalse(self.server.economy.is_reserved(self.peers[1].nick.decode()))
                    self.assertEqual(self.server.economy.snapshot(), before)

    async def test_failed_initial_release_still_closes_original_dependent(self):
        async with self.lobby():
            owner, dependent = self.peers[:2]
            release = self.server.default_table.table_release_updates

            def fail_first(peer):
                if peer is owner:
                    raise OSError("injected initial cleanup failure")
                return release(peer)

            with patch.object(
                self.server.default_table, "table_release_updates", side_effect=fail_first
            ):
                await owner.close()
            self.assertTrue(dependent.closed)
            self.assertIsNot(self.server.default_table.match, self.original)
            self.assertFalse(self.server.default_table.match_reservations)
            self.assertFalse(self.server.economy.is_reserved(dependent.nick.decode()))


if __name__ == "__main__":
    unittest.main()
