"""Optional local admin application; receives the running game's sole state owner."""

import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Route
from starlette_admin import BaseAdmin

from .auth import OperatorAuth, SESSION_SECONDS
from .views import AccountsView, ClaimsView, ReceiptHistoryView, RoomsView, overview_view


class LocalAdminBoundary:
    """Validate the browser origin and permit only explicitly enabled forms."""

    def __init__(self, app, *, origin, actions_enabled=False):
        self.app = app
        self.origin = origin
        self.host = urlsplit(origin).netloc
        self.post_paths = {"/admin/login", "/admin/logout"}
        if actions_enabled:
            self.post_paths.update((
                "/admin/awards/apply", "/admin/event-grants/apply", "/admin/event-members/apply",
            ))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        method = scope["method"]
        origin = headers.get("origin")

        async def private_send(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["Cache-Control"] = "private, no-store"
                # A no-referrer policy turns browser form POST origins into "null".
                response_headers["Referrer-Policy"] = "same-origin"
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["X-Frame-Options"] = "DENY"
            await send(message)

        rejection = None
        if headers.getlist("host") != [self.host]:
            rejection = PlainTextResponse("This admin address is not allowed.", status_code=400)
        elif len(headers.getlist("origin")) > 1 or (origin is not None and origin != self.origin):
            rejection = PlainTextResponse("This request origin is not allowed.", status_code=403)
        elif method not in ("GET", "HEAD"):
            if method != "POST" or scope["path"] not in self.post_paths:
                rejection = PlainTextResponse("This action is not enabled in the panel.", status_code=403)
            elif origin != self.origin:
                rejection = PlainTextResponse(
                    "An operator form must come from this panel.", status_code=403
                )
        if rejection is not None:
            await rejection(scope, receive, private_send)
        else:
            await self.app(scope, receive, private_send)


def create_app(
    host, *, username, password, origin, started_at, clock=time.monotonic, actions_enabled=False,
):
    if type(actions_enabled) is not bool:
        raise ValueError("admin actions enablement must be boolean")
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise ValueError("admin origin must be http://127.0.0.1:PORT")
    if origin != f"http://127.0.0.1:{parsed.port}":
        raise ValueError("admin origin must contain only the loopback address and port")
    auth = OperatorAuth(username, password, clock=clock)
    secret = secrets.token_urlsafe(32)
    app = Starlette(
        routes=[Route("/", lambda request: RedirectResponse("/admin/", status_code=303))],
        middleware=[
            Middleware(
                SessionMiddleware,
                secret_key=secret,
                session_cookie=f"tm_operator_{parsed.port}",
                max_age=SESSION_SECONDS,
                same_site="strict",
                path="/admin",
                https_only=False,
            )
        ],
    )
    admin = BaseAdmin(
        title="Tetra Master",
        auth_provider=auth,
        secret_key=secret,
        templates_dir=str(Path(__file__).with_name("templates")),
        index_view=overview_view(host, started_at),
    )
    for view in (
        AccountsView(host), ClaimsView(host), RoomsView(host),
        ReceiptHistoryView(host, actions_enabled=actions_enabled),
    ):
        admin.add_view(view)
    if actions_enabled:
        from .awards import PrizeAwardsView
        from .event_grants import EventGrantsView
        from .event_members import EventMembersView

        admin.add_view(PrizeAwardsView(host))
        admin.add_view(EventGrantsView(host))
        admin.add_view(EventMembersView(host))
    admin.templates.env.globals["operator_actions_enabled"] = actions_enabled
    admin.mount_to(app)
    return LocalAdminBoundary(app, origin=origin, actions_enabled=actions_enabled)
