"""Diagnostic disk errors cannot interrupt protocol responses or cleanup."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.rooms.test_ordinary_rooms import EXTRA
from tests.profiles.test_profile_accounts import KEYS, file_request, transact
from tests.network.test_session_recovery import Writer
from tmserver.game.packets import packet
from tmserver.profiles.service import ProfileServer
from tmserver.profiles.packets import checked
from tmserver.network.server import Server
from tmserver.network.session import Session


LOGGER = "tmserver.network.diagnostics"


class BrokenLog:
    def __init__(self, *failures):
        self.failures = failures
        self.calls = {"write": 0, "flush": 0, "close": 0}

    def operation(self, name):
        self.calls[name] += 1
        if name in self.failures:
            raise OSError("injected log " + name + " failure")

    def write(self, value):
        self.operation("write")
        return len(value)

    def flush(self):
        self.operation("flush")

    def close(self):
        self.operation("close")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class LoggingFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.servers = []

    async def asyncTearDown(self):
        for server in reversed(self.servers):
            await server.stop()
        self.temp.cleanup()

    async def occupied_game(self):
        server = Server(ordinary_rooms=(EXTRA,))
        self.servers.append(server)
        peers = []
        for index, nick in enumerate(server.accounts):
            peer = Session(server, None, Writer(), index + 1)
            peer.nick = nick
            server.ensure_account(nick)
            server.trade.bind(peer, nick.decode("ascii"))
            server.nicks[nick] = peer
            server.sessions.add(peer)
            peers.append(peer)
            await server.ordinary_tables[index].dispatch_table(peer, packet(65, 1, body="@GameEN="))
        return server, peers

    async def test_shutdown_closes_every_healthy_survivor_despite_write_flush_or_close_error(self):
        for failure in ("write", "flush", "close"):
            with self.subTest(failure=failure):
                server, peers = await self.occupied_game()
                before = server.economy.snapshot()
                broken = BrokenLog(failure)
                server.log_file = broken
                with self.assertLogs(LOGGER, level="ERROR") as logged:
                    await server.stop()
                    await server.stop()
                self.assertEqual(len(logged.records), 1)
                self.assertIn(failure, str(server.log_error))
                self.assertEqual(broken.calls["close"], 1)
                self.assertIsNone(server.log_file)
                self.assertFalse(server.sessions)
                self.assertTrue(all(peer.closed and peer.writer.closed for peer in peers))
                self.assertFalse(any(table.match_reservations for table in server.ordinary_tables))
                self.assertEqual(server.economy.snapshot(), before)
                self.assertTrue(all(not server.economy.is_busy(p.nick.decode()) for p in peers))

    async def test_cleanup_error_log_cannot_strand_an_already_closed_peers_reservation(self):
        server, peers = await self.occupied_game()
        broken = BrokenLog("flush", "close")
        server.log_file = broken
        with self.assertLogs(LOGGER, level="ERROR") as logged:
            with patch.object(server.trade, "disconnect", side_effect=ValueError("cleanup fault")):
                await peers[0].close()
            server.log("later_packet")
            await server.stop()
        self.assertEqual(len(logged.records), 1)
        self.assertEqual(broken.calls, {"write": 1, "flush": 1, "close": 1})
        self.assertIn("flush", str(server.log_error))
        self.assertFalse(server.default_table.match_reservations)
        self.assertFalse(server.economy.is_reserved(peers[0].nick.decode()))
        self.assertIsNone(server.storage_failed)

    async def test_connection_error_still_retires_accept_task(self):
        server = Server()
        self.servers.append(server)
        server.log_file = BrokenLog("write")
        writer = Writer()
        with self.assertLogs(LOGGER, level="ERROR") as logged:
            with patch.object(Session, "run", AsyncMock(side_effect=ValueError("bad protocol"))):
                task = asyncio.create_task(server.accept(None, writer))
                await task
        self.assertEqual(len(logged.records), 1)
        self.assertTrue(writer.closed)
        self.assertNotIn(task, server.tasks)
        self.assertFalse(server.sessions)

    async def test_failed_delivery_still_closes_its_recipient(self):
        server, peers = await self.occupied_game()
        server.log_file = BrokenLog("flush")
        peers[0].writer.fail = True
        with self.assertLogs(LOGGER, level="ERROR") as logged:
            self.assertFalse(await server.deliver(peers[0], b"PING :failure"))
            self.assertTrue(await server.deliver(peers[1], b"PING :healthy"))
        self.assertEqual(len(logged.records), 1)
        self.assertTrue(peers[0].closed)
        self.assertFalse(peers[1].closed)
        self.assertFalse(server.economy.is_reserved(peers[0].nick.decode()))
        self.assertTrue(server.economy.is_reserved(peers[1].nick.decode()))

    async def test_game_listener_can_start_and_stop_after_first_log_flush_fails(self):
        server = Server()
        self.servers.append(server)
        server.log_file = BrokenLog("flush")
        with self.assertLogs(LOGGER, level="ERROR") as logged:
            port = await server.start(0)
            self.assertGreater(port, 0)
            self.assertTrue(server.listener.is_serving())
            await server.stop()
        self.assertEqual(len(logged.records), 1)
        self.assertFalse(server.listener.is_serving())
        self.assertTrue(all(t.watchdog_task is None for t in server.ordinary_tables))

    async def test_profile_requests_continue_without_retrying_failed_log_io(self):
        for failure in ("open", "write", "flush", "close"):
            with self.subTest(failure=failure):
                profile = ProfileServer(self.root / (failure + ".jsonl"))
                self.servers.append(profile)
                port = await profile.start(0)
                broken = BrokenLog(failure)
                options = (
                    {"side_effect": OSError("injected log open failure")}
                    if failure == "open"
                    else {"return_value": broken}
                )
                with self.assertLogs(LOGGER, level="ERROR") as logged:
                    with patch.object(Path, "open", **options) as opened:
                        for _ in range(2):
                            error, data = await transact(port, KEYS[0], 3, 0, file_request())
                            self.assertEqual(error, 0)
                            self.assertEqual(
                                data, checked(profile.account_files[KEYS[0]]["U/g/TM0DataFile"])
                            )
                        self.assertEqual(opened.call_count, 1)
                self.assertEqual(len(logged.records), 1)
                self.assertIn(failure, str(profile.log_error))
                await profile.stop()
                self.assertFalse(profile.tasks)
                self.assertFalse(profile.writers)
                self.assertFalse(profile.listener.is_serving())

    async def test_successful_jsonl_records_keep_the_same_content_and_file_ownership(self):
        game_path, profile_path = self.root / "game.jsonl", self.root / "profile.jsonl"
        game = Server(log_path=game_path)
        profile = ProfileServer(profile_path)
        self.servers.extend((game, profile))
        stream = game.log_file
        fields = {"name": "Ren\u00e9e", "wire": "00FF", "items": [1, 2]}
        with patch("time.time", return_value=12.5):
            game.log("example", **fields)
            profile.log("example", **fields)
        expected = json.dumps({"time": 12.5, "event": "example", **fields}) + "\n"
        self.assertEqual(game_path.read_text(encoding="utf8"), expected)
        self.assertEqual(profile_path.read_text(encoding="utf8"), expected)
        self.assertFalse(stream.closed)
        await game.stop()
        self.assertTrue(stream.closed)
        self.assertIsNone(game.log_error)
        self.assertIsNone(profile.log_error)

    async def test_broken_fallback_logger_cannot_interrupt_cleanup_or_state_failure(self):
        server, peers = await self.occupied_game()
        server.log_file = BrokenLog("flush")
        with patch("logging.Logger.error", side_effect=OSError("stderr unavailable")) as report:
            server.log("first")
            server.log("second")
            state_error = OSError("state write failed")
            with self.assertRaises(OSError) as caught:
                with server.storage_write():
                    raise state_error
            self.assertIs(caught.exception, state_error)
            self.assertIs(server.storage_failed, state_error)
            await server.stop()
        self.assertEqual(report.call_count, 1)
        self.assertTrue(all(peer.closed for peer in peers))
        self.assertFalse(any(table.match_reservations for table in server.ordinary_tables))


if __name__ == "__main__":
    unittest.main()
