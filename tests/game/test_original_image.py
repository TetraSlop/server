"""Native execution requires a verified image and a compatible host."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmserver.game.original.image import IMAGE_SIZE, OriginalCodeError, read_image
from tmserver.data.resources import read_json
from tmserver.game.original.native import NativeRules, _card_record


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

    def test_card_records_match_the_retail_catalogue_bytes(self):
        records = b"".join(map(_card_record, read_json("economy_catalog.json")["cards"]))
        self.assertEqual(
            hashlib.sha256(records).hexdigest(),
            "6ed8f9f731206935186bd3281ee690d5662324d59e463eedf1417602b523d18b",
        )
