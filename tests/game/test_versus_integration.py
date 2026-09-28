"""Two human matches and rematch persistence over the real encrypted transport."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.network.test_server import Client
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.game.packets import decode, packet
from tmserver.rankings.stats import PracticeStats
from tmserver.network.server import ACCOUNTS, Server


class VersusIntegrationTests(unittest.IsolatedAsyncioTestCase):
    start_fields = None

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.server = Server(
            state_path=self.path, match_mode="versus", match_start_fields=self.start_fields
        )
        self.port = await self.server.start(0)
        self.clients = [Client(), Client()]
        for client, nick in zip(self.clients, ACCOUNTS):
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def flush(self, side):
        client = self.clients[side]
        await client.send(b"PING :match-barrier")
        out = []
        drained = False
        for _ in range(200):
            line = await client.read()
            self.assertTrue(line, "unexpected disconnect")
            if line == b"PONG match-barrier":
                task = self.server.default_table.match_task
                if drained and task is None:
                    return out
                if task is not None:
                    await asyncio.wait_for(asyncio.shield(task), 3)
                drained = True
                await client.send(b"PING :match-barrier")
            else:
                self.assertTrue(
                    line.startswith(b":" + service_nick(TABLE_RAW) + b"!p@local NOTICE ")
                )
                out.append(line.split(b" :", 1)[1])
        self.fail("match response limit exceeded")

    async def send(self, side, payload):
        await self.clients[side].send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + payload)
        first = await self.flush(side)
        second = await self.flush(1 - side)
        return [first, second] if side == 0 else [second, first]

    async def admit(self):
        for side in range(2):
            identity = f"{player_id(self.clients[side].nick):016X}"
            await self.send(side, packet(0x41, 1, body="@GameEN=/NN=" + identity))
            await self.send(side, packet(1, body="@Req=/NN=" + identity))
        for side in range(2):
            await self.send(side, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(self.server.default_table.match.phase, "selection")

    async def play_round(self, first_field=None):
        before = [self.server.economy.inventory(k) for k in self.keys]
        counts = [
            PracticeStats.from_profile(self.server.economy.profile_bytes(k)).vs_player_games
            for k in self.keys
        ]
        for side in range(2):
            cards = before[side][:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
            )
            await self.send(side, packet(0x43, 7, side, body))
        received = [[], []]
        for turn in range(10):
            match = self.server.default_table.match
            self.assertEqual(match.sequence, turn)
            for side in range(2):
                replies = await self.send(side, packet(0x43, 9, turn, f"@TurnData=/Ans={side}"))
                for index in range(2):
                    received[index] += replies[index]
            side = match.active
            field = (
                first_field if turn == 0 and first_field is not None else match.legal_fields()[0]
            )
            replies = await self.send(
                side, packet(0x43, 10, turn, f"@PutData=/P={side}/H=0/F={field}")
            )
            for index in range(2):
                received[index] += replies[index]
            while match.phase == "battle":
                replies = await self.send(
                    side, packet(0x43, 11, turn, f"@BattleSelect=/B={match.targets[0]}")
                )
                for index in range(2):
                    received[index] += replies[index]
        self.assertEqual(match.phase, "result")
        for side, key in enumerate(self.keys):
            peer = self.server.nicks[self.clients[side].nick]
            final = match.final_cards(peer)
            after = self.server.economy.inventory(key)
            self.assertEqual(
                tuple("|".join(map(str, c.full())) for c in after[:5]),
                tuple(c.full() for c in final.updated),
            )
            self.assertEqual(after[5:], before[side][5:])
            self.assertEqual(len(after), 10)
            self.assertEqual(self.server.economy.balance(key), 10000)
            self.assertEqual(
                sum(decode(x).primary == 0x43 and decode(x).subtype == 13 for x in received[side]),
                1,
            )
            stats = PracticeStats.from_profile(self.server.economy.profile_bytes(key))
            self.assertEqual(stats.vs_player_games, (counts[side] + 1) & 65535)
            result = next(
                decode(x)
                for x in received[side]
                if decode(x).primary == 0x43 and decode(x).subtype == 13
            )
            self.assertEqual(result.sections[f"No{side}"]["T"], f"0|0|{stats.vs_player_games}")
        saved = self.path.read_bytes()
        for side in range(2):
            result = await self.send(side, packet(0x43, 14, 10, f"@Ready=/Ans={side}"))
            self.assertEqual(len(result[side]), 1)
            self.assertEqual(decode(result[side][0]).subtype, 0x1A)
            duplicate = await self.send(side, packet(0x43, 14, 10, f"@Ready=/Ans={side}"))
            self.assertEqual(duplicate, [[], []])
        self.assertEqual(self.path.read_bytes(), saved)

    async def test_two_full_rounds_save_both_accounts_and_reload(self):
        await self.admit()
        await self.play_round()
        first = [self.server.economy.profile_bytes(k) for k in self.keys]
        for side in range(2):
            await self.send(side, packet(0x43, 17, side, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.assertEqual(self.server.default_table.match.phase, "selection")
        self.assertEqual([self.server.economy.profile_bytes(k) for k in self.keys], first)
        await self.play_round()
        self.assertEqual(self.server.default_table.match.completed_matches, 2)
        latest = [self.server.economy.profile_bytes(k) for k in self.keys]
        await self.send(0, packet(0x43, 17, 0, "@Continue=/Ans=2/PR=0/Conf=0"))
        finished = self.server.default_table.match
        self.assertEqual(finished.phase, "finished")
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(await self.send(0, packet(1, body="@Req=")), [[], []])
        self.assertIs(self.server.default_table.match, finished)
        self.assertEqual([self.server.economy.profile_bytes(k) for k in self.keys], latest)
        restored = Server(state_path=self.path, match_mode="versus")
        try:
            self.assertEqual([restored.economy.profile_bytes(k) for k in self.keys], latest)
        finally:
            await restored.stop()

    async def test_active_disconnect_aborts_both_without_partial_growth(self):
        await self.admit()
        before = self.server.economy.snapshot()
        original = self.server.default_table.match
        await self.clients[0].close()
        self.assertEqual(await self.clients[1].read(), b"")
        self.assertIsNot(self.server.default_table.match, original)
        self.assertEqual(self.server.default_table.match.players, [])
        self.assertFalse(self.server.default_table.match_reservations)
        self.assertEqual(self.server.economy.snapshot(), before)
        for key in self.keys:
            self.assertFalse(self.server.economy.is_reserved(key))


class SpecialVersusIntegrationTests(VersusIntegrationTests):
    start_fields = (3, 0, 0, 0, 0, 7, 9, 0, 0, 0, 17, 0, 0, 0, 0, 0)


class CBCRewardIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = VersusIntegrationTests.asyncSetUp
    asyncTearDown = VersusIntegrationTests.asyncTearDown
    flush = VersusIntegrationTests.flush
    send = VersusIntegrationTests.send
    admit = VersusIntegrationTests.admit
    play_round = VersusIntegrationTests.play_round
    start_fields = None

    async def cbc_round(self):
        from tmserver.game.ai import DIRECTIONS

        card = self.server.economy.inventory(self.keys[0])[0]
        self.assertNotEqual(card.arrows, 0)
        direction = next(i for i in range(8) if card.arrows & (1 << i))
        dx, dy = DIRECTIONS[direction]
        fields = [0] * 16
        fields[5 + dx + 4 * dy] = 18
        self.server.default_table.match_start_fields = fields
        self.server.default_table.allow_cbc = True
        self.server.default_table.reset_match()
        await self.admit()
        await self.play_round(first_field=5)
        finals = [
            self.server.default_table.match.final_cards(self.server.nicks[c.nick])
            for c in self.clients
        ]
        self.assertEqual(sum(len(f.extras) for f in finals), 1)
        self.assertTrue(all(self.server.result_rewards.has_pending(k) for k in self.keys))
        return finals

    async def test_generated_CBC_card_is_saved_once_at_continue_and_survives_reload(self):
        finals = await self.cbc_round()
        for side in range(2):
            await self.send(side, packet(0x43, 17, side, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.assertEqual(self.server.default_table.match.phase, "selection")
        for side, key in enumerate(self.keys):
            cards = self.server.economy.inventory(key)
            self.assertEqual(len(cards), 10 + len(finals[side].extras))
            if finals[side].extras:
                self.assertEqual(
                    "|".join(map(str, cards[-1].full())), finals[side].extras[0].full()
                )
            self.assertFalse(self.server.result_rewards.has_pending(key))
        before = self.server.economy.snapshot()
        for side in range(2):
            self.assertEqual(
                await self.send(side, packet(0x43, 17, side, "@Continue=/Ans=1/PR=0/Conf=0")),
                [[], []],
            )
        self.assertEqual(self.server.economy.snapshot(), before)
        restored = Server(state_path=self.path)
        try:
            self.assertEqual(restored.economy.snapshot(), before)
            self.assertFalse(any(restored.auction_ledger.pending(k) for k in self.keys))
        finally:
            await restored.stop()

    async def test_crash_before_continue_recovers_earned_card_through_real_collection(self):
        from tests.economy.auctions.test_auction_service import collection_request
        from tmserver.economy.packets import named, parse_named

        finals = await self.cbc_round()
        crash_path = self.path.with_name("crash_copy.json")
        crash_path.write_bytes(self.path.read_bytes())
        restored = Server(state_path=crash_path)
        try:
            for side, key in enumerate(self.keys):
                self.assertEqual(len(restored.economy.inventory(key)), 10)
                pending = restored.auction_ledger.pending(key)
                self.assertEqual(len(pending), len(finals[side].extras))
                self.assertFalse(restored.result_rewards.has_pending(key))
                if pending:
                    replies = restored.auction.handle(
                        key, collection_request(restored.economy, key)
                    )
                    number = int(parse_named(replies[0])[3]["Init"]["N"])
                    self.assertEqual(parse_named(replies[2])[3]["Card"]["E"], "1")
                    get = named(0xB2, 0x15, number, f"@Get=/No={number}")
                    restored.auction.handle(key, get)
                    restored.auction.handle(key, get)
                    restored.persist()
                    self.assertEqual(len(restored.economy.inventory(key)), 11)
            again = Server(state_path=crash_path)
            try:
                self.assertEqual(again.economy.snapshot(), restored.economy.snapshot())
                self.assertFalse(any(again.auction_ledger.pending(k) for k in self.keys))
            finally:
                await again.stop()
        finally:
            await restored.stop()

    async def test_result_discard_and_deck_edits_commit_only_with_valid_continue(self):
        finals = await self.cbc_round()
        side = next(i for i, f in enumerate(finals) if f.extras)
        key = self.keys[side]
        extra = finals[side].extras[0]
        before = self.server.economy.inventory(key)
        erase = packet(0x43, 0x10, side, f"@Erase=/P={side}/Mode=1/C=1@0=/D={extra.reduced()}")
        self.assertEqual(await self.send(side, erase), [[], []])
        decks = packet(0x43, 0x22, side, f"@Decks=/P={side}/C=0@DN1=/S=" + b"CBC Result".hex())
        self.assertEqual(await self.send(side, decks), [[], []])
        self.assertEqual(self.server.economy.inventory(key), before)
        self.assertNotEqual(
            dict(self.server.economy.account_view(key).deck_names)[1], b"CBC Result"
        )
        await self.send(side, packet(0x43, 17, side, "@Continue=/Ans=2/PR=0/Conf=0"))
        self.assertEqual(self.server.economy.inventory(key), before)
        self.assertEqual(dict(self.server.economy.account_view(key).deck_names)[1], b"CBC Result")
        self.assertFalse(self.server.auction_ledger.pending(key))


if __name__ == "__main__":
    unittest.main()
