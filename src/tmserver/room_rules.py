"""Configured fixed room rules and their original seven-byte RL encoding.

Enforcement is replacement-server policy. The native A-room projection displays
these fields; that alone does not establish historical server enforcement.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoomRules:
    bm: int = 0
    du: int = 0
    st: int = 0
    cb: int = 0
    ca: int = 0
    gs: int = 1
    tl: int = 2

    def __post_init__(self):
        for key, maximum in (
            ("bm", 2000),
            ("du", 1),
            ("st", 1),
            ("cb", 1),
            ("ca", 1),
            ("gs", 2),
            ("tl", 5),
        ):
            value = getattr(self, key)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"room rule {key} must be an integer in0..{maximum}")

    def as_dict(self):
        return {key: getattr(self, key) for key in ("bm", "du", "st", "cb", "ca", "gs", "tl")}

    def packed(self):
        """RL+49: four wager nibbles, rule flags, time limit, departure rule."""
        wager = bytes(65 + int(nibble, 16) for nibble in f"{self.bm:04X}")
        flags = self.du | self.st << 1 | self.cb << 2 | self.ca << 3
        return wager + bytes((65 + flags, 65 + self.tl, 65 + self.gs))
