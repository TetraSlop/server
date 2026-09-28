"""Published password metadata and authenticated table owner/joiner paths."""

import unittest

from tests.game import test_versus_integration as versus
from tests.game.test_match_service import NATIVE_SETTINGS
from tmserver.protocol.identities import TABLE_RAW, player_id, service_nick
from tmserver.game.match import decode, packet


class TableSettingsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    start_fields = None
    asyncSetUp = versus.VersusIntegrationTests.asyncSetUp
    asyncTearDown = versus.VersusIntegrationTests.asyncTearDown
    send = versus.VersusIntegrationTests.send
    flush = versus.VersusIntegrationTests.flush

    async def test_password_metadata_rules_and_existing_table_join(self):
        owner = f"{player_id(self.clients[0].nick):016X}"
        replies = await self.send(0, packet(0x41, 1, body="@GameEN=/NN=" + owner))
        self.assertEqual(decode(replies[0][0]).sections["GameEA"]["EN"], "1")
        password = b"AbCd1234"
        settings = NATIVE_SETTINGS.replace("/pa=0", "/pa=1/pw=" + password.hex())
        await self.send(0, packet(0x14, body=settings))
        self.assertIn(password, self.server.room.snapshot())
        replies = await self.send(1, packet(0x12, body="@Rule="))
        rules = decode(replies[1][0]).sections["Tab"]
        self.assertEqual((rules["pa"], rules["pw"]), ("1", password.hex().upper()))
        joiner = f"{player_id(self.clients[1].nick):016X}"
        replies = await self.send(1, packet(0x41, 1, 1, "@GameEN=/NN=" + joiner))
        self.assertEqual(decode(replies[1][0]).sections["GameEA"]["EN"], "2")
        self.assertEqual(self.server.default_table.match.owner_id, int(owner, 16))
        for seat, identity in enumerate((owner, joiner)):
            replies = await self.send(seat, packet(1, body="@Req=/NN=" + identity))
            playack = next(decode(p) for p in replies[seat] if decode(p).primary == 2)
            self.assertEqual(playack.sections["PLAYACK"]["EN"], "1")
            self.assertEqual(playack.sections["Tab"]["pw"], password.hex().upper())

    async def test_forged_display_identity_cannot_claim_another_accounts_table(self):
        foreign = f"{player_id(self.clients[1].nick):016X}"
        await self.clients[0].send(
            b"NOTICE "
            + service_nick(TABLE_RAW)
            + b" :"
            + packet(0x41, 1, body="@GameEN=/NN=" + foreign)
        )
        self.assertEqual(await self.clients[0].read(), b"")
        self.assertFalse(self.server.default_table.match.players)
        self.assertFalse(self.server.default_table.match_reservations)
        await self.clients[1].send(b"PING :still-here")
        self.assertEqual(await self.clients[1].read(), b"PONG still-here")


if __name__ == "__main__":
    unittest.main()
