"""Three-player WatchInfo/WatchCard consumers and encrypted observer isolation."""

import unittest
from pathlib import Path

from tests.game import test_three_player_integration as versus
from tests.profiles.test_profile_accounts import file_request, transact
from tests.rooms.harness import instant_spectators, read_notice, serve
from tests.support.memory_store import DATABASE
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.network.local_accounts import account_specs
from tmserver.game.packets import decode, packet
from tmserver.profiles.service import ProfileServer
from tmserver.rooms.packets import TABLE_CHANNEL
from tmserver.cli.serve import arguments


class ThreePlayerSpectatorIntegration(unittest.IsolatedAsyncioTestCase):
    flush = versus.ThreePlayerIntegrationTests.flush
    send = versus.ThreePlayerIntegrationTests.send
    admit = versus.ThreePlayerIntegrationTests.admit
    round = versus.ThreePlayerIntegrationTests.round

    async def asyncSetUp(self):
        await serve(
            self,
            account_count=4,
            match_mode="versus",
            match_player_count=3,
            spectator=instant_spectators(),
        )
        self.keys = [c.nick.decode() for c in self.clients[:3]]
        self.target = service_nick(TABLE_RAW)

    async def read_watch(self):
        return await read_notice(self, self.clients[3], self.target)

    async def test_fourth_account_watches_full_three_player_round_and_profile(self):
        await self.admit()
        observer = self.clients[3]
        key = observer.nick.decode()
        before = self.server.economy.profile_bytes(key)
        await observer.send(
            b"NOTICE " + self.target + b" :" + packet(0x2D, body="@CheckJoinTable=")
        )
        self.assertEqual(
            decode(await self.read_watch()).sections["CheckJoinTable_Ans"],
            {"Join": "1", "Mem": "0"},
        )
        await observer.join(TABLE_CHANNEL)
        await observer.send(
            b"NOTICE "
            + self.target
            + b" :"
            + packet(1, body=f"@Req=/NN={player_id(observer.nick):016X}/HID=4")
        )
        self.assertEqual(decode(await self.read_watch()).family, 2)
        await observer.send(
            b"NOTICE " + self.target + b" :" + packet(0x43, 0x25, 0, "@Data=/Watch=0")
        )
        boot = decode(await self.read_watch())
        self.assertEqual((boot.session, boot.sections["WatchInfo"]["N"]), (0, "3"))
        self.assertFalse(self.server.economy.is_busy(key))
        await self.round()
        live = []
        for _ in range(200):
            msg = decode(await self.read_watch())
            live.append(msg)
            if msg.subtype == 13:
                break
        else:
            self.fail("observer never received final result")
        self.assertEqual([m.session for m in live if m.subtype == 9], list(range(15)))
        self.assertEqual([m.session for m in live if m.subtype == 10], list(range(15)))
        self.assertFalse(any(m.subtype == 0x1A for m in live))
        self.assertEqual(self.server.economy.profile_bytes(key), before)
        profile = ProfileServer(Path(self.harness_temp.name) / "profile.jsonl", self.server)
        port = await profile.start(0)
        try:
            row = account_specs(4)[key]
            error, data = await transact(
                port,
                key,
                3,
                0,
                file_request(),
                context=(observer.cipher, observer.iv),
                account_id=row[0],
            )
            self.assertEqual(error, 0)
            self.assertEqual(data[:-4], before)
        finally:
            await profile.stop()
        await observer.send(
            b"NOTICE " + self.target + b" :" + packet(0x43, 9, 15, "@TurnData=/Ans=3")
        )
        self.assertEqual(await observer.read(), b"")
        self.assertTrue(all(not self.server.nicks[c.nick].closed for c in self.clients[:3]))

    def test_four_accounts_option_survives_three_player_auto_enrollment(self):
        args = arguments([*DATABASE, "--mode", "versus", "--players", "3", "--accounts", "4"])
        self.assertEqual(args.accounts, 4)


if __name__ == "__main__":
    unittest.main()
