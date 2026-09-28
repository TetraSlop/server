import unittest

from tmserver.economy.service import EconomyError, EconomyService, named
from tmserver.profiles.options import save_options

VALUES = {
    "Ar": 1,
    "Cu": 17,
    "Vi": 200,
    "Se": 3,
    "Bgm": 2,
    "Per": 1,
    "Ran": 1,
    "CMD": 1,
    "CAD": 1,
    "CL": 20,
    "CT": 37,
    "HNSS": 1,
}
OFFSETS = (0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0x100, 0x101, 0x102, 0x103, 0x126)


def request(nn=1, values=None, name=b"LocalPlayer"):
    body = f"@Opt=/NN={nn:016X}/HID=0/Dm=0/Vol=0/CN={name.hex()}"
    body += "".join(
        f"/{key}={value}" for key, value in (VALUES if values is None else values).items()
    )
    return named(0x84, 0, 0, body)


class OptionsTests(unittest.TestCase):
    def setUp(self):
        self.economy = EconomyService(seed=1)
        self.economy.provision("one")

    def test_exact_profile_offsets_idempotency_and_restore(self):
        before = self.economy.profile_bytes("one")
        self.assertEqual(
            save_options(self.economy, "one", request()), named(0x85, 0, 0, "@OptAns=/Ans=1")
        )
        after = self.economy.profile_bytes("one")
        self.assertEqual([after[i] for i in OFFSETS], list(VALUES.values()))
        self.assertTrue(all(before[i] == after[i] for i in range(len(before)) if i not in OFFSETS))
        snapshot = self.economy.snapshot()
        save_options(self.economy, "one", request())
        self.assertEqual(self.economy.snapshot(), snapshot)
        restored = EconomyService.restore(snapshot)
        self.assertEqual(restored.profile_bytes("one"), after)

    def test_forgery_and_late_invalid_field_are_atomic(self):
        before = self.economy.snapshot()
        for payload in (
            request(nn=2),
            request(name=b"Forged"),
            request(values=dict(VALUES, HNSS=2)),
            request(values=dict(VALUES, CT=29)),
            request(values=dict(VALUES, Cu=256)),
            request(values={k: v for k, v in VALUES.items() if k != "HNSS"}),
        ):
            with self.subTest(payload=payload), self.assertRaises(EconomyError):
                save_options(self.economy, "one", payload)
            self.assertEqual(self.economy.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
