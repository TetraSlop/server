"""The operator panel borrows the game host and the shared ASGI listener owner."""

import time

from tmserver.network.http import serving_http

from .web import create_app


def serving_admin(host, *, port, username, password, actions_enabled=False):
    def application(bound_port):
        return create_app(
            host, username=username, password=password,
            origin=f"http://127.0.0.1:{bound_port}", started_at=time.monotonic(),
            actions_enabled=actions_enabled,
        )

    return serving_http(application, port=port)
