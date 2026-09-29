"""The optional HTTP listener shares and drains the live protocol host."""

import asyncio
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tmserver.cli.serve import arguments, running_services
from tmserver.network.server import Server
from tmserver.profiles.service import ProfileServer
from tmserver.storage.json import writer_lease


class AdminListenerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from tmserver.network import http as server

        self.httpx, self.admin_server = httpx, server
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state.json"
        self.enterContext(patch.dict(os.environ, {"TM_ADMIN_PASSWORD": "test-operator-password"}))
        self.args = arguments([
            "--state", str(self.state), "--log-dir", str(self.root / "logs"),
            "--game-port", "0", "--profile-port", "0", "--admin-port", "0",
        ])

    def assert_owned(self):
        with self.assertRaisesRegex(OSError, "JSON writer lease"):
            with writer_lease(self.state):
                self.fail("writer lease released while a service was still stopping")

    async def assert_closed(self, ports):
        for port in ports:
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
        with writer_lease(self.state):
            pass

    async def test_http_inspects_the_same_host_as_encrypted_game_and_profile_connections(self):
        from tests.network.test_server import Client
        from tests.profiles.test_options_service import request
        from tests.profiles.test_profile_accounts import file_request, transact
        from tmserver.network.local_accounts import account_specs, display_name
        from tmserver.protocol.identities import SHOP_RAW, player_id, service_nick

        async with running_services(self.args) as services:
            self.assert_owned()
            ports = (services.game_port, services.profile_port, services.admin.port)
            key, (login, _, character) = next(iter(account_specs(2).items()))
            client = Client()
            try:
                await client.open(services.game_port, account=key.encode())
                self.assertIn(b" 422 ", await client.read())
                payload = request(player_id(key.encode()), name=display_name(character))
                await client.send(b"NOTICE " + service_nick(SHOP_RAW) + b" :" + payload)
                self.assertIn(b"@OptAns=/Ans=1", await client.read())
                error, _ = await transact(
                    services.profile_port, key, 3, 0, file_request(),
                    context=(client.cipher, client.iv), account_id=login,
                )
                self.assertEqual(error, 0)
                before = deepcopy(services.game.snapshot())
                origin = f"http://127.0.0.1:{services.admin.port}"
                async with self.httpx.AsyncClient(base_url=origin, trust_env=False) as browser:
                    self.assertEqual((await browser.get("/admin/")).status_code, 303)
                    self.assertEqual((await browser.get("/admin/login")).status_code, 200)
                    login = await browser.post("/admin/login", headers={"Origin": origin}, data={
                        "username": "admin", "password": "test-operator-password",
                        "csrftoken": browser.cookies["starlette_admin_csrftoken"],
                    })
                    self.assertEqual(login.status_code, 303)
                    for path, text in (("/admin/", "Live server overview"),
                                       ("/admin/accounts/list", "Accounts"),
                                       (f"/admin/accounts/detail?pk={key}", key)):
                        response = await browser.get(path)
                        self.assertEqual(response.status_code, 200, path)
                        self.assertIn(text, response.text)
                    denied = await browser.post("/admin/api/account", headers={"Origin": origin})
                    self.assertEqual(denied.status_code, 403)
                self.assertEqual(services.game.snapshot(), before)
            finally:
                await client.close()
        await self.assert_closed(ports)

    async def test_occupied_admin_port_releases_both_protocol_listeners_and_writer_lease(self):
        occupied = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
        self.addAsyncCleanup(occupied.wait_closed)
        self.addCleanup(occupied.close)
        self.args.admin_port = occupied.sockets[0].getsockname()[1]
        ports = []

        def capture(original):
            async def start(host, port):
                bound = await original(host, port)
                ports.append(bound)
                return bound
            return start

        with patch.object(Server, "start", capture(Server.start)), patch.object(
            ProfileServer, "start", capture(ProfileServer.start)
        ):
            with self.assertRaises(OSError):
                async with running_services(self.args):
                    self.fail("occupied admin listener started")
        self.assertEqual(len(ports), 2)
        await self.assert_closed(ports)
        self.assertTrue(occupied.is_serving())

    async def test_cancellation_drains_http_before_protocols_and_releases_lease(self):
        ready = asyncio.Event()
        ports = []

        async def owner():
            async with running_services(self.args) as services:
                ports.extend((services.game_port, services.profile_port, services.admin.port))
                ready.set()
                await services.admin.wait()

        task = asyncio.create_task(owner())
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.assert_closed(ports)
        self.assertFalse(any(task.get_name() == "tm-http" for task in asyncio.all_tasks()))

    async def test_failed_http_task_is_surfaced_and_still_closes_listeners(self):
        entered = asyncio.Event()
        failure = RuntimeError("simulated HTTP failure")

        async def fail(_server):
            await entered.wait()
            raise failure

        with patch.object(self.admin_server.EmbeddedServer, "main_loop", fail):
            with self.assertRaises(RuntimeError) as raised:
                async with running_services(self.args) as services:
                    ports = (services.game_port, services.profile_port, services.admin.port)
                    entered.set()
                    await services.admin.wait()
        self.assertIs(raised.exception, failure)
        await self.assert_closed(ports)

    async def test_http_shutdown_failure_still_stops_protocols_under_writer_lease(self):
        stop = self.admin_server._stop
        order = []

        async def fail(server, task):
            self.assert_owned()
            await stop(server, task)
            order.append("admin")
            raise OSError("simulated admin shutdown failure")

        def tracked(name, original):
            async def shutdown(host):
                self.assert_owned()
                order.append(name)
                await original(host)
            return shutdown

        with (
            patch.object(self.admin_server, "_stop", fail),
            patch.object(ProfileServer, "stop", tracked("profile", ProfileServer.stop)),
            patch.object(Server, "stop", tracked("game", Server.stop)),
        ):
            with self.assertRaisesRegex(OSError, "admin shutdown failure"):
                async with running_services(self.args) as services:
                    ports = (services.game_port, services.profile_port, services.admin.port)
        self.assertEqual(order, ["admin", "profile", "game"])
        await self.assert_closed(ports)
