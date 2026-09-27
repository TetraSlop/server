"""Three-player generated-card ownership, acceptance, overflow and recovery."""

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tests import test_three_player_integration as versus
from tmserver.match_service import decode, packet
from tmserver.native_ai import DIRECTIONS
from tmserver.server import Server


class ThreePlayerCBC(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = versus.ThreePlayerIntegrationTests.asyncSetUp
    asyncTearDown = versus.ThreePlayerIntegrationTests.asyncTearDown
    flush = versus.ThreePlayerIntegrationTests.flush
    send = versus.ThreePlayerIntegrationTests.send
    admit = versus.ThreePlayerIntegrationTests.admit

    async def play_cbc(self, full=False):
        card = self.server.economy.inventory(self.keys[0])[0]
        direction = next(i for i in range(8) if card.arrows & (1 << i))
        dx, dy = DIRECTIONS[direction]
        fields = [0] * 25
        fields[12 + dx + 5 * dy] = 18
        self.server.match_start_fields = fields
        self.server.allow_cbc = True
        self.server.reset_match()
        if full:
            for key in self.keys:
                token = self.server.economy.reserve_delivery(key)
                self.server.economy.commit_delivery(
                    token,
                    add=(replace(token.account.cards[-1], deck=255),) * 990,
                    keep_reserved=False,
                )
        self.server.persist()
        await self.admit()
        match = self.server.match
        for seat, key in enumerate(self.keys):
            cards = self.server.economy.inventory(key)[:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
            )
            await self.send(seat, packet(0x43, 7, seat, body))
        cbc_packets = []
        for turn in range(15):
            self.assertEqual(match.sequence, turn)
            for seat in range(3):
                await self.send(seat, packet(0x43, 9, turn, f"@TurnData=/Ans={seat}"))
            seat = match.active
            field = 12 if turn == 0 else match.legal_fields()[0]
            replies = await self.send(
                seat, packet(0x43, 10, turn, f"@PutData=/P={seat}/H=0/F={field}")
            )
            cbc_packets.extend(p for p in replies[0] if b"/CBC=" in p)
            while match.phase == "battle":
                replies = await self.send(
                    seat, packet(0x43, 11, turn, f"@BattleSelect=/B={match.targets[0]}")
                )
                cbc_packets.extend(p for p in replies[0] if b"/CBC=" in p)
        self.assertEqual(match.phase, "result")
        self.assertEqual(len(cbc_packets), 1)
        self.assertEqual(sum(match.observer_snapshot().scores), 16)
        finals = [match.final_cards(self.server.nicks[c.nick]) for c in self.clients]
        self.assertEqual(sum(len(f.extras) for f in finals), 1)
        winner = next(i for i, f in enumerate(finals) if f.extras)
        self.extra = finals[winner].extras[0]
        self.assertEqual(self.extra.deck, 255)
        for seat in range(3):
            replies = await self.send(seat, packet(0x43, 14, 15, f"@Ready=/Ans={seat}"))
            cards = next(decode(p).sections["TCList"] for p in replies[seat] if b"@TCList=" in p)
            self.assertEqual(cards["H"], "5|5|5")
            if not match.card_prizes:
                self.assertEqual(
                    len(self.server.economy.inventory(self.keys[seat])), 1000 if full else 10
                )
        return winner

    async def assert_restart(self):
        restored = Server(
            state_path=self.path, account_count=3, match_mode="versus", match_player_count=3
        )
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
            self.assertEqual(
                restored.auction_ledger.snapshot(), self.server.auction_ledger.snapshot()
            )
        finally:
            await restored.stop()

    async def test_generated_card_accepted_by_current_owner_and_saved_once(self):
        winner = await self.play_cbc()
        await self.send(winner, packet(0x43, 17, winner, "@Continue=/Ans=2/PR=0/Conf=0"))
        self.assertFalse(self.server.match_reservations)
        for seat, key in enumerate(self.keys):
            cards = self.server.economy.inventory(key)
            self.assertEqual(len(cards), 11 if seat == winner else 10)
            if seat == winner:
                self.assertEqual(cards[-1].full(), tuple(map(int, self.extra.full().split("|"))))
            self.assertEqual(self.server.auction_ledger.pending(key), ())
        await self.assert_restart()

    async def test_full_inventory_original_erase_then_continue_conserves_reward(self):
        winner = await self.play_cbc(full=True)
        key = self.keys[winner]
        discard = self.server.economy.inventory(key)[-1]
        body = f"@Erase=/P={winner}/Mode=1/C=1@0=/D=" + "|".join(map(str, discard.reduced()))
        await self.send(winner, packet(0x43, 0x10, winner, body))
        self.assertEqual(len(self.server.economy.inventory(key)), 1000)
        await self.send(winner, packet(0x43, 17, winner, "@Continue=/Ans=2/PR=0/Conf=0"))
        self.assertTrue(all(len(self.server.economy.inventory(k)) == 1000 for k in self.keys))
        self.assertEqual(
            self.server.economy.inventory(key)[-1].full(),
            tuple(map(int, self.extra.full().split("|"))),
        )
        await self.assert_restart()

    async def test_disconnected_owner_recovers_generated_card_once_without_giving_away_owned_cards(
        self,
    ):
        winner = await self.play_cbc()
        key = self.keys[winner]
        await self.clients[winner].close()
        for seat, c in enumerate(self.clients):
            if seat != winner:
                self.assertEqual(await c.read(), b"")
        self.assertFalse(self.server.match_reservations)
        for seat, k in enumerate(self.keys):
            self.assertEqual(len(self.server.economy.inventory(k)), 10)
            pending = self.server.auction_ledger.pending(k)
            self.assertEqual(len(pending), int(seat == winner))
            if pending:
                self.assertEqual(
                    pending[0].card.full(), tuple(map(int, self.extra.full().split("|")))
                )
        await self.assert_restart()
        await self.assert_restart()


if __name__ == "__main__":
    unittest.main()
