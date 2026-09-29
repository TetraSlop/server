"""Verify Supabase access tokens with the configured project's Auth server.

The caller owns the HTTP client. Tokens and provider response bodies are never
included in returned identities or errors; no local JWT claims grant access.
"""

import asyncio
import base64
import json
import re
import unicodedata
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError


MAX_TOKEN_LENGTH = 8192
VERIFY_TIMEOUT = 5.0


class InvalidToken(ValueError):
    """The submitted credential cannot authenticate a user."""


class ProviderUnavailable(RuntimeError):
    """Verification did not complete; this is not a rejected user credential."""


class Subject(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    issuer: str
    subject: str
    discord: bool = Field(default=False, exclude=True, repr=False)
    display_name: str | None = Field(default=None, min_length=1, max_length=15,
                                    pattern=r"^[ -~]+$", exclude=True, repr=False)


class _User(BaseModel):
    # Immutable ID owns the account; a linked Discord identity permits registration.
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    id: UUID
    identities: list[dict] = Field(default_factory=list)


def discord_name(identities):
    """Use provider identity data, never editable user_metadata or client claims.

    The current game accepts ASCII names and displays at most 15 bytes. Prefer
    Discord's global name, then its username; omit unusable names so saves keep
    their existing label. Unsupported glyphs do not grant or change ownership.
    """
    for identity in identities:
        data = identity.get("identity_data")
        if identity.get("provider") != "discord" or not isinstance(data, dict):
            continue
        for field in ("global_name", "full_name", "name"):
            value = data.get(field)
            if not isinstance(value, str):
                continue
            value = unicodedata.normalize("NFKD", value[:256]).encode("ascii", "ignore").decode()
            value = "".join(c if " " <= c <= "~" else " " for c in value)
            value = " ".join(value.split())[:15].rstrip()
            if value:
                return value
    return None


def _header_value(value, limit):
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and all(33 <= ord(character) <= 126 for character in value)
    )


def validate_public_key(value: str) -> str:
    """Classify publishable/legacy anon keys; decoded claims never authorize a user."""
    if _header_value(value, MAX_TOKEN_LENGTH):
        if value.startswith("sb_publishable_") and value != "sb_publishable_":
            return value
        if re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value):
            try:
                header, payload, signature = (
                    base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
                    for part in value.split(".")
                )
                header, payload = json.loads(header), json.loads(payload)
                if (
                    isinstance(header, dict)
                    and header.get("alg") == "HS256"
                    and isinstance(payload, dict)
                    and payload.get("role") == "anon"
                    and signature
                ):
                    return value
            except ValueError:
                pass
    raise ValueError("Supabase requires a publishable key or a legacy anon key")


def validate_project_url(value: str) -> str:
    """Return the canonical issuer for an explicitly trusted HTTPS project origin."""
    try:
        url = TypeAdapter(HttpUrl).validate_python(value, strict=True)
    except ValidationError:
        raise ValueError("Supabase requires a trusted HTTPS project origin") from None
    if (
        not isinstance(value, str)
        or "@" in value
        or url.scheme != "https"
        or url.username is not None
        or url.password is not None
        or url.query is not None
        or url.fragment is not None
        or url.path not in ("", "/")
    ):
        raise ValueError("Supabase requires a trusted HTTPS project origin")
    return str(url).rstrip("/") + "/auth/v1"


class SupabaseVerifier:
    """Borrow one async HTTP client; verify against a fixed trusted HTTPS origin."""

    def __init__(self, project_url: str, public_key: str, *, client: httpx.AsyncClient):
        self.issuer = validate_project_url(project_url)
        self._public_key = validate_public_key(public_key)
        self._client = client

    async def verify(self, access_token: str) -> Subject:
        if not _header_value(access_token, MAX_TOKEN_LENGTH):
            raise InvalidToken("An access token is required")
        try:
            async with asyncio.timeout(VERIFY_TIMEOUT):
                response = await self._client.get(
                    self.issuer + "/user",
                    headers={"apikey": self._public_key, "Authorization": "Bearer " + access_token},
                    auth=None,
                    follow_redirects=False,
                    timeout=VERIFY_TIMEOUT,
                )
        except (httpx.HTTPError, TimeoutError):
            raise ProviderUnavailable("The identity provider is unavailable; retry later") from None
        if response.status_code in (401, 403):
            raise InvalidToken("The access token is invalid or expired")
        if response.status_code != 200:
            raise ProviderUnavailable("The identity provider did not complete verification")
        try:
            user = _User.model_validate_json(response.content)
        except ValidationError:
            raise ProviderUnavailable("The identity provider returned an invalid user") from None
        return Subject(issuer=self.issuer, subject=str(user.id),
                       discord=any(identity.get("provider") == "discord" for identity in user.identities),
                       display_name=discord_name(user.identities))
