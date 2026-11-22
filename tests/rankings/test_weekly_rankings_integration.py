"""Optional UTC-week rankings from durable results through both native transports."""

import asyncio
import json
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.game import test_computer_menu_integration as computer
from tests.economy import test_match_wagers_integration as wagers
from tests.profiles.test_profile_accounts import file_request, transact
from tests.rankings.test_ranking_service import request as rank_request
from tests.support.irc import Client
from tmserver.protocol.identities import ROOM_RAW, TABLE_RAW, player_id
from tmserver.game.packets import packet
from tmserver.profiles.service import ProfileServer
from tmserver.profiles.packets import checksum
from tmserver.rankings.packets import METADATA_PATH, RECORD_SIZE, SCORE_OFFSETS
from tmserver.rankings.stats import PracticeStats
from tmserver.network.server import Server
from tmserver.protocol.commands import parse


WEEK = 7 * 24 * 60 * 60
MONDAY = int(datetime(2026, 9, 21, tzinfo=timezone.utc).timestamp())


class WeeklyRankingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    request = computer.ComputerMenuIntegrationTests.request
    menu = computer.ComputerMenuIntegrationTests.menu
    play = computer.ComputerMenuIntegrationTests.play
    select = wagers.ComputerWagerIntegrationTests.select

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory(dir=(ROOT / "tests"))
        self.state_path = Path(self.temp.name) / "state.json"
        self.now = MONDAY + WEEK - 1  # Sunday 23:59:59 UTC, frozen during each match.
        self.server = self.client = self.profile = None

    async def asyncTearDown(self):
        await self.stop_runtime()
        self.temp.cleanup()

    async def stop_runtime(self):
        if self.client is not None:
            await self.client.close()
            self.client = None
        if self.profile is not None:
            await self.profile.stop()
            self.profile = None
        if self.server is not None:
            await self.server.stop()
            self.server = None

    async def start(self, *, weekly_rankings=True):
        self.server = Server(
            state_path=self.state_path, allow_wagers=True, rating_mode="local-history",
            weekly_rankings=weekly_rankings, ranking_clock=lambda: self.now,
        )
        self.port = await self.server.start(0)
        # Exercise explicit/read-triggered rollover, independent of timer scheduling.
        timers = [t.watchdog_task for t in self.server.ordinary_tables if t.watchdog_task]
        for task in timers:
            task.cancel()
        await asyncio.gather(*timers, return_exceptions=True)
        for table in self.server.ordinary_tables:
            table.watchdog_task = None
        self.profile = ProfileServer(Path(self.temp.name) / "profile.jsonl", self.server)
        self.profile_port = await self.profile.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b" 422 ", await self.client.read())
        self.key = self.client.nick.decode()

    async def restart(self, *, weekly_rankings=True):
        await self.stop_runtime()
        await self.start(weekly_rankings=weekly_rankings)

    def stats(self):
        return PracticeStats.from_profile(self.server.economy.profile_bytes(self.key))

    def saved_ratings(self):
        return json.loads(self.state_path.read_text())["ratings"]

    async def read_file(self, path):
        error, data = await transact(
            self.profile_port, self.key, 3, 0, file_request(path, size=0x10000),
            context=(self.client.cipher, self.client.iv),
        )
        self.assertEqual(error, 0)
        self.assertEqual(checksum(data[:-4]), int.from_bytes(data[-4:], "little"))
        return data[:-4]

    async def ranking(self, category):
        replies = await self.request(ROOM_RAW, rank_request(category, player_id(self.client.nick)))
        self.assertEqual(len(replies), 1)
        commands = parse(replies[0][5:])
        self.assertEqual([c.token for c in commands], [b"RF", b"LN"])
        path = commands[0].payload.decode()
        data = await self.read_file(path)
        self.assertEqual(len(data), int(commands[1].payload) * RECORD_SIZE)
        return path, data

    def assert_single_score(self, data, category, expected):
        self.assertEqual(len(data), RECORD_SIZE)
        self.assertEqual(struct.unpack_from("<Q", data, 0x10)[0], player_id(self.client.nick))
        self.assertEqual(struct.unpack_from("<I", data, SCORE_OFFSETS[category])[0], expected)

    async def finish_cpu(self, *, rounds=1):
        before = self.stats()
        await self.menu()
        await self.select(200)
        for index in range(rounds):
            await self.play()
            answer = 1 if index + 1 < rounds else 2
            await self.request(
                TABLE_RAW, packet(67, 17, 0, f"@Continue=/Ans={answer}/PR=0/Conf=0")
            )
        self.assertEqual(self.server.default_table.match.phase, "finished")
        return before, self.stats()

    async def test_committed_rounds_roll_on_monday_and_issued_files_stay_immutable(self):
        await self.start()
        empty_path, empty = await self.ranking(1)
        self.assertEqual(empty, b"")
        before, after = await self.finish_cpu(rounds=2)
        prize = after.reward_sum - before.reward_sum
        path, rows = await self.ranking(1)
        self.assert_single_score(rows, 1, prize)
        self.assert_single_score((await self.ranking(2))[1], 2, after.rating)
        self.assertEqual((await self.ranking(3))[1], b"")
        self.assertEqual((await self.ranking(4))[1], b"")
        self.assertEqual(await self.read_file(empty_path), b"")
        self.assertFalse(self.server.advance_rankings())
        metadata = struct.unpack("<7I", await self.read_file(METADATA_PATH))
        self.assertEqual((metadata[2], metadata[3], metadata[5], metadata[6]), (1, 0, 0, 0))

        previous = self.server.ratings.snapshot()
        original = self.server.persist_changes
        saved = []

        def save(accounts, journals):
            if "ratings" in journals and journals["ratings"] != previous:
                # A new week must not be visible before its durable commit succeeds.
                self.assertEqual(self.server.ratings.snapshot(), previous)
                self.assertEqual(self.server.ranking.records(1), rows)
                saved.append(journals["ratings"])
            return original(accounts, journals)

        self.now = MONDAY + WEEK
        with patch.object(self.server, "persist_changes", side_effect=save):
            _, last_week = await self.ranking(3)
        self.assertTrue(saved)
        self.assert_single_score(last_week, 3, after.rating)
        self.assertEqual((await self.ranking(1))[1], b"")
        self.assertEqual((await self.ranking(4))[1], b"")
        self.assertEqual(await self.read_file(path), rows)
        self.assertEqual(self.saved_ratings(), self.server.ratings.snapshot())
        metadata = struct.unpack("<7I", await self.read_file(METADATA_PATH))
        self.assertEqual((metadata[2], metadata[3], metadata[5], metadata[6]),
                         (0, 0, 1, 0))

    async def test_metadata_read_restart_and_skipped_weeks_clear_stale_previous(self):
        await self.start()
        _, after = await self.finish_cpu()
        self.now = MONDAY + WEEK
        await self.read_file(METADATA_PATH)
        rolled = self.saved_ratings()
        self.assert_single_score((await self.ranking(3))[1], 3, after.rating)
        await self.restart()
        self.assertEqual(self.server.ratings.snapshot(), rolled)
        self.assertEqual(struct.unpack_from("<I", await self.read_file(METADATA_PATH), 12)[0], 0)
        self.assert_single_score((await self.ranking(3))[1], 3, after.rating)
        self.now = MONDAY + 3 * WEEK
        self.assertTrue(self.server.advance_rankings())
        self.assertFalse(self.server.advance_rankings())
        for category in (1, 3, 4):
            self.assertEqual((await self.ranking(category))[1], b"")
        await self.restart()
        self.assertEqual((await self.ranking(3))[1], b"")
        self.assertEqual(self.saved_ratings(), self.server.ratings.snapshot())

    async def test_result_commit_itself_rolls_week_before_any_ranking_read(self):
        await self.start()
        await self.menu()
        await self.select(200)
        await self.play()
        previous = self.stats()
        await self.request(TABLE_RAW, packet(67, 17, 0, "@Continue=/Ans=1/PR=0/Conf=0"))
        self.now = MONDAY + WEEK
        await self.play()
        await self.request(TABLE_RAW, packet(67, 17, 0, "@Continue=/Ans=2/PR=0/Conf=0"))
        current = self.stats()
        stored = self.saved_ratings()
        self.assertEqual(stored["periods"]["week_start"], MONDAY + WEEK)
        self.assertEqual(self.server.ratings.snapshot(), stored)
        self.assert_single_score((await self.ranking(1))[1], 1,
                                 current.reward_sum - previous.reward_sum)
        self.assert_single_score((await self.ranking(3))[1], 3, previous.rating)
        self.assertNotEqual(current.rating, previous.rating)

    async def test_disabled_runtime_preserves_v2_history_without_projecting_it(self):
        await self.start()
        await self.finish_cpu()
        self.now = MONDAY + WEEK
        self.assertNotEqual((await self.ranking(3))[1], b"")
        saved = self.saved_ratings()
        self.assertEqual(saved["version"], 2)
        await self.restart(weekly_rankings=False)
        self.now += WEEK
        self.assertFalse(self.server.advance_rankings())
        for category in (1, 3, 4):
            self.assertEqual((await self.ranking(category))[1], b"")
        metadata = struct.unpack("<7I", await self.read_file(METADATA_PATH))
        self.assertEqual((metadata[2], metadata[3], metadata[5], metadata[6]), (0, 0, 0, 0))
        self.assertEqual(self.server.ratings.snapshot(), saved)
        self.assertEqual(self.saved_ratings(), saved)
        self.assertEqual(len((await self.ranking(2))[1]), RECORD_SIZE)
        await self.restart()
        self.assertEqual((await self.ranking(1))[1], b"")
        self.assertEqual((await self.ranking(3))[1], b"")

    async def test_enabling_weekly_rankings_counts_only_future_commits(self):
        await self.start(weekly_rankings=False)
        # The deterministic first game is a loss; the rematch earns prize money.
        _, historical = await self.finish_cpu(rounds=2)
        self.assertGreater(historical.reward_sum, 0, "fixture must have prior prize money")
        self.assertEqual(self.saved_ratings()["version"], 1)
        await self.restart()
        for category in (1, 3, 4):
            self.assertEqual((await self.ranking(category))[1], b"")
        before, after = await self.finish_cpu()
        self.assertEqual(before.reward_sum, historical.reward_sum)
        self.assert_single_score((await self.ranking(1))[1], 1,
                                 after.reward_sum - before.reward_sum)

    async def test_failed_result_save_does_not_publish_weekly_prize_or_rating(self):
        await self.start()
        await self.menu()
        await self.select(200)
        before, saved = self.stats(), self.saved_ratings()
        original = self.server.persist_changes
        failures = []

        def save(accounts, journals):
            history = journals.get("ratings", {}).get("history", {}).get("accounts", {})
            if any(account["results"] for account in history.values()):
                failures.append(True)
                with patch.object(Path, "replace", side_effect=OSError("result save failed")):
                    return original(accounts, journals)
            return original(accounts, journals)

        with patch.object(self.server, "persist_changes", side_effect=save):
            with self.assertRaises((AssertionError, ConnectionError)):
                await self.play()
        self.assertTrue(failures)
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.saved_ratings(), saved)
        self.assertEqual(self.server.ranking.records(1), b"")
        await self.restart()
        self.assertEqual(self.stats(), before)
        for category in (1, 2, 3, 4):
            self.assertEqual((await self.ranking(category))[1], b"")

    async def failed_rollover(self, *, metadata=False):
        await self.start()
        _, after = await self.finish_cpu()
        before = self.server.ratings.snapshot()
        self.now = MONDAY + WEEK
        with patch.object(Path, "replace", side_effect=OSError("rollover save failed")):
            if metadata:
                with self.assertRaises((asyncio.IncompleteReadError, ConnectionError)):
                    await self.read_file(METADATA_PATH)
            else:
                with self.assertRaises((AssertionError, ConnectionError)):
                    await self.ranking(3)
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.server.ratings.snapshot(), before)
        self.assertEqual(self.saved_ratings(), before)
        await self.restart()
        self.assertEqual((await self.ranking(1))[1], b"")
        self.assert_single_score((await self.ranking(3))[1], 3, after.rating)
        self.assertEqual(self.saved_ratings(), self.server.ratings.snapshot())

    async def test_rank_request_does_not_publish_failed_rollover(self):
        await self.failed_rollover()

    async def test_metadata_read_does_not_publish_failed_rollover(self):
        await self.failed_rollover(metadata=True)


if __name__ == "__main__":
    unittest.main()
