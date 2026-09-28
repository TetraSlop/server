"""Expired event notifications cannot close a connection's newer service."""

import asyncio
import unittest

from tests.events.test_event_rooms import config, presence
from tests.network.test_session_recovery import Writer
from tests.rooms.test_table_abort_cleanup import BlockingWriter
from tmserver.economy.service import named
from tmserver.events.matchmaking import EventTableConfig
from tmserver.protocol.identities import SHOP_RAW, TABLE_RAW, game_id, service_nick
from tmserver.game.match import packet
from tmserver.network.server import Server, Session


class EventQueuedCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def check_reentry(self, destination):
        cfg = config(match_enabled=True, card_service_enabled=True)
        table = EventTableConfig("festival", game_id(0x1003), b"#EVTABLE001")
        server = Server(event_configs=(cfg,), event_tables=(table,), event_clock=lambda: 200)
        adapter = server.event_matches
        clock = [0.0]
        adapter.coordinator.clock = lambda: clock[0]
        peers, blocked, driver = [], BlockingWriter(), None

        async def send(peer, raw, payload):
            await peer.command(b"NOTICE", [service_nick(raw), payload])

        def entry(peer):
            view = server.economy.account_view(peer.nick.decode())
            return packet(0x98, body=(
                f"@EInitReq=/NN={view.nn:016X}/HID=0/CN={view.name.hex()}"
                f"/Dm=1/Vol=1/GID={view.player_id:016X}"
            ))

        try:
            for i, nick in enumerate(server.accounts):
                peer = Session(server, None, blocked if i == 0 else Writer(), i + 1)
                peer.nick = nick
                server.ensure_account(nick)
                server.trade.bind(peer, nick.decode())
                server.nicks[nick] = peer
                server.sessions.add(peer)
                peers.append(peer)
                view = server.economy.account_view(nick.decode())
                await send(peer, 0x1000, presence(cfg.room_id, view.nn, view.player_id, view.name))
                await send(peer, 0x1001, entry(peer))
                cards = "@CardSelect=/C=5" + "".join(
                    f"@N{i}=/D=" + "|".join(map(str, c.full()))
                    for i, c in enumerate(view.cards[:5])
                )
                await send(peer, 0x1001, packet(0xB2, 7, adapter.coordinator.session(peer), cards))
            async with adapter.lock:
                done = adapter._queue(adapter.coordinator.poll(), adapter.coordinator.take_closures())
            await adapter._wait_delivery(done)
            clock[0] = adapter.coordinator.snapshot("festival")["deadline"]
            blocked.block = True
            driver = asyncio.create_task(adapter._run())
            await asyncio.wait_for(blocked.entered.wait(), 2)
            survivor, key = peers[1], peers[1].nick.decode()
            self.assertIsNone(adapter.coordinator.snapshot("festival"))
            self.assertFalse(server.economy.is_busy(key))
            entering = None
            if destination == "shop":
                view = server.economy.account_view(key)
                request = named(0xB0, 0, 0,
                    f"@ShReq=/NN={view.nn:016X}/HID=0/Dm=0/Vol=0/CN={view.name.hex()}")
                await send(survivor, SHOP_RAW, request)
                self.assertTrue(server.economy.is_busy(key))
            elif destination == "ordinary":
                await send(survivor, TABLE_RAW, packet(0x41, 1, body="@GameEN="))
                token = server.default_table.match_reservations[survivor]
                current = server.default_table.match
            elif destination == "event":
                # Admission is synchronous; its replies wait behind old notifications.
                entering = asyncio.create_task(send(survivor, 0x1001, entry(survivor)))
                await asyncio.sleep(0)
                current = adapter.coordinator._entries[survivor]
            before = server.snapshot()
            blocked.release.set()
            if adapter.sender is not None:
                await asyncio.wait_for(asyncio.shield(adapter.sender), 2)
            if entering is not None:
                await asyncio.wait_for(entering, 2)
            self.assertTrue(peers[0].closed)
            self.assertEqual(survivor.closed, destination == "none")
            self.assertEqual(server.snapshot(), before)
            self.assertEqual(server.economy.is_busy(key), destination != "none")
            if destination == "ordinary":
                self.assertIs(server.default_table.match, current)
                self.assertIs(server.default_table.match_reservations[survivor], token)
            elif destination == "event":
                self.assertIs(adapter.coordinator._entries[survivor], current)
            if destination != "none":
                self.assertIs(server.nicks[survivor.nick], survivor)
        finally:
            blocked.release.set()
            if driver is not None:
                driver.cancel()
                await asyncio.gather(driver, return_exceptions=True)
            await server.stop()

    async def test_expired_event_preserves_new_service_and_closes_unclaimed_peers(self):
        for destination in ("none", "ordinary", "shop", "event"):
            with self.subTest(destination=destination):
                await self.check_reentry(destination)


if __name__ == "__main__":
    unittest.main()
