"""Profile shutdown and close failures must release connection admission slots."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from tests.network.test_connection_cleanup import ClosingWriter, FailedCloseWriter
from tmserver.network.server import Server
from tmserver.profiles.service import ProfileServer


class ProfileCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = ProfileServer(Path(self.temp.name) / "profile.jsonl", Server())
        self.running = []

    async def asyncTearDown(self):
        for task in self.running:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.running, return_exceptions=True)
        self.temp.cleanup()

    def handle(self, writer):
        reader = asyncio.StreamReader()
        reader.feed_eof()
        task = asyncio.create_task(self.server.handle(reader, writer))
        self.running.append(task)
        return task

    async def test_stop_during_transport_close_releases_admission_slots(self):
        writer = ClosingWriter()
        task = self.handle(writer)
        await asyncio.wait_for(writer.closing.wait(), 1)
        self.assertEqual(self.server.tasks, {task})
        self.assertEqual(self.server.writers, {writer})
        await asyncio.wait_for(self.server.stop(), 1)
        self.assertTrue(task.cancelled())
        self.assertTrue(writer.closed)
        self.assertFalse(self.server.tasks)
        self.assertFalse(self.server.writers)

    async def test_wait_closed_failure_propagates_without_retaining_registration(self):
        error = OSError("injected close acknowledgement failure")
        writer = ClosingWriter(error)
        task = self.handle(writer)
        await asyncio.wait_for(writer.closing.wait(), 1)
        writer.release.set()
        with self.assertRaises(OSError) as caught:
            await task
        self.assertIs(caught.exception, error)
        self.assertTrue(writer.closed)
        self.assertFalse(self.server.tasks)
        self.assertFalse(self.server.writers)

    async def test_close_failure_propagates_without_retaining_registration(self):
        error = OSError("injected transport close failure")
        writer = FailedCloseWriter(error)
        task = self.handle(writer)
        with self.assertRaises(OSError) as caught:
            await task
        self.assertIs(caught.exception, error)
        self.assertTrue(writer.closed)
        self.assertFalse(self.server.tasks)
        self.assertFalse(self.server.writers)

    async def test_successful_close_and_handled_reset_still_retire(self):
        for error in (None, ConnectionResetError("reset")):
            with self.subTest(error=error):
                writer = ClosingWriter(error)
                task = self.handle(writer)
                await asyncio.wait_for(writer.closing.wait(), 1)
                writer.release.set()
                await task
                self.assertTrue(writer.closed)
                self.assertFalse(self.server.tasks)
                self.assertFalse(self.server.writers)


if __name__ == "__main__":
    unittest.main()
