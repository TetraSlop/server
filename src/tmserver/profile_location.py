"""Original file owner packs a raw POL identity, directory and volume."""

from dataclasses import dataclass

from tmserver.identities import game_id


@dataclass(frozen=True)
class FileOwner:
    raw_identity: int
    directory: int
    volume: int

    @property
    def game_identity(self):
        return game_id(self.raw_identity)

    @classmethod
    def decode(cls, value):
        if type(value) is not int or not 0 <= value < 1 << 64:
            raise ValueError("file owner must be an unsigned qword")
        return cls(value & 0x1FFFFFFFFFF, (value >> 41) & 127, value >> 48)

    def encode(self):
        if (
            type(self.raw_identity) is not int
            or not 0 <= self.raw_identity <= 0x1FFFFFFFFFF
            or type(self.directory) is not int
            or not 0 <= self.directory <= 127
            or type(self.volume) is not int
            or not 0 <= self.volume <= 65535
        ):
            raise ValueError("file owner fields exceed original widths")
        return self.raw_identity | (self.directory << 41) | (self.volume << 48)
