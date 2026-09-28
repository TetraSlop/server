"""Original Common344 opcode/body, not the unrelated file-list operation."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import ROOT
from tests.profiles.test_profile_accounts import delete_request, file_request
from tmserver.profiles.service import ProfileServer, checked


class ProfileDeleteTests(unittest.TestCase):

    def test_wrong_opcode_or_body_extent_does_not_delete(self):
        deleted = []
        with tempfile.TemporaryDirectory(dir=(ROOT / "tests")) as folder:
            server = ProfileServer(
                Path(folder) / "profile.jsonl",
                SimpleNamespace(delete_profile_file=lambda key, path: deleted.append(path)),
            )
            self.assertEqual(server.dispatch(3, 3, delete_request("U/g/TM0_RANKLIST")), (b"", 1))
            self.assertEqual(server.dispatch(3, 2, file_request("U/g/TM0_RANKLIST")), (b"", 1))
            self.assertEqual(deleted, [])
            self.assertEqual(server.dispatch(3, 2, delete_request("./U/g/TM0_RANKLIST")), (b"", 0))
            self.assertEqual(deleted, ["U/g/TM0_RANKLIST"])


if __name__ == "__main__":
    unittest.main()
