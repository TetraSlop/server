"""Install the wheel in a clean environment and smoke its entry points and extras.

verify_wheel proves the installed files match the source tree byte for byte, so
behaviour is covered by the source test lanes. This checks what only an install
can show: dependency metadata, entry points and packaged templates.
"""

import argparse
import json
import os
import subprocess
import tempfile
import venv
from pathlib import Path

from tools.package import ROOT, verify_wheel


# The smokes check packaging, not PostgreSQL: the source lanes cover the store.
STORE = """
from copy import deepcopy
from unittest.mock import patch


class WheelStore:
    def __init__(self):
        self.snapshot = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def load(self):
        return deepcopy(self.snapshot)

    def save(self, snapshot):
        self.snapshot = deepcopy(snapshot)

    def save_changes(self, accounts, services):
        snapshot = self.snapshot or {"version": 1, "accounts": {}}
        snapshot["accounts"].update(deepcopy(accounts))
        snapshot.update(deepcopy(services))
        self.snapshot = snapshot


def stored():
    store = WheelStore()
    return store, patch("tmserver.cli.serve.open_store", return_value=store)
"""


SERVICES_SMOKE = STORE + """
import sys

from tmserver.cli import serve

sys.argv = ["tmserver", "--database-url", "postgresql+psycopg://wheel", "--game-port", "0",
            "--profile-port", "0", "--seconds", "0.1"]
store, opened = stored()
with opened:
    serve.main()
"""


ADMIN_SMOKE = """
import asyncio
from http.cookiejar import CookieJar
import json
import socket
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

import uvicorn

from tmserver.admin.web import create_app
from tmserver.network.server import Server


def check_http(origin, account):
    cookies = CookieJar()
    client = build_opener(HTTPCookieProcessor(cookies))

    def request(path, data=None):
        headers = {"Origin": origin} if data is not None else {}
        body = urlencode(data).encode() if data is not None else None
        with client.open(Request(origin + path, data=body, headers=headers), timeout=5) as response:
            return response.status, urlsplit(response.url).path, response.read().decode()

    status, path, page = request("/admin/accounts/list")
    assert status == 200 and path == "/admin/login", (status, path)
    csrf = next(cookie.value for cookie in cookies if cookie.name == "starlette_admin_csrftoken")
    status, path, page = request("/admin/login", {
        "username": "wheel-operator", "password": "disposable-wheel-credential", "csrftoken": csrf,
    })
    assert status == 200 and path == "/admin/", (status, path)
    for path, expected in (
        ("/admin/", "Tetra Master"),
        ("/admin/accounts/list", "Captured at"),
        ("/admin/accounts/detail?" + urlencode({"pk": account}), "Owned cards"),
        ("/admin/claims/list", "Captured at"),
        ("/admin/receipts/list", "Receipt history"),
        ("/admin/rooms/list", "Computer available"),
        ("/admin/awards", "Queue award"),
        ("/admin/event-grants", "Card name"),
        ("/admin/event-members", "Correct event membership"),
        ("/admin/static/css/vendor/tabler.min.css", "tabler"),
    ):
        status, actual, page = request(path)
        assert status == 200 and actual == urlsplit(path).path, (status, actual)
        assert expected in page, (path, expected)


async def main():
    host = Server()
    account = host.ensure_account(next(iter(host.accounts)))
    host.persist()
    before = host.snapshot()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    app = create_app(
        host, username="wheel-operator", password="disposable-wheel-credential",
        origin=origin, started_at=time.monotonic(), actions_enabled=True,
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            if task.done():
                await task
                raise AssertionError("admin listener exited before startup")
            assert time.monotonic() < deadline, "admin listener did not start"
            await asyncio.sleep(0.01)
        await asyncio.to_thread(check_http, origin, account)
        assert host.snapshot() == before, "admin reads changed game state"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
        await host.stop()
    print(json.dumps({"admin_login": True, "admin_templates": True, "admin_read_only": True}))


asyncio.run(main())
"""


AUTH_SMOKE = STORE + """
import asyncio
import json
from pathlib import Path

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from tmserver.auth.provider import Subject
from tmserver.cli.serve import arguments, running_services

USER = "11111111-1111-4111-8111-111111111111"
Path("auth.json").write_text(json.dumps(dict(
    supabase_url="https://example.supabase.co", publishable_key="sb_publishable_test_only",
    public_origin="https://tm.example",
)))

async def verified(self, token):
    assert token == "installed-test-identity"
    return Subject(issuer=self.issuer, subject=USER, discord=True, display_name="Wheel Discord")

async def main():
    args = arguments([
        "--auth-config", "auth.json", "--auth-port", "0", "--auth-insecure-loopback",
        "--database-url", "postgresql+psycopg://wheel", "--log-dir", "auth-logs",
        "--game-port", "0", "--profile-port", "0",
    ])
    store, opened = stored()
    with opened, patch("tmserver.auth.provider.SupabaseVerifier.verify", verified):
        async with running_services(args) as services:
            origin = f"http://127.0.0.1:{services.auth.port}"
            before = deepcopy(services.game.snapshot())
            async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
                config = await client.get("/v1/auth/config")
                assert config.status_code == 200 and config.json()["protocol"] == 1
                assert config.json()["provider"] == "discord"
                assert (await client.post("/v1/play/tickets", json={"protocol": 1})).status_code == 401
                issued = await client.post("/v1/play/tickets", json={"protocol": 1},
                    headers={"Authorization": "Bearer installed-test-identity"})
                assert issued.status_code == 201
                redeemed = await client.post("/v1/play/sessions", json={"ticket": issued.json()["ticket"]})
                assert redeemed.status_code == 201
                play = redeemed.json()
                headers = {"Authorization": "Bearer " + play["session_token"]}
                options = dict(additional_headers=headers, subprotocols=["tm-stream-v1"], proxy=None)
                async with connect(play["streams"]["game"], **options) as ws:
                    assert b"NOTICE AUTH" in await ws.recv()
                    assert (await client.delete("/v1/play/session", headers=headers)).status_code == 204
                    try:
                        await ws.recv()
                        raise AssertionError("revoked stream remained usable")
                    except ConnectionClosed as closed:
                        assert closed.rcvd.code == 4401
                try:
                    async with connect(play["streams"]["profile"], **options):
                        raise AssertionError("revoked profile capability accepted")
                except InvalidStatus as denied:
                    assert denied.response.status_code == 403
            saved = store.load()
            key = play["account_key"]
            assert services.game.identities.account("https://example.supabase.co/auth/v1", USER) == key
            assert saved["accounts"][key]["name_hex"] == b"Wheel Discord".hex()
            assert len(saved["accounts"][key]["cards"]) == 10
            assert all(services.game.snapshot()[name] == value for name, value in before.items()
                       if name not in {"accounts", "identities"})
    print(json.dumps(dict(auth_gateway=True, auth_websocket=True, auth_revocation=True,
                          auth_provider_mock=True, auth_auto_registration=True)))

asyncio.run(main())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--admin", action="store_true", help="also install and smoke the admin extra")
    parser.add_argument("--auth", action="store_true", help="also install and smoke the auth extra")
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    manifest = verify_wheel(wheel)
    with tempfile.TemporaryDirectory(prefix="tm-wheel-") as folder:
        root = Path(folder)
        environment = root / "environment"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "TM_DATABASE_URL", "TM_TEST_DATABASE_URL"}
        }

        def run(*command):
            result = subprocess.run(
                command, cwd=root, env=env, capture_output=True, text=True, timeout=90
            )
            if result.returncode:
                raise RuntimeError(
                    f"Installed command failed ({Path(command[0]).name}):\n"
                    f"{result.stdout}{result.stderr}"
                )
            return result.stdout

        def script(name):
            return str(scripts / (name + (".exe" if os.name == "nt" else "")))

        run(str(python), "-m", "pip", "install", str(wheel))
        run(str(python), "-m", "pip", "check")
        dependencies = json.loads(run(
            str(python), "-I", "-c",
            "import importlib.metadata as metadata, importlib.util, json\n"
            "assert all(importlib.util.find_spec(name) is None "
            "for name in ('sqlalchemy', 'alembic', 'psycopg', "
            "'starlette_admin', 'starlette', 'uvicorn', 'httpx', 'websockets'))\n"
            "print(json.dumps([requirement for requirement in metadata.requires('tmserver') "
            "if 'extra ==' not in requirement]))\n",
        ))
        run(str(python), "-I", str(ROOT / "tools" / "check_imports.py"))
        for name, command in (("tmserver", ()), ("tmserver-admin", ("accounts",))):
            run(script(name), "--help")
            refused = subprocess.run([script(name), *command], cwd=root, env=env,
                                     capture_output=True, text=True, timeout=15)
            assert refused.returncode != 0 and "TM_DATABASE_URL" in refused.stderr, refused.stderr
        accounts = run(script("tmserver"), "--list-accounts")
        assert len(accounts.strip().splitlines()) == 2, accounts
        ready = run(str(python), "-B", "-c", SERVICES_SMOKE)
        assert "Tetra Master services ready" in ready, ready
        assert (root / "var").is_dir(), "runtime output did not use the working directory"
        admin_report = {}
        if args.admin:
            run(str(python), "-m", "pip", "install", f"{wheel}[admin]")
            run(str(python), "-m", "pip", "check")
            admin_report = json.loads(run(str(python), "-I", "-c", ADMIN_SMOKE))
        auth_report = {}
        if args.auth:
            run(str(python), "-m", "pip", "install", f"{wheel}[auth]")
            run(str(python), "-m", "pip", "check")
            auth_report = json.loads(run(str(python), "-I", "-c", AUTH_SMOKE))
        print(json.dumps({
            "wheel": wheel.name,
            **manifest,
            "clean_install": True,
            "runtime_dependencies": dependencies,
            "console_entrypoints": True,
            "services_started_on_ephemeral_ports": True,
            **admin_report,
            **auth_report,
        }))


if __name__ == "__main__":
    main()
