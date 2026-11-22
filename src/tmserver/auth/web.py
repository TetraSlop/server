"""Versioned launcher API: Supabase identity -> ticket -> account-bound streams."""

import asyncio
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketState

from tmserver.accounts.provisioning import AccountCapacityError, import_bindings, provision_identity

from .provider import InvalidToken, ProviderUnavailable, header_value
from .sessions import AccessError, SESSION_SECONDS, TICKET_SECONDS, Sessions
from .streams import CHUNK_BYTES, relay


BROWSER_AUTH_SECONDS = 5
MAX_BROWSER_PENDING = 32


class BrowserCors(CORSMiddleware):
    """CORS for listed page origins only; other requests pass through byte for byte."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and Headers(scope=scope).get("origin") in self.allow_origins:
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)


class TicketRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    protocol: int = Field(ge=1, le=1)


class RedeemRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    ticket: str = Field(min_length=32, max_length=128)


class RenewRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    session_token: str = Field(min_length=32, max_length=128)


def bearer(connection):
    values = connection.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise AccessError("invalid_token")
    token = values[0][7:]
    if not header_value(token):
        raise AccessError("invalid_token")
    return token


async def read_model(request, model):
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise AccessError("json_required", 415)
    body = bytearray()
    async with asyncio.timeout(5):
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise AccessError("request_too_large", 413)
    try:
        return model.model_validate_json(body)
    except ValidationError:
        raise AccessError("invalid_request", 400) from None


def response(data, status=200):
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


def create_app(host, profile, config, verifier, *, public_origin=None):
    origin = public_origin or config.public_origin
    authority = urlsplit(origin).netloc
    websocket_origin = origin.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    import_bindings(host, verifier.issuer, config.account_bindings(host.account_specs))
    sessions = Sessions(lambda subject: host.identities.account(verifier.issuer, subject))
    browsers = frozenset(config.browser_origins)
    pending = 0

    def boundary(connection):
        if connection.headers.getlist("host") != [authority] or connection.url.query:
            raise AccessError("invalid_origin", 403)
        origins = connection.headers.getlist("origin")
        if origins and (len(origins) != 1 or origins[0] != origin and origins[0] not in browsers):
            raise AccessError("invalid_origin", 403)
        if host.storage_failed is not None:
            raise AccessError("storage_unavailable", 503)

    def endpoint(callback):
        async def handle(request):
            try:
                boundary(request)
                return await callback(request)
            except AccessError as error:
                return response({"error": error.code}, error.status)
            except InvalidToken:
                return response({"error": "invalid_token"}, 401)
            except ProviderUnavailable:
                return response({"error": "auth_unavailable"}, 503)
            except TimeoutError:
                return response({"error": "request_timeout"}, 408)
        return handle

    async def verified(request):
        subject = await verifier.verify(bearer(request))
        boundary(request)
        if subject.issuer != verifier.issuer:
            raise AccessError("account_not_linked", 403)
        return subject

    async def configuration(request):
        return response(dict(protocol=1, provider="discord",
                             supabase_url=config.supabase_url,
                             publishable_key=config.publishable_key,
                             ticket_ttl_seconds=TICKET_SECONDS, session_ttl_seconds=SESSION_SECONDS,
                             stream_subprotocol="tm-stream-v1", max_chunk_bytes=CHUNK_BYTES))

    async def issue(request):
        await read_model(request, TicketRequest)
        subject = await verified(request)
        if host.identities.account(subject.issuer, subject.subject) is None:
            if not config.auto_register:
                raise AccessError("account_not_linked", 403)
            if not subject.discord:
                raise AccessError("discord_required", 403)
            try:
                provision_identity(host, subject.issuer, subject.subject, name=subject.display_name)
            except OSError:
                raise AccessError("storage_unavailable", 503) from None
            except AccountCapacityError:
                raise AccessError("capacity", 503) from None
        return response(dict(ticket=sessions.issue(subject), expires_in=TICKET_SECONDS), 201)

    async def redeem(request):
        command = await read_model(request, RedeemRequest)
        boundary(request)
        token, session = sessions.redeem(command.ticket)
        try:
            if session.subject.display_name is not None:
                host.ensure_account(session.access.account_key.encode("ascii"),
                                    name=session.subject.display_name.encode("ascii"))
        except OSError:
            sessions.revoke(token)
            raise AccessError("storage_unavailable", 503) from None
        login, password, _ = host.account_specs[session.access.account_key]
        return response(dict(
            protocol=1, session_token=token, expires_in=SESSION_SECONDS,
            account_key=session.access.account_key,
            legacy=dict(pol_id=login.decode("ascii"), password=password.decode("ascii")),
            streams={kind: f"{websocket_origin}/v1/streams/{kind}" for kind in ("game", "profile")},
        ), 201)

    async def renew(request):
        command = await read_model(request, RenewRequest)
        subject = await verified(request)
        sessions.renew(subject, command.session_token)
        return response(dict(expires_in=SESSION_SECONDS))

    async def revoke(request):
        sessions.revoke(bearer(request))
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    async def revoke_account(request):
        sessions.revoke_subject(await verified(request))
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    def route(websocket):
        kind = websocket.path_params["kind"]
        if kind not in ("game", "profile"):
            raise AccessError("invalid_channel")
        if websocket.scope.get("subprotocols") != ["tm-stream-v1"]:
            raise AccessError("invalid_protocol")
        return kind

    def admit(websocket, token):
        session = sessions.get(token)
        kind = route(websocket)
        if session.streams[kind] >= (1 if kind == "game" else 4):
            raise AccessError("channel_busy")
        return session, kind

    async def first_message(websocket):
        """Browsers cannot set Authorization, so the play token is the first text message."""
        nonlocal pending
        route(websocket)
        if pending >= MAX_BROWSER_PENDING:
            raise AccessError("capacity", 503)
        pending += 1
        try:
            await websocket.accept(subprotocol="tm-stream-v1")
            async with asyncio.timeout(BROWSER_AUTH_SECONDS):
                message = await websocket.receive()
        except TimeoutError:
            raise AccessError("auth_timeout") from None
        finally:
            pending -= 1
        return message.get("text")  # None for binary data or a disconnect.

    async def stream(websocket):
        try:
            boundary(websocket)
            browser = (not websocket.headers.getlist("authorization")
                       and websocket.headers.get("origin") in browsers)
            if browser:
                token = await first_message(websocket)
                boundary(websocket)  # Storage may have failed during the wait.
            else:
                token = bearer(websocket)
            session, kind = admit(websocket, token)
        except AccessError as error:
            # Denial before accept becomes HTTP 403; after a browser accept, a 1008 close.
            connected = websocket.application_state is WebSocketState.CONNECTED
            try:
                await websocket.close(code=1008, reason=error.code if connected else None)
            except (RuntimeError, OSError):
                pass  # The browser already went away.
            return
        session.streams[kind] += 1
        try:
            if not browser:
                await websocket.accept(subprotocol="tm-stream-v1")
            await relay(websocket, host.accept if kind == "game" else profile.handle, session.access)
        finally:
            session.streams[kind] -= 1

    app = Starlette(routes=[
        Route("/v1/auth/config", endpoint(configuration)),
        Route("/v1/play/tickets", endpoint(issue), methods=["POST"]),
        Route("/v1/play/sessions", endpoint(redeem), methods=["POST"]),
        Route("/v1/play/renew", endpoint(renew), methods=["POST"]),
        Route("/v1/play/session", endpoint(revoke), methods=["DELETE"]),
        Route("/v1/play/account-session", endpoint(revoke_account), methods=["DELETE"]),
        WebSocketRoute("/v1/streams/{kind}", stream),
    ], middleware=[Middleware(
        BrowserCors, allow_origins=browsers, allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"], max_age=600,
    )] if browsers else None)
    app.state.play_sessions = sessions
    return app
