"""Native Computer-menu observers use real NPC identities and safe names."""

import asyncio
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests import test_observer_rules as rules
from tests import test_spectator_integration as base
from tests.test_server import Client
from tmserver.identities import TABLE_RAW, player_id, service_nick
from tmserver.match_service import MatchService, decode, packet
from tmserver.server import Server
from tmserver.spectator_service import SpectatorError, SpectatorService, bootstrap


class NPCObserverIntegration(unittest.IsolatedAsyncioTestCase):
    player = base.SpectatorIntegrationTests.player
    read_watch = base.SpectatorIntegrationTests.read_watch
    admit_watch = base.SpectatorIntegrationTests.admit_watch
    request_watch = base.SpectatorIntegrationTests.request_watch
    asyncTearDown = base.SpectatorIntegrationTests.asyncTearDown

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.server = Server(
            state_path=Path(self.temp.name) / "state.json",
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        port = await self.server.start(0)
        self.clients = []
        self.target = service_nick(TABLE_RAW)
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        identity = f"{player_id(self.clients[0].nick):016X}"
        await self.player(packet(65, 18, body=f"@GameENC=/NN={identity}/HID=1"))
        await self.player(packet(1, body=f"@Req=/NN={identity}/HID=1"))
        observation = getattr(self, "observation_rule", 0)
        await self.player(packet(67, 6, 2, f"@ComGame=/Rule=0|0|0|0|2|0|{observation}|1/Com=1"))
        cards = self.server.economy.inventory(self.clients[0].nick.decode())[:5]
        await self.player(
            packet(
                67,
                7,
                0,
                "@CardSelect=/C=5"
                + "".join(f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)),
            )
        )

    test_complete_original_NPC_round = (
        base.SpectatorIntegrationTests.test_real_watch_bootstrap_and_full_match_live_order
    )


class NPCObserveImpossible(unittest.IsolatedAsyncioTestCase):
    observation_rule = 1
    asyncSetUp = NPCObserverIntegration.asyncSetUp
    asyncTearDown = NPCObserverIntegration.asyncTearDown
    player = NPCObserverIntegration.player
    read_watch = NPCObserverIntegration.read_watch
    test_impossible = (
        rules.ObserveDisabledIntegration.test_advertised_impossible_blocks_discovery_and_admission_without_disconnect
    )


class NPCObserveNoComments(unittest.IsolatedAsyncioTestCase):
    observation_rule = 2
    asyncSetUp = NPCObserverIntegration.asyncSetUp
    asyncTearDown = NPCObserverIntegration.asyncTearDown
    player = NPCObserverIntegration.player
    read_watch = NPCObserverIntegration.read_watch
    admit_watch = NPCObserverIntegration.admit_watch
    request_watch = NPCObserverIntegration.request_watch
    test_no_comments = (
        rules.ObserveNoCommentsAdmission.test_no_comments_choice_still_allows_public_gameplay_observation
    )


if __name__ == "__main__":
    unittest.main()
