"""Configured E matchmaking and borrowed-card games over encrypted loopback."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.events.test_event_rooms import config, presence
from tests.network.test_server import Client
from tmserver.events.models import EventTableConfig
from tmserver.protocol.identities import GAME_KEY, game_id, service_nick
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server


class EventMatchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.cfg = config(match_enabled=True, card_service_enabled=True)
        self.table = EventTableConfig("festival", game_id(0x1003), b"#EVTABLE001")
        self.clients = []
        self.server = None

    async def start(self, n=2, policy="none"):
        from dataclasses import replace

        self.table = replace(self.table, player_count=n)
        self.server = Server(
            state_path=self.path,
            account_count=n,
            event_configs=(self.cfg,),
            event_tables=(self.table,),
            event_clock=lambda: 200,
            event_progress_policy=policy,
        )
        port = await self.server.start(0)
        self.clients = [Client() for _ in range(n)]
        self.inbox = [[] for _ in range(n)]
        for client, nick in zip(self.clients, self.server.accounts):
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        if self.server is not None:
            await self.server.stop()
        self.temp.cleanup()

    async def drain(self):
        # Let the real background continuation publish pending battles/proposals.
        for _ in range(80):
            await asyncio.sleep(0.015)
            if not self.server.event_matches.coordinator.pending():
                break
        for side, client in enumerate(self.clients):
            await client.send(b"PING :event-barrier")
            for _ in range(200):
                line = await client.read()
                self.assertTrue(line, "unexpected event disconnect")
                if line == b"PONG event-barrier":
                    break
                if b" NOTICE " in line:
                    source = line.split(b"!", 1)[0][1:]
                    self.inbox[side].append((source, line.split(b" :", 1)[1]))
            else:
                self.fail("event delivery limit")

    async def send(self, side, target, payload):
        await self.clients[side].send(
            b"NOTICE " + service_nick(target ^ GAME_KEY) + b" :" + payload
        )
        await self.drain()

    def identity(self, side, section):
        v = self.server.economy.account_view(self.keys[side])
        if section not in ("GameEN", "GameENC", "Req"):
            return f"@{section}=/NN={v.nn:016X}/HID=0/CN={v.name.hex()}"
        from tests.protocol.test_admission_identity import native_identity

        return native_identity(v, section)

    async def enter_selection(self, side, select=True):
        binding = self.server.event_rooms.rooms[0]
        key = self.keys[side]
        v = self.server.economy.account_view(key)
        await self.send(
            side, self.cfg.room_id, presence(binding.room_id, v.nn, v.player_id, v.name)
        )
        await self.send(
            side, self.cfg.room_id, packet(0xD1, 4, body=f"@EventTimeReq=/NN={v.nn:016X}")
        )
        await self.send(
            side,
            self.cfg.init_id,
            packet(
                0x98, body=self.identity(side, "EInitReq") + f"/Dm=1/Vol=1/GID={v.player_id:016X}"
            ),
        )
        init = next(
            decode(p).sections["EInit"] for _, p in reversed(self.inbox[side]) if b"@EInit=" in p
        )
        if select:
            cards = v.cards[2:7]
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
            )
            await self.send(side, self.cfg.init_id, packet(0xB2, 7, int(init["N"]), body))
            self.assertTrue(any(b"@Select=/A=1" in p for _, p in self.inbox[side]))
            self.assertTrue(any(b"@EQuit=/D=0/Stat=0" in p for _, p in self.inbox[side]))
        return int(init["N"])

    async def admit(self):
        for side in range(len(self.keys)):
            await self.enter_selection(side)
        for _ in range(20):
            if all(any(b"@MuchMake=/TblNo=" in p for _, p in box) for box in self.inbox):
                break
            await self.drain()
        for box in self.inbox:
            entry = [(s, p) for s, p in box if p.startswith(b"GTM0G99") or b"@EInit=" in p]
            self.assertEqual(len(entry), 2)
            self.assertTrue(all(s == service_nick(0x1001) for s, p in entry))
            self.assertIn(b"/Stat=0", entry[1][1])
            proposal = next(i for i, (_, p) in enumerate(box) if b"@MuchMake=/TblNo=" in p)
            self.assertTrue(any(p.startswith(b"GTM0L") for _, p in box[:proposal]))
        for side in range(len(self.clients)):
            await self.send(
                side,
                self.cfg.room_id,
                packet(0xD3, body=f"@MuchMakeAns=/Ans=1/TblNo=1/TblId={self.table.table_id:016X}"),
            )
        for side, client in enumerate(self.clients):
            await self.send(
                side, self.table.table_id, packet(0x41, 1, body=self.identity(side, "GameEN"))
            )
            await client.send(b"JOIN " + self.table.channel)
            await self.drain()
            await self.send(side, self.table.table_id, packet(1, body=self.identity(side, "Req")))
            await self.send(side, self.table.table_id, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(self.engine.phase, "turn")
        for side, box in enumerate(self.inbox):
            table = [p for s, p in box if s == service_nick(0x1003)]
            released = next(i for i, p in enumerate(table) if p == packet(0x41, 9))
            init = next(i for i, p in enumerate(table) if b"@EventGameInit=" in p)
            hand = next(i for i, p in enumerate(table) if b"@EventCard=" in p)
            self.assertLess(released, init)
            self.assertLess(init, hand)
            card = decode(table[hand]).sections["EventCard"]
            self.assertEqual(set(card), {"Ok"} | {f"D{x}" for x in range(side * 5, side * 5 + 5)})

    @property
    def engine(self):
        return self.server.event_matches.coordinator._rounds[self.table.table_id].engine

    async def play(self):
        for _ in range(200):
            engine = self.engine
            if engine.phase == "result":
                return
            if engine.pending_work:
                await self.drain()
                continue
            if engine.phase == "turn":
                for side in range(len(self.clients)):
                    await self.send(
                        side,
                        self.table.table_id,
                        packet(0x43, 9, engine.sequence, f"@TurnData=/Ans={side}"),
                    )
                field = engine._combat.legal_fields()[0]
                await self.send(
                    engine.active,
                    self.table.table_id,
                    packet(0x43, 10, engine.sequence, f"@PutData=/P={engine.active}/H=0/F={field}"),
                )
            elif engine.phase == "battle":
                await self.send(
                    engine.active,
                    self.table.table_id,
                    packet(
                        0x43,
                        11,
                        engine.sequence,
                        "@BattleSelect=/B=" + str(engine.snapshot()["battle_targets"][0]),
                    ),
                )
            else:
                self.fail(engine.phase)
        self.fail("event round did not finish")

    async def complete(self, n, policy):
        await self.start(n, policy)
        before = [self.server.economy.profile_bytes(k) for k in self.keys]
        await self.admit()
        await self.play()
        outcome = self.engine.outcome
        self.assertEqual(sum(outcome.scores), n * 5)
        saved = json.loads(self.path.read_text())
        self.assertEqual(len(saved["event_results"]["rounds"]), 1)
        for side, key in enumerate(self.keys):
            results = [p for _, p in self.inbox[side] if b"@EventResult=" in p]
            self.assertEqual(len(results), 1)
            self.assertFalse(
                any(b"@ResultData=" in p or b"@GetSelect=" in p for _, p in self.inbox[side])
            )
            expected = int(policy == "rounds" or policy == "wins" and outcome.classes[side] < 2)
            self.assertEqual(self.server.events.member(key, "festival").steps, expected)
            self.assertTrue(self.server.economy.is_busy(key))
        for side in range(n):
            await self.send(
                side, self.table.table_id, packet(0x43, 14, n * 5, f"@Ready=/Ans={side}")
            )
            self.assertTrue(
                any(
                    p
                    == packet(
                        0x43,
                        17,
                        2,
                        "@Continue=/Ans=" + "|".join(["3"] * n) + "/B=" + "|".join(["0"] * n),
                    )
                    for _, p in self.inbox[side]
                )
            )
        self.assertIsNone(self.server.event_matches.coordinator.snapshot("festival"))
        for side, key in enumerate(self.keys):
            self.assertFalse(self.server.economy.is_busy(key))
            self.assertEqual(self.server.economy.profile_bytes(key), before[side])
        await self.send(0, self.table.table_id, packet(0x43, 14, n * 5, "@Ready=/Ans=0"))
        self.assertEqual(len(self.server.event_results.snapshot()["rounds"]), 1)
        restored = Server(
            state_path=self.path,
            account_count=n,
            event_configs=(self.cfg,),
            event_tables=(self.table,),
            event_clock=lambda: 200,
            event_progress_policy=policy,
        )
        try:
            self.assertEqual(
                restored.event_results.snapshot(), self.server.event_results.snapshot()
            )
            self.assertEqual(restored.events.snapshot(), self.server.events.snapshot())
            self.assertEqual([restored.economy.profile_bytes(k) for k in self.keys], before)
        finally:
            await restored.stop()

    async def test_two_humans_complete_no_owned_mutations_or_invented_progress(self):
        await self.complete(2, "none")

    async def test_three_humans_complete_explicit_wins_progress(self):
        await self.complete(3, "wins")

    async def test_disconnect_aborts_all_and_releases_borrowed_hands(self):
        await self.start()
        await self.admit()
        before = self.server.economy.snapshot()
        await self.clients[0].close()
        for _ in range(8):
            if await self.clients[1].read() == b"":
                break
        else:
            self.fail("departed event table did not close remaining peer")
        self.assertEqual(self.server.economy.snapshot(), before)
        self.assertEqual(self.server.event_results.snapshot()["rounds"], [])
        self.assertTrue(all(not self.server.economy.is_busy(k) for k in self.keys))

    async def test_native_selection_cancel_deck_edits_and_fresh_session(self):
        await self.start()
        number = await self.enter_selection(0, False)
        self.assertFalse(any(b"@MuchMake=" in p for box in self.inbox for _, p in box))
        cards = self.server.economy.inventory(self.keys[0])
        card = cards[2]
        body = (
            "@Decks=/C=1@0=/D=" + "|".join(map(str, card.reduced() + (3,))) + "@DN1=/S=4576656E74"
        )
        await self.send(0, self.cfg.init_id, packet(0xB2, 0x22, number, body))
        view = self.server.economy.account_view(self.keys[0])
        self.assertEqual(view.cards[2].deck, 3)
        self.assertEqual(dict(view.deck_names)[1], b"Event")
        self.assertTrue(self.server.economy.is_busy(self.keys[0]))
        quit = packet(0xB2, 0x17, number, f"@Quit=/No={number}")
        await self.send(0, self.cfg.init_id, quit)
        await self.send(0, self.cfg.init_id, quit)
        self.assertFalse(self.server.economy.is_busy(self.keys[0]))
        self.assertEqual(sum(b"@EQuit=" in p for _, p in self.inbox[0]), 2)
        self.assertEqual(self.server.economy.account_view(self.keys[0]), view)
        # Delayed original deck editor after clean session exit retains the
        # authenticated bounded context and may rename the same owned cards.
        await self.send(
            0, self.cfg.init_id, packet(0xB2, 0x22, number, "@Decks=/C=0@DN1=/S=4C617465")
        )
        self.assertEqual(
            dict(self.server.economy.account_view(self.keys[0]).deck_names)[1], b"Late"
        )
        next_number = await self.enter_selection(0, False)
        self.assertNotEqual(next_number, number)
        self.assertTrue(self.server.economy.is_busy(self.keys[0]))

    async def test_early_proposal_rejection_retries_same_entries_without_reentry(self):
        await self.start()
        for side in range(2):
            await self.enter_selection(side)
        for _ in range(10):
            if all(any(b"@MuchMake=/TblNo=" in p for _, p in box) for box in self.inbox):
                break
            await self.drain()
        epoch = self.server.event_matches.coordinator.snapshot("festival")["epoch"]
        for side, answer in ((0, 1), (1, 0)):
            await self.send(
                side,
                self.cfg.room_id,
                packet(
                    0xD3,
                    body=f"@MuchMakeAns=/Ans={answer}/TblNo=1/TblId={self.table.table_id:016X}",
                ),
            )
        self.assertEqual(
            self.server.event_matches.coordinator.snapshot("festival")["phase"], "backoff"
        )
        self.assertTrue(all(self.server.economy.is_busy(k) for k in self.keys))
        await asyncio.sleep(2.05)
        await self.drain()
        state = self.server.event_matches.coordinator.snapshot("festival")
        self.assertEqual((state["epoch"], state["phase"]), (epoch, "proposal"))
        for box in self.inbox:
            self.assertEqual(sum(b"@MuchMake=/TblNo=" in p for _, p in box), 2)

    async def test_result_save_failure_withholds_result_and_progress(self):
        await self.start(policy="rounds")
        await self.admit()
        original = self.server.persist_changes

        def fail_after_outcome(accounts, journals):
            if journals.get("event_results", {}).get("rounds"):
                with patch.object(
                    Path,
                    "replace",
                    side_effect=OSError("injected event result persistence failure"),
                ):
                    return original(accounts, journals)
            return original(accounts, journals)

        # Inject exactly at the completed-board transaction, not admission.
        with patch.object(self.server, "persist_changes", side_effect=fail_after_outcome):
            with self.assertRaises((AssertionError, ConnectionError)):
                await self.play()
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["event_results"]["rounds"], [])
        self.assertTrue(all(not any(b"@EventResult=" in p for _, p in box) for box in self.inbox))


if __name__ == "__main__":
    unittest.main()
