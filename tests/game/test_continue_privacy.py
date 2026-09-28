"""Result-screen ranking choice persists and drives original ranking display."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.game import test_computer_menu_integration as computer
from tests.game.test_match_service import finish_arrowless_round, service
from tmserver.economy.service import EconomyError, EconomyService
from tmserver.protocol.identities import TABLE_RAW
from tmserver.game.match import MatchError, packet
from tmserver.rankings.service import RECORD_SIZE, RankingService
from tmserver.network.server import Server


class PrivacyTests(unittest.TestCase):

    def test_preference_changes_only_one_byte_and_preserves_reservation(self):
        e = EconomyService()
        e.provision("one")
        token = e.reserve_match("one")
        before = e.profile_bytes("one")
        e.save_ranking_privacy("one", 1)
        after = e.profile_bytes("one")
        self.assertEqual(after[:0xB6] + after[0xB7:], before[:0xB6] + before[0xB7:])
        self.assertEqual(after[0xB6], 1)
        self.assertTrue(e.is_reserved("one"))
        self.assertEqual(e.match_account(token).key, "one")
        for value in (-1, 2, True, "1", None):
            with self.subTest(value=value), self.assertRaises(EconomyError):
                e.save_ranking_privacy("one", value)
            self.assertEqual(e.profile_bytes("one"), after)

    def test_invalid_privacy_and_wrong_seat_cannot_vote_or_rematch(self):
        s, _ = service("versus")
        finish_arrowless_round(s)
        for value in ("2", "-1", "1|0", "", "x"):
            with self.subTest(value=value), self.assertRaises(MatchError):
                s.handle("alice", packet(0x43, 17, 0, f"@Continue=/Ans=1/PR={value}/Conf=0"))
            self.assertEqual(s.phase, "result")
            self.assertEqual(s.continues, {})
        with self.assertRaises(MatchError):
            s.handle("alice", packet(0x43, 17, 1, "@Continue=/Ans=1/PR=1/Conf=0"))
        self.assertEqual(s.continues, {})
        s.handle("alice", packet(0x43, 17, 0, "@Continue=/Ans=1/PR=1/Conf=0"))
        s.handle("bob", packet(0x43, 17, 1, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.assertEqual(s.phase, "selection")


class PrivacyIntegration(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = computer.ComputerMenuIntegrationTests.asyncSetUp
    asyncTearDown = computer.ComputerMenuIntegrationTests.asyncTearDown
    request = computer.ComputerMenuIntegrationTests.request
    menu = computer.ComputerMenuIntegrationTests.menu
    choose = computer.ComputerMenuIntegrationTests.choose
    play = computer.ComputerMenuIntegrationTests.play

    def check(self, value):
        self.assertEqual(self.server.economy.profile_bytes(self.key)[0xB6], value)
        rows = RankingService(self.server.economy).records(2)
        self.assertEqual(len(rows), RECORD_SIZE)
        self.assertEqual(rows[0xC0], value)

    async def test_rematch_reconfigure_leave_and_restart_keep_native_privacy(self):
        await self.menu()
        await self.choose(1)
        await self.play()
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=1/PR=1/Conf=0"))
        self.check(1)
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.check(1)
        await self.play()
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=1/PR=0/Conf=1"))
        self.check(0)
        await self.choose(30)
        await self.play()
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=2/PR=1/Conf=0"))
        self.check(1)
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.economy.profile_bytes(self.key)[0xB6], 1)
            self.assertEqual(RankingService(restored.economy).records(2)[0xC0], 1)
        finally:
            await restored.stop()

    async def test_invalid_choice_changes_neither_profile_nor_result(self):
        await self.menu()
        await self.choose(1)
        await self.play()
        peer = self.server.nicks[self.client.nick]
        before = self.server.economy.snapshot()
        for seat, privacy in ((0, 2), (1, 1)):
            with self.assertRaises(MatchError):
                await self.server.default_table.dispatch_table(
                    peer, packet(0x43, 17, seat, f"@Continue=/Ans=1/PR={privacy}/Conf=0")
                )
            self.assertEqual(self.server.default_table.match.phase, "result")
            self.assertEqual(self.server.economy.snapshot(), before)

    async def test_storage_failure_withholds_continue_acknowledgement(self):
        await self.menu()
        await self.choose(1)
        await self.play()
        peer = self.server.nicks[self.client.nick]
        with patch.object(
            self.server, "persist_changes", side_effect=OSError("injected storage failure")
        ):
            with patch.object(self.server, "deliver") as deliver:
                with self.assertRaises(OSError):
                    await self.server.default_table.dispatch_table(
                        peer, packet(0x43, 17, 0, "@Continue=/Ans=2/PR=1/Conf=0")
                    )
                deliver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
