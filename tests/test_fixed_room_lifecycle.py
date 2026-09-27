"""Fixed presets through real table rematches; no sockets or persistent files."""

import unittest
from dataclasses import replace

from tests.test_admission_identity import native_identity
from tests.test_event_rooms import presence
from tests.test_room_rules import computer, settings
from tests.test_session_recovery import Writer
from tmserver.match_service import MatchError, decode, packet
from tmserver.native_board_generation import generate_board
from tmserver.native_rules import CRTLCG
from tmserver.native_special_rules import start_board
from tmserver.ordinary_rooms import OrdinaryRoomConfig, directory_files
from tmserver.room_rules import RoomRules
from tmserver.server import Server, Session


class FixedRoomLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, rules, *, mode="computer", **options):
        self.rules = rules
        config = OrdinaryRoomConfig(
            b"Fixed", 0x101, 0x201, b"#FIXED", b"#FTABLE", mode=mode, rules=rules
        )
        self.server = Server(ordinary_rooms=(config,), **options)
        self.addAsyncCleanup(self.server.stop)
        self.table = self.server.ordinary_tables[1]
        self.peers = []
        for index, nick in enumerate(self.server.accounts):
            peer = Session(self.server, None, Writer(), index + 1)
            peer.nick = nick
            key = self.server.ensure_account(nick)
            self.server.trade.bind(peer, key)
            self.server.nicks[nick] = peer
            self.server.sessions.add(peer)
            self.peers.append(peer)
            view = self.server.economy.account_view(key)
            self.table.room.handle(
                peer, presence(self.table.room.room_id, view.nn, view.player_id, view.name, b"DE")
            )
        for side in range(1 if mode == "computer" else 2):
            view = self.server.economy.account_view(self.peers[side].nick.decode())
            section = "GameENC" if mode == "computer" else "GameEN"
            await self.send(
                side,
                packet(
                    0x41, 0x12 if mode == "computer" else 1, body=native_identity(view, section)
                ),
            )
        if mode == "versus":
            await self.send(0, packet(0x14, body=settings(rules)))
        for side in range(1 if mode == "computer" else 2):
            view = self.server.economy.account_view(self.peers[side].nick.decode())
            await self.send(side, packet(1, body=native_identity(view, "Req")))
            if mode == "versus":
                await self.send(side, packet(0x41, 8, body="@GameOK="))
        if mode == "computer":
            await self.send(0, computer(rules))

    async def send(self, side, payload):
        await self.table.dispatch_table(self.peers[side], payload)
        if self.table.match_task is not None:
            await self.table.match_task

    def published_rules(self, rules):
        match = self.table.match
        self.assertEqual(match.game_rules, rules.as_dict())
        self.assertEqual(self.table.room.table_metadata[30:37], rules.packed())
        self.assertEqual(
            directory_files(self.server)["b/g/RL000"][0x110 + 0x49 : 0x110 + 0x50],
            self.rules.packed(),
        )
        query = decode(packet(0x13, body=match.rules_body()))
        self.assertEqual({k: int(v) for k, v in query.sections["Tet"].items()}, rules.as_dict())

    async def select(self):
        match = self.table.match
        expected = generate_board(
            match.player_count, CRTLCG(match.rng.state), **match.special_flags
        )
        for side, player in enumerate(match.players):
            if player.peer is None:
                continue
            cards = self.server.economy.inventory(player.peer.nick.decode())[:5]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(cards)
            )
            await self.send(side, packet(0x43, 7, side, body))
        self.assertEqual(match.start_board, start_board(expected.fields))
        self.published_rules(replace(self.rules, bm=match.round_stake))

    async def finish(self):
        match = self.table.match
        for _ in range(100):
            if match.phase == "result":
                return
            self.assertIn(match.phase, ("turn", "battle"))
            for side, player in enumerate(match.players):
                if player.peer is not None and not player.departed:
                    await self.send(side, packet(0x43, 9, match.sequence, f"@TurnData=/Ans={side}"))
            if match.phase == "turn" and not match._controlled(match.players[match.active]):
                await self.send(
                    match.active,
                    packet(
                        0x43,
                        10,
                        match.sequence,
                        f"@PutData=/P={match.active}/H=0/F={match.legal_fields()[0]}",
                    ),
                )
            while match.phase == "battle":
                await self.send(
                    match.active,
                    packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}"),
                )
        self.fail("fixed-room match did not finish")

    async def continue_game(self, side=0, *, conf=0, leave=False):
        await self.send(
            side, packet(0x43, 17, side, f"@Continue=/Ans={2 if leave else 1}/PR=0/Conf={conf}")
        )

    async def test_capped_double_up_and_reconfiguration_keep_current_and_starting_rules_separate(
        self,
    ):
        rules = RoomRules(bm=1250, du=1, st=1, cb=1, ca=1, gs=0, tl=4)
        await self.start(rules, allow_wagers=True)
        key = self.peers[0].nick.decode()
        for round_number, conf in ((1, 0), (2, 1), (3, 0)):
            await self.select()
            await self.finish()
            self.assertEqual(self.table.match.completed_matches, round_number)
            self.published_rules(rules if round_number == 1 else replace(rules, bm=2000))
            balance = self.server.economy.balance(key)
            await self.continue_game(conf=conf, leave=round_number == 3)
            if round_number == 3:
                break
            if conf:
                self.assertEqual(self.server.economy.balance(key), balance)
                match = self.table.match
                before = (
                    match.snapshot(),
                    match.rng.state,
                    self.server.snapshot(),
                    self.table.room.snapshot(),
                )
                for changed in (
                    rules,
                    replace(rules, bm=2000, st=0),
                    replace(rules, bm=2000, gs=1),
                    replace(rules, bm=2000, tl=2),
                ):
                    with (
                        self.subTest(changed=changed),
                        self.assertRaisesRegex(MatchError, "room rules"),
                    ):
                        await self.send(0, computer(changed, count=3))
                    self.assertEqual(
                        (
                            match.snapshot(),
                            match.rng.state,
                            self.server.snapshot(),
                            self.table.room.snapshot(),
                        ),
                        before,
                    )
                await self.send(0, computer(replace(rules, bm=2000), count=3))
                self.assertEqual(self.table.match.player_count, 3)
            self.assertEqual(self.server.economy.balance(key), balance - 2000)
            self.published_rules(replace(rules, bm=2000))
        self.assertEqual(self.table.match.phase, "finished")
        self.assertFalse(self.server.economy.is_reserved(key))
        self.assertEqual(self.table.room.table_metadata[30:37], rules.packed())
        await self.table.release_table(self.peers[0])
        self.published_rules(rules)

    async def test_departure_after_rematch_preserves_each_fixed_policy(self):
        for rule in (0, 1, 2):
            with self.subTest(rule=rule):
                rules = RoomRules(gs=rule, tl=1, st=1, cb=1, ca=1)
                await self.start(rules, mode="versus", allow_departures=True)
                await self.select()
                await self.finish()
                await self.continue_game(0)
                self.assertEqual(self.table.match.phase, "result")
                await self.continue_game(1)
                self.assertEqual(self.table.match.phase, "selection")
                await self.select()
                match = self.table.match
                await self.send(1, packet(0x43, 5, 10, "@Quit=/No=1"))
                self.assertEqual(match.departure_seats, (1,))
                self.assertEqual(match.players[1].substitute, 1 if rule == 0 else 0)
                self.published_rules(rules)
                if rule == 1:
                    self.assertEqual((match.phase, match.completed_matches), ("finished", 1))
                else:
                    await self.finish()
                    self.assertEqual(match.completed_matches, 2)
                    await self.continue_game()
                    self.assertEqual(match.phase, "finished")
                self.assertFalse(self.table.match_reservations)
                self.published_rules(rules)
                await self.table.release_table(self.peers[0])
                self.assertIsNot(self.table.match, match)
                self.published_rules(rules)
                await self.server.stop()


if __name__ == "__main__":
    unittest.main()
