"""Only authenticated chat and consent-checked trade controls may cross peers."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests.support.irc import Client
from tmserver.economy.packets import named
from tmserver.protocol.identities import TRADE_RAW, game_id, player_id
from tmserver.network.server import Server


class PeerServiceSpoofingTests(unittest.IsolatedAsyncioTestCase):
    channel = b"#TM0-relay-audit"

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.json"
        self.log = Path(self.temp.name) / "game.jsonl"
        self.game = Server(state_path=self.path, log_path=self.log)
        self.accounts = list(self.game.accounts)
        for nick in self.accounts:
            self.game.ensure_account(nick)
        self.port = await self.game.start(0)
        self.clients = []
        self.receiver = await self.connect(1)
        await self.receiver.join(self.channel)
        self.before = self.game.snapshot()
        self.saved = self.path.read_bytes()

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.game.stop()
        self.temp.cleanup()

    async def connect(self, index=0, *, join=False):
        client = Client()
        self.clients.append(client)
        await client.open(self.port, account=self.accounts[index])
        self.assertIn(b" 422 ", await client.read())
        if join:
            await client.join(self.channel)
            self.assertIn(b"JOIN :" + self.channel, await self.receiver.read())
        return client

    async def reject(self, payload, verb, channel):
        sender = await self.connect(join=channel)
        session_id = self.game.nicks[sender.nick].id
        target = self.channel if channel else self.receiver.nick
        await sender.send(verb + b" " + target + b" :" + payload)
        await sender.send(b"PING :sender-barrier")
        self.assertEqual(await sender.read(), b"")
        await self.receiver.send(b"PING :receiver-barrier")
        self.assertEqual(await self.receiver.read(), b"PONG receiver-barrier")
        self.assertNotIn(sender.nick, self.game.nicks)
        self.assertIn(self.receiver.nick, self.game.nicks)
        self.assertEqual(self.game.snapshot(), self.before)
        self.assertEqual(self.path.read_bytes(), self.saved)
        self.assertFalse(self.game.economy.is_busy(self.receiver.nick.decode()))
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        errors = [row for row in records if row["event"] == "connection_error"]
        self.assertEqual(errors[-1]["session"], session_id)
        self.assertEqual(
            errors[-1]["reason"], "game service messages cannot be relayed between players"
        )

    async def test_service_controls_and_initializers_never_reach_another_player(self):
        payloads = (
            b"GTM0G2B000000",
            b"GTM0G2c000100",  # Hex family decoding is case insensitive.
            b"GTM0G2BFFFEA7@Ignored=/Value=1",
            b"GTM0g2B000000",  # Original ingress ignores the fifth selector.
            b"GTM0Z2C000100",
            b"GTM0LA1000000@TrEntAns=/Ans=1",
            b"GTM0GA1000000@TrEntAns=/Ans=1",
            b"GTM0GA2001800@Init=/ID=1@No0=/RA=0/M=0/CP=0/S=0@No1=/RA=0/M=0/CP=0/S=0",
            b"GTM0GB2001300@ExcInit=/N=1/PP=0|65535|0|0/LC=65535|65535|65535",
            b"GTM0G2B",  # A truncated header is not opaque peer traffic.
        )
        for verb in (b"NOTICE", b"PRIVMSG"):
            for channel in (False, True):
                for payload in payloads:
                    with self.subTest(verb=verb, channel=channel, payload=payload):
                        await self.reject(payload, verb, channel)

    async def test_broadcast_trade_controls_cannot_bypass_pair_validation(self):
        for verb in (b"NOTICE", b"PRIVMSG"):
            for family, body in (
                (0x21, "@Tr=/NN=0000000000000001/HID=0/Dm=0/Vol=0"),
                (0x22, "@TrAns=/Ans=-1"),
                (0x23, "@TrCan="),
                (0x25, "@Tr="),
                (0x2A, "@TrCanAck="),
                (0xA0, f"@TrID=/ID={game_id(TRADE_RAW):016X}"),
            ):
                with self.subTest(verb=verb, family=family):
                    await self.reject(named(family, 0, 0, body), verb, True)

    async def test_opaque_messages_and_authenticated_chat_still_relay(self):
        sender = await self.connect(join=True)
        for verb in (b"NOTICE", b"PRIVMSG"):
            for channel in (False, True):
                target = self.channel if channel else self.receiver.nick
                for payload in (b"ordinary text", b"<RI>\x07opaque\x07", b"GTm0G2B000000"):
                    with self.subTest(verb=verb, target=target, opaque=payload):
                        await sender.send(verb + b" " + target + b" :" + payload)
                        self.assertEqual(
                            await self.receiver.read(),
                            b":"
                            + sender.nick
                            + b"!p@local "
                            + verb
                            + b" "
                            + target
                            + b" :"
                            + payload,
                        )
                header = b"GTM0G42000800" if channel else b"GTM0G42000801"
                payload = header + b"@Chat=/La=1/Dt=#CHAT#ForgedName\tGTM0G2B000000"
                await sender.send(verb + b" " + target + b" :" + payload)
                name = self.game.economy.account_view(sender.nick.decode()).name
                self.assertEqual(
                    await self.receiver.read(),
                    b":"
                    + sender.nick
                    + b"!p@local "
                    + verb
                    + b" "
                    + target
                    + b" :"
                    + payload.replace(b"ForgedName", name),
                )
        self.assertEqual(self.game.snapshot(), self.before)

    async def test_valid_direct_trade_still_checks_consent_and_accepts_lower_hex(self):
        sender = await self.connect()

        async def relay(source, destination, payload):
            await source.send(b"NOTICE " + destination.nick + b" :" + payload)
            self.assertEqual(
                await destination.read(),
                b":" + source.nick + b"!p@local NOTICE " + destination.nick + b" :" + payload,
            )

        def metadata(client):
            return f"/NN={player_id(client.nick):016X}/HID=0/Dm=0/Vol=0"

        await relay(sender, self.receiver, named(0x21, 0, 0, "@Tr=" + metadata(sender)))
        await relay(
            self.receiver, sender, named(0x22, 0, 0, "@TrAns=/Ans=1" + metadata(self.receiver))
        )
        service = named(0xA0, 0, 0, f"@TrID=/ID={game_id(TRADE_RAW):016X}")
        await relay(sender, self.receiver, service.replace(b"GTM0GA0", b"GTM0Ga0"))
        self.assertEqual(self.game.snapshot(), self.before)
