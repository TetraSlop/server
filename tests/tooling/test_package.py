"""Packaging checks reject stale build output before it can be installed."""

from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile

from tools import package


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "checkout"
        self.files = {
            "tmserver/__init__.py": b"VERSION = 1\n",
            "tmserver/data/cards.json": b'{"cards": []}\n',
            "tmserver/storage/migrations/versions/first.py": b"revision = 'first'\n",
        }
        for name, data in self.files.items():
            target = self.source / "src" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (self.source / "pyproject.toml").write_text(
            '[tool.setuptools.package-data]\ntmserver = ["data/*.json"]\n', encoding="utf-8"
        )
        (self.source / "README.md").write_text("Package fixture\n", encoding="utf-8")
        (self.source / "LICENSE").write_text("Licence fixture\n", encoding="utf-8")

    def wheel(self, files=None, path=None):
        path = path or self.root / "fixture.whl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in (self.files if files is None else files).items():
                archive.writestr(name, data)
            archive.writestr("tmserver-0.1.0.dist-info/METADATA", "Name: tmserver\n")
        return path

    def test_exact_manifest_includes_data_and_migrations(self):
        self.assertEqual(package.runtime_manifest(self.source), self.files)
        self.assertEqual(package.verify_wheel(self.wheel(), self.source), {
            "files": 4, "runtime_files": 3, "exact_source": True,
        })

    def test_stale_missing_changed_or_external_files_fail(self):
        variants = (
            ({**self.files, "tmserver/old_module.py": b"stale"}, "extra="),
            ({key: value for key, value in self.files.items() if not key.endswith(".json")},
             "missing="),
            ({**self.files, "tmserver/__init__.py": b"VERSION = 0\n"}, "bytes differ"),
            ({**self.files, "tmserver/data/cards.json": b'{"cards": [1]}'}, "bytes differ"),
            ({**self.files, "tests/test_dev.py": b"unwanted"}, "outside the package"),
        )
        for files, message in variants:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                package.verify_wheel(self.wheel(files), self.source)

    def test_duplicate_archive_entry_fails_even_when_bytes_match(self):
        wheel = self.wheel()
        with warnings.catch_warnings(), zipfile.ZipFile(wheel, "a") as archive:
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("tmserver/__init__.py", self.files["tmserver/__init__.py"])
        with self.assertRaisesRegex(ValueError, "duplicate wheel entries"):
            package.verify_wheel(wheel, self.source)
