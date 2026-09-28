"""Encrypted closed-loop load test against an isolated child server.

The 8-operation mix is six heartbeat requests, one saved option and one owned-card
deck edit. Client encryption runs outside the measured server process. This does
not claim full-match throughput or historical production capacity.
"""

import argparse
import asyncio
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from tests.profiles.test_options_service import VALUES
from tests.profiles.test_options_service import request as options_request
from tests.network.test_server import Client
from tmserver.protocol import wire
from tmserver.economy.service import named, parse_named
from tmserver.protocol.identities import SHOP_RAW, service_nick
from tmserver.network.local_accounts import MAX_ACCOUNTS
from tools.load_server import distribution


class LoadClient(Client):
    async def read(self):
        raw = await asyncio.wait_for(self.reader.readline(), 60)
        if not raw:
            raise ConnectionError("server disconnected")
        plain = wire.crypt64(self.cipher, self.iv, raw) if self.cipher else raw
        if not plain.endswith(b"\r\n") or wire.checksum_chars(plain[:-6]) != plain[-6:-2]:
            raise ValueError("invalid encrypted response")
        return plain[:-6]

    async def request(self, payload=None):
        if payload is not None:
            await self.send(b"NOTICE " + service_nick(SHOP_RAW) + b" :" + payload)
        await self.send(b"PING :load-barrier")
        replies = []
        for _ in range(128):
            line = await self.read()
            if line == b"PONG load-barrier":
                return replies
            if b" NOTICE " not in line or b" :" not in line:
                raise ValueError(f"unexpected load response {line!r}")
            replies.append(line.split(b" :", 1)[1])
        raise ValueError("reply bound exceeded")


async def run(args):
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="tm-load-") as folder:
        with (Path(folder) / "server-errors.txt").open("w+") as errors:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-B",
                "-m",
                "tools.load_server",
                cwd=root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            clients = []

            async def send(value):
                process.stdin.write((json.dumps(value) + "\n").encode())
                await process.stdin.drain()

            async def receive():
                line = await asyncio.wait_for(process.stdout.readline(), 180)
                if not line:
                    errors.seek(0)
                    raise RuntimeError("load server exited: " + errors.read()[-5000:])
                return json.loads(line)

            try:
                await send(
                    {
                        "directory": folder,
                        "backend": args.backend,
                        "players": args.players,
                        "population": args.population,
                    }
                )
                ready = await receive()
                if ready["status"] != "ready":
                    raise ValueError(ready)
                print(
                    f"Prepared {args.population} accounts; connecting {args.players} clients.",
                    file=sys.stderr,
                    flush=True,
                )
                gate = asyncio.Semaphore(args.login_concurrency)
                login_samples = []

                async def connect(actor):
                    async with gate:
                        client = LoadClient()
                        clients.append(client)
                        start = time.perf_counter()
                        await client.open(ready["port"], account=actor["nick"].encode())
                        if b" 422 " not in await client.read():
                            raise ValueError("login rejected")
                        login_samples.append(time.perf_counter() - start)
                        replies = await client.request(
                            named(
                                0xB0,
                                0,
                                0,
                                f'@ShReq=/NN={actor["nn"]:016X}/HID=0/Dm=0/Vol=0/CN={actor["name"]}',
                            )
                        )
                        actor["session"] = int(parse_named(replies[1])[3]["Init"]["N"])
                        return client, actor

                actors = await asyncio.gather(*(connect(actor) for actor in ready["actors"]))
                await send({"command": "start"})
                if (await receive())["status"] != "measuring":
                    raise ValueError("measurement did not start")
                print(
                    "Measuring encrypted requests and durable writes.", file=sys.stderr, flush=True
                )
                start = time.perf_counter()
                deadline = start + args.seconds
                samples = {kind: [] for kind in ("heartbeat", "options", "inventory")}
                expected = {}
                failures = []

                async def exercise(client, actor):
                    index = 0
                    option = 0
                    deck = 0
                    state = expected.setdefault(actor["nick"], {})
                    try:
                        while time.perf_counter() < deadline:
                            payload = None
                            kind = "heartbeat"
                            if index % 8 == 0:
                                kind = "options"
                                option ^= 1
                                payload = options_request(
                                    actor["nn"],
                                    values={**VALUES, "Ar": option},
                                    name=bytes.fromhex(actor["name"]),
                                )
                            elif index % 8 == 4:
                                kind = "inventory"
                                deck ^= 1
                                values = "|".join(map(str, (*actor["card"], deck)))
                                payload = named(
                                    0xB2, 0x22, actor["session"], f"@Decks=/C=1@0=/D={values}"
                                )
                            tick = time.perf_counter()
                            replies = await client.request(payload)
                            latency = time.perf_counter() - tick
                            if kind == "options":
                                if replies != [named(0x85, 0, 0, "@OptAns=/Ans=1")]:
                                    raise ValueError(
                                        f"unexpected option acknowledgement {replies!r}"
                                    )
                                state["option"] = option
                            elif kind == "inventory":
                                if replies:
                                    raise ValueError(f"unexpected deck-edit response {replies!r}")
                                state["deck"] = deck
                            samples[kind].append(latency)
                            index += 1
                            if args.think_ms:
                                await asyncio.sleep(args.think_ms / 1000)
                    except (OSError, ValueError, TimeoutError) as exc:
                        failures.append({"account": actor["nick"], "reason": str(exc)})

                await asyncio.gather(*(exercise(client, actor) for client, actor in actors))
                elapsed = time.perf_counter() - start
                await send({"command": "stop", "expected": expected})
                server = await receive()
                if server["status"] != "complete":
                    raise ValueError(server)
                await asyncio.wait_for(process.wait(), 30)
                if process.returncode:
                    raise RuntimeError("load server cleanup failed")
                total = sum(map(len, samples.values()))
                report = {
                    "measured_at": datetime.now(UTC).isoformat(),
                    "backend": args.backend,
                    "players": args.players,
                    "population": args.population,
                    "requested_seconds": args.seconds,
                    "actual_seconds": round(elapsed, 3),
                    "think_ms": args.think_ms,
                    "login_concurrency": args.login_concurrency,
                    "operations": total,
                    "operations_per_second": round(total / elapsed, 2),
                    "latency": {kind: distribution(values) for kind, values in samples.items()},
                    "login_latency": distribution(login_samples),
                    "failures": failures,
                    "server": server,
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "workload": "six heartbeats, one option save, one owned-card deck edit per eight operations",
                    "limits": "Closed-loop loopback clients; no think time by default; no active games; traffic-file logging disabled; bounded synthetic account pool; population includes dormant accounts.",
                }
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
                print(json.dumps(report))
                return bool(failures)
            finally:
                for client in clients:
                    if hasattr(client, "writer"):
                        try:
                            await client.close()
                        except ConnectionError:
                            pass
                if process.returncode is None:
                    process.stdin.close()
                    try:
                        await asyncio.wait_for(process.wait(), 30)
                    except TimeoutError:
                        process.kill()
                        await process.wait()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("memory", "json", "postgres"), default="memory")
    parser.add_argument("--players", type=int, default=8)
    parser.add_argument("--population", type=int)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--think-ms", type=float, default=0)
    parser.add_argument("--login-concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path("results/load.json"))
    args = parser.parse_args()
    if args.population is None:
        args.population = max(args.players, 2)
    if (
        not 1 <= args.players <= MAX_ACCOUNTS
        or not max(2, args.players) <= args.population <= 10000
    ):
        parser.error(f"players must be 1..{MAX_ACCOUNTS}, population max(2,players)..10000")
    if (
        not math.isfinite(args.seconds)
        or not 0 < args.seconds <= 300
        or not math.isfinite(args.think_ms)
        or args.think_ms < 0
    ):
        parser.error("seconds must be finite in (0,300]; think-ms must be finite and nonnegative")
    if not 1 <= args.login_concurrency <= 16:
        parser.error("login concurrency must be 1..16")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(arguments())))
