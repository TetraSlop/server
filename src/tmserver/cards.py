"""Shared card values; catalogue and native-rule validation stay at their boundaries."""

import struct
from dataclasses import dataclass


def card_values(card):
    """Eight stored fields, regardless of a service's tuple/string wire format."""
    return tuple(getattr(card, name) for name in Card.__dataclass_fields__)


@dataclass(frozen=True)
class Card:
    model: int
    attack: int
    kind: int
    physical: int
    magical: int
    points: int
    arrows: int
    deck: int = 255

    @classmethod
    def from_card(cls, card):
        """Copy card-like values through the receiving class's constructor."""
        return cls(*card_values(card))

    def full(self):
        return card_values(self)

    def reduced(self):
        return (self.model, self.attack, self.kind, self.physical, self.magical, self.arrows)

    def compact(self):
        return struct.pack("<H7B3x", *card_values(self))
