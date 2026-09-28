"""Offline account inspection, rewards and event administration for a selected save."""

import argparse
import json
from contextlib import ExitStack
from pathlib import Path

from tmserver.admin.queries import account_summary
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.claim_transactions import event_transaction, prize_transaction
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.cards.model import Card
from tmserver.events.cards import EventCardService
from tmserver.events.config import load_event_setup
from tmserver.events.results import EventResults
from tmserver.events.service import EventHost
from tmserver.economy.prize_center import PrizeService
from tmserver.storage.recovery import preview_recovery
from tmserver.storage.validation import validate_snapshot
from tmserver.storage.json import JsonStateStore, writer_lease


def integers(count):
    def parse(value):
        try:
            parts = tuple(int(part) for part in value.split(","))
        except ValueError as error:
            raise argparse.ArgumentTypeError("expected comma-separated integers") from error
        if len(parts) != count:
            raise argparse.ArgumentTypeError(f"expected {count} comma-separated integers")
        return parts

    return parse


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    storage = parser.add_mutually_exclusive_group(required=True)
    storage.add_argument("--state", type=Path, help="existing JSON save; no default path")
    storage.add_argument("--database-url", help="explicit PostgreSQL connection URL")
    parser.add_argument("--events", type=Path, help="same event configuration used by the server")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("accounts", help="list exact saved account keys")
    commands.add_parser("recovery", help="preview startup refunds and returned cards without saving")
    account = commands.add_parser("account", help="inspect saved balances, progress and rewards")
    account.add_argument("--account-key", required=True)
    prize = commands.add_parser("prize-award", help="queue a Prize Center award")
    prize.add_argument("--account-key", required=True)
    prize.add_argument("--id", required=True, help="stable award ID for retries")
    for field in ("top30", "lucky", "rookies"):
        prize.add_argument("--" + field, type=int, default=0)
    prize.add_argument("--lucky-cards", type=integers(3), default=(65535,) * 3, metavar="A,B,C")
    grant = commands.add_parser("event-grant", help="queue explicit event cards or money")
    grant.add_argument("--account-key", required=True)
    grant.add_argument("--event", required=True)
    grant.add_argument("--id", required=True, help="stable grant ID for retries")
    grant.add_argument("--money", type=int, default=0)
    grant.add_argument(
        "--card",
        type=integers(6),
        action="append",
        default=[],
        metavar="MODEL,ATTACK,TYPE,PHYSICAL,MAGICAL,ARROWS",
    )
    eligibility = commands.add_parser("event-eligibility", help="set an existing member's status")
    eligibility.add_argument("--account-key", required=True)
    eligibility.add_argument("--event", required=True)
    eligibility.add_argument(
        "--value", type=int, required=True, help="signed integer; negative excludes"
    )
    progress = commands.add_parser("event-progress", help="record explicit local event progress")
    progress.add_argument("--account-key", required=True)
    progress.add_argument("--event", required=True)
    progress.add_argument("--id", required=True, help="stable update ID for retries")
    progress.add_argument("--steps", type=int, default=0, help="nonnegative additional steps")
    progress.add_argument(
        "--completed-missions", type=int, default=0, help="additional mission bitmask"
    )
    return parser.parse_args(argv)


class AdminState:
    """Claim transaction context with no listeners, timers or recovery side effects."""

    def __init__(self, store, snapshot, configs):
        self.store = store
        self.economy = validate_snapshot(snapshot, event_configs=configs)
        self.saved_snapshot = snapshot
        saved_events = {row["id"] for row in (snapshot.get("events") or {}).get("events", ())}
        if {config.id for config in configs} != saved_events:
            raise ValueError("--events must contain exactly the saved event IDs")
        self.prize = PrizeService.restore(self.economy, snapshot.get("prize"))
        self.events = EventHost.restore(self.economy, configs, snapshot.get("events"))
        self.event_cards = EventCardService.restore(
            self.economy, self.events, snapshot.get("event_cards")
        )
        self.event_results = EventResults.restore(snapshot.get("event_results"))
        self.auction_ledger = AuctionEconomy.restore(self.economy, snapshot.get("auction"))
        self.match_economy = MatchEconomy.restore(self.economy, snapshot.get("match_economy"))
        self.result_rewards = ResultRewardService.restore(
            self.economy, self.auction_ledger, snapshot.get("result_rewards")
        )
        self.saved_journals = {
            name: getattr(self, name).snapshot()
            for name in ("prize", "events", "event_cards", "event_results")
        }

    def persist_changes(self, accounts, journals):
        # Keep original unrelated journal payloads, including absence/null in
        # older saves. The ordinary claim contexts may supply empty defaults.
        changed = {
            name: value for name, value in journals.items() if value != self.saved_journals[name]
        }
        self.store.save_changes(accounts, changed)
        self.saved_journals.update(changed)

    def apply(self, args):
        if args.command == "recovery":
            return preview_recovery(self.saved_snapshot)
        if args.command == "accounts":
            return [
                dict(
                    account_key=key,
                    player_id=row["player_id"],
                    name=bytes.fromhex(row["name_hex"]).decode("latin1"),
                    guild=row["guild"],
                )
                for key, row in sorted(self.economy.snapshot()["accounts"].items())
            ]
        key = args.account_key
        self.economy.account_view(key)  # Exact saved key; never provision an account.
        if args.command == "account":
            return self.account_summary(key)
        result = {"account_key": key}
        if args.command == "prize-award":
            with prize_transaction(self, key) as prize:
                changed = prize.grant_award(
                    key,
                    args.id,
                    top30=args.top30,
                    lucky=args.lucky,
                    rookies=args.rookies,
                    lucky_cards=args.lucky_cards,
                )
            return dict(result, id=args.id, status="queued" if changed else "already-recorded")
        result["event_id"] = args.event
        with event_transaction(self, (key,)) as state:
            if args.command == "event-grant":
                cards = tuple(self.card(values) for values in args.card)
                changed = state.cards.grant(key, args.event, args.id, cards=cards, money=args.money)
                result.update(id=args.id, status="queued" if changed else "already-recorded")
            elif args.command == "event-progress":
                changed = state.events.update_progress(
                    key,
                    args.event,
                    args.id,
                    steps_delta=args.steps,
                    completed_missions=args.completed_missions,
                )
                result.update(id=args.id, status="recorded" if changed else "already-recorded")
            else:
                member = state.events.member(key, args.event)
                state.events.set_eligibility(key, args.event, args.value)
                result.update(
                    value=args.value,
                    status="unchanged" if member.eligibility == args.value else "updated",
                )
        return result

    def account_summary(self, key):
        """Describe saved claims without admitting, collecting or recovering them."""
        return account_summary(self, key)

    def card(self, values):
        model, attack, kind, physical, magical, arrows = values
        catalog = self.economy.catalog
        if not 0 <= model < len(catalog.cards):
            raise ValueError("unknown card model")
        points = catalog.points(model, attack, physical, magical)
        return catalog.validate(Card(model, attack, kind, physical, magical, points, arrows))


def postgres_store(url):
    try:
        from sqlalchemy.exc import SQLAlchemyError
        from tmserver.storage.postgres import PostgresStateStore
    except ImportError:
        raise ValueError(
            'PostgreSQL administration requires installation with ".[postgres]"'
        ) from None
    try:
        return PostgresStateStore(url)
    except SQLAlchemyError:
        raise OSError("cannot open database; check its URL, connectivity and schema") from None


def execute(args):
    with ExitStack() as resources:
        if args.state is not None:
            resources.enter_context(writer_lease(args.state))
            store = JsonStateStore(args.state)
        else:
            store = resources.enter_context(postgres_store(args.database_url))
        snapshot = store.load()
        if snapshot is None:
            raise ValueError("administration requires an existing saved economy")
        configs = load_event_setup(args.events)[0] if args.events else ()
        return AdminState(store, snapshot, configs).apply(args)


def cli(argv=None):
    try:
        result = execute(arguments(argv))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    cli()
