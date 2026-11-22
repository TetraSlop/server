"""PlayOnline profile file service, on loopback.

Layout: TetraPackets file/service.md. Unknown operations fail and are logged.

Local test accounts use the password tm-local-test. Encrypted requests try each
account's active authenticated IRC key and up to two previously nonce-validated
profile contexts; the complete legacy MD5(ID8 + password + nonce) selects both
identity and cipher. The opening packet
does not select an account. Cached contexts are process-local, bounded, and have
no expiry beyond replacement/restart; a restarted service needs a fresh IRC
context before encrypted profile use. Plain mode uses the same full digest.
Encrypted authentication failure closes without guessing an error-response key.

Gateway streams additionally bind the account and cached cipher history to one
revocable play session; they never consult the local listener's shared cache.
"""

import asyncio
import hashlib
import hmac
import secrets
import struct
import time
from pathlib import Path

from tmserver.protocol import wire
from tmserver.network.diagnostics import report_failure, write_record
from tmserver.protocol.identities import player_id as game_player_id
from tmserver.network.local_accounts import account_specs, display_name
from tmserver.profiles.location import FileOwner
from tmserver.profiles.packets import answer, checked, checksum
from tmserver.rooms.packets import directory_files

# These are the default two local fixtures. Keep the profile module
# independent of server's import graph; never accept arbitrary production IDs.
SYNTHETIC_ACCOUNTS = account_specs()
DEFAULT_ACCOUNT = next(iter(SYNTHETIC_ACCOUNTS))
MAX_CACHED_CIPHERS = 2  # Per account; the active IRC key is an extra candidate.


class ProfileServer:
    def __init__(self, log_path, game_server=None):
        self.accounts = getattr(game_server, "account_specs", SYNTHETIC_ACCOUNTS)
        self.log_path = Path(log_path)
        self.log_error = None
        self.listener = None
        self.tasks = set()
        self.writers = set()
        self.game_server = game_server
        self.files = directory_files()
        self.account_files = {}
        self.profile_ciphers = {}
        self.statuses = {}
        for key in self.accounts:
            self._account(key)

    def _account(self, account_key):
        if account_key not in self.accounts:
            raise ValueError("unknown profile account")
        if account_key in self.statuses:
            return self.accounts[account_key]
        _, _, player_id = self.accounts[account_key]
        # Synthetic player record; most fields are not mapped yet. The leading
        # 300 matches the value written by TM 0x10114e87; its meaning is unproven.
        player = bytearray(0x3028)
        struct.pack_into("<I", player, 0, 300)
        player[0x3B] = 1  # Existing local test player: Warriors Guild (TM 0x1014c871).
        struct.pack_into("<IQ", player, 4, player_id, game_player_id(account_key.encode("ascii")))
        name = display_name(player_id)[:16]
        player[0x10 : 0x10 + len(name)] = name
        self.account_files[account_key] = {"U/g/TM0DataFile": bytes(player)}
        self.profile_ciphers[account_key] = []
        status = bytearray(0x7C)
        status[3] = 1
        status[4] = 1  # Viewer before the game changes ContentsClass to 2.
        status[6] = 1  # Online, active handle zero.
        self.statuses[account_key] = status
        return self.accounts[account_key]

    def log(self, event, **details):
        if self.log_error is not None:
            return
        try:
            with self.log_path.open("a", encoding="utf-8") as stream:
                write_record(stream, dict(time=time.time(), event=event, **details))
        except OSError as error:
            self.log_error = error
            report_failure("Profile", error)

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

    def _local_accounts(self):
        identities = getattr(self.game_server, "identities", None)
        return {key: spec for key, spec in self.accounts.items()
                if identities is None or not identities.owns(key)}

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

    def _cipher_candidates(self, access=None):
        """At most three candidates per configured authenticated IRC account."""
        for key in self._local_accounts() if access is None else (access.account_key,):
            candidates = []
            if access is not None or self.game_server is not None:
                session = (
                    access.game
                    if access is not None
                    else self.game_server.nicks.get(key.encode("ascii"))
                )
                if access is not None and (
                    session is None or session.closed or session.nick != key.encode("ascii")
                ):
                    session = None
                if session is not None and getattr(session, "cipher", None) is not None:
                    candidates.append((session.cipher, session.iv))
            cached = self.profile_ciphers.get(key, ()) if access is None else access.profile_ciphers
            for cipher, iv in cached:
                if not any(cipher is old and iv == old_iv for old, old_iv in candidates):
                    candidates.append((cipher, iv))
            for cipher, iv in candidates:
                yield key, cipher, iv

    def _remember_cipher(self, key, cipher, iv, access=None):
        # Only nonce-authenticated profile contexts survive IRC reconnects.
        previous = self.profile_ciphers[key] if access is None else access.profile_ciphers
        cached = [(old, old_iv) for old, old_iv in previous if old is not cipher or old_iv != iv]
        contexts = [(cipher, iv)] + cached[: MAX_CACHED_CIPHERS - 1]
        if access is None:
            self.profile_ciphers[key] = contexts
        else:
            access.profile_ciphers = contexts

    def _authenticate(self, request, nonce, encrypted, access=None):
        accounts = self._local_accounts()
        if access is not None:
            access.check()
            accounts = {access.account_key: self._account(access.account_key)}
        expected = {
            key: hashlib.md5(account_id + password + struct.pack("<I", nonce)).digest()
            for key, (account_id, password, _) in accounts.items()
        }
        if not encrypted:
            for key, digest in expected.items():
                if hmac.compare_digest(request[24:40], digest):
                    return key, request, None
        else:
            for key, cipher, iv in self._cipher_candidates(access):
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

    async def handle(self, reader, writer, *, access=None):
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
                if access is not None:
                    access.check()
                self.log("open", wire=opening.hex())
                if opening[0] != 0 or opening[4:6] != b"\x01\0":
                    raise ValueError("invalid open")
                nonce = secrets.randbits(32)
                encrypted = opening[1] == 0
                writer.write(answer(nonce=nonce))
                await writer.drain()
                request = await reader.readexactly(40)
                authenticated = self._authenticate(request, nonce, encrypted, access)
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
                logged = request if access is None else request[:24] + bytes(16)
                self.log("request", account=account_key, wire=logged.hex())
                if request[0] != 2:
                    raise ValueError("unrecognized request (possibly encrypted)")
                size = struct.unpack_from("<I", request, 4)[0]
                if size > 1024 * 1024:
                    raise ValueError("request too large")
                if context is not None:
                    self._remember_cipher(account_key, *context, access=access)
                body = await reader.readexactly(size)
                if access is not None:
                    access.check()
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
            try:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
            finally:
                self.writers.discard(writer)
                self.tasks.discard(task)
