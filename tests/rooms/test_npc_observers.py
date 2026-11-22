"""Native Computer-menu observers use real NPC identities and safe names."""

import unittest

from tests.rooms import test_observer_rules as rules
from tests.rooms import test_spectator_integration as base
from tests.rooms.harness import instant_spectators, serve
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.game.packets import packet


class NPCObserverIntegration(unittest.IsolatedAsyncioTestCase):
    player = base.SpectatorIntegrationTests.player
    read_watch = base.SpectatorIntegrationTests.read_watch
    admit_watch = base.SpectatorIntegrationTests.admit_watch
    request_watch = base.SpectatorIntegrationTests.request_watch

    async def asyncSetUp(self):
        await serve(self, spectator=instant_spectators())
        self.target = service_nick(TABLE_RAW)
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
    player = NPCObserverIntegration.player
    read_watch = NPCObserverIntegration.read_watch
    test_impossible = (
        rules.ObserveDisabledIntegration.test_advertised_impossible_blocks_discovery_and_admission_without_disconnect
    )


class NPCObserveNoComments(unittest.IsolatedAsyncioTestCase):
    observation_rule = 2
    asyncSetUp = NPCObserverIntegration.asyncSetUp
    player = NPCObserverIntegration.player
    read_watch = NPCObserverIntegration.read_watch
    admit_watch = NPCObserverIntegration.admit_watch
    request_watch = NPCObserverIntegration.request_watch
    test_no_comments = (
        rules.ObserveNoCommentsAdmission.test_no_comments_choice_still_allows_public_gameplay_observation
    )


if __name__ == "__main__":
    unittest.main()
