"""Private server process for tools.load_test; never opens an existing save."""

import asyncio
import json
import os
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

from tmserver.economy.service import EconomyService
from tmserver.protocol.identities import player_id
from tmserver.network.local_accounts import display_name
from tmserver.network.server import Server
from tmserver.storage.json import JsonStateStore


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "max_ms": 0}
    return {
        "count": len(ordered),
        **{
            f"p{p}_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))] * 1000, 3)
            for p in (50, 95, 99)
        },
        "max_ms": round(ordered[-1] * 1000, 3),
    }


class MeasuredStore:
    def __init__(self, store):
        self.store = store
        self.samples = []

    def load(self):
        return self.store.load() if self.store is not None else None

    def _call(self, name, *args):
        start = time.perf_counter()
        try:
            if self.store is not None:
                return getattr(self.store, name)(*args)
        finally:
            self.samples.append(time.perf_counter() - start)

    def save(self, snapshot):
        self._call("save", snapshot)

    def save_accounts(self, accounts):
        self._call("save_accounts", accounts)

    def save_changes(self, accounts, services):
        self._call("save_changes", accounts, services)


@contextmanager
def temporary_store(backend, directory):
    if backend != "postgres":
        yield JsonStateStore(directory / "state.json") if backend == "json" else None
        return
    from tmserver.storage.database import upgrade
    from tmserver.storage.postgres import PostgresStateStore, database_engine

    # Deliberately separate from TM_DATABASE_URL. Only this random database is
    # touched; the supplied URL's database is used for administration alone.
    url = os.environ.get("TM_LOAD_DATABASE_URL")
    if not url:
        raise ValueError("PostgreSQL load tests require TM_LOAD_DATABASE_URL")
    name = "tm_load_" + uuid4().hex
    admin = database_engine(url).execution_options(isolation_level="AUTOCOMMIT")
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created = True
        isolated = admin.url.set(database=name)
        upgrade(isolated)
        with PostgresStateStore(isolated) as store:
            yield store
    finally:
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def emit(value):
    print(json.dumps(value), flush=True)


async def control():
    line = await asyncio.to_thread(sys.stdin.readline)
    if not line:
        raise EOFError("load controller disconnected")
    return json.loads(line)


async def serve(config):
    directory = Path(config["directory"])
    players = config["players"]
    with ExitStack() as resources:
        store = resources.enter_context(temporary_store(config["backend"], directory))
        measured = MeasuredStore(store)
        game = Server(store=measured, account_count=max(2, players))
        actors = []
        # Build fixture data once, outside measured requests and before admission.
        for index, nick in enumerate(game.accounts, 1):
            key = nick.decode()
            game.economy.provision(
                key, nn=player_id(nick), player_id=index, name=display_name(index)
            )
            actors.append(
                {
                    "nick": key,
                    "nn": player_id(nick),
                    "name": display_name(index).hex(),
                    "card": game.economy.inventory(key)[0].reduced(),
                }
            )
        for index in range(max(0, config["population"] - len(actors))):
            identity = index + 0x100000
            game.economy.provision(
                f"dormant-{index}", nn=identity, player_id=identity, name=f"Dormant{index}".encode()
            )
        game.account_keys = set(game.economy.snapshot()["accounts"])
        game.persist()
        port = await game.start(0)
        emit({"status": "ready", "port": port, "actors": actors[:players]})
        lag = []
        sampling = None

        async def sample_lag():
            loop = asyncio.get_running_loop()
            while True:
                expected = loop.time() + 0.01
                await asyncio.sleep(0.01)
                lag.append(max(0, loop.time() - expected))

        try:
            message = await control()
            if message["command"] != "start":
                raise ValueError("expected measurement start")
            measured.samples.clear()
            cpu_start = time.process_time()
            start = time.perf_counter()
            sampling = asyncio.create_task(sample_lag())
            emit({"status": "measuring"})
            message = await control()
            if message["command"] != "stop":
                raise ValueError("expected measurement stop")
            elapsed = time.perf_counter() - start
            cpu = time.process_time() - cpu_start
            sampling.cancel()
            await asyncio.gather(sampling, return_exceptions=True)
            measured_saves = distribution(measured.samples)
            await game.stop()
            restored = game.economy if store is None else EconomyService.restore(store.load())
            for key, expected in message["expected"].items():
                if "option" in expected and restored.profile_bytes(key)[0xB0] != expected["option"]:
                    raise AssertionError(f"profile update missing for {key}")
                if "deck" in expected and restored.inventory(key)[0].deck != expected["deck"]:
                    raise AssertionError(f"inventory update missing for {key}")
            emit(
                {
                    "status": "complete",
                    "server_wall_seconds": round(elapsed, 3),
                    "server_cpu_seconds": round(cpu, 3),
                    "save_latency": measured_saves,
                    "event_loop_lag": distribution(lag),
                    "verified_accounts": len(message["expected"]),
                    "durable_reload_verified": store is not None,
                    "save_bytes": (
                        (directory / "state.json").stat().st_size
                        if config["backend"] == "json"
                        else None
                    ),
                }
            )
        finally:
            if sampling is not None:
                sampling.cancel()
                await asyncio.gather(sampling, return_exceptions=True)
            await game.stop()


if __name__ == "__main__":
    asyncio.run(serve(json.loads(sys.stdin.readline())))
