"""Retired event observer writes cannot reach a replacement watch admission."""

import asyncio
import unittest
from unittest.mock import patch

from tests import test_event_spectators as base
from tests.test_admission_identity import native_identity
from tmserver.match_service import decode, packet


class EventSpectatorReentryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.EventSpectatorsIntegration.asyncSetUp
    asyncTearDown = base.EventSpectatorsIntegration.asyncTearDown
    start = base.EventSpectatorsIntegration.start
    drain = base.EventSpectatorsIntegration.drain
    send = base.EventSpectatorsIntegration.send
    identity = base.EventSpectatorsIntegration.identity
    enter_selection = base.EventSpectatorsIntegration.enter_selection
    admit = base.EventSpectatorsIntegration.admit
    engine = base.EventSpectatorsIntegration.engine
    observer_send = base.EventSpectatorsIntegration.observer_send
    read_watch = base.EventSpectatorsIntegration.read_watch
    watch = base.EventSpectatorsIntegration.watch

    async def observer_barrier(self):
        await self.observer.send(b"PING :observer-reentry")
        for _ in range(20):
            line = await self.observer.read()
            self.assertTrue(line, "observer was closed during channel transition")
            if line == b"PONG observer-reentry":
                return
            self.assertNotIn(b"GTM0G43", line, "retired gameplay leaked after channel exit")
        self.fail("missing observer transition barrier")

    async def reentry(self, *, part=False, completion="cancel"):
        await self.start()
        await self.admit()
        await self.watch()
        peer = self.server.nicks[self.observer.nick]
        spectators = self.server.event_matches.spectators
        original = peer.send
        blocked, cancelled, retired = asyncio.Event(), asyncio.Event(), asyncio.Event()
        new_blocked, release_new = asyncio.Event(), asyncio.Event()

        async def controlled(message):
            if b"@PutCard=" in message and not blocked.is_set():
                blocked.set()
                try:
                    await retired.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await retired.wait()
                    if completion == "cancel":
                        raise
                    if completion == "failure":
                        raise OSError("retired writer failure")
                    # Simulate completion of an old drain after cancellation.
                    return
            if b"@WatchInfo=" in message:
                new_blocked.set()
                await release_new.wait()
            await original(message)

        with patch.object(peer, "send", side_effect=controlled):
            try:
                for side in range(2):
                    await self.send(side, self.table.table_id,
                                    packet(67, 9, 0, f"@TurnData=/Ans={side}"))
                await self.send(0, self.table.table_id,
                                packet(67, 10, 0, "@PutData=/P=0/H=0/F=0"))
                await asyncio.wait_for(blocked.wait(), 2)
                old_task = spectators.tasks[peer]
                before = self.engine.snapshot()
                if part:
                    await self.observer.send(b"PART " + self.table.channel)
                else:
                    await self.observer_send(packet(32, body="@ExitCh="))
                await self.observer_barrier()
                await asyncio.wait_for(cancelled.wait(), 2)
                self.assertNotIn(peer, spectators.admissions)
                self.assertNotIn(peer, spectators.tasks)
                self.assertIn(old_task, spectators.senders)
                if part:
                    await self.observer.join(self.table.channel)
                await self.observer_send(packet(1, body=native_identity(self.observer_view, "Req")))
                self.assertEqual(decode(await self.read_watch()).primary, 2)
                await self.observer_send(packet(67, 37, body="@Data=/Watch=0"))
                await asyncio.wait_for(new_blocked.wait(), 2)
                new_task = spectators.tasks[peer]
                self.assertIsNot(new_task, old_task)

                # Retire the old writer while its replacement still owns queued writes.
                retired.set()
                await asyncio.wait_for(asyncio.gather(old_task, return_exceptions=True), 2)
                self.assertIs(spectators.tasks.get(peer), new_task)
                self.assertNotIn(old_task, spectators.senders)
                self.assertFalse(peer.closed)
                release_new.set()
                bootstrap = [decode(await self.read_watch()) for _ in range(3)]
                self.assertEqual([msg.subtype for msg in bootstrap], [37, 38, 9])
                await asyncio.wait_for(asyncio.shield(new_task), 2)
                self.assertIn(peer, spectators.admissions)
                self.assertFalse(peer.closed)
                self.assertEqual(self.engine.snapshot(), before)
                self.assertEqual(self.server.economy.profile_bytes(self.observer_key),
                                 self.observer_before)
                self.assertTrue(all(not self.server.nicks[c.nick].closed for c in self.clients))
                await self.observer_barrier()
            finally:
                retired.set()
                release_new.set()

    async def test_exit_ch_reentry_retires_old_sender_without_losing_new_writer(self):
        await self.reentry()

    async def test_part_reentry_retires_old_sender_without_losing_new_writer(self):
        await self.reentry(part=True)

    async def test_late_send_completion_cannot_acknowledge_replacement_bootstrap(self):
        await self.reentry(completion="complete")

    async def test_late_send_failure_cannot_close_replacement_admission(self):
        await self.reentry(completion="failure")

    async def test_disconnect_tracks_retired_writer_until_shutdown_awaits_it(self):
        await self.start()
        await self.admit()
        await self.watch()
        peer = self.server.nicks[self.observer.nick]
        spectators = self.server.event_matches.spectators
        original = peer.send
        blocked, retiring, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def slow(message):
            if b"@PutCard=" in message:
                blocked.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    retiring.set()
                    await release.wait()
                    raise
            await original(message)

        with patch.object(peer, "send", side_effect=slow):
            try:
                for side in range(2):
                    await self.send(side, self.table.table_id,
                                    packet(67, 9, 0, f"@TurnData=/Ans={side}"))
                await self.send(0, self.table.table_id,
                                packet(67, 10, 0, "@PutData=/P=0/H=0/F=0"))
                await asyncio.wait_for(blocked.wait(), 2)
                old_task = spectators.tasks[peer]
                await peer.close()
                await asyncio.wait_for(retiring.wait(), 2)
                self.assertNotIn(peer, spectators.admissions)
                self.assertNotIn(peer, spectators.tasks)
                self.assertIn(old_task, spectators.senders)
                await asyncio.wait_for(spectators.stop(), 2)
                self.assertTrue(old_task.done())
                self.assertFalse(spectators.senders)
                self.assertFalse(spectators.tasks)
                self.assertTrue(all(not self.server.nicks[c.nick].closed for c in self.clients))
            finally:
                release.set()


if __name__ == "__main__":
    unittest.main()
