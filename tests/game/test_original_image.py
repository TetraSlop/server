"""The optional original-code backend must stay inert without its verified image."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmserver.game.original.backend import OriginalRules
from tmserver.game.original.image import IMAGE_SIZE, OriginalCodeError, read_image
from tmserver.game.original.native import NativeRules


class OriginalImageTests(unittest.TestCase):
    def test_native_bridge_rejects_unsupported_hosts_before_loading(self):
        for target, value in (("sys.platform", "linux"), ("ctypes.sizeof", 8)):
            with self.subTest(target=target):
                replacement = (
                    {"return_value": value} if target == "ctypes.sizeof" else {"new": value}
                )
                with (
                    patch(f"tmserver.game.original.native.{target}", **replacement),
                    patch("tmserver.game.original.native.ctypes.CDLL") as load,
                    self.assertRaisesRegex(OriginalCodeError, "32-bit Windows Python"),
                ):
                    NativeRules("unused-image.dll", "unused-bridge.dll")
                load.assert_not_called()

    def test_missing_image_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                read_image(Path(directory) / "missing.dll")

    def test_packed_or_corrupt_images_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.dll"
            for data, message in ((b"MZ", "unpacked"), (bytes(IMAGE_SIZE), "SHA-256")):
                path.write_bytes(data)
                with self.assertRaisesRegex(OriginalCodeError, message):
                    read_image(path)

    def test_execution_limits_cannot_disable_bounding(self):
        for value in (0, -1, True, 1.5, 2**32, 2**64):
            with self.subTest(value=value), self.assertRaises(ValueError):
                OriginalRules("unused.dll", instruction_limit=value)

    def test_optional_dependency_has_actionable_error(self):
        with patch("tmserver.game.original.backend.read_image", return_value=b""):
            # Simulate the missing dependency at the lazy import boundary.
            import builtins

            original_import = builtins.__import__

            def missing(name, *args, **kwargs):
                if name == "tmserver.game.original.machine":
                    raise ModuleNotFoundError("unicorn", name="unicorn")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=missing):
                with self.assertRaisesRegex(OriginalCodeError, r"tmserver\[original\]"):
                    OriginalRules("unused.dll")
