"""Event directory, admission and durable F claims over encrypted sockets."""

import asyncio
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.network import test_services_integration as service_tests
from tests.events.test_event_rooms import presence
from tests.profiles.test_profile_accounts import transact
from tests.profiles.test_profile_location import request as file_request
from tests.support.irc import Client
from tmserver.economy.packets import named, parse_named
from tmserver.events.models import EventConfig, EventListing
from tmserver.events.packets import event_data
from tmserver.protocol.identities import game_id
from tmserver.profiles.service import ProfileServer
from tmserver.network.server import Server
from tmserver.protocol.commands import parse


class EventIntegrationTests(unittest.IsolatedAsyncioTestCase):
    request = service_tests.ServicesIntegrationTests.request

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.path = Path(self.temp.name) / "state.json"
        self.now = 200
        self.config = EventConfig(
            "festival",
            EventListing(b"Local Lobby", b"Festival", b"Local event", scalar=4096),
            event_data(classes=(b"First", b"Next"), steps_per_class=4),
            100,
            500,
            game_id(0x1000),
            game_id(0x1001),
            game_id(0x1002),
            card_service_enabled=True,
        )
        await self.start()

    async def start(self):
        self.server = Server(
            state_path=self.path, event_configs=(self.config,), event_clock=lambda: self.now
        )
        self.port = await self.server.start(0)
        self.profile = ProfileServer(Path(self.temp.name) / "profile.jsonl", self.server)
        self.pp = await self.profile.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()

    async def stop(self):
        await self.client.close()
        await self.profile.stop()
        await self.server.stop()

    async def asyncTearDown(self):
        await self.stop()
        self.temp.cleanup()

    async def profile_file(self, path, raw=0x1000):
        error, data = await transact(
            self.pp,
            self.key,
            3,
            0,
            file_request(path, raw, size=0x100000),
            context=(self.client.cipher, self.client.iv),
        )
        self.assertEqual(error, 0)
        return data[:-4]

    async def enter_room(self):
        binding = self.server.event_rooms.rooms[0]
        await self.client.join(binding.room_channel)
        view = self.server.economy.account_view(self.key)
        out = await self.request(0x1000, named(0xD1, 4, 0, f"@EventTimeReq=/NN={view.nn:016X}"))
        self.assertEqual(parse_named(out[0])[3]["EventTimeReqCheck"]["Now"], str(self.now))
        out = await self.request(
            0x1000, presence(binding.room_id, view.nn, view.player_id, view.name)
        )
        self.assertEqual([parse(p[5:])[0].token for p in out[:2]], [b"DS", b"DD"])
        self.assertEqual(parse_named(out[2])[3]["EventEn"]["Ans"], "1")
        return binding

    def card_request(self):
        view = self.server.economy.account_view(self.key)
        return named(
            0x90, 0, 0, f"@CardReq=/NN={view.nn:016X}/HID=0/Dm=1/Vol=1/CN=" + view.name.hex()
        )

    async def collection(self):
        out = await self.request(0x1002, self.card_request())
        self.assertEqual(parse_named(out[0])[3]["CardEnt"]["Ans"], "1")
        return int(parse_named(out[1])[3]["EcmInit"]["N"]), parse_named(out[1])[3]["EcmInit"], out

    async def test_directory_admission_twelve_card_grant_two_visits_and_post_end_claim(self):
        await self.enter_room()
        rooms = await self.profile_file("b/g/RL000")
        self.assertEqual(struct.unpack_from("<I", rooms, 0x40)[0], 2)
        ptl = await self.profile_file("b/g/PTL")
        self.assertEqual(struct.unpack_from("<I", ptl, 0x48)[0], 1)
        self.assertEqual(ptl[0x5878], ord("F"))
        self.assertEqual(struct.unpack_from("<Q", ptl, 0x5850)[0], self.config.card_id)
        card = replace(self.server.economy.inventory(self.key)[0], deck=255)
        self.assertTrue(
            self.server.event_cards.grant(
                self.key, "festival", "earned1", cards=(card,) * 12, money=250
            )
        )
        self.server.persist()
        session, init, original = await self.collection()
        self.assertEqual((init["C"], init["M"], init["PM"]), ("9", "10000", "250"))
        self.assertEqual(self.server.economy.balance(self.key), 10250)
        self.assertEqual(await self.request(0x1002, self.card_request()), original)
        self.assertEqual(
            await self.request(0x1002, named(0xB2, 0x15, session, f"@Get=/No={session}")), []
        )
        self.assertEqual(len(self.server.economy.inventory(self.key)), 19)
        out = await self.request(0x1002, self.card_request())
        self.assertEqual(parse_named(out[1])[3]["EcmInit"]["C"], "0")
        quit = named(0xB2, 0x17, session, f"@Quit=/No={session}")
        out = await self.request(0x1002, quit)
        self.assertEqual(await self.request(0x1002, quit), out)
        self.now = 600
        session, init, _ = await self.collection()
        self.assertEqual((init["C"], init["M"], init["PM"]), ("3", "10250", "0"))
        await self.request(0x1002, named(0xB2, 0x15, session, f"@Get=/No={session}"))
        await self.request(0x1002, named(0xB2, 0x17, session, f"@Quit=/No={session}"))
        self.assertEqual(len(self.server.economy.inventory(self.key)), 22)
        self.assertFalse(
            self.server.event_cards.grant(
                self.key, "festival", "earned1", cards=(card,) * 12, money=250
            )
        )
        snapshot = self.server.event_cards.snapshot()
        await self.stop()
        await self.start()
        await self.enter_room()
        self.assertEqual(self.server.event_cards.snapshot(), snapshot)
        session, init, _ = await self.collection()
        self.assertEqual((init["C"], init["PM"]), ("0", "0"))
        await self.request(0x1002, named(0xB2, 0x17, session, f"@Quit=/No={session}"))

    async def test_interrupted_reveal_preserves_cards_without_recrediting_money(self):
        await self.enter_room()
        card = replace(self.server.economy.inventory(self.key)[0], deck=255)
        self.server.event_cards.grant(self.key, "festival", "earned2", cards=(card,) * 3, money=75)
        self.server.persist()
        await self.collection()
        self.assertEqual(len(self.server.economy.inventory(self.key)), 10)
        await self.stop()
        await self.start()
        await self.enter_room()
        session, init, _ = await self.collection()
        self.assertEqual((init["C"], init["M"], init["PM"]), ("3", "10075", "0"))
        await self.request(0x1002, named(0xB2, 0x15, session, f"@Get=/No={session}"))
        await self.request(0x1002, named(0xB2, 0x17, session, f"@Quit=/No={session}"))
        self.assertEqual(
            (len(self.server.economy.inventory(self.key)), self.server.economy.balance(self.key)),
            (13, 10075),
        )

    async def test_failed_save_does_not_deliver_claim_or_consume_durable_grant(self):
        await self.enter_room()
        card = replace(self.server.economy.inventory(self.key)[0], deck=255)
        self.server.event_cards.grant(self.key, "festival", "earned3", cards=(card,), money=100)
        self.server.persist()
        saved = self.path.read_bytes()
        from tmserver.protocol.identities import service_nick

        with patch.object(Path, "replace", side_effect=OSError("injected event save failure")):
            await self.client.send(b"NOTICE " + service_nick(0x1002) + b" :" + self.card_request())
            self.assertEqual(await self.client.read(), b"")
        self.assertEqual(self.path.read_bytes(), saved)
        await self.stop()
        await self.start()
        await self.enter_room()
        session, init, _ = await self.collection()
        self.assertEqual((init["C"], init["M"], init["PM"]), ("1", "10000", "100"))
        await self.request(0x1002, named(0xB2, 0x15, session, f"@Get=/No={session}"))
        await self.request(0x1002, named(0xB2, 0x17, session, f"@Quit=/No={session}"))

    async def test_native_overflow_edits_and_get_preserve_inventory_cap(self):
        await self.enter_room()
        card = replace(self.server.economy.inventory(self.key)[0], deck=255)
        token = self.server.economy.reserve_auction(self.key)
        self.server.economy.commit_auction(token, add=(card,) * 990)
        self.server.event_cards.grant(self.key, "festival", "overflow", cards=(card, card))
        self.server.persist()
        session, init, _ = await self.collection()
        self.assertEqual((init["S"], init["C"]), ("1000", "2"))
        reduced = "|".join(map(str, card.reduced()))
        self.assertEqual(
            await self.request(
                0x1002,
                named(0xB2, 0x10, session, f"@Erase=/Mode=1/C=2@0=/D={reduced}@1=/D={reduced}"),
            ),
            [],
        )
        self.assertEqual(
            await self.request(
                0x1002,
                named(0xB2, 0x22, session, f"@Decks=/C=1@0=/D={reduced}|1@DN1=/S=4576656E74"),
            ),
            [],
        )
        await self.request(0x1002, named(0xB2, 0x10, session, "@Erase=/Mode=2"))
        self.assertEqual(len(self.server.economy.inventory(self.key)), 1000)
        await self.request(0x1002, named(0xB2, 0x15, session, f"@Get=/No={session}"))
        await self.request(0x1002, named(0xB2, 0x17, session, f"@Quit=/No={session}"))
        view = self.server.economy.account_view(self.key)
        self.assertEqual(len(view.cards), 1000)
        self.assertEqual(sum(c.deck == 1 for c in view.cards), 1)
        self.assertEqual(dict(view.deck_names)[1], b"Event")
        self.assertEqual(
            self.server.event_cards.pending(self.key, "festival"), {"cards": 0, "money": 0}
        )
        await self.stop()
        await self.start()
        self.assertEqual(self.server.economy.account_view(self.key), view)


if __name__ == "__main__":
    unittest.main()
