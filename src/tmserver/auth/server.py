"""Own optional outbound identity verification and the loopback auth gateway."""

from contextlib import asynccontextmanager

import httpx

from tmserver.network.http import serving_http

from .provider import SupabaseVerifier
from .web import create_app


@asynccontextmanager
async def serving_auth(host, profile, config, *, port=8081, insecure_loopback=False):
    async with httpx.AsyncClient(trust_env=False) as client:
        verifier = SupabaseVerifier(config.supabase_url, config.publishable_key, client=client)
        application = None

        def create(bound_port):
            nonlocal application
            origin = f"http://127.0.0.1:{bound_port}" if insecure_loopback else None
            application = create_app(host, profile, config, verifier, public_origin=origin)
            return application

        try:
            async with serving_http(create, port=port, websockets=True) as listener:
                yield listener
        finally:
            if application is not None:
                application.state.play_sessions.close()
