"""Run both local protocol services without launching or controlling the game."""

import argparse
import asyncio
import ipaddress
import math
import os
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tmserver.events.config import load_event_setup
from tmserver.network.local_accounts import MAX_ACCOUNTS, account_specs
from tmserver.rooms.config import load_ordinary_rooms
from tmserver.profiles.service import ProfileServer
from tmserver.network.server import Server
from tmserver.economy.shop_offers import load_shop_offers
from tmserver.rooms.spectators import SpectatorService
from tmserver.storage import open_store

if TYPE_CHECKING:
    from tmserver.network.http import HttpListener

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
            "board requires sixteen or twenty-five tile values from 0 through 18"
        )
    return values


def ipv4(value):
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        raise argparse.ArgumentTypeError("--bind must be a numeric IPv4 address") from None


def port(value):
    number = int(value)
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("ports must be between 0 and 65535")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-port", type=port, default=51241)
    parser.add_argument("--profile-port", type=port, default=51220)
    parser.add_argument(
        "--admin-port", type=port,
        help="enable the read-only admin panel on 127.0.0.1 (requires the admin extra)",
    )
    parser.add_argument("--admin-user", default="admin", help="local operator username")
    parser.add_argument(
        "--admin-bind", type=ipv4,
        help="IPv4 address for the admin panel (default 127.0.0.1); 0.0.0.0 only inside a "
             "container that publishes the port on the host's loopback",
    )
    parser.add_argument(
        "--bind", type=ipv4, default="127.0.0.1",
        help="IPv4 address for the game and profile listeners; 0.0.0.0 for a public server",
    )
    parser.add_argument(
        "--admin-actions", action="store_true",
        help="enable audited awards, event grants and event corrections in the admin panel",
    )
    parser.add_argument(
        "--admin-password-file", type=Path,
        help="operator password file; otherwise read TM_ADMIN_PASSWORD when admin is enabled",
    )
    parser.add_argument(
        "--database-url", default=os.environ.get("TM_DATABASE_URL"),
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
        action=argparse.BooleanOptionalAction,
        default=True,
        help="F18 generated-card rewards and durable collection recovery (default: on)",
    )
    parser.add_argument(
        "--card-prizes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="recovered winner-card transfers with durable collection recovery (default: on)",
    )
    parser.add_argument(
        "--wagers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="stakes, double-up and documented local money payouts (default: on); when off, "
             "a chosen stake is dropped and the round plays without one",
    )
    parser.add_argument(
        "--ratings",
        choices=("preserve", "local-history"),
        default="preserve",
        help="optional rating from the client's offline 32-result ring (server choice); stays "
             "preserve by default because a save that left local-history refuses to start",
    )
    parser.add_argument(
        "--weekly-rankings",
        action="store_true",
        help="track future local results in UTC weeks; requires --ratings local-history",
    )
    parser.add_argument(
        "--departures",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="recovered practice departure rules (default: on); they apply only to rounds "
             "without wagers, card prizes or local-history ratings, which still abort safely",
    )
    parser.add_argument(
        "--spectator-preview",
        action="store_true",
        help="opt-in spectator playback with unverified native pacing",
    )
    parser.add_argument("--events", type=Path, help="event configuration JSON")
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
    if not args.database_url and not args.list_accounts:
        parser.error("set TM_DATABASE_URL or pass --database-url; the server saves to PostgreSQL")
    args.flexible_seats = args.players == "auto"
    if args.weekly_rankings and args.ratings != "local-history":
        parser.error("weekly rankings require --ratings local-history")
    args.players = 3 if args.flexible_seats else int(args.players)
    if not 2 <= args.accounts <= MAX_ACCOUNTS:
        parser.error(f"accounts must be between 2 and {MAX_ACCOUNTS}")
    if args.admin_password_file is not None and args.admin_port is None:
        parser.error("--admin-password-file requires --admin-port")
    if args.admin_bind is not None and args.admin_port is None:
        parser.error("--admin-bind requires --admin-port")
    if args.admin_actions and args.admin_port is None:
        parser.error("--admin-actions requires --admin-port")
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
            f"Tetra Master services ready: game {args.bind}:{services.game_port}, "
            f"profile {args.bind}:{services.profile_port}",
            flush=True,
        )
        print("Gameplay options: " + enabled_options(args), flush=True)
        if services.admin is not None:
            print(f"Admin panel ready: http://127.0.0.1:{services.admin.port}/admin/", flush=True)
            await services.admin.wait(args.seconds)
        elif args.seconds:
            await asyncio.sleep(args.seconds)
        else:
            await asyncio.Event().wait()


def enabled_options(args):
    names = dict(
        wagers=args.wagers, card_prizes=args.card_prizes, cbc=args.allow_cbc,
        departures=args.departures, local_history_ratings=args.ratings == "local-history",
        weekly_rankings=args.weekly_rankings, spectator_preview=args.spectator_preview,
    )
    return ", ".join(name for name, on in names.items() if on) or "none"


@dataclass(frozen=True)
class RunningServices:
    game: Server
    profile: ProfileServer
    game_port: int
    profile_port: int
    admin: "HttpListener | None" = None


def admin_options(args):
    """Load explicit credentials without putting them in arguments or logs."""
    if args.admin_port is None:
        return None
    path = args.admin_password_file
    password = path.read_text(encoding="utf8").rstrip("\r\n") if path else os.environ.get(
        "TM_ADMIN_PASSWORD", ""
    )
    username = args.admin_user
    if not username.strip() or not password or "\n" in password or "\r" in password:
        raise ValueError("Admin requires a username and a nonempty, single-line password")
    try:
        from tmserver.admin.server import serving_admin  # noqa: F401
    except ModuleNotFoundError as exc:
        raise ValueError('Install the admin dependencies with pip install "tmserver[admin]"') from exc
    return dict(
        port=args.admin_port, bind=args.admin_bind or "127.0.0.1",
        username=username, password=password,
        actions_enabled=args.admin_actions,
    )


@asynccontextmanager
async def running_services(args):
    """Own both listeners and their store until the caller has finished."""
    admin = admin_options(args)
    events, tables, progress = load_event_setup(args.events) if args.events else ((), (), "none")
    with ExitStack() as resources:
        args.log_dir.mkdir(parents=True, exist_ok=True)
        store = resources.enter_context(open_store(args.database_url))
        game = Server(
            args.log_dir / "local_traffic.jsonl",
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
        try:
            profile = ProfileServer(args.log_dir / "local_profile_traffic.jsonl", game)
            try:
                game_port = await game.start(args.game_port, host=args.bind)
                profile_port = await profile.start(args.profile_port, host=args.bind)
                async with AsyncExitStack() as listeners:
                    panel = None
                    if admin is not None:
                        from tmserver.admin.server import serving_admin

                        panel = await listeners.enter_async_context(serving_admin(game, **admin))
                    yield RunningServices(game, profile, game_port, profile_port, panel)
            finally:
                await profile.stop()
        finally:
            await game.stop()


def main():
    try:
        asyncio.run(run(arguments()))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
