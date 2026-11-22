"""Full encrypted rounds covering interrupted and overflowing ordinary prizes.

Fixtures use real authentication/transport and public provisioning boundaries.
No native GUI, synthetic mid-match board injection, or mocked packet handlers.
"""

import asyncio
import json
import random
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.game import test_versus_integration as versus
from tests.support.irc import Client
from tests.support.memory_store import MemoryStore, failing_commits
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server


def values(card):
    return tuple(
        getattr(card, k)
        for k in ("model", "attack", "kind", "physical", "magical", "points", "arrows", "deck")
    )


def compact(card):
    return "|".join(map(str, values(card)[:5] + values(card)[6:7]))


class PrizeEdgeTests(unittest.IsolatedAsyncioTestCase):
    flush = versus.VersusIntegrationTests.flush
    send = versus.VersusIntegrationTests.send
    admit = versus.VersusIntegrationTests.admit

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.store = MemoryStore()
        self.server = Server(
            store=self.store,
            match_mode="versus",
            card_prizes=True,
            allow_wagers=getattr(self, "wagers", False),
        )
        self.port = await self.server.start(0)
        self.clients = [Client(), Client()]
        for client, nick in zip(self.clients, self.server.accounts):
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    def fixture(self, *, cbc=False, full=False):
        # Trusted initial inventory fixture, before table admission. Keep the
        # original starter hands so deterministic public moves stay reproducible.
        if cbc:
            card = self.server.economy.inventory(self.keys[0])[0]
            from tmserver.game.special_rules import DIRECTIONS

            direction = next(i for i in range(8) if card.arrows & (1 << i))
            dx, dy = DIRECTIONS[direction]
            fields = [0] * 16
            fields[5 + dx + 4 * dy] = 18
            self.server.default_table.match_start_fields = fields
            self.server.default_table.allow_cbc = True
            self.server.default_table.reset_match()
        if full:
            for key in self.keys:
                token = self.server.economy.reserve_auction(key)
                filler = replace(token.account.cards[-1], deck=255)
                extras = (filler,) * (1000 - len(token.account.cards))
                self.server.economy.commit_auction(token, add=extras)
        self.server.persist()

    async def play(self, *, cbc=False, seed=0):
        await self.admit()
        match = self.server.default_table.match
        self.before = [self.server.economy.inventory(k) for k in self.keys]
        for side, cards in enumerate(self.before):
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards[:5])
            )
            await self.send(side, packet(0x43, 7, side, body))
        rng = random.Random(seed)
        received = [[], []]
        for turn in range(10):
            self.assertEqual(match.sequence, turn)
            for side in range(2):
                await self.send(side, packet(0x43, 9, turn, f"@TurnData=/Ans={side}"))
            side = match.active
            field = 5 if cbc and turn == 0 else rng.choice(match.legal_fields())
            out = await self.send(side, packet(0x43, 10, turn, f"@PutData=/P={side}/H=0/F={field}"))
            for i in range(2):
                received[i] += out[i]
            while match.phase == "battle":
                out = await self.send(
                    side, packet(0x43, 11, turn, f"@BattleSelect=/B={match.targets[0]}")
                )
                for i in range(2):
                    received[i] += out[i]
        self.assertEqual(match.phase, "result")
        self.finals = [match.final_cards(self.server.nicks[c.nick]) for c in self.clients]
        for side in range(2):
            self.assertEqual(
                sum(decode(p).primary == 0x43 and decode(p).subtype == 13 for p in received[side]),
                1,
            )
            reply = await self.send(side, packet(0x43, 14, 10, f"@Ready=/Ans={side}"))
            self.assertEqual([decode(p).subtype for p in reply[side]], [0x1A])
        if cbc:
            self.assertEqual(sum(len(f.extras) for f in self.finals), 1)
        return match

    async def choices(self):
        match = self.server.default_table.match
        while match.prize_choice is not None:
            choice = match.prize_choice
            replies = await self.send(
                choice.winner,
                packet(0x43, 15, choice.winner, f"@GetSelect=/H={choice.options[0].hand_index}"),
            )
            for reply in replies:
                self.assertEqual([decode(p).subtype for p in reply], [15])
        self.assertFalse(match.prize_commit_required)
        return match.final_prizes().settlement

    def expected(self, settlement, seat):
        lost = {a.original_slot for a in settlement.transfers if a.loser == seat}
        grown = self.finals[seat].updated
        kept = [values(c) for i, c in enumerate(grown) if i not in lost] + [
            values(c) for c in self.before[seat][5:]
        ]
        earned = [values(c) for c in self.finals[seat].extras] + [
            values(a.card) for a in settlement.transfers if a.winner == seat
        ]
        return kept, earned

    async def restore_copy(self, name="crash.json"):
        store = MemoryStore()
        store.save(self.store.load())
        return Server(store=store), store

    async def test_unresolved_winner_disconnect_restores_original_ownership_and_releases_every_account(
        self,
    ):
        match = await self.play()
        self.assertIsNotNone(match.prize_choice)
        for key in self.keys:
            self.assertFalse(self.server.result_rewards.has_pending(key))
        grown = [Counter(values(c) for c in self.server.economy.inventory(k)) for k in self.keys]
        winner = match.prize_choice.winner
        await self.clients[winner].close()
        self.assertEqual(await self.clients[1 - winner].read(), b"")
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.server.default_table.match.players, [])
        for key, expected in zip(self.keys, grown):
            self.assertEqual(
                Counter(values(c) for c in self.server.economy.inventory(key)), expected
            )
            self.assertFalse(self.server.economy.is_busy(key))
            self.assertFalse(self.server.auction_ledger.pending(key))
        restored, store = await self.restore_copy()
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
        finally:
            await restored.stop()

    async def test_unresolved_cbc_disconnect_recovers_only_generated_card_once(self):
        self.fixture(cbc=True)
        match = await self.play(cbc=True)
        self.assertIsNotNone(
            match.prize_choice, "fixture needs a pending ordinary prize alongside CBC"
        )
        self.assertFalse(any(self.server.result_rewards.has_pending(k) for k in self.keys))
        winner = match.prize_choice.winner
        await self.clients[winner].close()
        self.assertEqual(await self.clients[1 - winner].read(), b"")
        for seat, key in enumerate(self.keys):
            self.assertEqual(len(self.server.economy.inventory(key)), 10)
            self.assertEqual(
                [values(p.card) for p in self.server.auction_ledger.pending(key)],
                [values(c) for c in self.finals[seat].extras],
            )
        restored, store = await self.restore_copy()
        try:
            first = restored.auction_ledger.snapshot()
            again = Server(store=store)
            try:
                self.assertEqual(again.auction_ledger.snapshot(), first)
            finally:
                await again.stop()
        finally:
            await restored.stop()

    async def test_combined_cbc_and_ordinary_prizes_have_one_ordered_receipt_and_restart_recovery(
        self,
    ):
        self.fixture(cbc=True)
        await self.play(cbc=True)
        settlement = await self.choices()
        self.assertTrue(settlement.transfers, "fixture must transfer a real player card")
        saved = self.store.load()
        pending = {p["key"]: p for p in saved["result_rewards"]["pending"]}
        for seat, key in enumerate(self.keys):
            kept, earned = self.expected(settlement, seat)
            self.assertEqual([tuple(c) for c in pending[key]["extras"]], earned)
            self.assertEqual(
                Counter(values(c) for c in self.server.economy.inventory(key)), Counter(kept)
            )
        restored, store = await self.restore_copy()
        try:
            for seat, key in enumerate(self.keys):
                kept, earned = self.expected(settlement, seat)
                self.assertEqual(
                    Counter(values(c) for c in restored.economy.inventory(key)), Counter(kept)
                )
                self.assertEqual(
                    [values(p.card) for p in restored.auction_ledger.pending(key)], earned
                )
            again = Server(store=store)
            try:
                self.assertEqual(
                    again.auction_ledger.snapshot(), restored.auction_ledger.snapshot()
                )
            finally:
                await again.stop()
        finally:
            await restored.stop()

    async def test_full_inventory_native_erase_continue_conserves_owned_cards_and_survives_reload(
        self,
    ):
        self.fixture(full=True)
        await self.play()
        settlement = await self.choices()
        self.assertTrue(settlement.transfers)
        winner = settlement.transfers[0].winner
        key = self.keys[winner]
        kept, earned = self.expected(settlement, winner)
        self.assertGreater(len(kept) + len(earned), 1000)
        discard = self.before[winner][-1]
        await self.send(
            winner,
            packet(0x43, 0x10, winner, f"@Erase=/P={winner}/Mode=1/C=1@0=/D={compact(discard)}"),
        )
        await self.send(winner, packet(0x43, 0x10, winner, f"@Erase=/P={winner}/Mode=2"))
        self.assertEqual(len(self.server.economy.inventory(key)), len(kept))
        await self.send(winner, packet(0x43, 0x11, winner, "@Continue=/Ans=2/PR=0/Conf=0"))
        expected = Counter(kept + earned)
        expected[values(discard)] -= 1
        self.assertEqual(Counter(values(c) for c in self.server.economy.inventory(key)), +expected)
        self.assertEqual(len(self.server.economy.inventory(key)), 1000)
        self.assertFalse(self.server.default_table.match_reservations)
        restored, store = await self.restore_copy()
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
        finally:
            await restored.stop()

    async def test_failed_final_choice_write_keeps_cbc_but_rolls_back_unacknowledged_ordinary_loss(
        self,
    ):
        self.fixture(cbc=True)
        match = await self.play(cbc=True)
        choice = match.prize_choice
        self.assertIsNotNone(choice)
        before = self.store.load()
        with failing_commits("edge fixture final-prize failure"):
            await self.clients[choice.winner].send(
                b"NOTICE "
                + service_nick(TABLE_RAW)
                + b" :"
                + packet(0x43, 15, choice.winner, f"@GetSelect=/H={choice.options[0].hand_index}")
            )
            for client in self.clients:
                self.assertEqual(await client.read(), b"")
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.store.load(), before)
        restored = Server(store=self.store)
        try:
            for seat, key in enumerate(self.keys):
                self.assertEqual(len(restored.economy.inventory(key)), 10)
                self.assertEqual(
                    [values(p.card) for p in restored.auction_ledger.pending(key)],
                    [values(c) for c in self.finals[seat].extras],
                )
        finally:
            await restored.stop()

    async def test_wrong_account_cannot_use_winner_header_to_take_a_card(self):
        self.fixture(cbc=True)
        match = await self.play(cbc=True)
        choice = match.prize_choice
        self.assertIsNotNone(choice)
        attacker = 1 - choice.winner
        await self.clients[attacker].send(
            b"NOTICE "
            + service_nick(TABLE_RAW)
            + b" :"
            + packet(0x43, 15, choice.winner, f"@GetSelect=/H={choice.options[0].hand_index}")
        )
        for client in self.clients:
            self.assertEqual(await client.read(), b"")
        for seat, key in enumerate(self.keys):
            self.assertEqual(len(self.server.economy.inventory(key)), 10)
            self.assertEqual(
                [values(p.card) for p in self.server.auction_ledger.pending(key)],
                [values(c) for c in self.finals[seat].extras],
            )

    async def test_accepted_winner_then_other_disconnect_never_recovers_winner_twice(self):
        self.fixture(cbc=True)
        await self.play(cbc=True)
        settlement = await self.choices()
        winner = settlement.transfers[0].winner
        loser = 1 - winner
        await self.send(winner, packet(0x43, 0x11, winner, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.assertEqual(self.server.default_table.match.phase, "result")
        expected = [self.expected(settlement, s) for s in range(2)]
        await self.clients[loser].close()
        self.assertEqual(await self.clients[winner].read(), b"")
        for seat, key in enumerate(self.keys):
            kept, earned = expected[seat]
            self.assertEqual(
                Counter(values(c) for c in self.server.economy.inventory(key)),
                Counter(kept + (earned if seat == winner else [])),
            )
            self.assertEqual(
                [values(p.card) for p in self.server.auction_ledger.pending(key)],
                [] if seat == winner else earned,
            )
        restored, store = await self.restore_copy()
        try:
            self.assertEqual(restored.economy.snapshot(), self.server.economy.snapshot())
            self.assertEqual(
                restored.auction_ledger.snapshot(), self.server.auction_ledger.snapshot()
            )
        finally:
            await restored.stop()

    async def test_postsave_final_choice_delivery_failure_recovers_both_committed_sides(self):
        self.fixture(cbc=True)
        match = await self.play(cbc=True)
        choice = match.prize_choice
        self.assertIsNotNone(choice)
        victim = self.server.nicks[self.clients[1 - choice.winner].nick]
        original = victim.send

        async def failed_send(message):
            if b" :GTM0G43000F" in message:
                raise OSError("edge fixture final choice delivery failure")
            return await original(message)

        with patch.object(victim, "send", side_effect=failed_send):
            await self.clients[choice.winner].send(
                b"NOTICE "
                + service_nick(TABLE_RAW)
                + b" :"
                + packet(0x43, 15, choice.winner, f"@GetSelect=/H={choice.options[0].hand_index}")
            )
            for client in self.clients:
                for _ in range(4):
                    if await client.read() == b"":
                        break
                else:
                    self.fail("disconnect cleanup left a participant connected")
        settlement = match.final_prizes().settlement
        self.assertIsNone(self.server.storage_failed)
        for seat, key in enumerate(self.keys):
            kept, earned = self.expected(settlement, seat)
            self.assertEqual(
                Counter(values(c) for c in self.server.economy.inventory(key)), Counter(kept)
            )
            self.assertEqual(
                [values(p.card) for p in self.server.auction_ledger.pending(key)], earned
            )
        restored, store = await self.restore_copy()
        try:
            self.assertEqual(
                restored.auction_ledger.snapshot(), self.server.auction_ledger.snapshot()
            )
        finally:
            await restored.stop()

    async def test_overflow_continue_rejection_does_not_leave_opponent_in_stale_result(self):
        self.fixture(full=True)
        await self.play()
        settlement = await self.choices()
        winner = settlement.transfers[0].winner
        await self.clients[winner].send(
            b"NOTICE "
            + service_nick(TABLE_RAW)
            + b" :"
            + packet(0x43, 0x11, winner, "@Continue=/Ans=2/PR=0/Conf=0")
        )
        self.assertEqual(await self.clients[winner].read(), b"")
        self.assertEqual(await asyncio.wait_for(self.clients[1 - winner].read(), 1), b"")
        for seat, key in enumerate(self.keys):
            kept, earned = self.expected(settlement, seat)
            self.assertEqual(
                Counter(values(c) for c in self.server.economy.inventory(key)), Counter(kept)
            )
            self.assertEqual(
                [values(p.card) for p in self.server.auction_ledger.pending(key)], earned
            )


class WagerPrizeEdges(unittest.IsolatedAsyncioTestCase):
    wagers = True
    asyncSetUp = PrizeEdgeTests.asyncSetUp
    asyncTearDown = PrizeEdgeTests.asyncTearDown
    flush = PrizeEdgeTests.flush
    send = PrizeEdgeTests.send
    fixture = PrizeEdgeTests.fixture
    play = PrizeEdgeTests.play
    choices = PrizeEdgeTests.choices
    expected = PrizeEdgeTests.expected
    restore_copy = PrizeEdgeTests.restore_copy

    async def offer(self, stake=200, double=1):
        from tests.game.test_match_service import NATIVE_SETTINGS

        for side, client in enumerate(self.clients):
            nn = f"{player_id(client.nick):016X}"
            await self.send(side, packet(0x41, 1, body="@GameEN=/NN=" + nn))
            if side == 0:
                await self.send(
                    side,
                    packet(
                        0x14, body=NATIVE_SETTINGS.replace("/bm=0/du=0", f"/bm={stake}/du={double}")
                    ),
                )
            await self.send(side, packet(1, body="@Req=/NN=" + nn))

    async def admit(self):
        await self.offer()
        for side in range(2):
            await self.send(side, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(self.server.default_table.match.phase, "selection")
        self.assertEqual([self.server.economy.balance(k) for k in self.keys], [9800, 9800])

    async def test_paid_rematch_late_decks_survive_stake_refresh_and_abort_refund(self):
        await self.play()
        settlement = await self.choices()
        for side in range(2):
            await self.send(side, packet(0x43, 0x11, side, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.assertEqual(self.server.default_table.match.phase, "selection")
        self.assertEqual(self.server.default_table.match.round_stake, 400)
        side = settlement.transfers[0].winner
        key = self.keys[side]
        balance = self.server.economy.balance(key)
        card = self.server.economy.inventory(key)[-1]
        wire = packet(
            0x43,
            0x22,
            side,
            f"@Decks=/P={side}/C=1@0=/D={compact(card)}|9@DN1=/S=" + b"Paid rematch".hex(),
        )
        self.assertEqual(await self.send(side, wire), [[], []])
        self.assertEqual(self.server.economy.balance(key), balance)
        self.assertEqual(dict(self.server.economy.account_view(key).deck_names)[1], b"Paid rematch")
        self.assertEqual(self.server.economy.inventory(key)[-1].deck, 9)
        before = self.server.economy.snapshot()
        for seat in range(2):
            self.assertEqual(
                await self.send(seat, packet(0x43, 0x11, seat, "@Continue=/Ans=1/PR=0/Conf=0")),
                [[], []],
            )
        self.assertEqual(self.server.economy.snapshot(), before)
        await self.clients[side].close()
        self.assertEqual(await self.clients[1 - side].read(), b"")
        self.assertEqual(self.server.economy.balance(key), balance + 400)
        self.assertFalse(self.server.default_table.match_reservations)
        restored, store = await self.restore_copy()
        try:
            self.assertEqual(
                dict(restored.economy.account_view(key).deck_names)[1], b"Paid rematch"
            )
            self.assertEqual(restored.economy.inventory(key)[-1].deck, 9)
        finally:
            await restored.stop()

    async def test_underfunded_start_rejects_all_humans_without_partial_stake(self):
        from tests.game.test_match_service import NATIVE_SETTINGS

        key = self.keys[1]
        token = self.server.economy.reserve_auction(key)
        self.server.economy.commit_auction(token, debit=9950)
        self.server.persist()
        # Admit both while the table is free, then let the owner raise its
        # wager. An already underfunded new guest is now refused at entry.
        for side in range(2):
            await self.send(side, packet(0x41, 1, body="@GameEN="))
        self.assertEqual(len(self.server.default_table.match.players), 2)
        await self.send(0, packet(0x14, body=NATIVE_SETTINGS.replace("/bm=0", "/bm=200")))
        for side in range(2):
            await self.send(side, packet(1, body="@Req="))
        before = self.server.economy.snapshot()
        await self.send(0, packet(0x41, 8, body="@GameOK="))
        await self.clients[1].send(
            b"NOTICE " + service_nick(TABLE_RAW) + b" :" + packet(0x41, 8, body="@GameOK=")
        )
        for client in self.clients:
            self.assertEqual(await client.read(), b"")
        self.assertEqual(self.server.economy.snapshot(), before)
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertFalse(self.server.match_economy.snapshot()["rounds"])

    async def test_failed_rematch_stake_save_emits_no_acceptance_and_restart_keeps_prior_result(
        self,
    ):
        await self.play()
        settlement = await self.choices()
        await self.send(0, packet(0x43, 0x11, 0, "@Continue=/Ans=1/PR=0/Conf=0"))
        paid = [self.server.economy.balance(k) for k in self.keys]
        before = self.store.load()
        with failing_commits("edge fixture next-stake save failure"):
            await self.clients[1].send(
                b"NOTICE "
                + service_nick(TABLE_RAW)
                + b" :"
                + packet(0x43, 0x11, 1, "@Continue=/Ans=1/PR=0/Conf=0")
            )
            for client in self.clients:
                self.assertEqual(await client.read(), b"")
        self.assertEqual(self.store.load(), before)
        self.assertIsNotNone(self.server.storage_failed)
        restored = Server(store=self.store)
        try:
            self.assertEqual([restored.economy.balance(k) for k in self.keys], paid)
            for side, key in enumerate(self.keys):
                kept, earned = self.expected(settlement, side)
                self.assertEqual(
                    Counter(values(c) for c in restored.economy.inventory(key)),
                    Counter(kept + (earned if side == 0 else [])),
                )
                self.assertEqual(
                    [values(p.card) for p in restored.auction_ledger.pending(key)],
                    [] if side == 0 else earned,
                )
        finally:
            await restored.stop()


if __name__ == "__main__":
    unittest.main()
