"""Per-connection legacy IRC transport and authenticated command handling.

Owns framing, the key exchange, login, channel commands and disconnect cleanup.
The host Server owns shared services, table runtimes and durable account state.
"""

import asyncio
import hashlib
import hmac
import os
import socket
import struct
import time

from tmserver.protocol import wire
from tmserver.network import dispatch as services
from tmserver.protocol import commands as tm_protocol
from tmserver.network.chat import relay_text
from tmserver.protocol.identities import service_nick
from tmserver.protocol.messages import FRAME_OVERHEAD, MAX_LINE, MAX_SERVICE_PAYLOAD


def split_message(message):
    """IRC trailing argument keeps spaces and every application payload byte."""
    if message.startswith(b":"):
        raise ValueError("client prefixes are not accepted")
    head, separator, trailing = message.partition(b" :")
    parts = head.split()
    if separator:
        parts.append(trailing)
    if not parts:
        raise ValueError("empty command")
    return parts[0].upper(), parts[1:]


def names_replies(nick, channel, members):
    """Snapshot the roster and split whole names within the legacy frame limit."""
    prefix = b":srv 353 " + nick + b" = " + channel + b" :"
    line = prefix
    for name in sorted(member.nick for member in members):
        entry = (b" " if line != prefix else b"") + name
        if len(line) + len(entry) + FRAME_OVERHEAD > MAX_LINE:
            yield line
            line = prefix + name
        else:
            line += entry
    yield line


class Session:
    def __init__(self, server, reader, writer, session_id):
        self.server, self.reader, self.writer, self.id = server, reader, writer, session_id
        self.cipher, self.iv, self.nick = None, 0, None
        self.tm_player = None
        self.channels = set()
        self.closed = False
        self.bad_checksums = 0
        self.send_lock = asyncio.Lock()
        blob = bytearray(25)
        struct.pack_into(">I", blob, 0, int(time.time()))
        blob[4:8] = socket.inet_aton(writer.get_extra_info("peername")[0])
        struct.pack_into(">H", blob, 20, writer.get_extra_info("sockname")[1])
        self.challenge = wire.b32enc(bytes(blob))

    async def send(self, message):
        if self.closed:
            return
        if (
            any(c in message for c in (b"\0", b"\r", b"\n"))
            or len(message) + FRAME_OVERHEAD > MAX_LINE
        ):
            raise ValueError("outbound line cannot be framed")
        async with self.send_lock:
            if self.closed or self.server.storage_failed is not None:
                return
            packet = wire.frame_line(message, self.cipher, self.iv)
            self.writer.write(packet)
            await asyncio.wait_for(self.writer.drain(), 5)
            self.server.log(
                "send",
                session=self.id,
                encrypted=self.cipher is not None,
                text=message.decode("latin1"),
                wire_hex=packet.hex(),
            )

    async def run(self):
        self.server.log("connect", session=self.id)
        await self.send(b":srv NOTICE AUTH " + self.challenge)
        deadline = time.monotonic() + 30
        missed_ping = False
        while not self.closed:
            timeout = 60 if self.nick else max(0.01, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(self.reader.readline(), timeout)
            except asyncio.TimeoutError:
                if not self.nick or missed_ping:
                    raise
                await self.send(b"PING :tm")
                missed_ping = True
                continue
            # Another writer can close this peer while its buffered read is ready.
            if self.closed or not raw:
                break
            if len(raw) > MAX_LINE or not raw.endswith(b"\r\n"):
                raise ValueError("oversized or unterminated frame")
            plain = wire.crypt64(self.cipher, self.iv, raw) if self.cipher else raw
            if len(plain) < 6:
                raise ValueError("short frame")
            message, checksum = plain[:-6], plain[-6:-2]
            if wire.checksum_chars(message) != checksum:
                self.bad_checksums += 1
                self.server.log("bad_checksum", session=self.id, wire_hex=raw.hex())
                if self.bad_checksums >= 3:
                    raise ValueError("repeated bad checksums")
                continue
            if b"\0" in message or b"\r" in message or b"\n" in message:
                raise ValueError("control byte in message")
            self.server.log(
                "receive",
                session=self.id,
                encrypted=self.cipher is not None,
                text=message.decode("latin1"),
                wire_hex=raw.hex(),
            )
            verb, args = split_message(message)
            missed_ping = False
            if self.cipher is None:
                if verb != b"USER" or len(args) != 4 or len(args[-1]) != 43:
                    raise ValueError("expected USER with 256-bit RSA modulus")
                if any(c not in wire.B64 for c in args[-1]):
                    raise ValueError("invalid modulus alphabet")
                modulus = wire.b64dec(args[-1])[:32]
                number = int.from_bytes(modulus, "little")
                if number.bit_length() < 254 or number % 2 == 0:
                    raise ValueError("invalid RSA modulus")
                key = os.urandom(8)
                cipher = await asyncio.to_thread(wire.SqBlowfish, key)
                await self.send(b":srv 300 * " + wire.rsa_wrap_session_key(modulus, key))
                self.cipher, self.iv = cipher, int.from_bytes(modulus[:8], "little")
                self.server.log("key_exchange", session=self.id)
            elif self.nick is None:
                if verb != b"NICK" or len(args) != 1:
                    raise ValueError("expected encrypted NICK")
                parts = args[0].split(b":", 2)
                if len(parts) != 3 or parts[0] not in self.server.accounts:
                    raise ValueError("unknown synthetic account")
                nick, digest, _ = parts
                expected = (
                    hashlib.md5(self.challenge + self.server.accounts[nick]).hexdigest().encode()
                )
                if not hmac.compare_digest(expected, digest):
                    raise ValueError("NICK challenge digest mismatch")
                if nick in self.server.nicks:
                    raise ValueError("account already connected")
                if any(t.departures.retains_account(nick) for t in self.server.ordinary_tables):
                    raise ValueError("account remains reserved by its departing match")
                self.nick = nick
                self.server.ensure_account(nick)
                self.server.trade.bind(self, nick.decode("ascii"))
                self.server.nicks[nick] = self
                await self.send(b":srv 422 " + nick + b" :no motd")
                self.server.log("authenticated", session=self.id, nick=nick.decode())
            else:
                await self.command(verb, args)

    @property
    def prefix(self):
        return b":" + self.nick + b"!p@local "

    def channel_name(self, channel):
        """Resolve ASCII case aliases while retaining native advertised spelling."""
        key = channel.upper()
        rooms = [table.room for table in self.server.ordinary_room_routes.values()]
        rooms.extend(binding.room for binding in self.server.event_rooms.rooms)
        for room in rooms:
            names = (
                room.room_channel,
                *(table.table_channel for table in room.tables if table.table_id is not None),
                *(record[0x18:0x28].split(b"\0", 1)[0] for record in room.service_records),
            )
            for name in names:
                if name.upper() == key:
                    return name
        # Generic channels keep their first spelling. This local compatibility
        # rule folds ASCII letters only, not IRC punctuation aliases.
        return next((name for name in self.server.channels if name.upper() == key), channel)

    async def command(self, verb, args):
        if self.server.storage_failed is not None:
            raise OSError("state storage failed; refusing further commands")
        if verb == b"PING":
            await self.send(b"PONG " + (args[0] if args else b"a"))
        elif verb == b"PONG":
            return
        elif verb == b"QUIT":
            await self.close()
        elif verb == b"JOIN" and args:
            channel = args[0]
            if not channel.startswith(b"#") or len(channel) > 64:
                raise ValueError("invalid or excessive channel joins")
            channel = self.channel_name(channel)
            if channel in self.channels:
                await self.send(self.prefix + b"JOIN :" + channel)
                return
            if len(self.channels) >= 8:
                raise ValueError("invalid or excessive channel joins")
            members = self.server.channels.setdefault(channel, set())
            members.add(self)
            self.channels.add(channel)
            for member in list(members):
                await self.server.deliver(member, self.prefix + b"JOIN :" + channel)
            for reply in names_replies(self.nick, channel, members):
                await self.send(reply)
            await self.send(b":srv 366 " + self.nick + b" " + channel + b" :End of NAMES list")
        elif verb == b"PART" and args:
            await self.part(args[0])
        elif verb in (b"PRIVMSG", b"NOTICE") and len(args) == 2:
            target, payload = args
            if len(payload) > MAX_SERVICE_PAYLOAD:
                raise ValueError("Tetra Master send ceiling exceeded")
            if not target.startswith(b"#") and await services.dispatch(self, target, payload):
                return
            if payload.startswith(b"GTM0G42"):
                key = self.server.ensure_account(self.nick)
                payload = relay_text(
                    payload,
                    self.server.economy.account_view(key).name,
                    private=not target.startswith(b"#"),
                )
            peer_control = False
            if target.startswith(b"#"):
                target = self.channel_name(target)
                if target not in self.channels:
                    await self.send(b":srv 442 " + self.nick + b" " + target + b" :Join first")
                    return
                recipients = self.server.channels[target] - {self}
            else:
                peer = self.server.nicks.get(target)
                recipients = {peer} if peer else set()
                if peer is not None:
                    peer_control = self.server.trade_router.observe(self, peer, payload)
                if peer is None:
                    self.server.log(
                        "unhandled_service_target",
                        session=self.id,
                        target=target.decode("latin1"),
                        payload_hex=payload.hex(),
                    )
            # Native initializers can adopt any sender; maintenance/status
            # controls do not check one. Ingress also ignores the fifth prefix
            # byte, so reserve every GTM0 selector for validated peer traffic.
            if (
                payload.startswith(b"GTM0")
                and (target.startswith(b"#") or recipients)
                and not payload.startswith(b"GTM0G42")
                and not peer_control
            ):
                raise ValueError("game service messages cannot be relayed between players")
            service = tm_protocol.inspect_service(payload)
            if service:
                self.server.log(
                    "tm_service", session=self.id, target=target.decode("latin1"), **service
                )
            # Logging only: no invented command responses or guessed game state.
            start = payload.find(b"<")
            if start >= 0:
                try:
                    commands = tm_protocol.parse(payload[start:])
                    self.server.log(
                        "tm_commands",
                        session=self.id,
                        prefix_hex=payload[:start].hex(),
                        commands=[
                            {
                                "token": c.token.decode("ascii"),
                                "ids": c.ids,
                                "payload_hex": c.payload.hex(),
                            }
                            for c in commands
                        ],
                    )
                except ValueError:
                    self.server.log("unparsed_payload", session=self.id, payload_hex=payload.hex())
            for peer in list(recipients):
                await self.server.deliver(
                    peer, self.prefix + verb + b" " + target + b" :" + payload
                )
        else:
            self.server.log("unsupported", session=self.id, verb=verb.decode("latin1"))
            await self.send(
                b":srv 421 " + self.nick + b" " + verb + b" :Unsupported in research server"
            )

    async def part(self, channel):
        channel = self.channel_name(channel)
        members = self.server.channels.get(channel)
        if members is None or self not in members:
            await self.send(b":srv 442 " + self.nick + b" " + channel + b" :Not on channel")
            return
        for peer in list(members):
            await self.server.deliver(peer, self.prefix + b"PART " + channel)
        if self.closed:
            return
        self.channels.discard(channel)
        members.discard(self)
        if not members:
            self.server.channels.pop(channel, None)
        ordinary = self.server.table_for_channel(channel)
        if ordinary is not None:
            await ordinary.release_table(self)
        events = self.server.event_matches
        event_table = next(
            (
                (nick, table_id)
                for nick, (_, table_id, table_channel) in events.routes.items()
                if table_channel.upper() == channel.upper()
            ),
            None,
        )
        if event_table is not None:
            table_id = event_table[1]
            watching = events.spectators.admissions.get(self)
            if events.coordinator.participant_table(self) == table_id or (
                watching is not None and watching[0] == table_id
            ):
                await events.disconnect(self)
        # Native table startup leaves room chat before Req/JOIN/PD. This PART
        # must preserve the logical room roster and pending table admission.
        group = (
            service_nick(ordinary.table_raw)
            if ordinary is not None
            else next(
                (
                    nick
                    for nick, table in self.server.ordinary_room_routes.items()
                    if table.room.room_channel.upper() == channel.upper()
                ),
                None,
            )
        )
        if group is None and event_table is not None:
            group = event_table[0]
        binding = self.server.event_rooms.room_for_channel(channel)
        if binding is not None and binding.room_channel.upper() == channel.upper():
            await self.leave_event_room(binding)
            group = service_nick(binding.raw_owner)
        if group is not None:
            await self.server.chat_updates(self.server.chat.leave(self, group))

    async def leave_event_room(self, binding):
        events = self.server.event_matches
        watching = events.spectators.admissions.get(self)
        if (
            self in binding.room.members
            or events.coordinator.participant_room(self) == binding.room_id
            or watching is not None
            and self.server.event_rooms.room_for_service(watching[0]) is binding
        ):
            await events.disconnect(self)
        # Delivery can close this socket and let the account reconnect. Its
        # replacement owns any new presence established while we were waiting.
        if self.closed:
            return
        key = self.server.ensure_account(self.nick)
        updates = binding.room.leave(self)
        self.server.events.leave_room(key, binding.event_id)
        await self.server.room_updates(updates, source=service_nick(binding.raw_owner))

    async def close(self):
        if self.closed:
            return
        self.closed = True
        # Detach synchronously, before any notification can block. A reconnect
        # may bind the account while the old socket finishes closing.
        self.tm_player = None
        for channel in list(self.channels):
            members = self.server.channels.get(channel, set())
            members.discard(self)
            if not members:
                self.server.channels.pop(channel, None)
        self.channels.clear()
        if self.server.nicks.get(self.nick) is self:
            self.server.nicks.pop(self.nick, None)
        self.server.sessions.discard(self)
        self.writer.close()
        ordinary = self.server.table_for_peer(self)
        retained = ordinary.departures.request(self) if ordinary is not None else False

        for table in self.server.ordinary_tables:
            table.detach_spectator(self)
            table.match.forget_control_peer(self)
        # Release event state now; pending notifications must not delay the
        # remaining account/trade cleanup and race a reconnect's fresh state.
        try:
            await self.server.event_matches.disconnect(self, wait_delivery=False)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            self.server.log("disconnect_cleanup_error", session=self.id, reason=str(exc))

        def cleanup(action):
            try:
                return action() or []
            except (OSError, ValueError, asyncio.TimeoutError) as exc:
                self.server.log("disconnect_cleanup_error", session=self.id, reason=str(exc))
                return []

        cleanup(lambda: self.server.trade_router.forget(self))
        trade_updates = cleanup(lambda: self.server.trade.disconnect(self))
        if self.nick is not None and self.nick.decode("ascii") in self.server.account_keys:
            cleanup(lambda: self.server.auction.disconnect(self.nick.decode("ascii")))
            cleanup(lambda: self.server.prize.disconnect(self.nick.decode("ascii")))
            cleanup(lambda: self.server.event_cards.disconnect(self.nick.decode("ascii")))
            cleanup(lambda: self.server.events.disconnect(self.nick.decode("ascii")))
            cleanup(lambda: self.server.economy.disconnect(self.nick.decode("ascii")))
        ordinary_match = ordinary.match if ordinary is not None else None
        affected = [] if retained or ordinary is None else ordinary.table_dependents(self)
        room_groups = [
            (table, cleanup(lambda: table.room.leave(self)))
            for table in self.server.ordinary_room_routes.values()
        ]
        event_updates = (
            cleanup(lambda: self.server.event_rooms.disconnect(self, self.nick.decode("ascii")))
            if self.nick is not None and self.nick.decode("ascii") in self.server.account_keys
            else []
        )
        if not retained and ordinary is not None:
            room_groups.append((ordinary, cleanup(lambda: ordinary.table_release_updates(self))))
        if (
            not retained
            and self.nick is not None
            and self.nick.decode("ascii") in self.server.account_keys
        ):
            cleanup(lambda: self.server.result_rewards.forget(self.nick.decode("ascii")))
        chat_updates = cleanup(lambda: self.server.chat.leave(self))
        if self.server.storage_failed is None:
            for table, updates in room_groups:
                try:
                    await table.publish_room(updates)
                except (OSError, ValueError, asyncio.TimeoutError) as exc:
                    self.server.log("disconnect_cleanup_error", session=self.id, reason=str(exc))
            for binding, updates in event_updates:
                try:
                    await self.server.room_updates(updates, source=service_nick(binding.raw_owner))
                except (OSError, ValueError, asyncio.TimeoutError) as exc:
                    self.server.log("disconnect_cleanup_error", session=self.id, reason=str(exc))
            for send, updates in (
                (self.server.trade_updates, trade_updates),
                (self.server.chat_updates, chat_updates),
            ):
                try:
                    await send(updates)
                except (OSError, ValueError, asyncio.TimeoutError) as exc:
                    self.server.log("disconnect_cleanup_error", session=self.id, reason=str(exc))
        if affected:
            await ordinary.close_released_peers(ordinary_match, affected)
        try:
            await asyncio.wait_for(self.writer.wait_closed(), 5)
        except (OSError, asyncio.TimeoutError):
            pass
        self.server.log("disconnect", session=self.id)
