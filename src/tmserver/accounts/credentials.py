"""Self-registered POL ID accounts: scrypt password hashes and login limits.

Saved under the ``identities`` journal as version 2. Version 1 held Supabase
identity links; those restore as no credentials and are replaced on the next
write. Their accounts stay saved but cannot sign in until an operator sets a
password.
"""

from collections import deque
import hashlib
import hmac
import json
import os
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tmserver.protocol.identities import player_id
from tmserver.protocol.wire import POLID_CHARS, polid_from_string, polid_to_string

# About 16 MiB and tens of milliseconds per check; callers run it off the event loop.
SCRYPT = dict(n=2**14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
MAX_PASSWORD = 15  # polcore's Game IRC open copies at most 15 bytes plus NUL.

Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]+$")]


def check_native_identity(economy, key):
    """A saved account's native player identity must still derive from its key."""
    if economy.account_view(key).nn != player_id(key.encode("ascii")):
        raise ValueError("credential account has a different native player identity")


class Credential(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    salt: Annotated[Hex, Field(min_length=32, max_length=32)]
    hash: Annotated[Hex, Field(min_length=64, max_length=64)]
    disabled: bool = False


class _Journal(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: Literal[2]
    credentials: dict[str, Credential]


def valid_pol_id(value):
    """Eight POL ID characters whose value fits polcore's 41 bits."""
    return (len(value) == 8 and all(c in POLID_CHARS for c in value)
            and polid_from_string(value) <= 0x1FFFFFFFFFF
            and polid_to_string(polid_from_string(value)).encode("ascii") == value)


def valid_password(value):
    return 1 <= len(value) <= MAX_PASSWORD and all(0x20 <= c <= 0x7E for c in value)


def hash_password(password, salt=None):
    salt = os.urandom(16) if salt is None else salt
    digest = hashlib.scrypt(password, salt=salt, **SCRYPT)
    return Credential(salt=salt.hex(), hash=digest.hex())


def verify_password(credential, password):
    digest = hashlib.scrypt(password, salt=bytes.fromhex(credential.salt), **SCRYPT)
    return hmac.compare_digest(digest, bytes.fromhex(credential.hash))


class CredentialRegistry:
    def __init__(self, credentials=None):
        self._credentials = dict(credentials or {})

    def get(self, key):
        return self._credentials.get(key)

    def staged(self, key, credential):
        return CredentialRegistry({**self._credentials, key: credential})

    def publish_state(self, staged):
        self._credentials = staged._credentials

    def snapshot(self):
        rows = {key: self._credentials[key].model_dump() for key in sorted(self._credentials)}
        return {"version": 2, "credentials": rows}

    @classmethod
    def restore(cls, economy, snapshot):
        if snapshot is None or snapshot.get("version") == 1:
            return cls()  # No save, or the retired Supabase identity links.
        journal = _Journal.model_validate_json(json.dumps(snapshot))
        for key in journal.credentials:
            check_native_identity(economy, key)
        return cls(journal.credentials)


class Limiter:
    """Sliding-window event counts per key.

    ponytail: in memory and per process, so a restart resets it and several
    server processes would not share it; keys past max_keys evict the oldest.
    """

    def __init__(self, limit, window, *, clock=time.monotonic, max_keys=4096):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self.events = {}

    def _recent(self, key):
        events = self.events.get(key)
        if events is None:
            return ()
        while events and events[0] <= self.clock() - self.window:
            events.popleft()
        if not events:
            del self.events[key]
        return events

    def blocked(self, key):
        return len(self._recent(key)) >= self.limit

    def hit(self, key):
        self._recent(key)
        if key not in self.events and len(self.events) >= self.max_keys:
            del self.events[next(iter(self.events))]
        self.events.setdefault(key, deque()).append(self.clock())
