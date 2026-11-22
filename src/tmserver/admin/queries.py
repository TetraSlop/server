"""Read-only account projections from validated service state.

These functions do no I/O and do not advance recovery, collection, expiry or
rankings. They return detached JSON-compatible dictionaries, shared by offline
administration and the admin panel.
"""

import json
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version


RECEIPTS_UNAVAILABLE = (
    "Receipt history is unavailable. Restart the server to reload saved receipts before "
    "inspecting history; a request may already have been saved."
)


def receipt_rows(host):
    """Describe committed operator actions, including assets already collected."""
    if host.storage_failed is not None:
        raise OSError(RECEIPTS_UNAVAILABLE)
    rows = []
    for receipt in host.admin_audit.snapshot()["receipts"]:
        command = receipt["request"]
        rows.append(dict(
            id=command["request_id"],
            action={
                "event-grant": "Event grant",
                "event-eligibility": "Event eligibility",
                "event-progress": "Event progress",
            }.get(command.get("action", receipt.get("action")), "Prize award"),
            account_key=command["account_key"], operator=command["operator"],
            source=command["source"], reason=command["reason"],
            committed_at=receipt["committed_at"],
        ))
    return rows


def _auction_claims(ledger, key):
    pending = ledger.pending(key)
    return dict(
        payments=sum(row.amount for row in pending if row.kind == "payment"),
        refunds=sum(row.amount for row in pending if row.kind == "refund"),
        returned_cards=sum(row.kind == "returned" for row in pending),
        won_cards=sum(row.kind == "won" for row in pending),
        receipts=[
            dict(
                id=row.id,
                kind=row.kind,
                amount=row.amount,
                card=None if row.card is None else list(row.card.full()),
            )
            for row in sorted(pending, key=lambda row: row.id)
        ],
    )


def _event_claims(cards, key):
    claims = []
    for row in cards.snapshot()["grants"]:
        remaining_cards = len(row["cards"]) - row["card_offset"]
        remaining_money = row["money"] - row["money_claimed"]
        if row["key"] == key and (remaining_cards or remaining_money):
            claims.append(dict(
                event_id=row["event_id"], id=row["id"],
                cards=remaining_cards, money=remaining_money,
            ))
    return sorted(claims, key=lambda row: (row["event_id"], row["id"]))


def _match_rounds(matches, key):
    rounds = []
    for row in matches.snapshot()["rounds"]:
        if row["closed"] or key not in row["seats"].values():
            continue
        seat = next(seat for seat, owner in row["seats"].items() if owner == key)
        if int(seat) in row["handed"] or int(seat) in row["recovered"]:
            continue
        settled = row["transfer_digest"] is not None
        cards = row["extras"] if settled else row["cbc"]
        rounds.append(dict(
            id=row["id"], seat=int(seat), money=row["money"],
            escrowed_money=row["stake"] if row["money"] == "escrow" else 0,
            has_result=row["result"] is not None, cards_settled=settled,
            pending_cards=len(cards.get(seat, ())),
        ))
    return sorted(rounds, key=lambda row: row["id"])


def account_summary(state, key):
    """Describe one exact account and its claims without crediting any assets."""
    account = state.economy.account_view(key)
    events = []
    for event_id in sorted(state.events.configs):
        member = state.events.member(key, event_id)
        if member is not None:
            events.append(dict(
                event_id=event_id, eligibility=member.eligibility,
                steps=member.steps, completed_missions=member.completed_missions,
                pending=state.event_cards.pending(key, event_id),
            ))
    prize = state.prize.snapshot()["accounts"].get(key, {})
    return dict(
        account_key=key, player_id=account.player_id,
        name=account.name.decode("latin1"), guild=account.guild,
        balance=account.balance, card_count=len(account.cards),
        prize_points=state.economy.prize_points(key), pending_prize=prize.get("pending"),
        events=events,
        event_claims=_event_claims(state.event_cards, key),
        auction_claims=_auction_claims(state.auction_ledger, key),
        pending_result=state.result_rewards.status(key),
        match_rounds=_match_rounds(state.match_economy, key),
    )


def _account_row(host, key):
    view = host.economy.account_view(key)
    peer = host.nicks.get(key.encode("utf8"))
    return dict(
        id=key, name=view.name.decode("latin1"), player_id=view.player_id,
        balance=view.balance, card_count=len(view.cards), rating=view.rating,
        connected=peer is not None and not peer.closed, busy=host.economy.is_busy(key),
    )


def account_rows(host):
    """List already provisioned accounts; inspection must never enroll a login."""
    return [_account_row(host, key) for key in sorted(host.account_keys)]


def _claim_rows(summary):
    key, name = summary["account_key"], summary["name"]
    rows = []

    def add(source, receipt, kind, *, money=0, cards=0, prize_points=0):
        rows.append(dict(
            id=json.dumps([key, source, receipt], separators=(",", ":")),
            account_key=key, account_name=name, kind=kind, receipt=str(receipt),
            money=money, cards=cards, prize_points=prize_points,
        ))

    award = summary["pending_prize"]
    if award is not None:
        add("prize", award["id"], "Prize point award",
            prize_points=sum(award[field] for field in ("top30", "lucky", "rookies")))
    labels = {
        "payment": "Auction payment", "refund": "Auction refund",
        "returned": "Returned card", "won": "Auction purchase",
    }
    for receipt in summary["auction_claims"]["receipts"]:
        add("auction", receipt["id"], labels[receipt["kind"]],
            money=receipt["amount"], cards=int(receipt["card"] is not None))
    for grant in summary["event_claims"]:
        add("event", [grant["event_id"], grant["id"]], "Event grant",
            money=grant["money"], cards=grant["cards"])
    result = summary["pending_result"]
    if result is not None:
        add("result", result["reward_id"], "Unaccepted match result", cards=result["extra_count"])
    for round in summary["match_rounds"]:
        add("round", round["id"], "Match escrow / unfinished settlement",
            money=round["escrowed_money"], cards=round["pending_cards"])
    return rows


def claim_rows(host):
    """Keep points, money, inventory deliveries and in-play escrow distinct."""
    return [
        row for key in sorted(host.account_keys)
        for row in _claim_rows(account_summary(host, key))
    ]


def account_detail(host, key):
    if key not in host.account_keys:
        raise KeyError(key)
    summary = account_summary(host, key)
    view = host.economy.account_view(key)
    return dict(
        summary | _account_row(host, key), rating_mode=host.ratings.mode,
        pending_claims=_claim_rows(summary),
        inventory=[dict(
            name=host.economy.catalog.cards[card.model]["name"], model=card.model,
            attack=card.attack, kind=("P", "M", "X", "A")[card.kind],
            physical=card.physical, magical=card.magical,
            arrows=f"{card.arrows:08b}", deck=None if card.deck == 255 else card.deck,
        ) for card in view.cards],
    )


def _rules_label(rules):
    if rules is None:
        return "Owner chooses"
    turns = ("15 second", "30 second", "1 minute", "3 minute", "5 minute", "10 minute")
    enabled = [label for field, label in (
        ("du", "Double up"), ("st", "Special tiles"),
        ("cb", "Chance blocks"), ("ca", "Rotating blocks"),
    ) if getattr(rules, field)]
    departure = ("Substitute", "Invalid", "Loss")[rules.gs]
    return "; ".join([
        f"Stake {rules.bm}", f"{turns[rules.tl]} turns", f"Departure: {departure}",
        *enabled,
    ])


def room_rows(host):
    """Describe the same room owners used by the native directory projection."""
    bindings = [("Local Lobby", "Local Room", host.room, None, "")]
    bindings += [
        ("Local Lobby", config.name.decode("ascii"), table.room, config.rules, "")
        for config, table in host.ordinary_room_bindings
    ]
    for binding in host.event_rooms.rooms:
        config = host.events.configs[binding.event_id]
        bindings.append((
            config.listing.key_region.decode("latin1"), config.listing.key_event.decode("latin1"),
            binding.room, None, binding.event_id,
        ))
    rows = []
    for lobby, name, room, rules, event_id in bindings:
        tables = [table for table in room.tables if table.table_id is not None]
        rows.append(dict(
            id=str(room.room_id), lobby=lobby, name=name, members=len(room.members),
            tables=len(tables), occupied_tables=sum(table.table_count > 0 for table in tables),
            computer_enabled=not event_id and any(table.computer_enabled for table in tables),
            event_id=event_id, rules="Event settings" if event_id else _rules_label(rules),
        ))
    return rows


def server_overview(host, *, started_at):
    rooms = room_rows(host)
    try:
        revision = version("tmserver")
    except PackageNotFoundError:
        revision = "Development checkout"
    storage = "Memory only" if host.store is None else "Ready"
    if host.storage_failed is not None:
        storage = "Writes stopped after a storage failure"
    return dict(
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        version=revision, uptime_seconds=max(0, int(time.monotonic() - started_at)),
        storage=storage, diagnostics="Ready" if host.log_error is None else "Logging stopped",
        connected_accounts=sum(not peer.closed for peer in host.nicks.values()),
        accounts=len(host.account_keys), rooms=len(rooms),
        tables=sum(room["tables"] for room in rooms),
        occupied_tables=sum(room["occupied_tables"] for room in rooms),
    )
