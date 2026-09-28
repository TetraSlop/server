"""Handle metadata cannot change the authenticated character or inventory."""

import sys
import unittest
from pathlib import Path

from tmserver.protocol.admission import validate_admission
from tmserver.game.match import MatchService, packet


def native_identity(view, section, handle=0):
    body = f"@{section}=/NN={view.nn:016X}/HID={handle}"
    if section == "Req":
        body += "/Tm0=0/Tm1=0"
    body += f"/Dm=0/Vol=0/CN={view.name.hex()}/HN={view.name.hex()}"
    if section != "Req":
        body += "/L=1"
    return body


class AdmissionIdentityTests(unittest.TestCase):

    def test_full_native_metadata_accepts_independent_handle_but_not_forged_account(self):
        fields = dict(
            NN="0000000000000001",
            HID="0",
            Dm="0",
            Vol="0",
            CN="416c696365",
            HN="48616e646c65",
            Tm0="0",
            Tm1="0",
        )
        for handle in (0, 7, 0x7FFFFFFF):
            validate_admission(
                dict(fields, HID=str(handle)), "Req", nn=1, name=b"Alice", require_nn=True
            )
        for change in (
            {"NN": "0000000000000002"},
            {"NN": "1"},
            {"CN": "426f62"},
            {"HID": "-1"},
            {"HID": "2147483648"},
            {"HID": "1|2"},
            {"HID": ""},
            {"Dm": "256"},
            {"Vol": "65536"},
            {"Tm0": "1"},
            {"Tm1": "1"},
            {"HN": "00"},
            {"HN": "41" * 32},
            {"CN": "0"},
            {"GID": "2"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_admission(
                    dict(fields, **change), "Req", nn=1, name=b"Alice", require_nn=True
                )
        with self.assertRaises(ValueError):
            validate_admission({}, "Req", nn=1, require_nn=True)

    def test_two_members_can_share_handle_zero_without_changing_character_binding(self):
        service = MatchService(mode="versus")
        for peer, char, nn in (("alice", 17, 0x111), ("bob", 18, 0x222)):
            service.register(peer, character_id=char, nickname_id=nn)
            service.handle(peer, packet(65, 1, body=f"@GameEN=/NN={nn:016X}/HID=0"))
            service.handle(peer, packet(1, body=f"@Req=/NN={nn:016X}/HID=0/Tm0=0/Tm1=0"))
        self.assertEqual(
            [(p.peer, p.character_id, p.nickname_id) for p in service.players],
            [("alice", 17, 0x111), ("bob", 18, 0x222)],
        )
        self.assertEqual(service.owner_peer, "alice")
        with self.assertRaises(ValueError):
            service.handle("bob", packet(1, body="@Req=/NN=0000000000000111/HID=17"))


if __name__ == "__main__":
    unittest.main()
