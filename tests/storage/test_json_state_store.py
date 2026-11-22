"""Real files and processes exercise JSON commit, conflict and failure recovery."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tmserver.storage.json import JsonStateStore


class JsonStateStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "state.json"
        self.store = JsonStateStore(self.path)
        self.snapshot = {
            "version": 1,
            "accounts": {"alice": {"balance": 100}, "bob": {"balance": 200}},
            "auction": {"pending": ["keep this claim"]},
        }

    def seed(self):
        self.store.save(self.snapshot)
        return self.path.read_bytes()

    def assert_failed(self, store=None):
        store = store or self.store
        self.assertTrue(store.failed)
        for operation in (
            store.load,
            lambda: store.save(self.snapshot),
            lambda: store.save_changes({}, {}),
        ):
            with self.assertRaisesRegex(OSError, "restart is required"):
                operation()

    def child(self, source):
        return subprocess.run(
            [sys.executable, "-B", "-c", source, str(self.path)],
            cwd=(ROOT / "tests"),
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).stdout.strip()

    def test_missing_initial_save_can_be_created_and_scoped_updates_preserve_others(
        self,
    ):
        self.assertIsNone(self.store.load())
        self.seed()
        self.store.save_changes({"alice": {"balance": 80}}, {"prize": {"awards": []}})
        expected = deepcopy(self.snapshot)
        expected["accounts"]["alice"]["balance"] = 80
        expected["prize"] = {"awards": []}
        self.assertEqual(JsonStateStore(self.path).load(), expected)

    def test_disappeared_save_is_not_recreated_with_a_partial_economy(self):
        self.seed()
        self.path.unlink()
        with self.assertRaises(OSError):
            self.store.save_changes({"alice": {"balance": 80}}, {})
        self.assertFalse(self.path.exists())
        self.assert_failed()

    def test_external_edit_is_preserved_and_even_a_noop_rejects_stale_memory(self):
        self.seed()
        updated = deepcopy(self.snapshot)
        updated["accounts"]["bob"]["balance"] = 999
        raw = json.dumps(updated).encode()
        self.path.write_bytes(raw)
        with self.assertRaises(OSError):
            self.store.save_changes({}, {})
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(JsonStateStore(self.path).load(), updated)
        self.assert_failed()

    def test_load_cannot_rebase_a_running_writer_after_external_changes(self):
        self.seed()
        JsonStateStore(self.path).save_changes({"alice": {"balance": 50}}, {})
        with self.assertRaises(OSError):
            self.store.load()
        self.assert_failed()

    def test_new_file_created_after_initial_load_is_not_overwritten(self):
        self.assertIsNone(self.store.load())
        JsonStateStore(self.path).save(self.snapshot)
        original = self.path.read_bytes()
        with self.assertRaises(OSError):
            self.store.save({"version": 1, "accounts": {}})
        self.assertEqual(self.path.read_bytes(), original)
        self.assert_failed()

    def test_external_change_between_merge_and_commit_is_rejected(self):
        self.seed()
        save = self.store.save

        def intervene(snapshot):
            JsonStateStore(self.path).save_changes({"bob": {"balance": 800}}, {})
            save(snapshot)

        with patch.object(self.store, "save", side_effect=intervene), self.assertRaises(OSError):
            self.store.save_changes({"alice": {"balance": 80}}, {})
        latest = JsonStateStore(self.path).load()
        self.assertEqual(latest["accounts"], {"alice": {"balance": 100}, "bob": {"balance": 800}})
        self.assert_failed()

    def test_noop_keeps_original_bytes_and_does_not_replace_or_fsync(self):
        original = json.dumps(self.snapshot, separators=(",", ":")).encode()
        self.path.write_bytes(original)
        self.store.load()
        with (
            patch.object(Path, "replace", side_effect=AssertionError("unexpected replace")),
            patch("tmserver.storage.json.os.fsync", side_effect=AssertionError("unexpected fsync")),
        ):
            self.store.save(self.snapshot)
            self.store.save_changes({"alice": {"balance": 100}}, {})
            self.store.save_changes({}, {"auction": self.snapshot["auction"]})
        self.assertEqual(self.path.read_bytes(), original)

    def test_serialization_failure_cannot_damage_save_or_leave_a_temporary_file(self):
        original = self.seed()
        with self.assertRaises(OSError):
            self.store.save_changes({}, {"prize": {"unsupported": object()}})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))
        self.assert_failed()

    def test_failed_flush_preserves_save_and_cleans_temporary_file(self):
        original = self.seed()
        with (
            patch("tmserver.storage.json.os.fsync", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            self.store.save_changes({"alice": {"balance": 80}}, {})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))
        self.assert_failed()

    def test_failed_replace_preserves_save_and_does_not_reuse_legacy_tmp_path(self):
        original = self.seed()
        unrelated = self.path.with_suffix(".tmp")
        unrelated.write_bytes(b"unrelated old artifact")
        with (
            patch.object(Path, "replace", side_effect=OSError("replace failed")),
            self.assertRaises(OSError),
        ):
            self.store.save_changes({"alice": {"balance": 80}}, {})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(unrelated.read_bytes(), b"unrelated old artifact")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [unrelated])
        self.assert_failed()

    def test_lost_replace_ack_requires_reload_and_preserves_committed_claim(self):
        self.seed()
        replace = Path.replace

        def lost_ack(source, target):
            replace(source, target)
            raise OSError("commit succeeded but acknowledgement was lost")

        with patch.object(Path, "replace", new=lost_ack), self.assertRaises(OSError):
            self.store.save_changes({"alice": {"balance": 150}}, {"auction": {"pending": []}})
        self.assert_failed()
        recovered = JsonStateStore(self.path).load()
        self.assertEqual(recovered["accounts"]["alice"]["balance"], 150)
        self.assertEqual(recovered["accounts"]["bob"]["balance"], 200)
        self.assertEqual(recovered["auction"], {"pending": []})
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_invalid_saved_root_or_encoding_never_becomes_a_new_economy(self):
        for raw in (b"null", b"[]", b"{broken", b"\xff"):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                store = JsonStateStore(self.path)
                with self.assertRaises(OSError):
                    store.load()
                self.assertEqual(self.path.read_bytes(), raw)
                self.assert_failed(store)

    def test_invalid_scoped_shape_fails_closed_without_changing_disk(self):
        for snapshot in (
            {"version": 1},
            {"version": 1, "accounts": []},
            {"version": 2, "accounts": {}},
        ):
            with self.subTest(snapshot=snapshot):
                raw = json.dumps(snapshot).encode()
                self.path.write_bytes(raw)
                store = JsonStateStore(self.path)
                with self.assertRaises(OSError):
                    store.save_changes({"alice": {"balance": 80}}, {})
                self.assertEqual(self.path.read_bytes(), raw)
                self.assert_failed(store)

    def test_real_process_write_invalidates_an_older_writer(self):
        self.seed()
        self.child("""
import sys
from tmserver.storage.json import JsonStateStore
JsonStateStore(sys.argv[1]).save_changes({'bob': {'balance': 800}}, {})
""")
        with self.assertRaises(OSError):
            self.store.save_changes({"alice": {"balance": 80}}, {})
        self.assertEqual(JsonStateStore(self.path).load()["accounts"]["bob"]["balance"], 800)
        self.assert_failed()

    def test_real_process_cannot_enter_compare_replace_section_concurrently(self):
        self.seed()
        replace = Path.replace

        def concurrent(source, target):
            outcome = self.child("""
import sys
from tmserver.storage.json import JsonStateStore
store = JsonStateStore(sys.argv[1])
try:
    store.save_changes({'bob': {'balance': 800}}, {})
except OSError:
    assert store.failed
    print('locked')
else:
    raise AssertionError('overlapping writer entered commit')
""")
            self.assertEqual(outcome, "locked")
            return replace(source, target)

        with patch.object(Path, "replace", new=concurrent):
            self.store.save_changes({"alice": {"balance": 80}}, {})
        self.assertEqual(
            self.store.load()["accounts"],
            {"alice": {"balance": 80}, "bob": {"balance": 200}},
        )

    def test_process_death_releases_lock_without_deleting_sidecar(self):
        self.seed()
        self.child("""
import os
from pathlib import Path
import sys
from tmserver.storage.json import _writer_lock
with _writer_lock(Path(sys.argv[1])):
    os._exit(0)
""")
        self.store.save_changes({"alice": {"balance": 80}}, {})
        self.assertTrue(self.path.with_name("state.json.lock").exists())
        self.assertEqual(self.store.load()["accounts"]["alice"]["balance"], 80)


class JsonClaimRecoveryTests(unittest.TestCase):
    def test_lost_auction_payment_ack_reloads_paid_account_without_repeating_credit(
        self,
    ):
        from tests.economy.test_claim_transactions import ClaimFixture
        from tmserver.network.server import Server

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "claims.json"
            fixture = ClaimFixture()
            fixture.prepare(JsonStateStore(path))
            fixture.sell()
            before = fixture.server.snapshot()
            replace = Path.replace

            def lost_ack(source, target):
                replace(source, target)
                raise OSError("lost replace acknowledgement")

            with patch.object(Path, "replace", new=lost_ack), self.assertRaises(OSError):
                fixture.collect(fixture.key)
            self.assertEqual(fixture.server.snapshot(), before)
            self.assertIsNotNone(fixture.server.storage_failed)
            for _ in range(2):
                fixture.server = Server(
                    store=JsonStateStore(path),
                    account_count=3,
                    event_configs=(fixture.config,),
                    event_clock=lambda: fixture.now,
                    auction_clock=lambda: fixture.now,
                )
                fixture.collect(fixture.key)
                self.assertEqual(fixture.server.economy.balance(fixture.key), 10100)
                self.assertFalse(fixture.server.auction_ledger.pending(fixture.key))


class JsonProtocolFailureTests(unittest.IsolatedAsyncioTestCase):
    async def check_failure(self, action):
        from tests.profiles.test_options_service import request
        from tests.network.test_server import Client
        from tmserver.protocol.identities import SHOP_RAW, player_id, service_nick
        from tmserver.network.server import Server

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            server = Server(state_path=path)
            client = Client()
            try:
                await client.open(await server.start(0))
                await client.read()
                before = server.snapshot()
                action(path)
                raw = path.read_bytes() if path.exists() else None
                await client.send(
                    b"NOTICE " + service_nick(SHOP_RAW) + b" :" + request(nn=player_id(client.nick))
                )
                self.assertEqual(await client.read(), b"")
                self.assertIsNotNone(server.storage_failed)
                self.assertEqual(server.snapshot(), before)
                self.assertEqual(path.read_bytes() if path.exists() else None, raw)
            finally:
                await client.close()
                await server.stop()
            self.assertFalse(server.sessions or server.nicks)

    async def test_missing_save_closes_client_without_recreating_or_acknowledging(self):
        await self.check_failure(lambda path: path.unlink())

    async def test_external_save_closes_client_and_preserves_other_writers_commit(self):
        def another_writer(path):
            store = JsonStateStore(path)
            account = next(iter(store.load()["accounts"].items()))
            store.save_changes({account[0]: {**account[1], "balance": 12345}}, {})

        await self.check_failure(another_writer)


if __name__ == "__main__":
    unittest.main()
