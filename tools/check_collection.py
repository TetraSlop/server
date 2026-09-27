"""Fail early on test import errors without starting the full suite."""

import unittest


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover("tests", top_level_dir=".")
    for error in loader.errors:
        print(error)
    print(f"Collected {suite.countTestCases()} tests; {len(loader.errors)} import errors")
    raise SystemExit(bool(loader.errors))
