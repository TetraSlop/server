"""Own optional outbound identity verification and the loopback auth gateway."""

from contextlib import asynccontextmanager

import httpx

from tmserver.network.http import serving_http

from .provider import DevVerifier, SupabaseVerifier
from .web import create_app


@asynccontextmanager
async def serving_auth(host, profile, config, *, port=8081, insecure_loopback=False,
                       dev_identity=False):
    # The listener always binds 127.0.0.1; the loopback origin also makes the Host
    # check refuse anything a reverse proxy forwards under a public name.
    if dev_identity and not insecure_loopback:
        raise ValueError("the dev identity requires the insecure loopback gateway")
    async with httpx.AsyncClient(trust_env=False) as client:
        verifier = (DevVerifier() if dev_identity else
                    SupabaseVerifier(config.supabase_url, config.publishable_key, client=client))
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
