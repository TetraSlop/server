"""Verify the wheel and its declared runtime dependencies in a clean environment."""

import argparse
import json
import os
import subprocess
import tempfile
import venv
from pathlib import Path

from tools.package import ROOT, verify_wheel


EVENT_SETUP = """
import json
from pathlib import Path
from tmserver.economy.service import EconomyService
from tmserver.events.config import parse_event_configs
from tmserver.events.service import EventHost
from tmserver.storage.json import JsonStateStore

document = {"events": [{
    "id": "wheel-event", "listing": {
        "key_region": "Local Lobby", "key_event": "Wheel Event",
        "description": "Disposable installed-package check", "scalar": 4096,
    },
    "data": {"classes": ["First", "Next"], "steps_per_class": 4},
    "start": 100, "end": 500, "room_raw": 4096, "init_raw": 4097,
    "card_raw": 4098, "card_service_enabled": True,
}]}
Path("events.json").write_text(json.dumps(document), encoding="utf8")
store = JsonStateStore("admin.json")
snapshot = store.load()
economy = EconomyService.restore(snapshot)
events = EventHost(economy, parse_event_configs(document), clock=lambda: 200)
events.admit_room("audited", "wheel-event")
store.save(dict(snapshot, events=events.snapshot()))
"""


ADMIN_SMOKE = """
import asyncio
from copy import deepcopy
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import json
import socket
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

import uvicorn

from tmserver.admin.web import create_app
from tmserver.events.config import load_event_configs
from tmserver.network.server import Server


class Inputs(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.values = {}
        self.feed(page)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "input" and "name" in attributes:
            self.values[attributes["name"]] = attributes.get("value", "")


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
        ("/admin/static/css/vendor/tabler.min.css", "tabler"),
    ):
        status, actual, page = request(path)
        assert status == 200 and actual == urlsplit(path).path, (status, actual)
        assert expected in page, (path, expected)
    return request, csrf


def check_award(request, csrf, account):
    status, path, page = request("/admin/awards")
    assert status == 200 and "Preview award" in page
    status, path, page = request("/admin/awards/preview", {
        "csrftoken": csrf, "request_id": "wheel-award-request", "award_id": "wheel-award",
        "account_key": account, "top30": "7", "reason": "Installed wheel acceptance",
    })
    assert status == 200 and "Review before queuing" in page
    confirmed = Inputs(page).values
    assert "command" in confirmed and "digest" in confirmed
    for _ in range(2):
        status, path, page = request("/admin/awards/apply", confirmed)
        assert status == 200 and path == "/admin/awards/receipt", (status, path)
        assert "Award queued and audit receipt saved" in page
        assert "wheel-award-request" in page


def check_event_grant(request, csrf, account):
    status, path, page = request("/admin/event-grants")
    assert status == 200 and "Card name" in page and "Wheel Event" in page
    status, path, page = request("/admin/event-grants/preview", {
        "csrftoken": csrf, "request_id": "wheel-event-request", "grant_id": "wheel-grant",
        "account_key": account, "event_id": "wheel-event", "money": "250",
        "reason": "Installed web event acceptance", "intent": "preview", "row_count": "1",
        "card_0_model": "0", "card_0_attack": "50", "card_0_kind": "0",
        "card_0_physical": "40", "card_0_magical": "30", "card_0_arrows": "1",
    })
    assert status == 200 and "Review before queuing" in page
    assert "Goblin" in page and "Current event eligibility: Eligible" in page
    confirmation = Inputs(page).values
    assert "command" in confirmation and "digest" in confirmation
    for _ in range(2):
        status, path, page = request("/admin/event-grants/apply", confirmation)
        assert status == 200 and path == "/admin/event-grants/receipt", (status, path)
        assert "Event grant queued and audit receipt saved" in page
        assert "wheel-event-request" in page


def check_event_members(request, csrf, account):
    status, path, page = request("/admin/event-members")
    assert status == 200 and "Correct event membership" in page
    for action, fields in (
        ("event-progress", {"update_id": "wheel-progress", "steps_delta": "3", "completed_missions": "0"}),
        ("event-eligibility", {"value": "-1"}),
    ):
        status, path, page = request("/admin/event-members/preview", {
            "csrftoken": csrf, "action": action, "request_id": "wheel-" + action,
            "account_key": account, "event_id": "wheel-event",
            "reason": "Installed correction acceptance", **fields,
        })
        assert status == 200 and "Review before applying" in page
        confirmation = Inputs(page).values
        for _ in range(2):
            status, path, page = request("/admin/event-members/apply", confirmation)
            assert status == 200 and path == "/admin/event-members/receipt"
            assert "Correction applied and audit receipt saved" in page


def check_receipts(request):
    status, path, page = request("/admin/receipts/list")
    assert status == 200 and "wheel-award-request" in page and "wheel-event-request" in page
    for key, route in (
        ("wheel-award-request", "awards"), ("wheel-event-request", "event-grants"),
        ("wheel-event-progress", "event-members"), ("wheel-event-eligibility", "event-members"),
    ):
        status, path, page = request("/admin/receipts/list?" + urlencode({"q": key}))
        assert status == 200 and key in page
        assert f"/admin/{route}/receipt?id={key}" in page
        status, path, page = request("/admin/receipts/detail?" + urlencode({"pk": key}))
        assert status == 200 and key in page and "wheel-operator" in page
        assert "This view is read-only" in page


async def main():
    host = Server(
        state_path="admin-web.json", event_configs=load_event_configs("events.json"),
        event_clock=lambda: 200,
    )
    account = host.ensure_account(next(iter(host.accounts)))
    host.events.admit_room(account, "wheel-event")
    host.persist()
    before = deepcopy(host.snapshot())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
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
        request, csrf = await asyncio.to_thread(check_http, origin, account)
        assert host.snapshot() == before, "admin reads changed game state"
        await asyncio.to_thread(check_award, request, csrf, account)
        await asyncio.to_thread(check_event_grant, request, csrf, account)
        await asyncio.to_thread(check_event_members, request, csrf, account)
        snapshot = host.snapshot()
        await asyncio.to_thread(check_receipts, request)
        assert host.snapshot() == snapshot, "receipt inspection changed game state"
        assert snapshot["admin_audit"]["version"] == 3
        assert len(snapshot["admin_audit"]["receipts"]) == 4
        assert snapshot["prize"]["accounts"][account]["pending"]["top30"] == 7
        assert snapshot["event_cards"]["grants"][0]["money"] == 250
        assert snapshot["accounts"] == before["accounts"]
        member = host.events.member(account, "wheel-event")
        assert (member.steps, member.eligibility) == (3, -1)
        assert host.store.load()["admin_audit"] == snapshot["admin_audit"]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
        await host.stop()
    print(json.dumps({"admin_login": True, "admin_templates": True, "admin_read_only": True,
                      "admin_audited_award": True, "admin_audited_event_grant": True,
                      "admin_audited_event_members": True,
                      "admin_receipt_history": True}))


asyncio.run(main())
"""


AUTH_SMOKE = """
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

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
        "--state", "auth-state.json", "--log-dir", "auth-logs", "--game-port", "0", "--profile-port", "0",
    ])
    with patch("tmserver.auth.provider.SupabaseVerifier.verify", verified):
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
            saved = json.loads(Path("auth-state.json").read_text())
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
    parser.add_argument("--native", action="store_true",
                        help="also launch services; requires Windows x86 and native image/bridge env")
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
        cli = scripts / ("tmserver.exe" if os.name == "nt" else "tmserver")
        run(str(cli), "--help")
        play_cli = scripts / ("tmserver-play.exe" if os.name == "nt" else "tmserver-play")
        run(str(play_cli), "--help")
        admin_cli = scripts / ("tmserver-admin.exe" if os.name == "nt" else "tmserver-admin")
        run(str(admin_cli), "--help")
        run(
            str(python), "-c",
            "from tmserver.economy.service import EconomyService\n"
            "from tmserver.storage.json import JsonStateStore\n"
            "economy = EconomyService(seed=1)\n"
            "economy.provision('wheel', nn=1, player_id=1, name=b'Wheel')\n"
            "economy.provision('audited', nn=2, player_id=2, name=b'Audited')\n"
            "JsonStateStore('admin.json').save(economy.snapshot())\n",
        )
        admin_state = root / "admin.json"
        original_accounts = json.loads(admin_state.read_text())["accounts"]
        assert "wheel" in run(str(admin_cli), "--state", str(admin_state), "accounts")
        run(
            str(admin_cli), "--state", str(admin_state), "prize-award",
            "--account-key", "wheel", "--id", "wheel-check", "--top30", "3",
        )
        awarded = json.loads(admin_state.read_text())
        assert awarded["accounts"] == original_accounts
        assert awarded["prize"]["accounts"]["wheel"]["pending"]["top30"] == 3
        before_summary = admin_state.read_bytes()
        summary = json.loads(run(
            str(admin_cli), "--state", str(admin_state), "account", "--account-key", "wheel"
        ))
        assert summary["pending_prize"]["id"] == "wheel-check"
        assert summary["prize_points"] == 0
        assert summary["card_count"] == 10
        assert admin_state.read_bytes() == before_summary
        award_args = (
            "--account-key", "audited", "--id", "installed-award", "--top30", "11",
            "--request-id", "installed-request", "--operator", "wheel-check",
            "--reason", "Installed command acceptance",
        )
        preview = json.loads(run(
            str(admin_cli), "--state", str(admin_state), "prize-preview", *award_args,
        ))
        assert admin_state.read_bytes() == before_summary
        receipt = json.loads(run(
            str(admin_cli), "--state", str(admin_state), "prize-apply", *award_args,
            "--digest", preview["digest"],
        ))
        committed = admin_state.read_bytes()
        retry = json.loads(run(
            str(admin_cli), "--state", str(admin_state), "prize-apply", *award_args,
            "--digest", preview["digest"],
        ))
        assert retry == receipt and admin_state.read_bytes() == committed
        assert json.loads(run(
            str(admin_cli), "--state", str(admin_state), "operator-receipt",
            "--request-id", "installed-request",
        )) == receipt
        recovery = json.loads(run(str(admin_cli), "--state", str(admin_state), "recovery"))
        assert recovery == dict(interrupted_rounds=0, unaccepted_results=0, accounts=[])
        assert admin_state.read_bytes() == committed
        run(str(python), "-I", "-c", EVENT_SETUP)
        event_cli = (
            str(admin_cli), "--state", str(admin_state), "--events", str(root / "events.json"),
        )
        event_args = (
            "--account-key", "audited", "--event", "wheel-event", "--id", "installed-grant",
            "--request-id", "installed-event-request", "--operator", "wheel-check",
            "--reason", "Installed event grant acceptance", "--money", "250",
            "--card", "0,50,0,40,30,1",
        )
        before_event = admin_state.read_bytes()
        event_preview = json.loads(run(*event_cli, "event-grant-preview", *event_args))
        assert admin_state.read_bytes() == before_event
        event_receipt = json.loads(run(
            *event_cli, "event-grant-apply", *event_args, "--digest", event_preview["digest"],
        ))
        granted = json.loads(admin_state.read_text())
        event_committed = admin_state.read_bytes()
        assert granted["accounts"] == awarded["accounts"]
        assert granted["admin_audit"] == dict(version=2, receipts=[receipt, event_receipt])
        assert event_receipt["cards"][0]["deck"] == 255
        assert granted["event_cards"]["grants"][0]["money"] == 250
        assert json.loads(run(
            *event_cli, "event-grant-apply", *event_args, "--digest", "0" * 64,
        )) == event_receipt
        assert json.loads(run(
            *event_cli, "operator-receipt", "--request-id", "installed-request",
        )) == receipt
        assert json.loads(run(
            *event_cli, "operator-receipt", "--request-id", "installed-event-request",
        )) == event_receipt
        assert admin_state.read_bytes() == event_committed
        for kind, values in (
            ("progress", ("--id", "installed-progress", "--steps", "3")),
            ("eligibility", ("--value", "-1")),
        ):
            member_args = (
                "--account-key", "audited", "--event", "wheel-event",
                "--request-id", "installed-" + kind, "--operator", "wheel-check",
                "--reason", "Installed membership acceptance", *values,
            )
            member_before = admin_state.read_bytes()
            member_preview = json.loads(run(*event_cli, f"event-{kind}-preview", *member_args))
            assert admin_state.read_bytes() == member_before
            member_receipt = json.loads(run(
                *event_cli, f"event-{kind}-apply", *member_args, "--digest", member_preview["digest"],
            ))
            member_committed = admin_state.read_bytes()
            assert member_receipt["status"] == "applied"
            assert json.loads(run(
                *event_cli, f"event-{kind}-apply", *member_args, "--digest", "old",
            )) == member_receipt
            assert admin_state.read_bytes() == member_committed
        assert json.loads(admin_state.read_text())["accounts"] == granted["accounts"]
        accounts = run(str(cli), "--list-accounts")
        assert len(accounts.strip().splitlines()) == 2, accounts
        if args.native:
            ready = run(str(python), "-B", "-m", "tmserver", "--game-port", "0",
                        "--profile-port", "0", "--seconds", "0.1")
            assert "rules: native x86" in ready, ready
            assert (root / "var").is_dir(), "runtime output did not use the working directory"
        else:
            no_native = {key: value for key, value in env.items()
                         if key not in {"TM_ORIGINAL_IMAGE", "TM_NATIVE_BRIDGE"}}
            refused = subprocess.run([str(python), "-m", "tmserver"], cwd=root,
                                     env=no_native, capture_output=True, text=True, timeout=15)
            assert refused.returncode != 0 and "Native rules require" in refused.stderr
            assert not (root / "var").exists(), "missing native configuration created state"
        admin_report = {}
        if args.admin:
            run(str(python), "-m", "pip", "install", f"{wheel}[admin]")
            run(str(python), "-m", "pip", "check")
            admin_report = json.loads(run(str(python), "-I", "-c", ADMIN_SMOKE))
        auth_report = {}
        if args.auth:
            run(str(python), "-m", "pip", "install", f"{wheel}[auth]")
            run(str(python), "-m", "pip", "check")
            if args.native:
                auth_report = json.loads(run(str(python), "-I", "-c", AUTH_SMOKE))
            else:
                run(str(python), "-I", "-c", "import tmserver.auth.server")
                auth_report = {"auth_imports": True, "auth_native_service_smoke": False}
        print(
            json.dumps(
                {
                    "wheel": wheel.name,
                    **manifest,
                    "clean_install": True,
                    "runtime_dependencies": dependencies,
                    "console_entrypoint": True,
                    "module_entrypoint": True,
                    "json_admin_award": True,
                    "json_audited_award": True,
                    "json_audited_event_grant": True,
                    "json_audited_event_members": True,
                    "json_admin_account": True,
                    "json_recovery_preview": True,
                    "services_started_on_ephemeral_ports": args.native,
                    **admin_report,
                    **auth_report,
                }
            )
        )


if __name__ == "__main__":
    main()
