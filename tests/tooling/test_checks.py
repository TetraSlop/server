"""Validation failures must remain failures, including skips and stale imports."""

import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr

from tools import check_collection, check_imports, check_tests


def passing():
    pass


def skipped():
    raise unittest.SkipTest("missing dependency")


class CheckTests(unittest.TestCase):
    def test_collection_rejects_duplicate_ids_import_errors_and_empty_suite(self):
        case = unittest.FunctionTestCase(passing)
        for suite, errors, message in (
            (unittest.TestSuite([case, unittest.TestSuite([case])]), (), "duplicate_ids"),
            (unittest.TestSuite([case]), ["failed import"], "import_errors"),
            (unittest.TestSuite(), (), "empty"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                check_collection.validate_collection(suite, errors)
        self.assertEqual(check_collection.validate_collection(unittest.TestSuite([case])), 1)

    def test_import_boundary_rejects_service_dependencies_and_wrong_package(self):
        root = Path(__file__).resolve().parent
        good = SimpleNamespace(__file__=str(root / "models.py"))
        check_imports.check_loaded_modules(root, {"tmserver.game.models": good})
        for forbidden in ("sqlalchemy", "tmserver.network.server", "tmserver.rooms.service"):
            with self.subTest(module=forbidden), self.assertRaisesRegex(ValueError, "dependencies"):
                check_imports.check_loaded_modules(root, {forbidden: ModuleType(forbidden)})
        outside = SimpleNamespace(__file__=str(root.parent / "models.py"))
        with self.assertRaisesRegex(ValueError, "another package"):
            check_imports.check_loaded_modules(root, {"tmserver.game.models": outside})

    def test_lanes_select_disjoint_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("tests/storage/test_store.py", "tests/storage/test_postgres_store.py",
                         "tests/cli/test_admin_postgres.py"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual(check_tests.test_modules("portable", root), ["tests.storage.test_store"])
            self.assertEqual(check_tests.test_modules("postgres", root), [
                "tests.cli.test_admin_postgres", "tests.storage.test_postgres_store",
            ])

    def test_test_workers_do_not_inherit_database_urls_or_import_overrides(self):
        with patch.dict(os.environ, {"TM_DATABASE_URL": "live", "TM_TEST_DATABASE_URL": "live",
                                     "PYTHONPATH": "elsewhere", "KEEP": "yes"}, clear=True):
            self.assertEqual(check_tests.clean_environment(), {"KEEP": "yes"})
            with patch.object(check_tests.subprocess, "run") as run:
                check_tests.run_worker("postgres", "owned-container")
        self.assertEqual(run.call_args.kwargs["env"], {
            "KEEP": "yes", "TM_TEST_DATABASE_URL": "owned-container",
        })

    def test_unexpected_skips_fail_both_lanes(self):
        for lane in ("portable", "postgres"):
            with self.subTest(lane=lane):
                success, report = check_tests.run_suite(
                    unittest.TestSuite([unittest.FunctionTestCase(skipped)]),
                    lane=lane, stream=io.StringIO(),
                )
                self.assertFalse(success)
                self.assertEqual(report["unexpected_skips"], [("skipped", "missing dependency")])
        success, report = check_tests.run_suite(
            unittest.TestSuite([unittest.FunctionTestCase(passing)]), lane="portable",
            stream=io.StringIO(),
        )
        self.assertTrue(success)
        self.assertEqual(report["collected"], report["run"])

    def test_optional_import_skip_requires_exact_identity_reason_and_portable_lane(self):
        allowed_id, allowed_reason = next(iter(check_tests.PORTABLE_SKIPS))

        def optional():
            raise unittest.SkipTest(allowed_reason)

        for lane, expected in (("portable", True), ("postgres", False)):
            case = unittest.FunctionTestCase(optional)
            case.id = lambda: allowed_id
            success, report = check_tests.run_suite(
                unittest.TestSuite([case]), lane=lane, stream=io.StringIO(),
            )
            self.assertEqual(success, expected)

        case = unittest.FunctionTestCase(skipped)
        case.id = lambda: allowed_id
        success, report = check_tests.run_suite(
            unittest.TestSuite([case]), lane="portable", stream=io.StringIO(),
        )
        self.assertFalse(success)

    def test_owned_container_uses_loopback_and_is_removed_after_test_error(self):
        calls = []

        def docker(command, **kwargs):
            calls.append(command)
            output = "127.0.0.1:43210\n" if command[1] == "port" else ""
            return subprocess.CompletedProcess(command, 0, output, "")

        with patch.object(check_tests.subprocess, "run", side_effect=docker):
            with self.assertRaisesRegex(RuntimeError, "test failed"):
                with check_tests.postgres_container() as url:
                    self.assertIn("@127.0.0.1:43210/postgres", url)
                    raise RuntimeError("test failed")
        launch, ready, port, remove = calls
        self.assertIn("127.0.0.1::5432", launch)
        self.assertIn("127.0.0.1", ready)
        name = launch[launch.index("--name") + 1]
        self.assertTrue(name.startswith("tmserver-test-"))
        self.assertEqual(remove, ["docker", "rm", "--force", "--volumes", name])

    def test_container_cleanup_on_launch_failure_and_bad_port(self):
        for failure in ("launch", "port"):
            calls = []

            def docker(command, **kwargs):
                calls.append(command)
                if command[1] == "run" and failure == "launch":
                    raise subprocess.TimeoutExpired(command, 120)
                return subprocess.CompletedProcess(command, 0, "0.0.0.0:43210\n", "")

            expected = subprocess.TimeoutExpired if failure == "launch" else ValueError
            with patch.object(check_tests.subprocess, "run", side_effect=docker):
                with self.assertRaises(expected), check_tests.postgres_container():
                    self.fail("unsafe container became a test target")
            self.assertEqual(calls[-1][1:4], ["rm", "--force", "--volumes"])

    def test_unavailable_database_lane_fails_and_displays_docker_error(self):
        error = subprocess.CalledProcessError(1, ["docker"], stderr="daemon unavailable")
        output = io.StringIO()
        with (
            patch("sys.argv", ["check_tests", "postgres"]),
            patch.object(check_tests.importlib.util, "find_spec", return_value=object()),
            patch.object(check_tests, "postgres_container", side_effect=error),
            redirect_stderr(output),
            self.assertRaises(SystemExit) as raised,
        ):
            check_tests.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("daemon unavailable", output.getvalue())
