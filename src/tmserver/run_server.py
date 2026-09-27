"""Run both local protocol services without launching or controlling the game."""

import argparse
import asyncio
import math
import os
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from tmserver.event_configuration import load_event_setup
from tmserver.local_accounts import MAX_ACCOUNTS, account_specs
from tmserver.ordinary_rooms import load_ordinary_rooms
from tmserver.profile_service import ProfileServer
from tmserver.server import Server
from tmserver.shop_offers import load_shop_offers
from tmserver.spectator_service import SpectatorService

DIRECTORY = Path.cwd() / "var"


def board(value):
    try:
        values = tuple(int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "board requires sixteen or twenty-five comma-separated integers"
        ) from exc
    if len(values) not in (16, 25) or any(v not in range(19) for v in values):
        raise argparse.ArgumentTypeError(
            "board requires sixteen or twenty-five tile values from0 through18"
        )
    return values


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-port", type=int, default=51241)
    parser.add_argument("--profile-port", type=int, default=51220)
    storage = parser.add_mutually_exclusive_group()
    storage.add_argument("--state", type=Path, help="JSON save (default: var/local_state.json)")
    storage.add_argument(
        "--database-url",
        help="PostgreSQL URL; prefer TM_DATABASE_URL to keep credentials out of command history",
    )
    parser.add_argument("--log-dir", type=Path, default=DIRECTORY)
    parser.add_argument("--mode", choices=("computer", "versus"), default="computer")
    parser.add_argument(
        "--accounts",
        type=int,
        default=2,
        metavar="2..128",
        help="size of synthetic local account pool (default:2)",
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="print enabled local login IDs and exit without starting services",
    )
    parser.add_argument(
        "--players",
        choices=("2", "3", "auto"),
        default="2",
        help="versus seats; auto freezes two or three seated humans when Start is offered",
    )
    parser.add_argument(
        "--board",
        type=board,
        help="explicit F layout in row order:16 cells for two players,25 for three",
    )
    parser.add_argument(
        "--allow-cbc",
        action="store_true",
        help="enable F18 generated-card rewards and durable collection recovery",
    )
    parser.add_argument(
        "--card-prizes",
        action="store_true",
        help="enable recovered winner-card transfers with durable collection recovery",
    )
    parser.add_argument(
        "--wagers",
        action="store_true",
        help="enable configured stakes, double-up and documented local money payouts",
    )
    parser.add_argument(
        "--ratings",
        choices=("preserve", "local-history"),
        default="preserve",
        help="optional original offline32-result rating as explicit local policy",
    )
    parser.add_argument(
        "--weekly-rankings",
        action="store_true",
        help="track future local results in UTC weeks; requires --ratings local-history",
    )
    parser.add_argument(
        "--departures",
        action="store_true",
        help="opt-in recovered practice departure rules; economic/rated rounds still abort safely",
    )
    parser.add_argument(
        "--spectator-preview",
        action="store_true",
        help="opt-in spectator playback with unverified native pacing",
    )
    parser.add_argument("--events", type=Path, help="explicit local event configuration JSON")
    parser.add_argument(
        "--rooms",
        type=Path,
        help="additional ordinary rooms with independent CPU or human tables",
    )
    parser.add_argument(
        "--shop-offers",
        type=Path,
        help="explicit personalized/special shop catalogue availability JSON",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=0,
        help="optional bounded run; zero waits until interrupted",
    )
    args = parser.parse_args(argv)
    args.database_url = args.database_url or (
        None if args.state else os.environ.get("TM_DATABASE_URL")
    )
    if not args.database_url and args.state is None:
        args.state = DIRECTORY / "local_state.json"
    args.flexible_seats = args.players == "auto"
    if args.weekly_rankings and args.ratings != "local-history":
        parser.error("weekly rankings require --ratings local-history")
    args.players = 3 if args.flexible_seats else int(args.players)
    if not 2 <= args.accounts <= MAX_ACCOUNTS:
        parser.error(f"accounts must be between2 and{MAX_ACCOUNTS}")
    if not all(0 <= port <= 65535 for port in (args.game_port, args.profile_port)):
        parser.error("ports must be between0 and65535")
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("seconds must be finite and nonnegative")
    if args.players == 3:
        if args.mode != "versus":
            parser.error("three players require --mode versus")
        args.accounts = max(args.accounts, 3)
    if args.board is not None:
        if args.flexible_seats:
            parser.error("automatic seats require a generated board; omit --board")
        if len(args.board) != (args.players + 2) ** 2:
            parser.error("board dimensions must match --players")
        if 18 in args.board:
            if not args.allow_cbc:
                parser.error("F18 requires --allow-cbc")
    return args


async def run(args):
    if args.list_accounts:
        for nickname, (login, _, character) in account_specs(args.accounts).items():
            print(f"{character:3}  {login.decode('ascii')}  {nickname}")
        return
    async with running_services(args) as services:
        print(
            f"Tetra Master services ready: game 127.0.0.1:{services.game_port}, "
            f"profile 127.0.0.1:{services.profile_port}",
            flush=True,
        )
        if args.seconds:
            await asyncio.sleep(args.seconds)
        else:
            await asyncio.Event().wait()


@dataclass(frozen=True)
class RunningServices:
    game: Server
    profile: ProfileServer
    game_port: int
    profile_port: int


@asynccontextmanager
async def running_services(args):
    """Own both listeners and their store until the caller has finished."""
    args.log_dir.mkdir(parents=True, exist_ok=True)
    events, tables, progress = load_event_setup(args.events) if args.events else ((), (), "none")
    with ExitStack() as resources:
        store = None
        if args.database_url:
            from tmserver.postgres_store import PostgresStateStore

            store = resources.enter_context(PostgresStateStore(args.database_url))
        async with serving(args, events, tables, progress, store) as services:
            yield services


@asynccontextmanager
async def serving(args, events, tables, progress, store):
    game = Server(
        args.log_dir / "local_traffic.jsonl",
        args.state,
        store=store,
        match_mode=args.mode,
        match_player_count=args.players,
        match_start_fields=args.board,
        allow_cbc=args.allow_cbc,
        account_count=args.accounts,
        flexible_seats=args.flexible_seats,
        card_prizes=args.card_prizes,
        allow_wagers=args.wagers,
        rating_mode=args.ratings,
        weekly_rankings=args.weekly_rankings,
        allow_departures=args.departures,
        ordinary_rooms=load_ordinary_rooms(args.rooms) if args.rooms else (),
        event_configs=events,
        event_tables=tables,
        event_progress_policy=progress,
        shop_offers=load_shop_offers(args.shop_offers) if args.shop_offers else None,
        spectator=SpectatorService(enabled=args.spectator_preview),
    )
    profile = ProfileServer(args.log_dir / "local_profile_traffic.jsonl", game)
    try:
        game_port = await game.start(args.game_port)
        profile_port = await profile.start(args.profile_port)
        yield RunningServices(game, profile, game_port, profile_port)
    finally:
        await profile.stop()
        await game.stop()


def main():
    try:
        asyncio.run(run(arguments()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
