"""Loopback PC PlayOnline profile service for synthetic test accounts.

Layouts come from profile_ops_pc.md, not a modern web API. Unknown operations
fail explicitly and are logged so incomplete protocol support stays visible.

Local authentication policy: the configured pool of synthetic fixtures
use tm-local-test. Encrypted requests try each account's active authenticated IRC key
and up to two previously nonce-validated profile contexts; the complete legacy
MD5(ID8 + password + nonce) selects both identity and cipher. The opening packet
does not select an account. Cached contexts are process-local, bounded, and have
no expiry beyond replacement/restart; a restarted service needs a fresh IRC
context before encrypted profile use. Plain mode uses the same full digest.
Encrypted authentication failure closes without guessing an error-response key.
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import struct
import time
from pathlib import Path

from tmserver import pol_wire as wire
from tmserver.identities import player_id as game_player_id
from tmserver.local_accounts import account_specs, display_name
from tmserver.profile_location import FileOwner
from tmserver.room_service import directory_files

# These are the default two local fixtures. Keep the profile module
# independent of server's import graph; never accept arbitrary production IDs.
SYNTHETIC_ACCOUNTS = account_specs()
DEFAULT_ACCOUNT = next(iter(SYNTHETIC_ACCOUNTS))
MAX_CACHED_CIPHERS = 2  # Per account; the active IRC key is an extra candidate.


def checksum(data):
    whole = len(data) & ~3
    value = sum(struct.unpack("<" + "I" * (whole // 4), data[:whole]))
    if whole < len(data):
        value += int.from_bytes(data[whole:], "little") << (8 * (4 - len(data[whole:])))
    return value & 0xFFFFFFFF


def checked(data):
    return bytes(data) + struct.pack("<I", checksum(data))


def answer(size=0, error=0, nonce=0):
    data = bytearray(24)
    data[0:2] = bytes((0x81, error))
    struct.pack_into("<I", data, 4, size)
    struct.pack_into("<I", data, 20, nonce)
    return data


class ProfileServer:
    def __init__(self, log_path, game_server=None):
        self.accounts = getattr(game_server, "account_specs", SYNTHETIC_ACCOUNTS)
        self.log_path = Path(log_path)
        self.listener = None
        self.tasks = set()
        self.writers = set()
        # Synthetic player record; most fields are not mapped yet. The leading
        # 300 matches the value written by TM 0x10114e87; its meaning is unproven.
        player = bytearray(0x3028)
        struct.pack_into("<I", player, 0, 300)
        player[0x3B] = 1  # Existing local test player: Warriors Guild (TM 0x1014c871).
        # ZL consumer 0x1008d402: count +0x40, records +0x48, stride 0x40.
        # EN chooses the English page; AAA are unrestricted filter values.
        zones = bytearray(0x848)
        struct.pack_into("<I", zones, 0x40, 1)
        struct.pack_into("<II", zones, 0x48, 1, 1)
        zones[0x54:0x59] = b"ENAAA"
        zones[0x59:0x65] = b"Local Lobby\0"
        # 0x10085c80 / 0x10085cf0 use record +0x2c for the zone IRC host.
        zones[0x74:0x7E] = b"127.0.0.1\0"
        # 0x1008e065: count +0x40, records +0x48 with stride 0xc8.
        # +0x28 'A' follows the fixed-room branch; +0x2a is a 32-byte name.
        rooms = bytearray(0xC848)
        struct.pack_into("<I", rooms, 0x40, 1)
        struct.pack_into("<Q", rooms, 0x48, 1)
        rooms[0x48 + 0x28] = ord("A")
        rooms[0x48 + 0x2A : 0x48 + 0x35] = b"Local Room*"
        rooms[0x48 + 0x6D : 0x48 + 0x71] = b"AAAA"
        self.files = {"b/g/ZL": bytes(zones), "b/g/RL000": bytes(rooms)}
        self.account_files = {}
        for key, (_, _, player_id) in self.accounts.items():
            fallback = bytearray(player)
            struct.pack_into("<IQ", fallback, 4, player_id, game_player_id(key.encode("ascii")))
            name = display_name(player_id)
            fallback[0x10 : 0x10 + len(name)] = name
            self.account_files[key] = {"U/g/TM0DataFile": bytes(fallback)}
        self.game_server = game_server
        self.files.update(directory_files())
        self.profile_ciphers = {key: [] for key in self.accounts}
        self.statuses = {}
        for key in self.accounts:
            status = bytearray(0x7C)
            status[3] = 1
            status[4] = 1  # Viewer before the game changes ContentsClass to 2.
            status[6] = 1  # Online, active handle zero.
            self.statuses[key] = status

    def log(self, event, **details):
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(time=time.time(), event=event, **details)) + "\n")

    async def start(self, port=51220):
        self.listener = await asyncio.start_server(self.handle, "127.0.0.1", port)
        return self.listener.sockets[0].getsockname()[1]

    async def stop(self):
        if self.listener:
            self.listener.close()
        for writer in list(self.writers):
            writer.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.listener:
            await self.listener.wait_closed()

    def _account(self, account_key):
        if account_key not in self.accounts:
            raise ValueError("unknown synthetic profile account")
        return self.accounts[account_key]

    def _identity(self, account_key):
        account_id, _, player_id = self._account(account_key)
        name = display_name(player_id)
        if self.game_server is not None and hasattr(self.game_server, "economy"):
            self.game_server.ensure_account(account_key.encode("ascii"))
            view = self.game_server.economy.account_view(account_key)
            name, player_id = view.name, view.player_id
        # Generic profile handles contain the raw POL identity. TM0DataFile's
        # separate game-domain identity comes directly from the economy save.
        return wire.polid_from_string(account_id.decode("ascii")), player_id, name[:16]

    def _cipher_candidates(self):
        """At most three candidates per configured authenticated IRC account."""
        for key in self.accounts:
            candidates = []
            if self.game_server is not None:
                session = self.game_server.nicks.get(key.encode("ascii"))
                if session is not None and getattr(session, "cipher", None) is not None:
                    candidates.append((session.cipher, session.iv))
            for cipher, iv in self.profile_ciphers[key]:
                if not any(cipher is old and iv == old_iv for old, old_iv in candidates):
                    candidates.append((cipher, iv))
            for cipher, iv in candidates:
                yield key, cipher, iv

    def _remember_cipher(self, key, cipher, iv):
        # Only nonce-authenticated profile contexts survive IRC reconnects.
        cached = [
            (old, old_iv)
            for old, old_iv in self.profile_ciphers[key]
            if old is not cipher or old_iv != iv
        ]
        self.profile_ciphers[key] = [(cipher, iv)] + cached[: MAX_CACHED_CIPHERS - 1]

    def _authenticate(self, request, nonce, encrypted):
        expected = {
            key: hashlib.md5(account_id + password + struct.pack("<I", nonce)).digest()
            for key, (account_id, password, _) in self.accounts.items()
        }
        if not encrypted:
            for key, digest in expected.items():
                if hmac.compare_digest(request[24:40], digest):
                    return key, request, None
        else:
            for key, cipher, iv in self._cipher_candidates():
                plain = wire.crypt64(cipher, iv, request)
                # No structural byte guesses: all 16 authentication bytes must
                # match the account that owns this particular cipher context.
                if hmac.compare_digest(plain[24:40], expected[key]):
                    return key, plain, (cipher, iv)
        return None

    def dispatch(self, cmd, sub, body, account_key=None):
        if getattr(self.game_server, "storage_failed", None) is not None:
            raise ValueError("state storage failed; restart is required")
        # Legacy unit helpers are trusted direct calls. Network callers MUST
        # supply the identity proven by the complete nonce authentication.
        account_key = DEFAULT_ACCOUNT if account_key is None else account_key
        self._account(account_key)
        status = self.statuses[account_key]
        op = cmd, sub
        if op in ((4, 5), (4, 6), (4, 7), (3, 0), (3, 2), (3, 3)):
            if len(body) < 4 or checksum(body[:-4]) != int.from_bytes(body[-4:], "little"):
                raise ValueError("object checksum mismatch")
        if op == (0, 9) and not body:
            pol_id, _, name = self._identity(account_key)
            record = bytearray(0x88)
            struct.pack_into("<Q", record, 8, pol_id)
            record[16 : 16 + len(name)] = name
            return bytes((1,)) + bytes(7) + record, 0
        if op == (1, 3) and not body:
            pol_id, player_id, name = self._identity(account_key)
            record = bytearray(0x68)
            record[4] = 1  # Attached to local handle 0, character slot 0.
            struct.pack_into("<H", record, 8, 2)  # Tetra Master content class.
            struct.pack_into("<I", record, 12, player_id)
            struct.pack_into("<Q", record, 16, pol_id)
            record[24 : 24 + len(name)] = name
            return bytes((1,)) + bytes(7) + record, 0
        if op == (2, 3) and not body:
            return bytes(8), 0
        if op == (4, 6) and len(body) == 0x18:
            return checked(status), 0
        if op == (4, 5) and len(body) == 0x28:
            status[:8] = body[0x10:0x18]
            self.log(
                "status_changed",
                account=account_key,
                handle=status[0],
                contents_class=int.from_bytes(status[4:6], "little"),
                online_status=status[6],
            )
            return checked(secrets.token_bytes(16) + bytes(12)), 0
        if op == (4, 7) and len(body) == 0x40 and not any(body[:-4]):
            return b"", 0
        if cmd == 3 and len(body) >= 0x198:
            name = body[0x10:0x190].split(b"\0", 1)[0].decode("ascii", "replace")
            if name.startswith("./"):
                name = name[2:]
            self.log("file_request", account=account_key, operation=sub, name=name, body=body.hex())
            if sub == 0 and len(body) == 0x1A0:
                provider = getattr(self.game_server, "profile_file", None)
                owner = FileOwner.decode(struct.unpack_from("<Q", body, 8)[0])
                supplied = (
                    provider(account_key, name, owner=owner) if provider is not None else None
                )
                if (
                    name == "U/g/TM0DataFile"
                    and self.game_server is not None
                    and hasattr(self.game_server, "economy")
                ):
                    self.game_server.ensure_account(account_key.encode("ascii"))
                    data = self.game_server.economy.profile_bytes(account_key)
                elif supplied is not None:
                    data = supplied
                elif (
                    name == "b/g/PTL"
                    and provider is None
                    and self.game_server is not None
                    and hasattr(self.game_server, "room")
                ):
                    data = self.game_server.room.snapshot()
                elif name.startswith("U/"):
                    data = self.account_files[account_key].get(name)
                else:
                    data = self.files.get(name)
                if data is None:
                    return b"", 0x7E
                offset, maximum = struct.unpack_from("<II", body, 0x190)
                return checked(data[offset : offset + min(maximum, 1024 * 1024)]), 0
            if sub == 2 and len(body) == 0x198:
                delete = getattr(self.game_server, "delete_profile_file", None)
                if delete is not None:
                    delete(account_key, name)
                # Common344/10027250 completes on ReadAns only; no result body.
                return b"", 0
            if sub == 3 and len(body) == 0x1A0:
                # Viewer GetMessageList polls this authenticated owner's
                # mailbox. No hosted messages exist in the local service.
                # Original GetMessageList consumes an eight-byte count then a
                # checked four-byte trailer, even for an empty mailbox.
                owner = FileOwner.decode(struct.unpack_from("<Q", body, 8)[0])
                raw_identity, _, _ = self._identity(account_key)
                if name != "O/m/" or owner != FileOwner(raw_identity, 0, 0):
                    return b"", 0x7E
                return checked(bytes(8)), 0
        self.log("unsupported_operation", cmd=cmd, sub=sub, body=body.hex())
        return b"", 1

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        if len(self.tasks) >= max(8, len(self.accounts)):
            writer.close()
            await writer.wait_closed()
            return
        self.tasks.add(task)
        self.writers.add(writer)
        try:
            async with asyncio.timeout(20):
                opening = await reader.readexactly(40)
                self.log("open", wire=opening.hex())
                if opening[0] != 0 or opening[4:6] != b"\x01\0":
                    raise ValueError("invalid open")
                nonce = secrets.randbits(32)
                encrypted = opening[1] == 0
                writer.write(answer(nonce=nonce))
                await writer.drain()
                request = await reader.readexactly(40)
                authenticated = self._authenticate(request, nonce, encrypted)
                if authenticated is None:
                    # Without an authenticated encrypted context, selecting a
                    # response key would reintroduce the same identity guess.
                    if not encrypted:
                        writer.write(answer(error=0xE4))
                        await writer.drain()
                    raise ValueError("synthetic account authentication failed")
                account_key, request, context = authenticated
                crypt = None
                if context is not None:
                    cipher, iv = context
                    crypt = lambda data, offset=0: wire.crypt64(cipher, iv, bytes(offset) + data)[
                        offset:
                    ]
                self.log("request", account=account_key, wire=request.hex())
                if request[0] != 2:
                    raise ValueError("unrecognized request (possibly encrypted)")
                size = struct.unpack_from("<I", request, 4)[0]
                if size > 1024 * 1024:
                    raise ValueError("request too large")
                if context is not None:
                    self._remember_cipher(account_key, *context)
                body = await reader.readexactly(size)
                if crypt:
                    body = crypt(body, 40)
                payload, error = self.dispatch(
                    request[1], request[2], body, account_key=account_key
                )
                self.log(
                    "response",
                    account=account_key,
                    cmd=request[1],
                    sub=request[2],
                    error=error,
                    body=payload.hex(),
                )
                header = answer(len(payload), error)
                if crypt:
                    writer.write(crypt(header + payload))
                else:
                    writer.write(header + payload)
                await writer.drain()
                # Let the native async receiver consume the response before its close.
                await reader.read(1)
        except (ValueError, asyncio.IncompleteReadError, TimeoutError, OSError) as exc:
            self.log("connection_error", error=str(exc))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.writers.discard(writer)
            self.tasks.discard(task)
