"""The reported wagered COM request through CLI-owned authenticated services."""

from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.auth.test_transport import local_bridge
from tests.game import test_three_player_computer_integration as computer
from tests.support.irc import Client
from tmserver.cli.serve import arguments, running_services
from tmserver.game.packets import packet
from tmserver.protocol.identities import TABLE_RAW, service_nick
from tmserver.storage.json import JsonStateStore


USER = "11111111-1111-4111-8111-111111111111"
CHOICE = packet(0x43, 6, 3, "@ComGame=/Rule=310|0|0|0|2|0|0|0/Com=3|4")


class HostedComputerMatchTests(unittest.IsolatedAsyncioTestCase):
    menu = computer.ThreePlayerComputerIntegration.menu
    request = computer.ThreePlayerComputerIntegration.request
    play = computer.ThreePlayerComputerIntegration.play
    continue_game = computer.ThreePlayerComputerIntegration.continue_game

    @asynccontextmanager
    async def connected(self, *, wagers):
        import httpx
        from tmserver.auth.provider import Subject

        async def verified(verifier, token):
            self.assertEqual(token, "fixture-access")
            return Subject(issuer=verifier.issuer, subject=USER, discord=True, display_name="Discord Player")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "auth.json"
            self.state_path = root / "save.json"
            config.write_text(json.dumps(dict(
                supabase_url="https://fixture.supabase.co", publishable_key="sb_publishable_fixture",
                public_origin="https://example.test",
            )), encoding="utf8")
            self.args = arguments([
                "--auth-config", str(config), "--auth-insecure-loopback", "--auth-port", "0",
                "--game-port", "0", "--profile-port", "0", "--accounts", "3",
                "--rooms", str(ROOT / "examples/auto_rooms.example.json"),
                "--state", str(self.state_path), "--log-dir", str(root / "logs"),
                *(["--wagers"] if wagers else []),
            ])
            with patch("tmserver.auth.provider.SupabaseVerifier.verify", verified):
                async with running_services(self.args) as services:
                    self.server = services.game
                    origin = f"http://127.0.0.1:{services.auth.port}"
                    async with httpx.AsyncClient(base_url=origin, trust_env=False) as http:
                        ticket = await http.post("/v1/play/tickets", json={"protocol": 1},
                                                headers={"Authorization": "Bearer fixture-access"})
                        self.assertEqual(ticket.status_code, 201)
                        launch = await http.post("/v1/play/sessions", json={"ticket": ticket.json()["ticket"]})
                        self.assertEqual(launch.status_code, 201)
                        play = launch.json()
                    async with local_bridge(play["streams"]["game"], play["session_token"]) as port:
                        self.client = Client()
                        await self.client.open(port, play["account_key"].encode(), play["legacy"]["password"].encode())
                        try:
                            self.assertIn(b" 422 ", await self.client.read())
                            self.key = self.client.nick.decode()
                            self.other = self.server.ensure_account(list(self.server.accounts)[1])
                            self.other_before = self.server.snapshot()["accounts"][self.other]
                            await self.menu()
                            yield
                        finally:
                            await self.client.close()
                saved = JsonStateStore(self.state_path).load()
                self.assertEqual(saved["accounts"][self.key]["balance"], self.expected_balance)

    async def test_wagered_two_opponent_match_finishes_through_websocket(self):
        async with self.connected(wagers=True):
            await self.request(TABLE_RAW, CHOICE)
            match = self.server.default_table.match
            self.assertEqual(match.phase, "selection")
            self.assertEqual(tuple(player.npc_id for player in match.players[1:]), (3, 4))
            self.assertEqual(self.server.economy.balance(self.key), 9690)
            self.assertEqual(JsonStateStore(self.state_path).load()["accounts"][self.key]["balance"], 9690)
            await self.play()
            await self.continue_game(leave=True)
            self.assertEqual(match.phase, "finished")
            self.assertFalse(self.server.economy.is_reserved(self.key))
            self.expected_balance = self.server.economy.balance(self.key)
            await self.client.send(b"PING :after-com-result")
            self.assertEqual(await self.client.read(), b"PONG after-com-result")

    async def test_disabled_wagers_reject_before_deducting_balance(self):
        async with self.connected(wagers=False):
            await self.client.send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + CHOICE)
            self.assertEqual(await self.client.read(), b"")
            self.assertEqual(self.server.economy.balance(self.key), 10000)
            self.expected_balance = 10000
