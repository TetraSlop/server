"""Configured fixed room rules and their seven-byte RL encoding.

Server choice: enforcing these rules. The client only displays them.
"""

from dataclasses import asdict
from typing import Annotated

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from tmserver.rooms.records import alphahex

Flag = Annotated[int, Field(ge=0, le=1)]


def pack_game_rules(bm, du, st, cb, ca, tl, gs):
    """Four wager nibbles, rule flags, time limit, departure rule."""
    flags = du | st << 1 | cb << 2 | ca << 3
    return alphahex(bm, 4) + bytes((65 + flags, 65 + tl, 65 + gs))


@dataclass(frozen=True, config=ConfigDict(strict=True, extra="forbid"))
class RoomRules:
    bm: Annotated[int, Field(ge=0, le=2000)] = 0
    du: Flag = 0
    st: Flag = 0
    cb: Flag = 0
    ca: Flag = 0
    gs: Annotated[int, Field(ge=0, le=2)] = 1
    tl: Annotated[int, Field(ge=0, le=5)] = 2

    def as_dict(self):
        return asdict(self)

    def packed(self):
        """RL+49."""
        return pack_game_rules(**self.as_dict())
