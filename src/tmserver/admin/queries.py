"""Read-only account projections from validated service state.

These functions neither perform I/O nor advance recovery, collection, expiry or
rankings. Returned dictionaries contain detached JSON-compatible values and can
be shared by offline administration and a future server-owned admin interface.
"""


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
