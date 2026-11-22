"""Observer snapshots and skipped turns after opt-in practice departures."""

import unittest

from tests.rooms import test_departure_integration as departures
from tests.support.irc import Client
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.game.packets import decode, packet
from tmserver.rooms.packets import TABLE_CHANNEL
from tmserver.rooms.spectators import SpectatorService


class ObserverDepartureIntegration(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = departures.DepartureIntegrationTests.asyncSetUp
    start = departures.DepartureIntegrationTests.start
    send = departures.DepartureIntegrationTests.send
    flush = departures.DepartureIntegrationTests.flush
    flush_all = departures.DepartureIntegrationTests.flush_all
    settle = departures.DepartureIntegrationTests.settle
    drop = departures.DepartureIntegrationTests.drop
    finish = departures.DepartureIntegrationTests.finish
    continue_leave = departures.DepartureIntegrationTests.continue_leave

    async def read_watch(self):
        for _ in range(100):
            line = await self.observer.read()
            self.assertTrue(line, "observer unexpectedly closed")
            if b" NOTICE " in line:
                return decode(line.split(b" :", 1)[1])
        self.fail("observer response limit")

    async def scenario(self, rule, late):
        await self.start(
            count=3,
            accounts=4,
            rule=rule,
            spectator=SpectatorService(
                enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
            ),
        )
        self.observer = Client()
        await self.observer.open(self.port, account=list(self.server.accounts)[3])
        self.addAsyncCleanup(self.observer.close)
        self.assertIn(b" 422 ", await self.observer.read())
        before = self.server.economy.profile_bytes(self.observer.nick.decode())
        if late:
            await self.drop(1)
        await self.observer.join(TABLE_CHANNEL)
        target = service_nick(TABLE_RAW)
        await self.observer.send(
            b"NOTICE "
            + target
            + b" :"
            + packet(1, body=f"@Req=/NN={player_id(self.observer.nick):016X}/HID=4")
        )
        self.assertEqual((await self.read_watch()).family, 2)
        await self.observer.send(
            b"NOTICE " + target + b" :" + packet(0x43, 0x25, 0, "@Data=/Watch=0")
        )
        initial = [await self.read_watch() for _ in range(3)]
        self.assertEqual([m.subtype for m in initial], [0x25, 0x26, 9])
        fields = initial[0].sections["WatchInfo"]
        self.assertEqual(fields["Q"], "0|1|0" if late else "0|0|0")
        self.assertEqual(fields["C"], "0|1|0" if late and rule == 0 else "0|0|0")
        if not late:
            await self.drop(1)
        await self.finish()
        seen = []
        for _ in range(200):
            msg = await self.read_watch()
            seen.append(msg)
            if msg.subtype == 13:
                break
        else:
            self.fail("missing observer departure result")
        expected = [
            decode(p).session
            for p in self.received[0]
            if decode(p).family == 0x43 and decode(p).subtype == 9
        ]
        self.assertEqual(
            [initial[-1].session] + [m.session for m in seen if m.subtype == 9], expected
        )
        self.assertEqual(sum(m.subtype == 0x23 for m in seen), int(not late))
        self.assertEqual(sum(m.subtype == 0x24 for m in seen), int(not late and rule == 0))
        self.assertEqual(self.server.economy.profile_bytes(self.observer.nick.decode()), before)
        await self.continue_leave(0)

    async def test_live_substitution(self):
        await self.scenario(0, False)

    async def test_late_substitution(self):
        await self.scenario(0, True)

    async def test_live_invalid_skips(self):
        await self.scenario(1, False)

    async def test_late_invalid_skips(self):
        await self.scenario(1, True)

    async def test_live_loss_skips(self):
        await self.scenario(2, False)

    async def test_late_loss_skips(self):
        await self.scenario(2, True)


if __name__ == "__main__":
    unittest.main()
