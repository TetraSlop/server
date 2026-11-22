"""Actual encrypted native gameplay with real PostgreSQL persistence."""

import unittest
from typing import ClassVar
from unittest.mock import patch

from tests.storage import test_postgres_protocol as protocol
from tests.storage.test_postgres_store import TEST_URL
from tests.economy import test_match_prizes_integration as prizes
from tests.economy import test_match_wagers_integration as wagers
from tmserver.game.packets import packet

@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class PostgresMatchTests(protocol.ProtocolFixture):
    player_count = 2
    options: ClassVar[dict] = {
        "match_mode": "versus",
        "card_prizes": True,
        "allow_wagers": True,
        "rating_mode": "local-history",
    }
    flush = prizes.PrizeIntegrationTests.flush
    send = prizes.PrizeIntegrationTests.send
    play = prizes.PrizeIntegrationTests.play
    choose = prizes.PrizeIntegrationTests.choose
    admit = wagers.WagerIntegrationTests.admit

    async def test_complete_match_prizes_stakes_ratings_and_restart(self):
        await self.admit()
        await self.play()
        await self.choose()
        self.assertEqual(sum(self.server.economy.balance(key) for key in self.keys), 20000)
        for side in range(2):
            await self.send(side, packet(0x43, 17, side, "@Continue=/Ans=1/PR=0/Conf=0"))
        expected = await self.assert_restart()
        self.assertTrue(expected["ratings"]["history"]["accounts"])
        self.assertTrue(expected["match_economy"]["rounds"])
        self.assertEqual(sum(len(self.server.economy.inventory(k)) for k in self.keys), 20)


@unittest.skipUnless(TEST_URL, "set TM_TEST_DATABASE_URL for real PostgreSQL tests")
class ScopedMatchProtocolTests(protocol.ProtocolFixture):
    player_count = 2
    options = PostgresMatchTests.options
    flush = PostgresMatchTests.flush
    send = PostgresMatchTests.send
    play = PostgresMatchTests.play
    choose = PostgresMatchTests.choose
    admit = PostgresMatchTests.admit

    async def test_encrypted_match_never_calls_the_full_save_path(self):
        with (
            patch.object(self.server, "persist", side_effect=AssertionError("full match save")),
            patch.object(self.store, "save", side_effect=AssertionError("full snapshot write")),
        ):
            await PostgresMatchTests.test_complete_match_prizes_stakes_ratings_and_restart(
                self
            )
