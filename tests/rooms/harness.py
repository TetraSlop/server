"""Shared setup for room and table integration tests."""

import asyncio
import tempfile
from pathlib import Path

from tests import ROOT
from tests.support.irc import Client
from tests.network.test_session_recovery import Writer
from tests.support.memory_store import MemoryStore
from tmserver.network.server import Server
from tmserver.network.session import Session
from tmserver.rooms.spectators import SpectatorService


def instant_spectators():
    """Enabled spectators with every preview delay set to zero."""
    return SpectatorService(
        enabled=True, bootstrap_delay=0, turn_delay=0, action_delay=0, packet_delay=0
    )


def prepare(case):
    """Give case an empty store and a temporary directory, and register teardown."""
    asyncio.get_running_loop().slow_callback_duration = 1
    case.harness_temp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
    case.store = MemoryStore()
    case.clients = []
    case.addAsyncCleanup(teardown, case)
    return case.store


async def serve(case, *, clients=None, **server_kwargs):
    """Start a Server on a temporary save and log in the first `clients` accounts.

    Sets case.server, case.port and case.clients; teardown runs as a cleanup.
    """
    if not hasattr(case, "harness_temp"):
        prepare(case)
    case.server = Server(store=case.store, **server_kwargs)
    case.port = await case.server.start(0)
    for nick in list(case.server.accounts)[:clients]:
        client = Client()
        case.clients.append(client)
        await client.open(case.port, account=nick)
        case.assertIn(b" 422 ", await client.read())
    case.harness_clients = list(case.clients)
    return case.clients


async def teardown(case):
    """Close the clients, stop case.server, then remove the save directory."""
    temp = case.__dict__.pop("harness_temp", None)
    if temp is None:
        return
    for client in dict.fromkeys(getattr(case, "harness_clients", []) + case.clients):
        await client.close()
    if getattr(case, "server", None) is not None:
        await case.server.stop()
    temp.cleanup()


async def read_notice(case, client, source):
    """Read client's next line, which must be a NOTICE from source; return its payload."""
    line = await client.read()
    case.assertTrue(line.startswith(b":" + source + b"!p@local NOTICE "), line)
    return line.split(b" :", 1)[1]


def local_peer(server, nick):
    """A logged-in Session on a fake transport, for tests that call tables directly."""
    peer = Session(server, None, Writer(), len(server.sessions) + 1)
    peer.nick = nick
    server.ensure_account(nick)
    server.trade.bind(peer, nick.decode("ascii"))
    server.nicks[nick] = peer
    server.sessions.add(peer)
    return peer
