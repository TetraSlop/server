"""Offline account inspection, rewards and event administration for a PostgreSQL save."""

import argparse
import json
import os
import sys
from pathlib import Path

from tmserver.accounts.credentials import hash_password, valid_password
from tmserver.accounts.provisioning import MAX_REGISTERED_RAW, MIN_REGISTERED_RAW
from tmserver.admin.actions import apply_event_grant, apply_event_member, apply_prize_award
from tmserver.admin.audit import (
    EventEligibilityRequest,
    EventGrantCard,
    EventGrantRequest,
    EventProgressRequest,
    PrizeAwardRequest,
)
from tmserver.admin.queries import account_summary
from tmserver.events.config import load_event_setup
from tmserver.storage import open_store
from tmserver.protocol.wire import polid_to_string, polid_unscramble
from tmserver.storage.restore import restore_state

CARD_FIELDS = ("model", "attack", "kind", "physical", "magical", "arrows")


def pol_id_of(key):
    """The POL ID a player types for this account key, if it is one."""
    try:
        return polid_to_string(polid_unscramble(key.encode("ascii")))
    except (ValueError, UnicodeError):
        return None


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
    parser.add_argument(
        "--database-url", default=os.environ.get("TM_DATABASE_URL"),
        help="PostgreSQL URL; prefer TM_DATABASE_URL to keep credentials out of command history",
    )
    parser.add_argument("--events", type=Path, help="same event configuration used by the server")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("accounts", help="list exact saved account keys")
    account = commands.add_parser("account", help="inspect saved balances, progress and rewards")
    account.add_argument("--account-key", required=True)
    receipt = commands.add_parser("operator-receipt", help="read an exact operator request receipt")
    receipt.add_argument("--request-id", required=True)
    for command, help_text in (
        ("account-password", "set a login password, read as one line from standard input"),
        ("account-disable", "refuse LOGIN for an account"),
        ("account-enable", "allow LOGIN again"),
    ):
        commands.add_parser(command, help=help_text).add_argument("--account-key", required=True)
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
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("set TM_DATABASE_URL or pass --database-url; administration needs the database")
    return args


class AdminState:
    """Audited action host with no listeners, timers or recovery side effects."""

    def __init__(self, store, snapshot, configs):
        self.store = store
        self.storage_failed = None
        if snapshot is None:
            raise ValueError("administration requires an existing saved economy")
        state = restore_state(snapshot, event_configs=configs)
        saved_events = {row["id"] for row in (snapshot.get("events") or {}).get("events", ())}
        if {config.id for config in configs} != saved_events:
            raise ValueError("--events must contain exactly the saved event IDs")
        self.economy = state.economy
        self.prize = state.prize
        self.events = state.events
        self.event_cards = state.event_cards
        self.admin_audit = state.admin_audit
        self.auction_ledger = state.auction_ledger
        self.match_economy = state.match_economy
        self.result_rewards = state.result_rewards
        self.credentials = state.credentials
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
                    pol_id=pol_id_of(key),
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
        if args.command.startswith("account-"):
            return self.set_login(key, args.command)
        _, build, action = ACTIONS[args.command]
        request = build(
            args, request_id=args.request_id, account_key=key, operator=args.operator,
            source="cli", reason=args.reason,
        )
        return action(self, request).model_dump(mode="json")


    def set_login(self, key, command):
        if pol_id_of(key) is None or not (
            MIN_REGISTERED_RAW <= polid_unscramble(key.encode("ascii")) <= MAX_REGISTERED_RAW
        ):
            raise ValueError("only accounts with a registrable POL ID can have a password")
        current = self.credentials.get(key)
        if command == "account-password":
            password = sys.stdin.readline().rstrip("\r\n").encode("ascii", "replace")
            if not valid_password(password):
                raise ValueError("password must be 1-15 printable ASCII characters")
            disabled = current is not None and current.disabled
            credential = hash_password(password).model_copy(update={"disabled": disabled})
        elif current is None:
            raise ValueError("account has no password; set one with account-password first")
        else:
            credential = current.model_copy(update={"disabled": command == "account-disable"})
        staged = self.credentials.staged(key, credential)
        self.store.save_changes({}, {"identities": staged.snapshot()})
        self.credentials.publish_state(staged)
        return dict(account_key=key, pol_id=pol_id_of(key), disabled=credential.disabled)


def execute(args):
    with open_store(args.database_url) as store:
        configs = load_event_setup(args.events)[0] if args.events else ()
        return AdminState(store, store.load(), configs).apply(args)


def cli(argv=None):
    try:
        result = execute(arguments(argv))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    cli()
