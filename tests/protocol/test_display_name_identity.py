"""Full saved names survive bounded client labels without merging identities."""

import asyncio
import struct
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.events import test_event_matches_integration as events
from tests.game import test_versus_integration as versus
from tests.protocol.test_admission_identity import native_identity
from tests.events.test_event_rooms import config
from tests.game.test_match_service import finish_arrowless_round, select
from tests.support.irc import Client
from tmserver.economy.service import EconomyService
from tmserver.events.models import EventError
from tmserver.events.service import EventHost
from tmserver.protocol.identities import player_id
from tmserver.game.errors import MatchError
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server

PREFIX = b"ABCDEFGHIJKLMNO"
NAMES = (PREFIX + b"1", PREFIX + b"2")


def provision_names(server):
    for index, (nick, name) in enumerate(zip(server.accounts, NAMES)):
        server.economy.provision(nick.decode(), nn=player_id(nick), player_id=index + 1, name=name)
        server.account_keys.add(nick.decode())
    server.persist()


class DisplayNameTests(unittest.TestCase):
    def test_full_names_share_display_but_keep_distinct_seats_through_result(self):
        match = MatchService(mode="versus", seed=11)
        for seat, name in enumerate(NAMES):
            match.register(seat, character_id=seat + 1, nickname_id=seat + 101, name=name)
        self.assertEqual([p.name for p in match.players], [PREFIX, PREFIX])
        init = decode(match.begin()[0].payload)
        self.assertEqual(init.sections["VsGameInit"]["CN"].split("|"), [PREFIX.hex().upper()] * 2)
        self.assertEqual(init.sections["VsGameInit"]["ID"].split("|"), ["1", "2"])
        for seat in range(2):
            match.handle(seat, select(seat=seat))
        finish_arrowless_round(match)
        self.assertEqual(match.phase, "result")
        self.assertEqual([p.nickname_id for p in match.players], [101, 102])

    def test_invalid_suffix_is_rejected_before_display_projection(self):
        match = MatchService(mode="versus")
        for name in (b"", PREFIX + b"12", PREFIX + b"\0", PREFIX + b"\x1f", PREFIX + b"\x7f"):
            with self.subTest(name=name), self.assertRaises(MatchError):
                match.register("peer", name=name)
            self.assertEqual(match.players, [])

    def test_event_projection_preserves_canonical_authentication_and_restore(self):
        economy = EconomyService(seed=123)
        cfg = config()
        for seat, name in enumerate(NAMES):
            economy.provision(str(seat), nn=seat + 101, player_id=seat + 1, name=name)
        before = economy.snapshot()
        host = EventHost(economy, (cfg,), clock=lambda: 200)
        for seat, name in enumerate(NAMES):
            key = str(seat)
            host.admit_room(key, cfg.id)
            body = (
                f"@EInitReq=/NN={seat + 101:016X}/HID=0/Dm=1/Vol=1"
                f"/CN={name.hex()}/GID={seat + 1:016X}"
            )
            host.authenticate_entry(key, packet(0x98, body=body), route="init")
            for forged in (PREFIX, NAMES[1 - seat]):
                with self.subTest(seat=seat, forged=forged), self.assertRaises(EventError):
                    host.authenticate_entry(
                        key, packet(0x98, body=body.replace(name.hex(), forged.hex())), route="init"
                    )
        restored_economy = EconomyService.restore(before)
        restored = EventHost.restore(restored_economy, (cfg,), host.snapshot(), clock=lambda: 200)
        members = restored.file("b/g/TM0EventMemberList", cfg.id)
        for seat, name in enumerate(NAMES):
            at = 8 + seat * 0x28
            self.assertEqual(struct.unpack_from("<Q", members, at)[0], seat + 101)
            self.assertEqual(members[at + 8 : at + 24], PREFIX + b"\0")
            self.assertEqual(restored_economy.account_view(str(seat)).name, name)
        self.assertEqual(host.snapshot(), restored.snapshot())
        self.assertEqual(economy.snapshot(), before)


class FullNameTableIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = versus.VersusIntegrationTests.asyncTearDown
    send = versus.VersusIntegrationTests.send
    flush = versus.VersusIntegrationTests.flush
    play_round = versus.VersusIntegrationTests.play_round

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.server = Server(state_path=self.path, match_mode="versus")
        provision_names(self.server)
        self.port = await self.server.start(0)
        self.clients = [Client(), Client()]
        for client, nick in zip(self.clients, self.server.accounts):
            await client.open(self.port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        self.keys = [c.nick.decode() for c in self.clients]

    async def test_canonical_names_admit_and_complete_encrypted_ordinary_round(self):
        for side, key in enumerate(self.keys):
            view = self.server.economy.account_view(key)
            await self.send(side, packet(0x41, 1, body=native_identity(view, "GameEN")))
            replies = await self.send(side, packet(1, body=native_identity(view, "Req")))
            ack = next(decode(p) for p in replies[side] if b"@PLAYACK=" in p)
            self.assertEqual(ack.sections["PLAYACK"]["EN"], "1")
        for side in range(2):
            await self.send(side, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(
            [p.name for p in self.server.default_table.match.players], [PREFIX, PREFIX]
        )
        await self.play_round()
        restored = Server(state_path=self.path, match_mode="versus")
        try:
            for key, name in zip(self.keys, NAMES):
                self.assertEqual(restored.economy.account_view(key).name, name)
                self.assertEqual(
                    restored.economy.profile_bytes(key), self.server.economy.profile_bytes(key)
                )
        finally:
            await restored.stop()


class FullNameEventIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = events.EventMatchIntegrationTests.asyncSetUp
    asyncTearDown = events.EventMatchIntegrationTests.asyncTearDown
    drain = events.EventMatchIntegrationTests.drain
    send = events.EventMatchIntegrationTests.send
    identity = events.EventMatchIntegrationTests.identity
    enter_selection = events.EventMatchIntegrationTests.enter_selection
    admit = events.EventMatchIntegrationTests.admit
    engine = events.EventMatchIntegrationTests.engine
    play = events.EventMatchIntegrationTests.play
    complete = events.EventMatchIntegrationTests.complete

    async def start(self, n=2, policy="none"):
        seed = Server(
            state_path=self.path,
            event_configs=(self.cfg,),
            event_tables=(self.table,),
            event_clock=lambda: 200,
            event_progress_policy=policy,
        )
        try:
            provision_names(seed)
        finally:
            await seed.stop()
        await events.EventMatchIntegrationTests.start(self, n, policy)

    async def test_restored_canonical_names_complete_encrypted_event_and_progress(self):
        await self.complete(2, "rounds")
        for key, name in zip(self.keys, NAMES):
            self.assertEqual(self.server.economy.account_view(key).name, name)
            self.assertEqual(self.server.events.member(key, self.cfg.id).name, PREFIX)


if __name__ == "__main__":
    unittest.main()
