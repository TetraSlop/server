"""Opt-in loopback dev identity: browser sign-in without Supabase or Discord."""

import asyncio
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from tests.auth.test_transport import local_bridge
from tests.support.irc import Client
from tests.support.memory_store import DATABASE, MemoryStore


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "examples" / "auth-dev.json"
PAGE = "http://localhost:5173"


class DevIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_names_map_to_stable_separate_subjects_and_bad_names_are_refused(self):
        from tmserver.auth.provider import DEV_ISSUER, DevVerifier, InvalidToken

        verifier = DevVerifier()
        alice, again, bob = [await verifier.verify(name) for name in ("alice", "alice", "bob")]
        self.assertEqual(alice, again)
        self.assertNotEqual(alice.subject, bob.subject)
        self.assertEqual((alice.issuer, alice.discord, alice.display_name), (DEV_ISSUER, True, "alice"))
        for name in ("", "two words", "x" * 16, "café", None):
            with self.subTest(name=name), self.assertRaises(InvalidToken):
                await verifier.verify(name)

    async def test_dev_identity_is_refused_off_the_insecure_loopback_gateway(self):
        from tmserver.auth.config import load_auth_config
        from tmserver.auth.server import serving_auth
        from tmserver.cli.serve import arguments

        for argv in (["--auth-config", str(CONFIG), "--auth-dev-identity"], ["--auth-dev-identity"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                arguments([*argv, *DATABASE])
        with self.assertRaisesRegex(ValueError, "insecure loopback"):
            async with serving_auth(None, None, load_auth_config(CONFIG), port=0, dev_identity=True):
                self.fail("dev identity served on the public gateway")

    async def test_browser_on_localhost_signs_in_and_opens_the_game_stream(self):
        import httpx
        from tmserver.auth.config import load_auth_config
        from tmserver.auth.server import serving_auth
        from tmserver.cli.serve import arguments
        from tmserver.network.server import Server
        from tmserver.profiles.service import ProfileServer

        args = arguments(["--auth-config", str(CONFIG), "--auth-insecure-loopback",
                          "--auth-dev-identity", *DATABASE])
        self.assertTrue(args.auth_dev_identity)
        with tempfile.TemporaryDirectory() as folder:
            host = Server(store=MemoryStore())
            self.addAsyncCleanup(host.stop)
            profile = ProfileServer(Path(folder) / "profile.jsonl", host)
            self.addAsyncCleanup(profile.stop)
            await host.start(0)
            await profile.start(0)
            async with serving_auth(host, profile, load_auth_config(CONFIG), port=0,
                                    insecure_loopback=True, dev_identity=True) as auth:
                gateway = f"http://127.0.0.1:{auth.port}"
                page = {"Origin": PAGE}
                async with httpx.AsyncClient(base_url=gateway, trust_env=False) as http:
                    issued = await http.post("/v1/play/tickets", json={"protocol": 1},
                                             headers=page | {"Authorization": "Bearer alice"})
                    self.assertEqual(issued.status_code, 201, issued.text)
                    self.assertEqual(issued.headers["access-control-allow-origin"], PAGE)
                    redeemed = await http.post("/v1/play/sessions", headers=page,
                                               json={"ticket": issued.json()["ticket"]})
                    self.assertEqual(redeemed.status_code, 201, redeemed.text)
                    play = redeemed.json()
                    self.assertEqual(play["streams"]["game"], f"ws://127.0.0.1:{auth.port}"
                                                              "/v1/streams/game")
                    # A name other than the numeric loopback origin is refused, as a proxy would be.
                    proxied = await http.post("/v1/play/tickets", json={"protocol": 1},
                                              headers={"Host": "tm.example.com",
                                                       "Authorization": "Bearer alice"})
                    self.assertEqual(proxied.json(), {"error": "invalid_origin"})
                game = play["streams"]["game"]
                async with local_bridge(game, play["session_token"], PAGE) as port:
                    client = Client()
                    await client.open(port, play["account_key"].encode(),
                                      play["legacy"]["password"].encode())
                    try:
                        self.assertIn(b" 422 ", await asyncio.wait_for(client.read(), 5))
                    finally:
                        await client.close()
                self.assertEqual(host.economy.account_view(play["account_key"]).name,
                                 b"alice")
