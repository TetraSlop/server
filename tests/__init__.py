"""Shared locations for portable test fixtures and disposable state, and the asyncio test loop setting."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def _setup_runner_without_debug(case, setup=unittest.IsolatedAsyncioTestCase._setupAsyncioRunner):
    setup(case)
    case._asyncioRunner.get_loop().set_debug(False)


# IsolatedAsyncioTestCase forces asyncio debug mode, whose per-callback source
# tracebacks took about half of the portable lane. No test relies on it.
unittest.IsolatedAsyncioTestCase._setupAsyncioRunner = _setup_runner_without_debug
