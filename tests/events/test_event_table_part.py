"""Encrypted event table exits affect only the exact current table owner."""

import asyncio
import unittest

from tests.events import test_event_multitable as event_tests
from tests.protocol.test_admission_identity import native_identity
from tests.events.test_event_rooms import presence
from tests.network.test_server import Client
from tmserver.protocol.identities import GAME_KEY, service_nick
from tmserver.game.packets import decode, packet


class EventTablePartTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = event_tests.EventMultiTable.asyncSetUp
    start = event_tests.EventMultiTable.start
    drain = event_tests.EventMultiTable.drain
    send = event_tests.EventMultiTable.send
    identity = event_tests.EventMultiTable.identity
    enter_selection = event_tests.EventMultiTable.enter_selection
    round = event_tests.EventMultiTable.round
    admit_all = event_tests.EventMultiTable.admit_all

    async def asyncTearDown(self):
        clients = [*self.clients, *getattr(self, "observers", ())]
        await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
        if self.server is not None:
            await self.server.stop()
        self.temp.cleanup()

    async def start_observer(self):
        await self.start(observer=True)
        await self.admit_all()
        self.observer = Client()
        self.observers.append(self.observer)
        await self.observer.open(self.port, account=list(self.server.accounts)[4])
        self.assertIn(b" 422 ", await self.observer.read())
        self.observer_key = self.observer.nick.decode()
        self.observer_view = self.server.economy.account_view(self.observer_key)
        self.observer_peer = self.server.nicks[self.observer.nick]
        view = self.observer_view
        await self.observer_send(
            self.cfg.room_id, presence(self.cfg.room_id, view.nn, view.player_id, view.name)
        )
        await self.observer_barrier()

    async def observer_send(self, target, payload):
        await self.observer.send(b"NOTICE " + service_nick(target ^ GAME_KEY) + b" :" + payload)

    async def observer_read(self, target):
        source = b":" + service_nick(target ^ GAME_KEY) + b"!p@local NOTICE "
        for _ in range(200):
            line = await self.observer.read()
            self.assertTrue(line, "observer unexpectedly disconnected")
            if line.startswith(source):
                return line.split(b" :", 1)[1]
        self.fail("missing observer table response")

    async def observer_barrier(self):
        await self.observer.send(b"PING :table-part")
        for _ in range(200):
            line = await self.observer.read()
            self.assertTrue(line, "observer unexpectedly disconnected")
            if line == b"PONG table-part":
                return
        self.fail("missing observer barrier")

    async def watch(self, index):
        table = self.tables[index]
        await self.observer_send(table.table_id, packet(0x2D, body="@CheckJoinTable="))
        discovery = decode(await self.observer_read(table.table_id))
        self.assertEqual(discovery.sections["CheckJoinTable_Ans"]["Join"], "1")
        await self.observer.send(b"JOIN " + table.channel)
        await self.observer_barrier()
        await self.observer_send(
            table.table_id, packet(1, body=native_identity(self.observer_view, "Req"))
        )
        self.assertEqual(decode(await self.observer_read(table.table_id)).primary, 2)
        await self.observer_send(table.table_id, packet(0x43, 0x25, body="@Data=/Watch=0"))
        bootstrap = [decode(await self.observer_read(table.table_id)).subtype for _ in range(3)]
        self.assertEqual(bootstrap, [0x25, 0x26, 9])

    async def exit_first_watch(self):
        await self.watch(0)
        await self.observer_send(self.tables[0].table_id, packet(32, body="@ExitCh="))
        await self.observer_barrier()
        self.assertNotIn(self.observer_peer, self.server.event_matches.spectators.admissions)
        self.assertIn(self.tables[0].channel, self.observer_peer.channels)

    async def test_old_table_part_preserves_new_observer_and_current_part_releases_it(self):
        await self.start_observer()
        await self.exit_first_watch()
        await self.watch(1)
        spectators = self.server.event_matches.spectators
        admission = spectators.admissions[self.observer_peer]
        before = self.server.snapshot(), [self.round(i).engine.snapshot() for i in (0, 1)]
        await self.observer.send(b"PART " + self.tables[0].channel)
        await self.observer_barrier()
        self.assertEqual(spectators.admissions.get(self.observer_peer), admission)
        self.assertIn(self.tables[1].channel, self.observer_peer.channels)
        await self.observer.send(b"PART " + self.tables[1].channel)
        await self.observer_barrier()
        self.assertNotIn(self.observer_peer, spectators.admissions)
        self.assertFalse(self.observer_peer.closed)
        self.assertEqual(
            (self.server.snapshot(), [self.round(i).engine.snapshot() for i in (0, 1)]), before
        )
        self.assertTrue(all(self.server.economy.is_reserved(key) for key in self.keys))

    async def test_old_table_part_preserves_unassigned_same_room_selection(self):
        await self.start_observer()
        await self.exit_first_watch()
        view = self.observer_view
        await self.observer_send(
            self.cfg.room_id, packet(0xD1, 4, body=f"@EventTimeReq=/NN={view.nn:016X}")
        )
        await self.observer_send(
            self.cfg.init_id,
            packet(
                0x98,
                body=f"@EInitReq=/NN={view.nn:016X}/HID=0/CN={view.name.hex()}"
                f"/Dm=1/Vol=1/GID={view.player_id:016X}",
            ),
        )
        for _ in range(10):
            reply = decode(await self.observer_read(self.cfg.init_id))
            if "EInit" in reply.sections:
                number = int(reply.sections["EInit"]["N"])
                break
        else:
            self.fail("missing event selection session")
        coordinator = self.server.event_matches.coordinator
        before = self.server.snapshot(), [self.round(i).engine.snapshot() for i in (0, 1)]
        # Neither the modal nor a selected hand waiting for a free table owns A.
        for selected in (False, True):
            if selected:
                await self.observer.send(b"JOIN " + self.tables[0].channel)
                cards = view.cards[2:7]
                body = "@CardSelect=/C=5" + "".join(
                    f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(cards)
                )
                await self.observer_send(self.cfg.init_id, packet(0xB2, 7, number, body))
                self.assertIn(b"@Select=/A=1", await self.observer_read(self.cfg.init_id))
                self.assertIn(b"@EQuit=", await self.observer_read(self.cfg.init_id))
            await self.observer.send(b"PART " + self.tables[0].channel)
            await self.observer_barrier()
            self.assertEqual(coordinator.session(self.observer_peer), number)
            self.assertTrue(self.server.economy.is_reserved(self.observer_key))
            self.assertTrue(
                all(
                    entry.peer is not self.observer_peer
                    for i in (0, 1)
                    for entry in self.round(i).entries
                )
            )
        room = self.server.event_rooms.rooms[0].room
        await self.observer.send(b"JOIN " + room.room_channel)
        await self.observer.send(b"PART " + room.room_channel)
        await self.observer_barrier()
        self.assertIsNone(coordinator.session(self.observer_peer))
        self.assertFalse(self.server.economy.is_reserved(self.observer_key))
        self.assertNotIn(self.observer_peer, room.members)
        self.assertEqual(
            (self.server.snapshot(), [self.round(i).engine.snapshot() for i in (0, 1)]), before
        )

    async def test_unrelated_table_part_preserves_participant_current_part_aborts_only_owner(self):
        await self.start()
        await self.admit_all()
        peer = self.server.nicks[self.clients[2].nick]
        before = self.server.snapshot(), [self.round(i).engine.snapshot() for i in (0, 1)]
        await self.clients[2].send(b"JOIN " + self.tables[0].channel)
        await self.drain()
        await self.clients[2].send(b"PART " + self.tables[0].channel)
        await self.drain()
        self.assertFalse(peer.closed)
        self.assertEqual(
            (self.server.snapshot(), [self.round(i).engine.snapshot() for i in (0, 1)]), before
        )
        self.assertTrue(all(self.server.economy.is_reserved(key) for key in self.keys))
        self.closed = {2, 3}
        await self.clients[2].send(b"PART " + self.tables[1].channel)
        for client in self.clients[2:]:
            for _ in range(100):
                if not await client.read():
                    break
            else:
                self.fail("current event table PART did not close affected participants")
        await self.drain()
        self.assertEqual(self.round(0).engine.snapshot(), before[1][0])
        self.assertIsNone(
            self.server.event_matches.coordinator.snapshot(
                "festival", table_id=self.tables[1].table_id
            )
        )
        self.assertEqual(self.server.snapshot(), before[0])
        self.assertTrue(all(self.server.economy.is_reserved(key) for key in self.keys[:2]))
        self.assertFalse(any(self.server.economy.is_reserved(key) for key in self.keys[2:]))


if __name__ == "__main__":
    unittest.main()
