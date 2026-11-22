"""Pre-start member removal through original code and encrypted sessions."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.game import test_three_player_integration as three
from tests.game import test_versus_integration as versus
from tests.game.test_match_service import NATIVE_SETTINGS
from tests.support.irc import Client
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.game.errors import MatchError
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server


def make_table():
    m = MatchService(mode="versus", player_count=3)
    for i in range(3):
        m.register(i, character_id=i + 1, nickname_id=i + 1, name=f"Player{i}")
        m.handle(i, packet(0x41, 1, body="@GameEN="))
    m.handle(0, packet(0x14, body=NATIVE_SETTINGS))
    return m


def kick(target):
    return packet(0x41, 5, body=f"@GameQT=/ID={target:016X}")


class KickUnit(unittest.TestCase):

    def test_owner_removes_only_target_and_duplicate_only_repeats_ack(self):
        m = make_table()
        settings = m.rules_body()
        replies = m.handle(0, kick(2))
        self.assertEqual([p.peer for p in m.players], [0, 2])
        self.assertEqual(m.phase, "lobby")
        self.assertEqual(m.owner_peer, 0)
        self.assertEqual(m.rules_body(), settings)
        self.assertEqual(
            [(d.peer, decode(d.payload).subtype) for d in replies],
            [(0, 6), (0, 12), (1, 12), (2, 12)],
        )
        again = m.handle(0, kick(2))
        self.assertEqual(
            [(d.peer, decode(d.payload).sections) for d in again], [(0, {"GameQA": {"QT": "2"}})]
        )
        m.register(1, character_id=2, nickname_id=2, name="Player1")
        with self.assertRaises(MatchError):
            m.handle(1, packet(1, body="@Req="))
        m.handle(1, packet(0x41, 1, body="@GameEN="))
        self.assertEqual(len(m.handle(0, kick(2))), 4)

    def test_nonowner_unknown_malformed_and_started_removals_do_not_mutate(self):
        for who, payload, start in [
            (1, kick(3), None),
            (0, kick(999), None),
            (0, packet(0x41, 5, body="@GameQT=/ID=garbage"), None),
            (0, kick(2), "offer"),
            (0, kick(2), "request"),
        ]:
            with self.subTest(who=who, payload=payload, start=start):
                m = make_table()
                if start == "offer":
                    m.handle(0, packet(0x41, 10, body="@GameReady="))
                if start == "request":
                    m.handle(1, packet(1, body="@Req="))
                before = m.snapshot()
                players = list(m.players)
                with self.assertRaises(MatchError):
                    m.handle(who, payload)
                self.assertEqual(m.snapshot(), before)
                self.assertEqual(m.players, players)

    def test_configured_owner_leave_transfers_host_and_guest_leave_preserves_it(self):
        for side, subtype in ((0, 5), (0, 13), (1, 5), (1, 13)):
            with self.subTest(side=side, subtype=subtype):
                m = make_table()
                rules = m.rules_body()
                out = m.handle(
                    side, packet(65, subtype, body="@GameQT=" if subtype == 5 else "@GameExit=")
                )
                self.assertEqual(m.owner_peer, 1 if side == 0 else 0)
                self.assertEqual([p.peer for p in m.players], [i for i in range(3) if i != side])
                self.assertEqual(m.phase, "lobby")
                self.assertEqual(m.rules_body(), rules)
                self.assertEqual(
                    len([d for d in out if decode(d.payload).primary == 26]), 2 if side == 0 else 0
                )
                self.assertEqual(out[0].peer, side)
                self.assertEqual(
                    decode(out[0].payload).sections,
                    {"GameQA": {"QT": "1"}} if subtype == 5 else {"GameEA": {"Exit": "1"}},
                )


class KickIntegration(unittest.IsolatedAsyncioTestCase):
    flush = versus.VersusIntegrationTests.flush
    round = three.ThreePlayerIntegrationTests.round

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.server = Server(
            state_path=self.path, match_mode="versus", match_player_count=3, account_count=4
        )
        port = await self.server.start(0)
        self.clients = []
        for nick in self.server.accounts:
            c = Client()
            self.clients.append(c)
            await c.open(port, account=nick)
            self.assertIn(b" 422 ", await c.read())
        self.all_clients = list(self.clients)
        self.keys = [c.nick.decode() for c in self.clients]

    async def asyncTearDown(self):
        for c in self.all_clients:
            await c.close()
        await self.server.stop()
        self.temp.cleanup()

    async def send(self, side, payload):
        await self.clients[side].send(b"NOTICE " + service_nick(TABLE_RAW) + b" :" + payload)
        out = [None] * len(self.clients)
        out[side] = await self.flush(side)
        for i in range(len(self.clients)):
            if i != side:
                out[i] = await self.flush(i)
        return out

    async def admit(self, side):
        return await self.send(
            side, packet(0x41, 1, body=f"@GameEN=/NN={player_id(self.clients[side].nick):016X}")
        )

    async def test_removed_connection_stays_live_and_replacement_finishes_full_round(self):
        for i in range(3):
            await self.admit(i)
        await self.send(0, packet(0x14, body=NATIVE_SETTINGS))
        match = self.server.default_table.match
        before = self.server.economy.snapshot()
        tokens = dict(self.server.default_table.match_reservations)
        removed = self.clients[1]
        removed_peer = self.server.nicks[removed.nick]
        removed_key = removed.nick.decode()
        removed_profile = self.server.economy.profile_bytes(removed_key)
        replies = await self.send(0, kick(player_id(removed.nick)))
        self.assertEqual(
            [[(decode(p).primary, decode(p).subtype) for p in r] for r in replies],
            [[(65, 6), (65, 12)], [(65, 12)], [(65, 12)], []],
        )
        self.assertIs(self.server.default_table.match, match)
        self.assertEqual(self.server.economy.snapshot(), before)
        self.assertFalse(self.server.economy.is_reserved(removed_key))
        for peer, token in tokens.items():
            if peer is not removed_peer:
                self.assertEqual(self.server.default_table.match_reservations[peer], token)
        self.assertEqual(
            await self.send(0, kick(player_id(removed.nick))),
            [[packet(0x41, 6, body="@GameQA=/QT=2")], [], [], []],
        )
        with self.assertRaises(ValueError):
            await self.server.default_table.dispatch_table(removed_peer, packet(1, body="@Req="))
        self.assertEqual(await self.send(1, packet(0x41, 8, body="@GameOK=")), [[], [], [], []])
        await self.admit(3)
        self.clients = [self.clients[i] for i in (0, 2, 3)]
        self.keys = [c.nick.decode() for c in self.clients]
        await self.send(0, packet(0x41, 10, body="@GameReady="))
        for i in range(3):
            await self.send(i, packet(1, body="@Req="))
        for i in range(3):
            await self.send(i, packet(0x41, 8, body="@GameOK="))
        self.assertEqual(match.phase, "selection")
        await self.round()
        await self.send(0, packet(0x43, 17, 0, "@Continue=/Ans=2/PR=0/Conf=0"))
        self.assertEqual(match.phase, "finished")
        self.assertEqual(self.server.economy.profile_bytes(removed_key), removed_profile)
        await removed.send(b"PING :removed-still-connected")
        self.assertEqual(await removed.read(), b"PONG removed-still-connected")
        self.assertFalse(self.server.default_table.match_reservations)

    async def test_host_can_leave_transfer_ownership_and_replacement_plays(self):
        for i in range(3):
            await self.admit(i)
        await self.send(0, packet(0x14, body=NATIVE_SETTINGS))
        match = self.server.default_table.match
        before = self.server.economy.snapshot()
        old_host = self.clients[0]
        request = packet(65, 5, body="@GameQT=")
        replies = await self.send(0, request)
        self.assertEqual(
            [[(decode(p).primary, decode(p).subtype) for p in r] for r in replies],
            [[(65, 6)], [(65, 4), (26, 0)], [(65, 4), (26, 0)], []],
        )
        self.assertIs(self.server.default_table.match, match)
        self.assertEqual(match.owner_peer, self.server.nicks[self.clients[1].nick])
        self.assertEqual(self.server.room.table_owner, player_id(self.clients[1].nick))
        self.assertEqual(self.server.room.table_count, 2)
        self.assertEqual(self.server.economy.snapshot(), before)
        self.assertFalse(self.server.economy.is_reserved(old_host.nick.decode()))
        self.assertEqual(
            await self.send(0, request), [[packet(65, 6, body="@GameQA=/QT=1")], [], [], []]
        )
        await self.admit(3)
        self.clients = self.clients[1:]
        self.keys = [c.nick.decode() for c in self.clients]
        await self.send(0, packet(65, 10, body="@GameReady="))
        for i in range(3):
            await self.send(i, packet(1, body="@Req="))
        for i in range(3):
            await self.send(i, packet(65, 8, body="@GameOK="))
        await self.round()
        await self.send(0, packet(67, 17, 0, "@Continue=/Ans=2/PR=0/Conf=0"))
        await old_host.send(b"PING :old-host-connected")
        self.assertEqual(await old_host.read(), b"PONG old-host-connected")

    async def test_guest_exit_then_channel_part_keeps_other_seats(self):
        for i in range(3):
            await self.admit(i)
        await self.send(0, packet(20, body=NATIVE_SETTINGS))
        match = self.server.default_table.match
        owner = match.owner_peer
        guest = self.clients[1]
        channel = self.server.default_table.table_channel
        await guest.send(b"JOIN " + channel)
        self.assertIn(b"JOIN :" + channel, await guest.read())
        self.assertIn(b" 353 ", await guest.read())
        self.assertIn(b" 366 ", await guest.read())
        replies = await self.send(1, packet(65, 13, body="@GameExit="))
        self.assertEqual(
            [[(decode(p).primary, decode(p).subtype) for p in r] for r in replies],
            [[(65, 4)], [(65, 14)], [(65, 4)], []],
        )
        await guest.send(b"PART " + channel)
        self.assertIn(b"PART " + channel, await guest.read())
        for i in range(4):
            self.assertEqual(await self.flush(i), [])
        self.assertIs(self.server.default_table.match, match)
        self.assertIs(match.owner_peer, owner)
        self.assertEqual(self.server.room.table_count, 2)
        self.assertEqual(len(self.server.default_table.match_reservations), 2)

    async def test_kick_before_settings_updates_empty_table_metadata(self):
        for i in range(3):
            await self.admit(i)
        self.assertFalse(self.server.default_table.match.settings_applied)
        self.assertEqual((self.server.room.table_state, self.server.room.table_count), (0, 3))
        await self.send(0, kick(player_id(self.clients[1].nick)))
        self.assertEqual((self.server.room.table_state, self.server.room.table_count), (0, 2))
        self.assertEqual(self.server.room.table_owner, player_id(self.clients[0].nick))

    async def test_closed_removed_peer_releases_retry_markers_without_resetting_table(self):
        for i in range(3):
            await self.admit(i)
        await self.send(0, packet(20, body=NATIVE_SETTINGS))
        match = self.server.default_table.match
        peer = self.server.nicks[self.clients[1].nick]
        await self.send(1, packet(65, 5, body="@GameQT="))
        self.assertIn(peer, match.kicked_peers)
        self.assertIn((peer, 5), match.lobby_exit_receipts)
        await peer.close()
        self.assertNotIn(peer, match.kicked_peers)
        self.assertNotIn((peer, 5), match.lobby_exit_receipts)
        self.assertIs(self.server.default_table.match, match)
        self.assertEqual(len(self.server.default_table.match_reservations), 2)


if __name__ == "__main__":
    unittest.main()
