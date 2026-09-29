"""Run portable, optional admin, or disposable PostgreSQL tests without skipped coverage."""

import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import time
import unittest
from uuid import uuid4

from tools.check_collection import validate_collection
from tools.package import ROOT

PORTABLE_SKIPS = {
    (
        "tests.cli.test_admin.AdminTests.test_snapshot_validation_public_imports_remain_compatible",
        "optional database dependencies absent",
    ),
}


def test_modules(lane, root=ROOT):
    def selected_lane(path):
        if path.is_relative_to(root / "tests" / "auth"):
            return "auth"
        if path.is_relative_to(root / "tests" / "admin" / "web"):
            return "admin"
        return "postgres" if "postgres" in path.stem.split("_") else "portable"

    return sorted(
        ".".join(path.with_suffix("").relative_to(root).parts)
        for path in (root / "tests").rglob("test_*.py")
        if selected_lane(path) == lane
    )


def clean_environment():
    # Never inherit the user's game database, test database or import overrides.
    return {
        key: value for key, value in os.environ.items()
        if key not in {"TM_DATABASE_URL", "TM_TEST_DATABASE_URL", "PYTHONPATH"}
    }


@contextmanager
def postgres_container():
    name = "tmserver-test-" + uuid4().hex
    password = secrets.token_hex(16)
    environment = {**clean_environment(), "POSTGRES_PASSWORD": password}

    def docker(*args, check=True):
        return subprocess.run(
            ["docker", *args], env=environment, check=check, capture_output=True, text=True,
            timeout=120,
        )

    try:
        docker("run", "--detach", "--rm", "--name", name, "--env", "POSTGRES_PASSWORD",
               "--publish", "127.0.0.1::5432", "postgres:16")
        deadline = time.monotonic() + 60
        while docker("exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres",
                     check=False).returncode:
            if time.monotonic() >= deadline:
                raise TimeoutError("disposable PostgreSQL did not become ready")
            time.sleep(0.25)
        published = docker("port", name, "5432/tcp").stdout.strip()
        if not published.startswith("127.0.0.1:"):
            raise ValueError("test database must be published only on loopback")
        port = int(published.rsplit(":", 1)[1])
        yield f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/postgres"
    finally:
        removed = docker("rm", "--force", "--volumes", name, check=False)
        if removed.returncode and sys.exc_info()[0] is None:
            removed.check_returncode()


def run_suite(suite, *, lane, errors=(), stream=None):
    count = validate_collection(suite, errors)
    result = unittest.TextTestRunner(verbosity=2, stream=stream).run(suite)
    allowed = PORTABLE_SKIPS if lane == "portable" else set()
    unexpected = [(test.id(), reason) for test, reason in result.skipped
                  if (test.id(), reason) not in allowed]
    report = {
        "lane": lane, "collected": count, "run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped),
        "unexpected_skips": unexpected,
    }
    # The portable lane permits one documented optional-dependency import check.
    # Optional admin and database coverage must never become a successful skipped run.
    success = result.wasSuccessful() and not unexpected and result.testsRun == count
    return success, report


def run_worker(lane, url=None):
    environment = clean_environment()
    if url is not None:
        environment["TM_TEST_DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-B", "-m", "tools.check_tests", lane, "--worker"],
        cwd=ROOT, env=environment,
    ).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=("portable", "postgres", "admin", "auth"))
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.lane == "auth" and any(
        importlib.util.find_spec(name) is None
        for name in ("starlette", "uvicorn", "httpx", "websockets")
    ):
        parser.error('install ".[dev,auth]" before selecting the auth lane')
    if args.lane == "admin" and any(
        importlib.util.find_spec(name) is None
        for name in ("starlette_admin", "starlette", "uvicorn", "httpx")
    ):
        parser.error('install ".[dev,admin]" before selecting the admin lane')
    if not args.worker:
        if args.lane in ("portable", "admin", "auth"):
            return run_worker(args.lane)
        if any(importlib.util.find_spec(name) is None for name in ("sqlalchemy", "alembic", "psycopg")):
            parser.error('install ".[postgres]" before selecting the PostgreSQL lane')
        try:
            with postgres_container() as url:
                return run_worker(args.lane, url)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            detail = getattr(error, "stderr", None) or str(error)
            parser.exit(1, f"PostgreSQL lane could not complete: {detail.strip()}\n")
    if args.lane == "postgres" and not os.environ.get("TM_TEST_DATABASE_URL"):
        parser.error("PostgreSQL worker requires its disposable container")
    sys.path.insert(0, str(ROOT / "src"))
    os.chdir(ROOT)
    subprocess.run(
        [sys.executable, "-I", str(ROOT / "tools/check_imports.py"),
         "--source-root", str(ROOT / "src")], check=True,
    )
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(test_modules(args.lane))
    success, report = run_suite(suite, lane=args.lane, errors=loader.errors)
    print(json.dumps(report))
    return int(not success)


if __name__ == "__main__":
    raise SystemExit(main())
