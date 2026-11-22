"""Offline account inspection, rewards and event administration for a selected save."""

import argparse
import json
from contextlib import ExitStack
from pathlib import Path

from tmserver.admin.actions import apply_event_grant, apply_event_member, apply_prize_award
from tmserver.admin.audit import (
    AdminAudit,
    EventEligibilityRequest,
    EventGrantCard,
    EventGrantRequest,
    EventProgressRequest,
    PrizeAwardRequest,
)
from tmserver.admin.queries import account_summary
from tmserver.economy.auctions.ledger import AuctionEconomy
from tmserver.economy.match_ledger import MatchEconomy
from tmserver.economy.prize_center import PrizeService
from tmserver.economy.result_rewards import ResultRewardService
from tmserver.events.cards import EventCardService
from tmserver.events.config import load_event_setup
from tmserver.events.service import EventHost
from tmserver.storage.json import JsonStateStore, writer_lease
from tmserver.storage.validation import validate_snapshot

CARD_FIELDS = ("model", "attack", "kind", "physical", "magical", "arrows")


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


def prize_request(args, **fields):
    return PrizeAwardRequest(
        **fields, award_id=args.id, top30=args.top30, lucky=args.lucky, rookies=args.rookies,
        lucky_cards=args.lucky_cards,
    )


def grant_request(args, **fields):
    return EventGrantRequest(
        **fields, event_id=args.event, grant_id=args.id, money=args.money,
        cards=tuple(EventGrantCard(**dict(zip(CARD_FIELDS, card))) for card in args.card),
    )


def eligibility_request(args, **fields):
    return EventEligibilityRequest(**fields, event_id=args.event, value=args.value)


def progress_request(args, **fields):
    return EventProgressRequest(
        **fields, event_id=args.event, update_id=args.id, steps_delta=args.steps,
        completed_missions=args.completed_missions,
    )


# Command name: (help, request builder, audited action).
ACTIONS = {
    "prize-award": ("queue an audited Prize Center award", prize_request, apply_prize_award),
    "event-grant": ("queue audited event cards or money", grant_request, apply_event_grant),
    "event-eligibility": ("set an event member's eligibility", eligibility_request,
                          apply_event_member),
    "event-progress": ("add trusted event progress", progress_request, apply_event_member),
}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    storage = parser.add_mutually_exclusive_group(required=True)
    storage.add_argument("--state", type=Path, help="existing JSON save; no default path")
    storage.add_argument("--database-url", help="explicit PostgreSQL connection URL")
    parser.add_argument("--events", type=Path, help="same event configuration used by the server")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("accounts", help="list exact saved account keys")
    account = commands.add_parser("account", help="inspect saved balances, progress and rewards")
    account.add_argument("--account-key", required=True)
    receipt = commands.add_parser("operator-receipt", help="read an exact operator request receipt")
    receipt.add_argument("--request-id", required=True)
    for command, (help_text, _, _) in ACTIONS.items():
        action = commands.add_parser(command, help=help_text)
        action.add_argument("--account-key", required=True)
        action.add_argument("--request-id", required=True, help="stable operator request ID")
        action.add_argument("--operator", required=True, help="operator recorded in the receipt")
        action.add_argument("--reason", required=True)
        if command != "prize-award":
            action.add_argument("--event", required=True)
        if command != "event-eligibility":
            action.add_argument("--id", required=True, help="stable reference for retries")
        if command == "prize-award":
            for field in ("top30", "lucky", "rookies"):
                action.add_argument("--" + field, type=int, default=0)
            action.add_argument(
                "--lucky-cards", type=integers(3), default=(65535,) * 3, metavar="A,B,C"
            )
        elif command == "event-grant":
            action.add_argument("--money", type=int, default=0)
            action.add_argument(
                "--card", type=integers(6), action="append", default=[],
                metavar="MODEL,ATTACK,TYPE,PHYSICAL,MAGICAL,ARROWS",
            )
        elif command == "event-eligibility":
            action.add_argument(
                "--value", type=int, required=True, help="signed integer; negative excludes"
            )
        else:
            action.add_argument("--steps", type=int, default=0, help="nonnegative added steps")
            action.add_argument(
                "--completed-missions", type=int, default=0, help="additional mission bitmask"
            )
    return parser.parse_args(argv)


class AdminState:
    """Audited action host with no listeners, timers or recovery side effects."""

    def __init__(self, store, snapshot, configs):
        self.store = store
        self.storage_failed = None
        self.economy = validate_snapshot(snapshot, event_configs=configs)
        saved_events = {row["id"] for row in (snapshot.get("events") or {}).get("events", ())}
        if {config.id for config in configs} != saved_events:
            raise ValueError("--events must contain exactly the saved event IDs")
        self.prize = PrizeService.restore(self.economy, snapshot.get("prize"))
        self.events = EventHost.restore(self.economy, configs, snapshot.get("events"))
        self.event_cards = EventCardService.restore(
            self.economy, self.events, snapshot.get("event_cards")
        )
        self.admin_audit = AdminAudit.restore(
            self.economy, self.prize, snapshot.get("admin_audit"), event_cards=self.event_cards,
        )
        self.auction_ledger = AuctionEconomy.restore(self.economy, snapshot.get("auction"))
        self.match_economy = MatchEconomy.restore(self.economy, snapshot.get("match_economy"))
        self.result_rewards = ResultRewardService.restore(
            self.economy, self.auction_ledger, snapshot.get("result_rewards")
        )
        self.saved_journals = {
            name: getattr(self, name).snapshot()
            for name in ("prize", "events", "event_cards", "admin_audit")
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
        if args.command == "operator-receipt":
            receipt = self.admin_audit.get(args.request_id)
            if receipt is None:
                raise ValueError("operator request receipt not found")
            return receipt.model_dump(mode="json")
        key = args.account_key
        self.economy.account_view(key)  # Exact saved key; never provision an account.
        if args.command == "account":
            return account_summary(self, key)
        _, build, action = ACTIONS[args.command]
        request = build(
            args, request_id=args.request_id, account_key=key, operator=args.operator,
            source="cli", reason=args.reason,
        )
        return action(self, request).model_dump(mode="json")


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
