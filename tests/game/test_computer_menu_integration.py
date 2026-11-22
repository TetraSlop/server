"""Original computer menu and NPC rounds through encrypted game transport."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.network import test_services_integration as service_tests
from tests.support.irc import Client
from tests.support.memory_store import MemoryStore
from tmserver.cards.catalog import collection_points
from tmserver.protocol.identities import TABLE_RAW, player_id
from tmserver.game.packets import decode, packet
from tmserver.game.opponents import OPPONENTS
from tmserver.rankings.stats import PracticeStats
from tmserver.rooms.packets import TABLE_CHANNEL
from tmserver.network.server import Server


class ComputerMenuIntegrationTests(unittest.IsolatedAsyncioTestCase):
    request = service_tests.ServicesIntegrationTests.request

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.store = MemoryStore()
        self.server = Server(store=self.store)
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def menu(self):
        identity = f"{player_id(self.client.nick):016X}"
        from tests.protocol.test_admission_identity import native_identity

        view = self.server.economy.account_view(self.key)
        replies = await self.request(
            TABLE_RAW, packet(0x41, 0x12, body=native_identity(view, "GameENC"))
        )
        self.assertEqual(decode(replies[0]).subtype, 0x13)
        replies = await self.request(TABLE_RAW, packet(1, body=native_identity(view, "Req")))
        self.assertEqual(
            [(decode(r).primary, decode(r).subtype) for r in replies], [(2, 0), (0x43, 2)]
        )
        info = decode(replies[1]).sections
        self.assertEqual(info["ComGameInit"]["S"], "10")
        self.assertEqual(
            int(info["ComGameInit"]["CP"]),
            collection_points(self.server.economy.inventory(self.key)),
        )
        self.assertEqual(
            list(map(int, info["ComList"]["D"].split("|"))), [n.rating for n in OPPONENTS]
        )
        self.assertEqual(self.server.default_table.match.phase, "computer_menu")
        self.assertEqual(
            self.server.default_table.watchdog.observe(self.server.default_table.match).reason,
            "computer_configuration",
        )

    async def choose(self, npc):
        self.assertEqual(
            await self.request(
                TABLE_RAW, packet(0x43, 6, 2, f"@ComGame=/Rule=0|0|0|0|2|0|0|1/Com={npc}")
            ),
            [],
        )
        self.assertEqual(
            (self.server.default_table.match.phase, self.server.default_table.match.npc_id),
            ("selection", npc),
        )

    async def play(self):
        previous_count = PracticeStats.from_profile(
            self.server.economy.profile_bytes(self.key)
        ).vs_player_games
        cards = self.server.economy.inventory(self.key)[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
        )
        replies = await self.request(TABLE_RAW, packet(0x43, 7, 0, body))
        self.assertEqual([decode(r).subtype for r in replies], [7, 8, 9])
        self.assertFalse(any(b"VsGameInit" in r or b"/CN=" in r for r in replies))
        for _ in range(30):
            match = self.server.default_table.match
            if match.phase == "result":
                break
            await self.request(TABLE_RAW, packet(0x43, 9, match.sequence, "@TurnData=/Ans=0"))
            if match.phase == "turn" and match.active == 0:
                await self.request(
                    TABLE_RAW,
                    packet(
                        0x43, 10, match.sequence, f"@PutData=/P=0/H=0/F={match.legal_fields()[0]}"
                    ),
                )
            while match.phase == "battle":
                await self.request(
                    TABLE_RAW,
                    packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}"),
                )
        self.assertEqual(match.phase, "result")
        self.assertEqual(
            PracticeStats.from_profile(self.server.economy.profile_bytes(self.key)).vs_player_games,
            previous_count,
        )
        replies = await self.request(TABLE_RAW, packet(0x43, 14, 10, "@Ready=/Ans=0"))
        self.assertEqual(decode(replies[0]).subtype, 0x1A)

    async def test_original_menu_three_rounds_rematch_reconfigure_and_durable_leave(self):
        self.assertFalse(self.server.default_table.match.computer_menu)
        await self.menu()
        await self.choose(1)
        await self.play()
        self.assertEqual(self.server.default_table.match.players[1].name, b"Flower Girl Natasha")
        old = self.server.default_table.match.ai_cards
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.assertNotEqual(self.server.default_table.match.ai_cards, old)
        await self.play()
        replies = await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=1/PR=0/Conf=1"))
        self.assertEqual([decode(r).subtype for r in replies], [4])
        self.assertEqual(self.server.default_table.match.phase, "computer_menu")
        await self.choose(30)
        await self.play()
        self.assertEqual(self.server.default_table.match.completed_matches, 3)
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=2/PR=0/Conf=0"))
        self.assertEqual(self.server.default_table.match.phase, "finished")
        self.assertFalse(self.server.economy.is_reserved(self.key))
        restored = Server(store=self.store)
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
        finally:
            await restored.stop()

    async def test_original_entry_is_per_table_generation(self):
        await self.client.join(TABLE_CHANNEL)
        await self.menu()
        self.assertTrue(self.server.default_table.match.computer_menu)
        await self.request(TABLE_RAW, packet(0x41, 0x0D, body="@GameExit="))
        await self.client.send(b"PART :" + TABLE_CHANNEL)
        self.assertIn(b" PART ", await self.client.read())
        await self.client.send(b"PING :released")
        self.assertEqual(await self.client.read(), b"PONG released")
        self.assertFalse(self.server.default_table.match.computer_menu)
        self.assertEqual(self.server.default_table.match.players, [])
        self.assertFalse(self.server.economy.is_reserved(self.key))


if __name__ == "__main__":
    unittest.main()
