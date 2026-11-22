"""Self-registered accounts: usernames, scrypt password hashes and login limits.

Saved under the ``identities`` journal as version 2, keyed by account. Version 1
held Supabase identity links; those restore as no credentials and are replaced
on the next write. Their accounts stay saved but cannot sign in.
"""

from collections import deque
import hashlib
import hmac
import json
import os
import re
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tmserver.protocol.identities import player_id

# About 16 MiB and tens of milliseconds per check; callers run it off the event loop.
SCRYPT = dict(n=2**14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
MAX_PASSWORD = 15  # polcore's Game IRC open copies at most 15 bytes plus NUL.

Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]+$")]
# Also the default display name, so it stays within the client's 15-byte field.
USERNAME = r"[A-Za-z0-9_.-]{1,15}"


def check_native_identity(economy, key):
    """A saved account's native player identity must still derive from its key."""
    if economy.account_view(key).nn != player_id(key.encode("ascii")):
        raise ValueError("credential account has a different native player identity")


class Credential(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    username: Annotated[str, StringConstraints(pattern="^" + USERNAME + "$")]
    salt: Annotated[Hex, Field(min_length=32, max_length=32)]
    hash: Annotated[Hex, Field(min_length=64, max_length=64)]
    disabled: bool = False


class _Journal(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: Literal[2]
    credentials: dict[str, Credential]


def valid_username(value):
    return re.fullmatch(USERNAME.encode("ascii"), value) is not None


def valid_password(value):
    return 1 <= len(value) <= MAX_PASSWORD and all(0x20 <= c <= 0x7E for c in value)


def hash_password(username, password, salt=None):
    salt = os.urandom(16) if salt is None else salt
    digest = hashlib.scrypt(password, salt=salt, **SCRYPT)
    return Credential(username=username, salt=salt.hex(), hash=digest.hex())


def verify_password(credential, password):
    digest = hashlib.scrypt(password, salt=bytes.fromhex(credential.salt), **SCRYPT)
    return hmac.compare_digest(digest, bytes.fromhex(credential.hash))


class CredentialRegistry:
    def __init__(self, credentials=None):
        self._credentials = dict(credentials or {})
        # Usernames are unique ignoring ASCII case.
        self._names = {row.username.lower(): key for key, row in self._credentials.items()}
        if len(self._names) != len(self._credentials):
            raise ValueError("duplicate username")

    def get(self, key):
        return self._credentials.get(key)

    def account(self, username):
        return self._names.get(username.lower())

    def staged(self, key, credential):
        return CredentialRegistry({**self._credentials, key: credential})

    def publish_state(self, staged):
        self._credentials, self._names = staged._credentials, staged._names

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
