"""Advertised collection-point bounds govern encrypted human table admission."""

import unittest

from tests.game import test_versus_integration as versus
from tests.protocol.test_admission_identity import native_identity
from tests.game.test_match_service import NATIVE_SETTINGS
from tmserver.cards.catalog import collection_points
from tmserver.game.packets import decode, packet


class TableEligibilityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    start_fields = None
    asyncSetUp = versus.VersusIntegrationTests.asyncSetUp
    asyncTearDown = versus.VersusIntegrationTests.asyncTearDown
    send = versus.VersusIntegrationTests.send
    flush = versus.VersusIntegrationTests.flush

    def admission_state(self):
        return (
            self.server.snapshot(),
            self.server.default_table.match.snapshot(),
            self.server.default_table.match.rng.state,
            dict(self.server.default_table.match_reservations),
            self.server.room.snapshot(),
            self.server.room.sequence,
            self.server.store.load(),
        )

    async def test_collection_bound_rejects_without_disconnect_then_admits_exact_boundary(self):
        owner = self.server.economy.account_view(self.keys[0])
        guest = self.server.economy.account_view(self.keys[1])
        guest_peer = self.server.nicks[self.clients[1].nick]
        points = collection_points(guest.cards)
        self.assertLess(points, 99999)

        replies = await self.send(0, packet(0x41, 1, body=native_identity(owner, "GameEN")))
        self.assertEqual(decode(replies[0][0]).sections["GameEA"]["EN"], "1")
        settings = NATIVE_SETTINGS.replace("/lu=100/ll=0", f"/lu={points + 1}/ll={points + 1}")
        await self.send(0, packet(0x14, body=settings))
        self.assertTrue(self.server.default_table.match.settings_applied)
        before = self.admission_state()

        for request, section, answer, error in (
            ((0x41, 1, 1), "GameEN", "GameEA", "-32871"),
            ((1, 0, 0), "Req", "PLAYACK", "-32869"),
        ):
            with self.subTest(request=section):
                replies = await self.send(1, packet(*request, body=native_identity(guest, section)))
                self.assertEqual(replies[0], [])
                self.assertEqual(len(replies[1]), 1)
                self.assertEqual(decode(replies[1][0]).sections, {answer: {"EN": error}})
                self.assertEqual(self.admission_state(), before)
                self.assertNotIn(guest_peer, self.server.default_table.match_reservations)
                self.assertFalse(self.server.economy.is_reserved(self.keys[1]))

        # The same authenticated connection can join when both inclusive
        # collection-point bounds are exactly its committed collection value.
        settings = NATIVE_SETTINGS.replace("/lu=100/ll=0", f"/lu={points}/ll={points}")
        await self.send(0, packet(0x14, body=settings))
        replies = await self.send(1, packet(0x41, 1, 1, native_identity(guest, "GameEN")))
        self.assertEqual(decode(replies[1][0]).sections["GameEA"]["EN"], "2")
        self.assertEqual(len(self.server.default_table.match.players), 2)
        self.assertIn(guest_peer, self.server.default_table.match_reservations)
        self.assertTrue(self.server.economy.is_reserved(self.keys[1]))

        replies = await self.send(1, packet(1, body=native_identity(guest, "Req")))
        ack = next(decode(reply) for reply in replies[1] if decode(reply).family == 2)
        self.assertEqual(ack.sections["PLAYACK"]["EN"], "1")
        self.assertEqual((ack.sections["Tab"]["ll"], ack.sections["Tab"]["lu"]), (str(points),) * 2)
        self.assertEqual(self.server.snapshot(), before[0])


if __name__ == "__main__":
    unittest.main()
