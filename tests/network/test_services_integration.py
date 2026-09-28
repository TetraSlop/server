"""Encrypted loopback integration of room, shop, profile state and match routing."""

import asyncio
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.profiles.test_options_service import OFFSETS, VALUES
from tests.profiles.test_options_service import request as options_request
from tests.network.test_server import Client
from tests.profiles.test_table_preferences import (
    GAME as SAVED_GAME,
)
from tests.profiles.test_table_preferences import (
    TABLE as SAVED_TABLE,
)
from tests.profiles.test_table_preferences import (
    save as save_preferences,
)
from tmserver.economy.service import named, parse_named
from tmserver.protocol.identities import ROOM_RAW, SHOP_RAW, TABLE_RAW, player_id, service_nick
from tmserver.game.match import decode, packet
from tmserver.profiles.service import ProfileServer, checked
from tmserver.rooms.service import ROOM_CHANNEL, ROOM_ID, TABLE_CHANNEL, TABLE_ID
from tmserver.network.server import Server
from tmserver.profiles.preferences import read_preferences
from tmserver.protocol.commands import Command, encode, parse


class ServicesIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.state_path = Path(self.temp.name) / "state.json"
        self.server = Server(Path(self.temp.name) / "traffic.jsonl", self.state_path)
        self.port = await self.server.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode("ascii")

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def request(self, raw, payload):
        target = service_nick(raw)
        await self.client.send(b"NOTICE " + target + b" :" + payload)
        # PONG orders input processing. Scheduled battle continuations need a
        # second barrier after their task completes to drain their wire replies.
        await self.client.send(b"PING :service-check")
        responses = []
        drained_work = False
        for _ in range(200):
            line = await self.client.read()
            if line == b"PONG service-check":
                task = self.server.default_table.match_task
                if drained_work and task is None:
                    return responses
                if task is not None:
                    await asyncio.wait_for(asyncio.shield(task), 3)
                drained_work = True
                await self.client.send(b"PING :service-check")
                continue
            self.assertTrue(line, "server disconnected while processing request")
            self.assertTrue(
                any(
                    line.startswith(b":" + source + b"!p@local NOTICE " + self.client.nick + b" :")
                    for source in (target, service_nick(ROOM_RAW))
                ),
                line,
            )
            responses.append(line.split(b" :", 1)[1])
        self.fail("service reply limit exceeded")

    async def test_native_empty_table_settings_and_channel_start(self):
        await self.client.join(ROOM_CHANNEL)
        fields = [
            f"0x{player_id(self.client.nick):016X}".encode(),
            b"1",
            b"0",
            b"0",
            b"LocalPlayer",
            b"2",
            b"0",
            b"0",
            b"0",
            f"0x{ROOM_ID:016X}".encode(),
            b"LocalPlayer    LAAAABAGDBAABIAB",
        ]
        await self.request(ROOM_RAW, b"GTM0L" + encode([Command(b"DE", b"\x06".join(fields))]))
        out = await self.request(
            TABLE_RAW, packet(0x41, 1, body=f"@GameEN=/NN={player_id(self.client.nick):016X}")
        )
        self.assertEqual(parse(out[0][5:])[-1].token, b"TD")
        self.assertIn(b"@GameEA=/EN=1", out[1])
        rules = (
            "@Tet=/bm=0/du=0/st=0/cb=0/ca=0/gs=0/tl=0@Tab=/in=0/lu=100/ll=0/au=300/al=100/co=0/pa=0"
        )
        out = await self.request(TABLE_RAW, packet(0x14, body=rules))
        self.assertEqual([parse(p[5:])[-1].token for p in out[:2]], [b"PD", b"TD"])
        self.assertEqual(
            [(decode(p).primary, decode(p).subtype) for p in out[2:]], [(0x41, 4), (0x1A, 0)]
        )
        self.assertIn(b"@GameML=", out[2])
        self.assertIn(b"/Num=2", out[2])
        self.assertEqual((self.server.room.table_state, self.server.room.table_count), (1, 2))
        table_info = parse(out[1][5:])[-1].payload.split(b"\x06")[-1]
        self.assertEqual(table_info[35:37], b"AA")  # native tl=0, gs=0
        self.assertEqual(table_info[39:44], b"AAAGE")  # native upper level100
        self.assertEqual(self.server.room.snapshot()[0x5878 : 0x5878 + 49], table_info)
        self.assertEqual(self.server.room.table_owner, player_id(self.client.nick))
        self.assertEqual(self.server.room.npcs[512][1][0x38 + 23 : 0x38 + 25], b"DB")
        # The actual client writes PART with a colon. Echo with a bare first
        # parameter, so original polcore removes its channel record.
        await self.client.send(b"PART :" + ROOM_CHANNEL)
        self.assertEqual(
            await self.client.read(), b":" + self.client.nick + b"!p@local PART " + ROOM_CHANNEL
        )
        await self.client.join(TABLE_CHANNEL)
        await self.request(TABLE_RAW, packet(0x41, 0x0A, body="@GameReady="))
        out = await self.request(
            TABLE_RAW, packet(1, body=f"@Req=/NN={player_id(self.client.nick):016X}")
        )
        self.assertIn(b"@PLAYACK=/EN=1" + rules.encode(), out[-1])
        out = await self.request(TABLE_RAW, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(parse(out[0][5:])[-1].token, b"TD")
        self.assertEqual(
            [(decode(p).primary, decode(p).subtype) for p in out[1:]], [(0x41, 9), (0x43, 1)]
        )
        self.assertEqual(self.server.default_table.match.phase, "selection")
        await self.client.send(b"PART :" + TABLE_CHANNEL)
        self.assertEqual(
            await self.client.read(), b":" + self.client.nick + b"!p@local PART " + TABLE_CHANNEL
        )
        # Releasing the seat publishes the empty table and removes the CPU.
        for token in (b"TD", b"PC"):
            line = await self.client.read()
            self.assertEqual(parse(line.split(b" :", 1)[1][5:])[-1].token, token)
        self.assertEqual(self.server.room.table_count, 0)
        self.assertEqual(self.server.room.npcs, {})

    async def enter_shop(self):
        out = await self.request(
            SHOP_RAW,
            named(
                0xB0,
                0,
                0,
                f"@ShReq=/NN={player_id(self.client.nick):016X}/HID=0/Dm=0/Vol=0/CN="
                + b"LocalPlayer".hex(),
            ),
        )
        self.assertEqual(len(out), 2)
        self.assertEqual(parse_named(out[0])[3], {"ShEnter": {"EN": "1"}})
        self.assertEqual(parse_named(out[1])[2], 0)
        return int(parse_named(out[1])[3]["Init"]["N"])

    async def test_native_options_ack_and_persisted_profile(self):
        # Actual native traffic addresses Opt to the current shop identity.
        replies = await self.request(SHOP_RAW, options_request(nn=player_id(self.client.nick)))
        self.assertEqual(replies, [named(0x85, 0, 0, "@OptAns=/Ans=1")])
        profile = self.profile()
        self.assertEqual([profile[offset] for offset in OFFSETS], list(VALUES.values()))
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.economy.profile_bytes(self.key), profile)
        finally:
            await restored.stop()

    async def test_saved_table_defaults_persist_without_activating_current_rules(self):
        await self.request(
            TABLE_RAW, packet(0x41, 1, body=f"@GameEN=/NN={player_id(self.client.nick):016X}")
        )
        current = self.server.default_table.match.rules_body()
        before = read_preferences(self.profile())
        self.assertEqual(await self.request(TABLE_RAW, save_preferences(tbl=0)), [])
        saved = read_preferences(self.profile())
        self.assertEqual(saved["Tet"], SAVED_GAME)
        self.assertEqual(saved["Tab"], before["Tab"])
        self.assertEqual(self.server.default_table.match.rules_body(), current)
        self.assertTrue(self.server.economy.is_reserved(self.key))
        self.assertEqual(await self.request(TABLE_RAW, save_preferences(gm=0)), [])
        expected = {"Tet": SAVED_GAME, "Tab": dict(SAVED_TABLE, pw=SAVED_TABLE["pw"].upper())}
        self.assertEqual(read_preferences(self.profile()), expected)
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(read_preferences(restored.economy.profile_bytes(self.key)), expected)
        finally:
            await restored.stop()
        self.assertEqual(self.server.default_table.match.rules_body(), current)

    def profile(self):
        service = ProfileServer(Path(self.temp.name) / "profile.jsonl", self.server)
        request = bytearray(0x19C)
        request[0x10:0x20] = b"U/g/TM0DataFile\0"
        struct.pack_into("<II", request, 0x190, 0, 0x3028)
        data, error = service.dispatch(3, 0, checked(request))
        self.assertEqual(error, 0)
        return data[:-4]

    async def test_purchase_over_encrypted_socket_persists_and_reloads_profile(self):
        session = await self.enter_shop()
        before = self.profile()
        self.assertEqual(struct.unpack_from("<iHBBH", before, 0x34)[::4], (10000, 10))
        replies = await self.request(SHOP_RAW, named(0xB2, 0x14, session, "@Buy=/No=1"))
        self.assertEqual(parse_named(replies[0])[3]["Card"]["S"], "10")
        self.assertEqual(self.profile(), before)
        # Duplicate Buy replays the same ten cards without a second debit.
        self.assertEqual(
            await self.request(SHOP_RAW, named(0xB2, 0x14, session, "@Buy=/No=1")), replies
        )
        self.assertEqual(
            await self.request(SHOP_RAW, named(0xB2, 0x15, session, f"@Get=/No={session}")), []
        )
        await self.request(SHOP_RAW, named(0xB2, 0x17, session, f"@Quit=/No={session}"))
        after = self.profile()
        self.assertEqual(struct.unpack_from("<i", after, 0x34)[0], 9000)
        self.assertEqual(struct.unpack_from("<H", after, 0x3C)[0], 20)
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.economy.profile_bytes(self.key), after)
        finally:
            await restored.stop()

    async def test_complete_practice_match_through_encrypted_socket(self):
        before = self.profile()
        await self.request(
            TABLE_RAW, packet(1, body=f"@Req=/NN={player_id(self.client.nick):016X}")
        )
        await self.request(TABLE_RAW, packet(0x41, 8, body="@GameOK="))
        cards = self.server.economy.inventory(self.key)[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
        )
        await self.request(TABLE_RAW, packet(0x43, 7, 0, body))
        received = []
        for _ in range(30):
            m = self.server.default_table.match
            if m.phase == "result":
                break
            received += await self.request(
                TABLE_RAW, packet(0x43, 9, m.sequence, "@TurnData=/Ans=0")
            )
            if m.phase == "turn" and m.active == 0:
                field = m.legal_fields()[0]
                received += await self.request(
                    TABLE_RAW, packet(0x43, 10, m.sequence, f"@PutData=/P=0/H=0/F={field}")
                )
            while m.phase == "battle":
                received += await self.request(
                    TABLE_RAW, packet(0x43, 11, m.sequence, f"@BattleSelect=/B={m.targets[0]}")
                )
        self.assertEqual(self.server.default_table.match.phase, "result")
        self.assertEqual(self.server.default_table.match.sequence, 10)
        self.assertEqual(self.server.default_table.match.snapshot()["hand_counts"], [0, 0])
        self.assertEqual(sum(decode(x).subtype == 13 for x in received), 1)
        peer = self.server.nicks[self.client.nick]
        final = self.server.default_table.match.final_cards(peer)
        saved = self.server.economy.inventory(self.key)
        self.assertEqual(
            tuple("|".join(map(str, c.full())) for c in saved[:5]),
            tuple(c.full() for c in final.updated),
        )
        self.assertEqual(self.server.economy.balance(self.key), 10000)
        self.assertEqual(len(saved), 10)
        after = self.profile()
        self.assertNotEqual(after, before)
        result = await self.request(TABLE_RAW, packet(0x43, 14, 10, "@Ready=/Ans=0"))
        self.assertEqual(decode(result[0]).subtype, 0x1A)
        self.assertEqual(await self.request(TABLE_RAW, packet(0x43, 14, 10, "@Ready=/Ans=0")), [])
        await self.request(TABLE_RAW, packet(0x43, 17, 0, "@Continue=/Ans=2/PR=0/Conf=0"))
        self.assertEqual(self.server.default_table.match.phase, "finished")
        self.assertEqual(self.profile(), after)
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.economy.profile_bytes(self.key), after)
        finally:
            await restored.stop()

    async def test_complete_special_cpu_match_through_encrypted_socket(self):
        self.server.default_table.match_start_fields = (
            3,
            0,
            0,
            0,
            0,
            7,
            9,
            0,
            0,
            0,
            17,
            0,
            0,
            0,
            0,
            0,
        )
        self.server.default_table.reset_match()
        await self.test_complete_practice_match_through_encrypted_socket()

    async def test_detached_result_deck_name_edit_after_continue_and_part(self):
        await self.client.join(TABLE_CHANNEL)
        await self.test_complete_practice_match_through_encrypted_socket()
        await self.client.send(b"PART :" + TABLE_CHANNEL)
        self.assertEqual(
            await self.client.read(), b":" + self.client.nick + b"!p@local PART " + TABLE_CHANNEL
        )
        self.assertEqual(self.server.default_table.match.players, [])
        self.assertFalse(self.server.economy.is_reserved(self.key))
        before = self.server.economy.inventory(self.key)
        payload = packet(0x43, 0x22, 0, "@Decks=/P=0/C=0@DN1=/S=" + b"After Match".hex())
        self.assertEqual(await self.request(TABLE_RAW, payload), [])
        self.assertEqual(
            dict(self.server.economy.account_view(self.key).deck_names)[1], b"After Match"
        )
        self.assertEqual(self.server.economy.inventory(self.key), before)
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(
                dict(restored.economy.account_view(self.key).deck_names)[1], b"After Match"
            )
        finally:
            await restored.stop()

    async def test_disconnect_rolls_back_incomplete_purchase(self):
        session = await self.enter_shop()
        before = self.profile()
        await self.request(SHOP_RAW, named(0xB2, 0x14, session, "@Buy=/No=1"))
        await self.client.close()
        await self.server.stop()
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.economy.profile_bytes(self.key), before)
        finally:
            await restored.stop()

    async def test_result_save_failure_never_acknowledges_unsaved_growth(self):
        before = self.profile()
        durable = self.state_path.read_bytes()
        await self.request(
            TABLE_RAW, packet(1, body=f"@Req=/NN={player_id(self.client.nick):016X}")
        )
        await self.request(TABLE_RAW, packet(0x41, 8, body="@GameOK="))
        cards = self.server.economy.inventory(self.key)[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
        )
        await self.request(TABLE_RAW, packet(0x43, 7, 0, body))
        match = self.server.default_table.match
        for _ in range(20):
            if match.sequence == 9:
                break
            await self.request(TABLE_RAW, packet(0x43, 9, match.sequence, "@TurnData=/Ans=0"))
            if match.phase == "turn" and match.active == 0:
                field = next(i for i, c in enumerate(match.board) if c is None)
                await self.request(
                    TABLE_RAW, packet(0x43, 10, match.sequence, f"@PutData=/P=0/H=0/F={field}")
                )
            while match.phase == "battle":
                await self.request(
                    TABLE_RAW,
                    packet(0x43, 11, match.sequence, f"@BattleSelect=/B={match.targets[0]}"),
                )
        self.assertEqual((match.sequence, match.active), (9, 1))
        with patch.object(
            Path, "replace", side_effect=OSError("injected final-round save failure")
        ):
            await self.client.send(
                b"NOTICE "
                + service_nick(TABLE_RAW)
                + b" :"
                + packet(0x43, 9, 9, "@TurnData=/Ans=0")
            )
            for _ in range(100):
                line = await self.client.read()
                if not line:
                    break
                payload = line.split(b" :", 1)[-1]
                decoded = decode(payload)
                self.assertFalse(
                    decoded and decoded.primary == 0x43 and decoded.subtype == 13,
                    "unsaved result was acknowledged",
                )
            else:
                self.fail("storage failure did not terminate the connection")
            await self.server.stop()
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.state_path.read_bytes(), durable)
        restored = Server(state_path=self.state_path)
        try:
            self.assertEqual(restored.economy.profile_bytes(self.key), before)
        finally:
            await restored.stop()

    async def test_room_join_snapshot_and_replay_through_socket(self):
        fields = [
            f"0x{player_id(self.client.nick):016X}".encode(),
            b"1",
            b"2",
            b"0",
            b"LocalPlayer",
            b"0",
            b"0",
            b"0",
            b"0",
            f"0x{ROOM_ID:016X}".encode(),
            b"A" * 31,
        ]
        out = await self.request(
            ROOM_RAW, b"GTM0L" + encode([Command(b"DE", b"\x06".join(fields))])
        )
        self.assertEqual(parse(out[0][5:]), [Command(b"DS", f"0x{ROOM_ID:016X}".encode())])
        self.assertEqual(parse(out[1][5:])[-1].token, b"PD")
        snap = self.server.room.snapshot()
        self.assertEqual(struct.unpack_from("<III", snap, 0x40), (101, 1, 2))
        self.assertEqual(struct.unpack_from("<Q", snap, 0x5850)[0], TABLE_ID)
        replay = await self.request(ROOM_RAW, b"GTM0L" + encode([Command(b"DR", b"100")]))
        self.assertEqual(replay, [out[1]])

    async def test_match_admission_and_owned_selection_through_socket(self):
        before = self.profile()
        replies = await self.request(
            TABLE_RAW, packet(1, body=f"@Req=/NN={player_id(self.client.nick):016X}")
        )
        self.assertIn(b"@PLAYACK=/EN=1", replies[0])
        replies = await self.request(TABLE_RAW, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(
            [(decode(x).primary, decode(x).subtype) for x in replies], [(0x41, 9), (0x43, 1)]
        )
        cards = self.server.economy.inventory(self.key)[:5]
        body = "@CardSelect=/C=5" + "".join(
            f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards)
        )
        replies = await self.request(TABLE_RAW, packet(0x43, 7, 0, body))
        self.assertEqual([decode(x).subtype for x in replies], [7, 8, 9])
        self.assertEqual(self.server.default_table.match.snapshot()["hand_counts"], [5, 5])
        self.assertEqual(await self.request(TABLE_RAW, packet(0x43, 9, 0, "@TurnData=/Ans=0")), [])
        self.assertEqual(self.profile(), before)


if __name__ == "__main__":
    unittest.main()
