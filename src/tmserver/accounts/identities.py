"""Provider identities own accounts; display names never establish ownership."""

import json
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator

from tmserver.protocol.identities import player_id
from tmserver.protocol.wire import polid_scramble, polid_unscramble


def check_native_identity(economy, key):
    """A saved account's native player identity must still derive from its key."""
    if economy.account_view(key).nn != player_id(key.encode("ascii")):
        raise ValueError("identity account has a different native player identity")


class IdentityLink(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    issuer: Annotated[str, Field(max_length=2048)]
    subject: str
    account_key: str

    @field_validator("issuer")
    @classmethod
    def canonical_issuer(cls, value):
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.path != "/auth/v1" or parts.query or parts.fragment
                or str(TypeAdapter(HttpUrl).validate_python(value)) != value):
            raise ValueError("identity issuer must be a canonical HTTPS Auth endpoint")
        return value

    @field_validator("subject")
    @classmethod
    def canonical_subject(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("identity subject must be a canonical UUID")
        return value

    @field_validator("account_key")
    @classmethod
    def native_key(cls, value):
        try:
            nick = value.encode("ascii")
            if len(nick) != 9 or polid_scramble(polid_unscramble(nick)) != nick:
                raise ValueError("invalid native account key")
        except (UnicodeError, IndexError) as exc:
            raise ValueError("invalid native account key") from exc
        return value


class _Journal(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: Annotated[int, Field(ge=1, le=1)]
    links: tuple[IdentityLink, ...]


class IdentityRegistry:
    def __init__(self, links=()):
        self._links = {}
        self._owners = {}
        for link in links:
            identity = (link.issuer, link.subject)
            if identity in self._links or link.account_key in self._owners:
                raise ValueError("duplicate identity or account owner")
            self._links[identity] = link
            self._owners[link.account_key] = identity

    def account(self, issuer, subject):
        link = self._links.get((issuer, subject))
        return None if link is None else link.account_key

    def owns(self, key):
        return key in self._owners

    def stage_links(self, links):
        added = dict(self._links)
        for link in links:
            identity = (link.issuer, link.subject)
            old = added.get(identity)
            if old is not None and old != link:
                raise ValueError("saved identity cannot be reassigned by auth configuration")
            added[identity] = link
        return IdentityRegistry(added.values())

    def publish_state(self, staged):
        self._links, self._owners = staged._links, staged._owners

    def snapshot(self):
        return _Journal(version=1, links=tuple(self._links[key] for key in sorted(self._links))).model_dump(mode="json")

    @classmethod
    def restore(cls, economy, snapshot):
        if snapshot is None:
            return cls()
        journal = _Journal.model_validate_json(json.dumps(snapshot))
        registry = cls(journal.links)
        for link in journal.links:
            check_native_identity(economy, link.account_key)
        return registry
