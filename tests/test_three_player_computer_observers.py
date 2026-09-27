"""Encrypted early/late observers of one human and two original NPCs.

Original WatchInfo 10101B15..101020EF reads C for each of N seats and G[N]
as human count. Read-only receiver probes checked N3/C0|1|30/G0|0|0|1 at
viewer levels0/65535; native presentation timing remains unverified.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests import test_spectator_integration as observers
from tests.test_admission_identity import native_identity
from tests.test_server import Client
from tmserver.identities import TABLE_RAW, service_nick
from tmserver.match_service import decode, packet
from tmserver.native_opponents import opponent
from tmserver.room_service import TABLE_CHANNEL
from tmserver.server import Server
from tmserver.spectator_service import SpectatorService


class ThreePlayerComputerObservers(unittest.IsolatedAsyncioTestCase):
    player = observers.SpectatorIntegrationTests.player
    asyncTearDown = observers.SpectatorIntegrationTests.asyncTearDown

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.state_path = Path(self.temp.name) / "state.json"
        self.server = Server(
            state_path=self.state_path,
            account_count=3,
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        self.port = await self.server.start(0)
        self.target = service_nick(TABLE_RAW)
        self.clients = []
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        view = self.server.economy.account_view(self.clients[0].nick.decode())
        await self.player(packet(0x41, 0x12, body=native_identity(view, "GameENC")))
        await self.player(packet(1, body=native_identity(view, "Req")))
        await self.player(packet(0x43, 6, 3, "@ComGame=/Rule=0|0|0|0|2|0|0|1/Com=1|30"))
        self.assertEqual(self.server.match.phase, "selection")
        self.observer_accounts = {
            client.nick.decode(): self.server.snapshot()["accounts"][client.nick.decode()]
            for client in self.clients[1:]
        }

    async def read_watch(self, client):
        line = await client.read()
        self.assertTrue(line.startswith(b":" + self.target + b"!p@local NOTICE "), line)
        payload = line.split(b" :", 1)[1]
        self.assertLessEqual(len(payload), 469)
        return payload

    async def watch(self, client, *, joined=False):
        await client.send(b"NOTICE " + self.target + b" :" + packet(0x2D, body="@CheckJoinTable="))
        self.assertEqual(
            decode(await self.read_watch(client)).sections["CheckJoinTable_Ans"],
            {"Join": "1", "Mem": "0"},
        )
        if not joined:
            await client.join(TABLE_CHANNEL)
        view = self.server.economy.account_view(client.nick.decode())
        await client.send(
            b"NOTICE " + self.target + b" :" + packet(1, body=native_identity(view, "Req"))
        )
        reply = decode(await self.read_watch(client))
        self.assertEqual((reply.primary, reply.sections["PLAYACK"]["EN"]), (2, "1"))
        await client.send(
            b"NOTICE " + self.target + b" :" + packet(0x43, 0x25, 0, "@Data=/Watch=0")
        )

    def check_roster(self, payload):
        message = decode(payload)
        self.assertEqual((message.primary, message.subtype), (0x43, 0x25))
        info = message.sections["WatchInfo"]
        self.assertEqual(info["N"], "3")
        self.assertEqual(info["C"], "0|1|30")
        self.assertEqual(info["Q"], "0|0|0")
        self.assertEqual(info["G"], "0|0|0|1")
        self.assertEqual(info["L"], "0|0|0")
        self.assertEqual(
            tuple(bytes.fromhex(name) for name in info["CN"].split("|")),
            (
                self.server.economy.account_view(self.clients[0].nick.decode()).name,
                opponent(1).name[:15],
                opponent(30).name[:15],
            ),
        )
        return info

    async def advance(self):
        match = self.server.match
        await self.player(packet(0x43, 9, match.sequence, "@TurnData=/Ans=0"))
        if match.phase == "turn" and match.active == 0:
            await self.player(
                packet(0x43, 10, match.sequence, f"@PutData=/P=0/H=0/F={match.legal_fields()[0]}")
            )
        while match.phase == "battle":
            self.assertEqual(match.active, 0, "both NPCs must make their own battle choices")
            await self.player(
                packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}")
            )

    async def result_stream(self, client):
        result = []
        for _ in range(200):
            message = decode(await self.read_watch(client))
            self.assertIn(message.subtype, SpectatorService.PUBLIC_SUBTYPES)
            result.append(message)
            if message.subtype == 13:
                self.assertEqual(message.sections["ResultData"]["P"], "3")
                return result
        self.fail("observer never received the three-seat result")

    async def test_early_and_late_observers_follow_both_npcs_without_account_changes(self):
        human, early, late = self.clients
        # Join both first so a later JOIN cannot be mistaken for game output.
        await early.join(TABLE_CHANNEL)
        await late.join(TABLE_CHANNEL)
        self.assertIn(b"JOIN :" + TABLE_CHANNEL, await early.read())
        match = self.server.match
        before = match.snapshot()
        await self.watch(early, joined=True)
        boot = await self.read_watch(early)
        self.assertEqual(decode(boot).argument, 0)
        self.assertNotIn("H", self.check_roster(boot))
        self.assertEqual(match.snapshot(), before)
        cards = self.server.economy.inventory(human.nick.decode())[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(cards)
        )
        await self.player(packet(0x43, 7, 0, body))
        for _ in range(3):
            await self.advance()
        self.assertEqual((match.phase, match.sequence), ("turn", 3))
        snapshot = match.observer_snapshot()
        before = match.snapshot()
        await self.watch(late, joined=True)
        bootstrap = [await self.read_watch(late) for _ in range(3)]
        self.assertEqual([decode(p).subtype for p in bootstrap], [0x25, 0x26, 9])
        self.assertEqual(decode(bootstrap[0]).argument, 1)
        info = self.check_roster(bootstrap[0])
        self.assertEqual((info["T"], info["A"]), ("3", str(snapshot.active)))
        self.assertEqual(tuple(map(int, info["H"].split("|"))), (4, 4, 4))
        self.assertEqual(len(info["F"].split("|")), 25)
        self.assertEqual(len(info["CH"].split("|")), 25)
        visible = {
            f"N{i}": {"D": cell.card.reduced()}
            for i, cell in enumerate(snapshot.board)
            if cell.card is not None
        }
        self.assertEqual(len(visible), 3)
        self.assertEqual(decode(bootstrap[1]).sections, {"WatchCard": {"T": "3"}, **visible})
        self.assertEqual(bootstrap[2], snapshot.turn_packet)
        self.assertEqual(match.snapshot(), before)
        for _ in range(60):
            if match.phase == "result":
                break
            await self.advance()
        self.assertEqual(match.phase, "result")
        early_live = await self.result_stream(early)
        late_live = await self.result_stream(late)
        start = next(m.sections["StartData"] for m in early_live if m.subtype == 8)
        self.assertEqual(len(start["F"].split("|")), 25)
        self.assertEqual(len(start["L"].split("|")), 3)
        for stream, first_turn, first_put in ((early_live, 0, 0), (late_live, 4, 3)):
            turns = [m for m in stream if m.subtype == 9]
            puts = [m for m in stream if m.subtype == 10]
            self.assertEqual([m.argument for m in turns], list(range(first_turn, 15)))
            self.assertEqual([m.argument for m in puts], list(range(first_put, 15)))
            self.assertEqual({m.sections["PutCard"]["A"] for m in puts}, {"0", "1", "2"})
            self.assertEqual(sum(m.subtype == 13 for m in stream), 1)
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        for client in (early, late):
            key = client.nick.decode()
            self.assertFalse(self.server.economy.is_busy(key))
            self.assertEqual(self.server.snapshot()["accounts"][key], self.observer_accounts[key])
            self.assertEqual(saved["accounts"][key], self.observer_accounts[key])
        self.assertEqual(set(self.server.match_reservations), {self.server.nicks[human.nick]})
        await late.send(b"NOTICE " + self.target + b" :" + packet(0x43, 9, 15, "@TurnData=/Ans=0"))
        self.assertEqual(await late.read(), b"")
        for client in (human, early):
            await client.send(b"PING :unaffected")
            self.assertEqual(await client.read(), b"PONG unaffected")
        self.assertEqual(match.phase, "result")
