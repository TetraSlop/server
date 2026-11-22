"""Configured shop offers, encoded with the client's Init catalogue flags."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShopOffers:
    special_flag: int = 0
    special_name: bytes = b""
    highlighted: tuple = ()
    highlighted_available: bool = False

    def __post_init__(self):
        if type(self.special_flag) is not int or self.special_flag not in (0, 1, 2, 3):
            raise ValueError("special pack flag must use original hidden/visible categories0..3")
        # Original SN truncates unusually for lengths above7; keep exact names.
        if (
            not isinstance(self.special_name, bytes)
            or len(self.special_name) > 7
            or any(x < 32 or x > 126 for x in self.special_name)
        ):
            raise ValueError("special pack name must contain at most 7 printable bytes")
        if (
            type(self.highlighted) is not tuple
            or len(self.highlighted) > 78
            or any(type(x) is not int or not 0 <= x < 78 for x in self.highlighted)
        ):
            raise ValueError("highlighted pack IDs must be an immutable original catalogue list")
        if len(set(self.highlighted)) != len(self.highlighted):
            raise ValueError("duplicate highlighted pack ID")
        if type(self.highlighted_available) is not bool:
            raise ValueError("highlight availability must be boolean")
        if (
            self.special_flag in (1, 2, 3) or self.highlighted_available and 5 in self.highlighted
        ) and not self.special_name:
            raise ValueError("a visible personalized pack requires a nonempty name")

    def fields(self):
        hd = "|".join(map(str, (len(self.highlighted),) + self.highlighted))
        return f"/SP={self.special_flag}/SN={self.special_name.hex().upper()}/CMP={int(self.highlighted_available)}/HD={hd}"

    def flags(self, catalog, guild):
        if type(guild) is not int or guild not in range(4):
            raise ValueError("invalid original guild")
        flags = [p["category"] for p in catalog.packs]
        flags[5] = self.special_flag
        for i in self.highlighted:
            flags[i] = 1 if self.highlighted_available else 4
        return tuple(int(guild == 0 or x == guild + 6) if 7 <= x <= 9 else x for x in flags)

    def visible(self, catalog, guild, balance, card_count):
        # 101190B0: pauper requires both fewer than 5 cards and less than the
        # native Beginner's Pack price, even if another pack is marked category2.
        pauper = balance < catalog.packs[1]["price"] and card_count < 5
        return tuple(
            i
            for i, flag in enumerate(self.flags(catalog, guild))
            if flag in (1, 3) or flag == 2 and pauper
        )


def load_shop_offers(path):
    data = json.loads(Path(path).read_text(encoding="utf8"))
    if not isinstance(data, dict) or set(data) - {
        "special_flag",
        "special_name",
        "highlighted",
        "highlighted_available",
    }:
        raise ValueError("unknown shop offer configuration fields")
    data = dict(data)
    if "special_name" in data:
        if not isinstance(data["special_name"], str):
            raise ValueError("special pack name must be text")
        data["special_name"] = data["special_name"].encode("ascii")
    if "highlighted" in data:
        if not isinstance(data["highlighted"], list):
            raise ValueError("highlighted must be an array")
        data["highlighted"] = tuple(data["highlighted"])
    return ShopOffers(**data)
