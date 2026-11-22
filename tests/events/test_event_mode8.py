"""Original mode8 entry skips EInit/selection; no playable table is invented."""

import copy
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tests.events import test_event_matches_integration as events
from tests.events.test_event_configuration import DOCUMENT
from tests.events.test_event_rooms import presence
from tests.support.irc import Client
from tmserver.events.config import parse_event_setup
from tmserver.game.packets import decode, packet
from tmserver.network.server import Server


class EventMode8Integration(events.EventMatchHelpers, unittest.IsolatedAsyncioTestCase):
    async def test_entry_with_zero_owned_cards_is_nonplaying_and_durable(self):
        self.cfg = replace(self.cfg, entry_answer=2)
        self.server = Server(
            store=self.store, event_configs=(self.cfg,), event_clock=lambda: 200
        )
        # Explicitly provision an empty owned collection before authentication.
        nick = next(iter(self.server.accounts))
        key = self.server.ensure_account(nick)
        view = self.server.economy.account_view(key)
        self.server.economy._accounts[key].cards = []
        before = self.server.economy.profile_bytes(key)
        port = await self.server.start(0)
        client = Client()
        self.clients = [client]
        self.inbox = [[]]
        await client.open(port, account=nick)
        self.assertIn(b" 422 ", await client.read())
        self.keys = [key]
        binding = self.server.event_rooms.rooms[0]
        await self.send(
            0, self.cfg.room_id, presence(binding.room_id, view.nn, view.player_id, view.name)
        )
        request = packet(
            0x98, body=self.identity(0, "EInitReq") + f"/Dm=1/Vol=1/GID={view.player_id:016X}"
        )
        for _ in range(2):
            await self.send(0, self.cfg.init_id, request)
        replies = [decode(p) for _, p in self.inbox[0] if p.startswith(b"GTM0G99")]
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(p.sections == {"EInitEnt": {"Card": "0", "Ans": "2"}} for p in replies))
        self.assertFalse(
            any(
                b"@EInit=" in p or b"@MuchMake=" in p or b"@EventGameInit=" in p
                for _, p in self.inbox[0]
            )
        )
        self.assertEqual(self.server.events.admission(key).mode, 8)
        self.assertFalse(self.server.economy.is_busy(key))
        self.assertEqual(self.server.event_matches.coordinator.pending(), ())
        self.assertEqual(self.server.economy.profile_bytes(key), before)
        restored = Server(store=self.store, event_configs=(self.cfg,), event_clock=lambda: 200)
        try:
            self.assertEqual(restored.economy.profile_bytes(key), before)
        finally:
            await restored.stop()
        await self.send(
            0, self.cfg.room_id, packet(0xD1, 4, body=f"@EventTimeReq=/NN={view.nn:016X}")
        )
        self.assertTrue(any(b"@EventTimeReqCheck=" in p for _, p in self.inbox[0]))
        await client.close()
        await self.server.stop()
        self.assertIsNone(self.server.events.admission(key))


class EventMode8Configuration(unittest.TestCase):
    def test_observer_entry_requires_no_playable_table_and_rejects_one(self):
        document = copy.deepcopy(DOCUMENT)
        document["events"][0].update(match_enabled=True, entry_answer=2)
        configs, tables, policy = parse_event_setup(document)
        self.assertEqual((configs[0].entry_answer, tables, policy), (2, (), "none"))
        self.assertEqual([r[0x28] for r in configs[0].service_records()], [ord("E"), ord("F")])
        document["tables"] = [dict(event_id="festival", table_raw=4099, channel="#EVTABLE001")]
        with self.assertRaises(ValueError):
            parse_event_setup(document)


if __name__ == "__main__":
    unittest.main()
