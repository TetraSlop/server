"""Current rules stay fixed once players enter the ordinary start handshake."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests import test_versus_integration as versus
from tests.test_match_service import NATIVE_SETTINGS
from tests.test_server import Client
from tmserver.identities import TABLE_RAW, service_nick
from tmserver.match_service import MatchError, MatchService, decode, packet
from tmserver.server import Server
from tmserver.table_roster import TableRoster


CHANGED_SETTINGS = NATIVE_SETTINGS.replace("/tl=0", "/tl=5").replace(
    "/pa=0", "/pa=1/pw=" + b"NewPass!".hex().upper()
)


def lobby():
    match = MatchService(mode="versus")
    for peer in (0, 1):
        match.register(peer, character_id=peer + 1, nickname_id=peer + 1)
        match.handle(peer, packet(0x41, 1, body="@GameEN="))
    match.handle(0, packet(0x14, body=NATIVE_SETTINGS))
    return match


def start_handshake(match, stage):
    if stage == "offer":
        return match.handle(0, packet(0x41, 10, body="@GameReady="))
    match.handle(1, packet(1, body="@Req="))
    if stage == "accepted":
        match.handle(1, packet(0x41, 8, body="@GameOK="))
    return []


class SettingsStartTests(unittest.TestCase):
    def test_pending_start_rejects_changes_but_accepts_exact_retry_without_mutation(self):
        for stage in ("offer", "request", "accepted"):
            with self.subTest(stage=stage):
                match = lobby()
                start_handshake(match, stage)
                before = match.snapshot()
                for body in (CHANGED_SETTINGS, NATIVE_SETTINGS.replace("/st=0", "/st=1")):
                    with self.assertRaisesRegex(MatchError, "start handshake"):
                        match.handle(0, packet(0x14, body=body))
                    self.assertEqual(match.snapshot(), before)
                    self.assertEqual(match.rules_body(), NATIVE_SETTINGS)
                    self.assertEqual(match.special_flags, {"st": 0, "cb": 0, "ca": 0})
                    self.assertEqual(match.settings_revision, 1)
                self.assertEqual(match.handle(0, packet(0x14, body=NATIVE_SETTINGS)), [])
                self.assertEqual(match.snapshot(), before)
                self.assertEqual(match.settings_revision, 1)
                with self.assertRaisesRegex(MatchError, "owner"):
                    match.handle(1, packet(0x14, body=NATIVE_SETTINGS))

    def test_saved_defaults_remain_separate_during_start_handshake(self):
        for stage in ("offer", "request", "accepted"):
            with self.subTest(stage=stage):
                match = lobby()
                start_handshake(match, stage)
                for peer in (0, 1):
                    match.handle(peer, packet(0x24, body="@Save=/Tbl=1/Game=1" + CHANGED_SETTINGS))
                    saved = match.saved_preferences[peer]
                    self.assertEqual(saved["Tet"]["tl"], 5)
                    self.assertEqual(bytes.fromhex(saved["Tab"]["pw"]), b"NewPass!")
                self.assertEqual(match.rules_body(), NATIVE_SETTINGS)
                self.assertEqual(match.settings_revision, 1)

    def test_delayed_guest_invitation_retains_original_rules_and_completes(self):
        match = lobby()
        roster = TableRoster()
        roster.ready.add(0)
        roster.publish(match, [])
        self.assertEqual(roster.publish(match, start_handshake(match, "offer")), [])
        invitation = dict(roster.invitations)
        self.assertEqual(set(invitation), {1})
        with self.assertRaises(MatchError):
            match.handle(0, packet(0x14, body=CHANGED_SETTINGS))
        retry = match.handle(0, packet(0x14, body=NATIVE_SETTINGS))
        self.assertEqual(roster.publish(match, retry), [])
        self.assertEqual(roster.invitations, invitation)
        roster.ready.add(1)
        delivered = roster.publish(match, [])
        self.assertEqual(
            [(row.peer, decode(row.payload).subtype) for row in delivered], [(1, 4), (1, 7)]
        )
        self.assertFalse(roster.invitations)
        for peer in (0, 1):
            ack = match.handle(peer, packet(1, body="@Req="))
            self.assertEqual(decode(ack[0].payload).sections["Tet"]["tl"], "0")
            match.handle(peer, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(match.phase, "selection")


class SettingsStartIntegrationTests(unittest.IsolatedAsyncioTestCase):
    send = versus.VersusIntegrationTests.send
    flush = versus.VersusIntegrationTests.flush

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(state_path=Path(self.temp.name) / "state.json", match_mode="versus")
        port = await self.server.start(0)
        self.clients = []
        for nick in self.server.accounts:
            client = Client()
            self.clients.append(client)
            await client.open(port, account=nick)
            self.assertIn(b" 422 ", await client.read())
        for side in (0, 1):
            await self.send(side, packet(0x41, 1, body="@GameEN="))
        await self.send(0, packet(0x14, body=NATIVE_SETTINGS))

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.server.stop()
        self.temp.cleanup()

    async def reject_changed_settings(self, stage):
        if stage in ("offer", "accepted"):
            await self.send(0, packet(0x41, 10, body="@GameReady="))
        if stage != "offer":
            replies = await self.send(1, packet(1, body="@Req="))
            ack = next(decode(row) for row in replies[1] if decode(row).primary == 2)
            self.assertEqual(ack.sections["Tet"]["tl"], "0")
        if stage == "accepted":
            await self.send(1, packet(0x41, 8, body="@GameOK="))
        match = self.server.match
        before = self.server.economy.snapshot()
        self.assertEqual(await self.send(0, packet(0x14, body=NATIVE_SETTINGS)), [[], []])
        self.assertEqual(match.settings_revision, 1)
        await self.clients[0].send(
            b"NOTICE " + service_nick(TABLE_RAW) + b" :" + packet(0x14, body=CHANGED_SETTINGS)
        )
        # Invalid current Apply uses the established fail-closed table cleanup;
        # neither client can proceed with a different PLAYACK or stale admission.
        for client in self.clients:
            self.assertEqual(await client.read(), b"")
        self.assertEqual(match.rules_body(), NATIVE_SETTINGS)
        self.assertEqual(match.settings_revision, 1)
        self.assertIsNot(self.server.match, match)
        self.assertFalse(self.server.match.players)
        self.assertFalse(self.server.match_reservations)
        self.assertFalse(self.server.table_roster.invitations)
        self.assertEqual(self.server.room.table_count, 0)
        self.assertEqual(self.server.economy.snapshot(), before)
        for client in self.clients:
            self.assertFalse(self.server.economy.is_reserved(client.nick.decode()))

    async def test_change_after_start_offer_cleans_up_both_clients(self):
        await self.reject_changed_settings("offer")

    async def test_change_after_direct_request_cleans_up_both_clients(self):
        await self.reject_changed_settings("request")

    async def test_change_after_guest_acceptance_cleans_up_both_clients(self):
        await self.reject_changed_settings("accepted")

    async def test_edit_after_guest_entry_before_start_publishes_consistent_rules(self):
        await self.send(0, packet(0x14, body=CHANGED_SETTINGS))
        self.assertIn(b"NewPass!", self.server.room.snapshot())
        await self.send(0, packet(0x41, 10, body="@GameReady="))
        for side in (0, 1):
            replies = await self.send(side, packet(1, body="@Req="))
            ack = next(decode(row) for row in replies[side] if decode(row).primary == 2)
            self.assertEqual(ack.sections["Tet"]["tl"], "5")
            self.assertEqual(bytes.fromhex(ack.sections["Tab"]["pw"]), b"NewPass!")
            await self.send(side, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(self.server.match.phase, "selection")
        self.assertEqual(self.server.match.rules_body(), CHANGED_SETTINGS)
        self.assertEqual(self.server.match.settings_revision, 2)


if __name__ == "__main__":
    unittest.main()
