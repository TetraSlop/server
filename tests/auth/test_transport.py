"""Actual HTTP/WebSocket gateway carries unchanged legacy encrypted game/profile bytes."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import unittest

from tests.support.irc import Client
from tests.profiles.test_profile_accounts import file_request, transact
from tmserver.network.server import Server
from tmserver.profiles.service import ProfileServer


@asynccontextmanager
async def local_bridge(url, token):
    """Test-only stand-in for the loader's loopback socket to WSS forwarding."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    tasks, writers = set(), set()

    async def accept(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.add(writer)
        pumps = []
        try:
            async with connect(url, additional_headers={"Authorization": "Bearer " + token},
                               subprotocols=["tm-stream-v1"], proxy=None, compression=None) as ws:
                async def upstream():
                    while data := await reader.read(65536):
                        await ws.send(data)

                async def downstream():
                    async for data in ws:
                        assert isinstance(data, bytes)
                        writer.write(data)
                        await writer.drain()

                pumps = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
        except (ConnectionClosed, ConnectionError):
            pass
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)
            tasks.discard(task)

    listener = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield listener.sockets[0].getsockname()[1]
    finally:
        listener.close()
        for writer in list(writers):
            writer.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*list(tasks), return_exceptions=True)
        await listener.wait_closed()


class GatewayTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from tmserver.auth.config import AuthConfig
        from tmserver.auth.provider import Subject
        from tmserver.auth.web import create_app
        from tmserver.network.http import serving_http

        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.host = Server(state_path=self.directory / "save.json")
        self.addAsyncCleanup(self.host.stop)
        self.profile = ProfileServer(self.directory / "profile.log", self.host)
        self.addAsyncCleanup(self.profile.stop)
        await self.host.start(0)
        await self.profile.start(0)
        self.keys = list(self.host.account_specs)
        config = AuthConfig(
            supabase_url="https://example.supabase.co", publishable_key="sb_publishable_test_only",
            public_origin="https://tm.example", bindings={
                "11111111-1111-4111-8111-111111111111": "EEEEEEEF",
                "22222222-2222-4222-8222-222222222222": "EEEEEEEK",
            },
        )

        class Verifier:
            issuer = "https://example.supabase.co/auth/v1"

            async def verify(self, token):
                return Subject(issuer=self.issuer, subject=token, display_name="Discord Player")

        def application(port):
            self.origin = f"http://127.0.0.1:{port}"
            self.app = create_app(self.host, self.profile, config, Verifier(),
                                  public_origin=self.origin)
            return self.app

        self.listener = await self.enterAsyncContext(serving_http(application, port=0, websockets=True))
        self.addCleanup(self.app.state.play_sessions.close)
        self.http = await self.enterAsyncContext(httpx.AsyncClient(base_url=self.origin, trust_env=False))
        self.play = await self.new_session()

    async def new_session(self):
        issued = await self.http.post("/v1/play/tickets", json={"protocol": 1},
            headers={"Authorization": "Bearer 11111111-1111-4111-8111-111111111111"})
        self.assertEqual(issued.status_code, 201, issued.text)
        redeemed = await self.http.post("/v1/play/sessions", json={"ticket": issued.json()["ticket"]})
        self.assertEqual(redeemed.status_code, 201, redeemed.text)
        return redeemed.json()

    async def test_encrypted_game_and_profile_bytes_fragmentation_and_reconnect(self):
        async with local_bridge(self.play["streams"]["game"], self.play["session_token"]) as game_port:
            client = Client()
            await client.open(game_port, fragmented=True)
            try:
                self.assertIn(b" 422 ", await client.read())
                await client.send(b"PING :hosted")
                self.assertEqual(await client.read(), b"PONG hosted")
                async with local_bridge(self.play["streams"]["profile"], self.play["session_token"]) as port:
                    error, data = await transact(port, self.keys[0], 3, 0, file_request(),
                                                context=(client.cipher, client.iv), fragmented=True)
                    self.assertEqual(error, 0)
                    self.assertEqual(len(data), 0x302C)
                    self.assertIn(b"Discord Player\0", data)
                    self.assertEqual(self.host.economy.account_view(self.keys[0]).name, b"Discord Player")
                    await client.close()
                    # The same play session retains its validated profile cipher.
                    self.assertEqual((await transact(port, self.keys[0], 0, 9,
                                      context=(client.cipher, client.iv)))[0], 0)
            finally:
                await client.close()
        async with local_bridge(self.play["streams"]["game"], self.play["session_token"]) as port:
            again = Client()
            await again.open(port)
            try:
                self.assertIn(b" 422 ", await again.read())
            finally:
                await again.close()

    async def test_known_other_account_password_cannot_cross_the_gateway_binding(self):
        before = self.host.snapshot()
        async with local_bridge(self.play["streams"]["game"], self.play["session_token"]) as port:
            other = Client()
            await other.open(port, self.keys[1].encode("ascii"))
            self.assertEqual(await other.read(), b"")
            await other.close()
        async with local_bridge(self.play["streams"]["profile"], self.play["session_token"]) as port:
            error, _ = await transact(port, self.keys[1], 0, 9)
            self.assertEqual(error, 0xE4)
        self.assertNotIn(self.keys[1].encode("ascii"), self.host.nicks)
        self.assertEqual(self.host.snapshot(), before)

    async def test_revoke_closes_open_game_and_profile_streams_and_rejects_replay(self):
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        headers = {"Authorization": "Bearer " + self.play["session_token"]}
        options = dict(additional_headers=headers, subprotocols=["tm-stream-v1"], proxy=None)
        async with connect(self.play["streams"]["game"], **options) as game:
            await game.recv()
            async with connect(self.play["streams"]["profile"], **options) as profile:
                result = await self.http.delete("/v1/play/session", headers=headers)
                self.assertEqual(result.status_code, 204)
                for ws in (game, profile):
                    with self.assertRaises(ConnectionClosed) as closed:
                        await asyncio.wait_for(ws.recv(), 3)
                    self.assertEqual(closed.exception.rcvd.code, 4401)
        with self.assertRaises(InvalidStatus):
            async with connect(self.play["streams"]["game"], **options):
                self.fail("revoked capability accepted")

    async def test_expiry_closes_idle_connection_and_text_is_not_a_byte_stream(self):
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        headers = {"Authorization": "Bearer " + self.play["session_token"]}
        options = dict(additional_headers=headers, subprotocols=["tm-stream-v1"], proxy=None)
        async with connect(self.play["streams"]["profile"], **options) as ws:
            await ws.send("wrong frame type")
            with self.assertRaises(ConnectionClosed) as closed:
                await ws.recv()
            self.assertEqual(closed.exception.rcvd.code, 1003)
        session = self.app.state.play_sessions.get(self.play["session_token"])
        session.access.deadline = session.access.clock() + 0.1
        async with connect(self.play["streams"]["profile"], **options) as ws:
            with self.assertRaises(ConnectionClosed) as closed:
                await asyncio.wait_for(ws.recv(), 3)
            self.assertEqual(closed.exception.rcvd.code, 4401)

    async def test_renewal_keeps_existing_stream_and_stream_contract_rejects_bad_upgrades(self):
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        headers = {"Authorization": "Bearer " + self.play["session_token"]}
        options = dict(additional_headers=headers, subprotocols=["tm-stream-v1"], proxy=None)
        game_url, profile_url = (self.play["streams"][kind] for kind in ("game", "profile"))
        for bad in (
            dict(options, additional_headers={}), dict(options, subprotocols=["wrong"]),
            dict(options, origin="https://another.example"),
        ):
            with self.subTest(options=bad), self.assertRaises(InvalidStatus):
                async with connect(game_url, **bad):
                    self.fail("invalid WebSocket upgrade accepted")
        async with connect(game_url, **options) as game:
            await game.recv()
            with self.assertRaises(InvalidStatus):
                async with connect(game_url, **options):
                    self.fail("duplicate game stream accepted")
        session = self.app.state.play_sessions.get(self.play["session_token"])
        session.access.deadline = session.access.clock() + 0.25
        async with connect(profile_url, **options) as ws:
            renewed = await self.http.post("/v1/play/renew",
                headers={"Authorization": "Bearer 11111111-1111-4111-8111-111111111111"},
                json={"session_token": self.play["session_token"]})
            self.assertEqual(renewed.status_code, 200)
            await asyncio.sleep(0.3)
            opening = bytearray(40)
            opening[4] = 1
            await ws.send(bytes(opening))
            self.assertEqual(len(await asyncio.wait_for(ws.recv(), 3)), 24)
            await ws.send(bytes(65537))
            with self.assertRaises(ConnectionClosed) as closed:
                await ws.recv()
            self.assertEqual(closed.exception.rcvd.code, 1009)
