"""Fail early on test import errors and duplicate IDs without running tests."""

from collections import Counter
import unittest


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def validate_collection(suite, errors=()):
    ids = [test.id() for test in cases(suite)]
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if errors or duplicates:
        raise ValueError(f"test collection failed: import_errors={list(errors)}, duplicate_ids={duplicates}")
    if not ids:
        raise ValueError("test collection is empty")
    return len(ids)


def main():
    loader = unittest.TestLoader()
    suite = loader.discover("tests", top_level_dir=".")
    print(f"Collected {validate_collection(suite, loader.errors)} unique tests; no import errors")


if __name__ == "__main__":
    main()
