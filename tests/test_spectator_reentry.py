"""Ordinary observer re-entry cannot reuse completions from a retired sender."""

import asyncio
import unittest
from unittest.mock import patch

from tests import test_spectator_integration as base
from tests.test_admission_identity import native_identity
from tmserver.match_service import decode, packet
from tmserver.room_service import TABLE_CHANNEL


class SpectatorReentryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.SpectatorIntegrationTests.asyncSetUp
    asyncTearDown = base.SpectatorIntegrationTests.asyncTearDown
    player = base.SpectatorIntegrationTests.player
    read_watch = base.SpectatorIntegrationTests.read_watch
    admit_watch = base.SpectatorIntegrationTests.admit_watch
    request_watch = base.SpectatorIntegrationTests.request_watch

    async def observer_barrier(self):
        await self.clients[1].send(b"PING :ordinary-reentry")
        for _ in range(20):
            line = await self.clients[1].read()
            self.assertTrue(line, "observer closed during channel transition")
            if line == b"PONG ordinary-reentry":
                return
            self.assertNotIn(b"GTM0G43", line, "old gameplay escaped the retired sender")
        self.fail("missing observer transition barrier")

    async def reentry(self, *, part=False, completion="cancel"):
        await self.admit_watch()
        table = self.server.default_table
        peer = self.server.nicks[self.clients[1].nick]
        original = peer.send
        blocked, cancelled, retire = asyncio.Event(), asyncio.Event(), asyncio.Event()
        new_blocked, release_new = asyncio.Event(), asyncio.Event()
        old_completed = asyncio.Event()
        before = (
            table.match.snapshot(),
            dict(table.match_reservations),
            self.server.economy.snapshot(),
        )

        async def controlled(message):
            if b"@WatchInfo=" in message:
                if not blocked.is_set():
                    blocked.set()
                    try:
                        await retire.wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        await retire.wait()
                        if completion == "cancel":
                            raise
                        if completion == "failure":
                            raise OSError("retired observer write failed")
                        # Model an already-written packet whose drain completes late.
                        old_completed.set()
                        return
                new_blocked.set()
                await release_new.wait()
            await original(message)

        with patch.object(peer, "send", side_effect=controlled):
            try:
                await self.request_watch()
                await asyncio.wait_for(blocked.wait(), 2)
                old_task = table.spectator_senders[peer][1]
                if part:
                    await self.clients[1].send(b"PART " + TABLE_CHANNEL)
                else:
                    await self.clients[1].send(
                        b"NOTICE " + self.target + b" :" + packet(32, body="@ExitCh=")
                    )
                await self.observer_barrier()
                await asyncio.wait_for(cancelled.wait(), 2)
                self.assertNotIn(peer, table.spectator_admissions)
                self.assertNotIn(peer, table.spectator_senders)
                self.assertIn(old_task, table.spectator_tasks)
                if part:
                    await self.clients[1].join(TABLE_CHANNEL)
                view = self.server.economy.account_view(peer.nick.decode())
                await self.clients[1].send(
                    b"NOTICE " + self.target + b" :" + packet(1, body=native_identity(view, "Req"))
                )
                self.assertEqual(decode(await self.read_watch()).primary, 2)
                await self.request_watch()
                await asyncio.wait_for(new_blocked.wait(), 2)
                new_task = table.spectator_senders[peer][1]
                self.assertIsNot(old_task, new_task)
                retire.set()
                if completion == "complete":
                    await asyncio.wait_for(old_completed.wait(), 2)
                    async with table.match_lock:
                        pending = table.spectators._observers[peer].outstanding
                        self.assertEqual([decode(p).subtype for p in pending], [37, 38, 9])
                await asyncio.wait_for(asyncio.gather(old_task, return_exceptions=True), 2)
                await asyncio.sleep(0)  # Let the task tracking callback retire a completed writer.
                self.assertFalse(peer.closed)
                self.assertIs(table.spectator_senders[peer][1], new_task)
                self.assertNotIn(old_task, table.spectator_tasks)
                release_new.set()
                self.assertEqual(
                    [decode(await self.read_watch()).subtype for _ in range(3)], [37, 38, 9]
                )
                await self.observer_barrier()
                self.assertIn(peer, table.spectator_admissions)
                self.assertFalse(peer.closed)
                self.assertFalse(self.server.nicks[self.clients[0].nick].closed)
                self.assertEqual(
                    (
                        table.match.snapshot(),
                        table.match_reservations,
                        self.server.economy.snapshot(),
                    ),
                    before,
                )
            finally:
                retire.set()
                release_new.set()

    async def test_exit_ch_reentry_keeps_new_sender(self):
        await self.reentry()

    async def test_part_reentry_keeps_new_sender(self):
        await self.reentry(part=True)

    async def test_old_bootstrap_completion_cannot_acknowledge_new_bootstrap(self):
        await self.reentry(completion="complete")

    async def test_old_write_failure_after_part_cannot_close_new_admission(self):
        await self.reentry(part=True, completion="failure")

    async def test_current_writer_failure_closes_only_observer(self):
        await self.admit_watch()
        table = self.server.default_table
        peer = self.server.nicks[self.clients[1].nick]
        before = (
            table.match.snapshot(),
            dict(table.match_reservations),
            self.server.economy.snapshot(),
        )
        with patch.object(peer, "send", side_effect=OSError("current observer writer failed")):
            await self.request_watch()
            self.assertEqual(await self.clients[1].read(), b"")
        self.assertTrue(peer.closed)
        self.assertNotIn(peer, table.spectator_admissions)
        self.assertNotIn(peer, table.spectator_senders)
        self.assertFalse(self.server.nicks[self.clients[0].nick].closed)
        self.assertEqual(
            (table.match.snapshot(), table.match_reservations, self.server.economy.snapshot()),
            before,
        )


if __name__ == "__main__":
    unittest.main()
