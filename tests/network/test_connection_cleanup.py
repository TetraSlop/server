"""Handler tasks retire even when transport cleanup is interrupted."""

import asyncio
import unittest

from tests.network.test_session_recovery import Writer
from tmserver.network.server import Server


class ClosingWriter(Writer):
    """Pause real handler cleanup at the transport's close acknowledgement."""

    def __init__(self, error=None):
        super().__init__()
        self.error = error
        self.closing = asyncio.Event()
        self.release = asyncio.Event()

    async def wait_closed(self):
        self.closing.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error


class FailedCloseWriter(Writer):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def close(self):
        super().close()
        raise self.error


class ConnectionCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server()
        self.running = []

    async def asyncTearDown(self):
        for task in self.running:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.running, return_exceptions=True)
        await self.server.stop()

    def accept(self, writer):
        reader = asyncio.StreamReader()
        reader.feed_eof()
        task = asyncio.create_task(self.server.accept(reader, writer))
        self.running.append(task)
        return task

    async def test_cancellation_during_transport_close_retires_accept_task(self):
        writer = ClosingWriter()
        task = self.accept(writer)
        await asyncio.wait_for(writer.closing.wait(), 1)
        self.assertIn(task, self.server.tasks)
        self.assertFalse(self.server.sessions)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(writer.closed)
        self.assertFalse(self.server.tasks)

    async def test_close_failure_propagates_without_retaining_accept_task(self):
        error = OSError("injected transport close failure")
        writer = FailedCloseWriter(error)
        task = self.accept(writer)
        with self.assertRaises(OSError) as caught:
            await task
        self.assertIs(caught.exception, error)
        self.assertTrue(writer.closed)
        self.assertFalse(self.server.sessions)
        self.assertFalse(self.server.tasks)

    async def test_successful_close_and_handled_transport_errors_still_retire(self):
        for error in (None, ConnectionResetError("reset"), OSError("transport lost")):
            with self.subTest(error=error):
                writer = ClosingWriter(error)
                task = self.accept(writer)
                await asyncio.wait_for(writer.closing.wait(), 1)
                writer.release.set()
                await task
                self.assertTrue(writer.closed)
                self.assertFalse(self.server.sessions)
                self.assertFalse(self.server.tasks)


if __name__ == "__main__":
    unittest.main()
