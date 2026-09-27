"""Backend selection does not accidentally switch an existing JSON launch."""

import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from run_server import DIRECTORY, arguments
from server import Server
from state_store import JsonStateStore


class DatabaseConfigurationTests(unittest.TestCase):
    def test_default_keeps_json_without_database_dependencies(self):
        with patch.dict(os.environ, {}, clear=True):
            args = arguments([])
        self.assertEqual(args.state, DIRECTORY / "local_state.json")
        self.assertIsNone(args.database_url)

    def test_database_environment_selects_database_and_explicit_state_overrides_it(
        self,
    ):
        with patch.dict(os.environ, {"TM_DATABASE_URL": "postgresql://example"}):
            args = arguments([])
            self.assertEqual(args.database_url, "postgresql://example")
            self.assertIsNone(args.state)
            args = arguments(["--state", "explicit.json"])
            self.assertIsNone(args.database_url)
            self.assertEqual(args.state, Path("explicit.json"))

    def test_cannot_write_json_and_database_together(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            arguments(
                ["--state", "save.json", "--database-url", "postgresql://example"]
            )
        with self.assertRaises(ValueError):
            Server(state_path="save.json", store=JsonStateStore("other.json"))
