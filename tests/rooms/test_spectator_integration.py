"""Opt-in spectator transport is ordered and cannot hold participant I/O."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.protocol.test_admission_identity import native_identity
from tests.game.test_match_service import NATIVE_SETTINGS
from tests.network.test_server import Client
from tmserver.protocol.identities import TABLE_RAW, service_nick
from tmserver.game.packets import decode, packet
from tmserver.rooms.packets import TABLE_CHANNEL
from tmserver.network.server import ACCOUNTS, Server
from tmserver.rooms.spectators import SpectatorService


class SpectatorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.server = Server(
            state_path=Path(self.temp.name) / "state.json",
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        self.port = await self.server.start(0)
        self.clients = [Client(), Client()]
        for client, nick in zip(self.clients, ACCOUNTS):
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.target = service_nick(TABLE_RAW)
        view = self.server.economy.account_view(self.clients[0].nick.decode())
        observation = getattr(self, "observation_rule", 0)
        # Captured ordinary CPU startup applies settings before GameReady/Req;
        # PLAYACK then carries the rules that GameOK accepts.
        await self.player(packet(0x41, 1, body=native_identity(view, "GameEN")))
        await self.player(packet(20, body=NATIVE_SETTINGS.replace("/in=0", f"/in={observation}")))
        await self.player(packet(0x41, 10, body="@GameReady="))
        replies = await self.player(packet(1, body=native_identity(view, "Req")))
        playack = next(decode(reply) for reply in replies if decode(reply).primary == 2)
        self.assertEqual(playack.sections["Tab"]["in"], str(observation))
        await self.player(packet(0x41, 8, body="@GameOK="))
        cards = self.server.economy.inventory(self.clients[0].nick.decode())[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
        )
        await self.player(packet(0x43, 7, 0, body))

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def player(self, payload):
        c = self.clients[0]
        await c.send(b"NOTICE " + self.target + b" :" + payload)
        await c.send(b"PING :player-barrier")
        out = []
        drained = False
        for _ in range(100):
            line = await c.read()
            self.assertTrue(line)
            if line == b"PONG player-barrier":
                task = self.server.default_table.match_task
                if drained and task is None:
                    return out
                if task is not None:
                    await asyncio.wait_for(asyncio.shield(task), 2)
                drained = True
                await c.send(b"PING :player-barrier")
            else:
                out.append(line.split(b" :", 1)[1])
        self.fail("player output limit exceeded")

    async def read_watch(self):
        line = await self.clients[1].read()
        self.assertTrue(line)
        self.assertTrue(line.startswith(b":" + self.target + b"!p@local NOTICE "))
        return line.split(b" :", 1)[1]

    async def admit_watch(self):
        c = self.clients[1]
        await c.send(b"NOTICE " + self.target + b" :" + packet(0x2D, body="@CheckJoinTable="))
        self.assertEqual(decode(await self.read_watch()).primary, 0x2E)
        await c.join(TABLE_CHANNEL)
        view = self.server.economy.account_view(c.nick.decode())
        await c.send(
            b"NOTICE " + self.target + b" :" + packet(1, body=native_identity(view, "Req"))
        )
        reply = decode(await self.read_watch())
        self.assertEqual(reply.primary, 2)
        self.assertEqual(reply.sections["PLAYACK"]["EN"], "1")
        self.assertFalse(self.server.economy.is_reserved(c.nick.decode()))
        self.assertEqual(len(self.server.default_table.match.players), 2)
        self.assertIsNone(self.server.default_table.match.players[1].peer)

    async def request_watch(self):
        await self.clients[1].send(
            b"NOTICE " + self.target + b" :" + packet(0x43, 0x25, 0, "@Data=/Watch=0")
        )

    async def test_viewer_switch_is_not_watch_entry_and_ExitCh_detaches_only_observer(self):
        await self.admit_watch()
        await self.request_watch()
        for _ in range(3):
            await self.read_watch()
        peer = self.server.nicks[self.clients[1].nick]
        match = self.server.default_table.match
        before = match.snapshot()
        tokens = dict(self.server.default_table.match_reservations)
        for body, expected in (("@Viewer=/IN=0", True), ("@Viewer=/OUT=0", False)):
            await self.clients[1].send(b"NOTICE " + self.target + b" :" + packet(65, 17, body=body))
            await self.clients[1].send(b"PING :viewer-barrier")
            self.assertEqual(await self.clients[1].read(), b"PONG viewer-barrier")
            self.assertEqual(peer in match.viewer_peers, expected)
        await self.clients[1].send(b"NOTICE " + self.target + b" :" + packet(32, body="@ExitCh="))
        await self.clients[1].send(b"PING :exit-barrier")
        self.assertEqual(await self.clients[1].read(), b"PONG exit-barrier")
        self.assertNotIn(peer, self.server.default_table.spectator_admissions)
        self.assertEqual(match.snapshot(), before)
        self.assertEqual(self.server.default_table.match_reservations, tokens)
        self.assertFalse(peer.closed)

    async def test_real_watch_bootstrap_and_full_match_live_order(self):
        await self.admit_watch()
        before = self.server.default_table.match.snapshot()
        economy = self.server.economy.snapshot()
        await self.request_watch()
        bootstrap = [await self.read_watch() for _ in range(3)]
        self.assertEqual([decode(r).subtype for r in bootstrap], [0x25, 0x26, 9])
        self.assertEqual(self.server.default_table.match.snapshot(), before)
        self.assertEqual(self.server.economy.snapshot(), economy)
        for _ in range(30):
            m = self.server.default_table.match
            if m.phase == "result":
                break
            await self.player(packet(0x43, 9, m.sequence, "@TurnData=/Ans=0"))
            if m.phase == "turn" and m.active == 0:
                await self.player(
                    packet(0x43, 10, m.sequence, f"@PutData=/P=0/H=0/F={m.legal_fields()[0]}")
                )
            while m.phase == "battle":
                await self.player(packet(0x43, 11, m.sequence, f"@BattleSelect=/B={m.targets[0]}"))
        self.assertEqual(m.phase, "result")
        live = []
        for _ in range(100):
            msg = decode(await self.read_watch())
            live.append(msg)
            if msg.subtype == 13:
                break
        else:
            self.fail("observer never received result")
        self.assertEqual([msg.argument for msg in live if msg.subtype == 9], list(range(1, 10)))
        self.assertEqual([msg.argument for msg in live if msg.subtype == 10], list(range(10)))
        self.assertEqual(sum(msg.subtype == 13 for msg in live), 1)
        self.assertFalse(any(msg.subtype == 0x1A for msg in live))
        await self.clients[1].send(
            b"NOTICE " + self.target + b" :" + packet(0x43, 9, 9, "@TurnData=/Ans=1")
        )
        self.assertEqual(await self.clients[1].read(), b"")
        self.assertEqual(self.server.default_table.match.phase, "result")
        self.assertFalse(self.server.nicks[self.clients[0].nick].closed)

    async def test_slow_observer_write_does_not_block_participant_or_timer(self):
        await self.admit_watch()
        peer = self.server.nicks[self.clients[1].nick]
        original = peer.send
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(message):
            entered.set()
            await release.wait()
            await original(message)

        with patch.object(peer, "send", side_effect=slow):
            try:
                await self.request_watch()
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.wait_for(self.player(packet(0x43, 9, 0, "@TurnData=/Ans=0")), 2)
                await asyncio.wait_for(self.player(packet(0x43, 10, 0, "@PutData=/P=0/H=0/F=0")), 2)
                self.assertTrue(
                    self.server.default_table.match.sequence > 0
                    or self.server.default_table.match.phase == "battle"
                )
                async with asyncio.timeout(1):
                    async with self.server.default_table.match_lock:
                        pass
            finally:
                release.set()
            self.assertEqual(
                [decode(await self.read_watch()).subtype for _ in range(3)], [0x25, 0x26, 9]
            )

    async def test_watch_without_channel_admission_has_no_player_or_account_effect(self):
        before = self.server.default_table.match.snapshot()
        await self.request_watch()
        self.assertEqual(await self.clients[1].read(), b"")
        self.assertEqual(self.server.default_table.match.snapshot(), before)
        self.assertFalse(self.server.economy.is_reserved(self.clients[1].nick.decode()))


if __name__ == "__main__":
    unittest.main()
