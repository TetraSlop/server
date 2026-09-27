"""Start the packaged services and user-operated native clients together."""

import argparse
import asyncio
import json
import os
import subprocess
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tmserver.economy_service import EconomyService
from tmserver.local_accounts import NAMES
from tmserver.run_server import arguments as server_arguments
from tmserver.run_server import running_services
from tmserver.state_store import JsonStateStore


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--loader-dir", type=Path, help="sibling tmloader checkout by default")
    parser.add_argument("--state", type=Path, help="default: PROJECT/var/local_state.json")
    parser.add_argument("--clients", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument(
        "--rewards", action="store_true", help="enable local card prizes, CBC and wagers"
    )
    parser.add_argument(
        "--check", action="store_true", help="read-only setup check; no listeners or game"
    )
    args = parser.parse_args(argv)
    args.project_dir = args.project_dir.resolve()
    args.loader_dir = (args.loader_dir or args.project_dir.parent / "tmloader").resolve()
    args.state = (args.state or args.project_dir / "var/local_state.json").resolve()
    args.loader = args.loader_dir / "build/RelWithDebInfo/tmloader.exe"
    args.config = args.loader_dir / "tmloader.ini"
    return args


def child_environment():
    return {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"TM_DATABASE_URL", "TM_TEST_DATABASE_URL", "TM_LOAD_DATABASE_URL", "__COMPAT_LAYER"}
        and not key.startswith("TM_PROBE_")
    }


def hidden_process():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


async def preflight(args):
    for path in (args.loader, args.config, args.project_dir / "examples/auto_rooms.example.json"):
        if not path.is_file():
            raise ValueError(f"Missing setup file: {path}. See docs/LOCAL_PLAY.md.")
    if args.state.exists():
        EconomyService.restore(JsonStateStore(args.state).load())
    process = await asyncio.create_subprocess_exec(
        str(args.loader),
        "--config",
        str(args.config),
        "--check",
        cwd=args.loader_dir,
        env=child_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **hidden_process(),
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 30)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise ValueError(output.decode(errors="replace").strip() or "Loader setup check failed")


@contextmanager
def session_lock(state):
    """Prevent two launchers from running independent economies on the same save."""
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_name(state.name + ".play.lock").open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OSError(
                "Another Play session is using this save; close its game windows first."
            ) from exc
        yield


def service_settings(args, log_dir):
    command = [
        "--state",
        str(args.state),
        "--log-dir",
        str(log_dir),
        "--game-port",
        "0",
        "--profile-port",
        "0",
        "--accounts",
        "3",
        "--rooms",
        str(args.project_dir / "examples/auto_rooms.example.json"),
    ]
    if args.rewards:
        command += ["--card-prizes", "--allow-cbc", "--wagers"]
    return server_arguments(command)


def client_command(args, services, player, session):
    if services.profile_port == 51241:
        raise ValueError("Selected profile port conflicts with the legacy game port; retry Play.")
    return [
        str(args.loader),
        "--config",
        str(args.config),
        "--server",
        "127.0.0.1",
        "--game-port",
        str(services.game_port),
        "--profile-port",
        str(services.profile_port),
        "--pol-id",
        NAMES[player - 1],
        "--password",
        "tm-local-test",
        "--log",
        f"logs/{session}/player-{player}.log",
    ]


async def play_clients(args, services, session, log_dir):
    """Keep services alive until every owned client exits; retire only our children."""
    children = []
    with ExitStack() as outputs:
        try:
            for player in range(1, args.clients + 1):
                output = outputs.enter_context((log_dir / f"loader-{player}.log").open("wb"))
                child = await asyncio.create_subprocess_exec(
                    *client_command(args, services, player, session),
                    cwd=args.loader_dir,
                    env=child_environment(),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    **hidden_process(),
                )
                children.append(child)
            return await asyncio.gather(*(child.wait() for child in children))
        finally:
            for child in children:
                if child.returncode is None:
                    child.kill()
                    await child.wait()


async def run(args):
    await preflight(args)
    if args.check:
        print("Setup checks passed. No game launched or services started.")
        print(f"Loader: {args.loader}\nSave: {args.state}")
        return 0
    with session_lock(args.state):
        session = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        log_dir = args.project_dir / "var/sessions" / session
        settings = service_settings(args, log_dir)
        async with running_services(settings) as services:
            metadata = {
                "session": session,
                "game_port": services.game_port,
                "profile_port": services.profile_port,
                "clients": args.clients,
                "accounts": list(NAMES[: args.clients]),
                "state": str(args.state),
                "loader": str(args.loader),
                "rewards": args.rewards,
                "native_checks": "Awaiting user observation; process exit is not a UI pass.",
            }
            (log_dir / "session.json").write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf8"
            )
            print(
                f"Starting {args.clients} Tetra Master window(s). Close all game windows to stop.",
                flush=True,
            )
            print(f"Session: {session}\nSave: {args.state}\nLogs: {log_dir}", flush=True)
            print("Use Computer for CPU play; Open Tables for a human match.", flush=True)
            codes = await play_clients(args, services, session, log_dir)
            (log_dir / "exits.json").write_text(json.dumps(codes) + "\n", encoding="utf8")
        if any(codes):
            raise RuntimeError(
                f"A game process exited with an error ({codes}). See {log_dir} and runtime/logs/{session}."
            )
        print("Game windows closed; services stopped and save retained.")
        return 0


def main():
    try:
        raise SystemExit(asyncio.run(run(arguments())))
    except KeyboardInterrupt:
        print("Play session stopped.")
        raise SystemExit(130) from None
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"Could not complete the Play session: {exc}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
