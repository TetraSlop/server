"""Authoritative table-entry restrictions without native UI or persistent saves."""

import unittest
from unittest.mock import AsyncMock, patch

from tests.protocol.test_admission_identity import native_identity
from tests.game.test_match_service import NATIVE_SETTINGS
from tests.network.test_session_recovery import Writer
from tmserver.cards.catalog import Catalog, collection_points
from tmserver.protocol.identities import player_id
from tmserver.game.packets import decode, packet
from tmserver.rooms.config import OrdinaryRoomConfig
from tmserver.rooms.rules import RoomRules
from tmserver.network.server import Server, Session
from tmserver.rooms.spectators import SpectatorService

CATALOG = Catalog()
CARDS = tuple(CATALOG.generate(i, lambda: 0) for i in range(5))


class TableAdmissionRulesTests(unittest.IsolatedAsyncioTestCase):
    def host(
        self, *, guest_cards=CARDS, guest_balance=10000, mode="versus", rules=None, watch=False
    ):
        rooms = (
            ()
            if rules is None
            else (
                OrdinaryRoomConfig(
                    b"Fixed", 0x101, 0x201, b"#FIXED", b"#FIXEDTABLE", mode=mode, rules=rules
                ),
            )
        )
        host = Server(
            match_mode=mode,
            allow_wagers=True,
            ordinary_rooms=rooms,
            spectator=SpectatorService(enabled=watch),
        )
        self.addAsyncCleanup(host.stop)
        peers = []
        for index, nick in enumerate(host.accounts):
            key = nick.decode("ascii")
            host.economy.provision(
                key,
                nn=player_id(nick),
                player_id=host.account_specs[key][2],
                name=f"Player{index}".encode(),
                cards=CARDS if index == 0 else guest_cards,
                balance=10000 if index == 0 else guest_balance,
            )
            host.account_keys.add(key)
            peer = Session(host, None, Writer(), index + 1)
            peer.nick = nick
            host.trade.bind(peer, key)
            host.nicks[nick] = peer
            host.sessions.add(peer)
            peers.append(peer)
        return host, host.ordinary_tables[-1], peers

    async def admit(self, table, peer, section="GameEN"):
        view = table.host.economy.account_view(peer.nick.decode())
        primary, subtype = (
            (1, 0) if section == "Req" else (0x41, 0x12 if section == "GameENC" else 1)
        )
        return await table.dispatch_table(
            peer, packet(primary, subtype, body=native_identity(view, section))
        )

    async def configure(self, table, owner, *, lower=0, upper=100, wager=0):
        body = NATIVE_SETTINGS.replace("/ll=0", f"/ll={lower}").replace("/lu=100", f"/lu={upper}")
        body = body.replace("/bm=0", f"/bm={wager}")
        await table.dispatch_table(owner, packet(0x14, body=body))

    async def rejected(self, host, table, peer, section):
        match = table.match
        generation = table.match_instance_id
        before = (
            table.match.snapshot(),
            dict(table.match_reservations),
            host.economy.snapshot(),
            table.room.snapshot(),
            table.match.rng.state,
        )
        with patch.object(table, "deliver_table_packet", new_callable=AsyncMock) as deliver:
            self.assertTrue(await self.admit(table, peer, section))
        reply_section, code = {
            "GameEN": ("GameEA", -32871),
            "GameENC": ("GameECA", -32871),
            "Req": ("PLAYACK", -32869),
        }[section]
        deliver.assert_awaited_once()
        recipient, reply = deliver.await_args.args
        self.assertIs(recipient, peer)
        self.assertEqual(decode(reply).sections, {reply_section: {"EN": str(code)}})
        self.assertIs(table.match, match)
        self.assertEqual(table.match_instance_id, generation)
        self.assertEqual(
            before,
            (
                table.match.snapshot(),
                dict(table.match_reservations),
                host.economy.snapshot(),
                table.room.snapshot(),
                table.match.rng.state,
            ),
        )
        self.assertFalse(host.economy.is_reserved(peer.nick.decode()))
        self.assertFalse(peer.closed)

    async def test_occupied_table_rejects_cards_money_and_both_point_bounds_without_mutation(self):
        cp = collection_points(CARDS)
        for cards, balance, lower, upper, wager in (
            (CARDS[:4], 10000, 0, 100, 0),
            (CARDS, 24, 0, 100, 25),
            (CARDS, 10000, cp + 1, 100, 0),
            (CARDS, 10000, 0, cp - 1, 0),
        ):
            with self.subTest(cards=len(cards), balance=balance, lower=lower, upper=upper):
                host, table, (owner, guest) = self.host(guest_cards=cards, guest_balance=balance)
                await self.admit(table, owner)
                await self.configure(table, owner, lower=lower, upper=upper, wager=wager)
                for section in ("GameEN", "Req"):
                    await self.rejected(host, table, guest, section)

    async def test_exact_boundaries_admit_and_existing_members_can_retry_after_settings_change(
        self,
    ):
        host, table, (owner, guest) = self.host(guest_balance=25)
        cp = collection_points(CARDS)
        await self.admit(table, owner)
        await self.configure(table, owner, lower=cp, upper=cp, wager=25)
        await self.admit(table, guest)
        token = table.match_reservations[guest]
        await self.configure(table, owner, lower=cp + 1, upper=100, wager=26)
        for section in ("GameEN", "Req"):
            await self.admit(table, guest, section)
            self.assertIs(table.match_reservations[guest], token)
        self.assertEqual(len(table.match.players), 2)
        self.assertEqual(host.economy.account_view(guest.nick.decode()).balance, 25)

    async def test_fewer_than_five_cards_cannot_create_human_or_computer_table(self):
        for mode, section in (("versus", "GameEN"), ("versus", "Req"), ("computer", "GameENC")):
            with self.subTest(mode=mode, section=section):
                host, table, (_, guest) = self.host(mode=mode, guest_cards=CARDS[:4])
                await self.rejected(host, table, guest, section)
                self.assertFalse(table.match.players)

    async def test_fixed_empty_human_wager_and_separate_computer_gate(self):
        for section in ("GameEN", "Req"):
            with self.subTest(section=section):
                host, table, (_, guest) = self.host(guest_balance=24, rules=RoomRules(bm=25))
                await self.rejected(host, table, guest, section)
        for mode, section, rules in (
            ("versus", "GameEN", None),
            ("computer", "GameENC", RoomRules(bm=25, gs=0)),
        ):
            with self.subTest(mode=mode):
                host, table, (_, guest) = self.host(mode=mode, guest_balance=0, rules=rules)
                await self.admit(table, guest, section)
                self.assertTrue(host.economy.is_reserved(guest.nick.decode()))

    async def test_observer_needs_no_owned_cards_or_matching_point_range(self):
        host, table, (owner, guest) = self.host(
            mode="computer",
            guest_cards=(),
            guest_balance=0,
            watch=True,
        )
        await self.admit(table, owner)
        await self.configure(table, owner, lower=10, upper=10)
        await table.dispatch_table(owner, packet(0x41, 10, body="@GameReady="))
        await self.admit(table, owner, "Req")
        await table.dispatch_table(owner, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(table.match.phase, "selection")
        guest.channels.add(table.table_channel)
        before = table.match.snapshot(), host.economy.snapshot(), dict(table.match_reservations)
        with patch.object(table, "queue_spectator") as queued:
            await self.admit(table, guest, "Req")
        queued.assert_called_once()
        recipient, reply = queued.call_args.args[0][0]
        self.assertIs(recipient, guest)
        self.assertEqual(decode(reply).sections["PLAYACK"]["EN"], "1")
        self.assertIn(guest, table.spectator_admissions)
        self.assertEqual(
            (table.match.snapshot(), host.economy.snapshot(), dict(table.match_reservations)),
            before,
        )


if __name__ == "__main__":
    unittest.main()
