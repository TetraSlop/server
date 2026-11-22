"""Hosted registration policy and optional import of existing account bindings."""

from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .provider import https_origin, validate_public_key


class AuthConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)
    supabase_url: str
    publishable_key: str
    public_origin: str
    auto_register: bool = True
    bindings: dict[str, str] = Field(default_factory=dict)
    # Pages allowed to call the gateway from a browser; empty disables browser clients.
    browser_origins: list[str] = Field(default_factory=list)

    @field_validator("supabase_url")
    @classmethod
    def project(cls, value):
        return https_origin(value, "Supabase requires a trusted HTTPS project origin")

    @field_validator("publishable_key")
    @classmethod
    def public_key(cls, value):
        return validate_public_key(value)

    @field_validator("public_origin")
    @classmethod
    def origin(cls, value):
        return https_origin(value, "public_origin must be an HTTPS origin without a path")

    @field_validator("browser_origins")
    @classmethod
    def browser_pages(cls, values):
        if len(values) > 16:
            raise ValueError("allow at most 16 browser origins")
        return list(dict.fromkeys(browser_origin(value) for value in values))

    @field_validator("bindings")
    @classmethod
    def enrolled_accounts(cls, values):
        if len(values) > 128 or len(set(values.values())) != len(values):
            raise ValueError("bind at most 128 distinct subjects to distinct legacy accounts")
        if any(str(UUID(subject)) != subject for subject in values):
            raise ValueError("subject keys must be canonical Supabase user UUIDs")
        return values

    def account_bindings(self, account_specs):
        by_login = {row[0].decode("ascii"): key for key, row in account_specs.items()}
        if any(login not in by_login for login in self.bindings.values()):
            raise ValueError("auth bindings refer to a legacy account not enabled on this host")
        return {subject: by_login[login] for subject, login in self.bindings.items()}


def browser_origin(value):
    """HTTPS page origins, plus plain HTTP on 127.0.0.1/localhost for development."""
    error = "browser_origins must be HTTPS origins, or http://127.0.0.1 or localhost"
    if not value.startswith("http://"):
        return https_origin(value, error)
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError(error) from None
    if url.hostname not in ("127.0.0.1", "localhost") or value != (
        f"http://{url.hostname}" + (f":{port}" if port is not None else "")
    ):
        raise ValueError(error)
    return value


def load_auth_config(path):
    return AuthConfig.model_validate_json(Path(path).read_bytes())
