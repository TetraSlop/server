"""One authenticated play session shared by its legacy game and profile streams."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(eq=False)
class AccountAccess:
    account_key: str
    deadline: float
    revoked: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    game: Any = field(default=None, repr=False)
    profile_ciphers: list = field(default_factory=list, repr=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def check(self):
        if self.revoked.is_set() or self.clock() >= self.deadline:
            raise ValueError("play session is no longer authorized")

    def revoke(self):
        self.revoked.set()
