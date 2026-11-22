"""Audited operator actions shared by the CLI and the authenticated web forms.

Each action applies once per request ID. Call it only on the live host's owner
event loop, or under the offline writer lease: there are no awaits between
validation, persistence and publication. The change and its receipt commit
together.
"""

import hashlib
import json
from datetime import UTC, datetime

from tmserver.cards.model import Card
from tmserver.economy.errors import EconomyError

from .audit import (
    EventEligibilityRequest,
    EventGrantReceipt,
    EventMemberReceipt,
    EventMemberState,
    GrantedEventCard,
    PrizeAwardReceipt,
)


class AdminActionError(EconomyError):
    """The requested action cannot be applied to the current account state."""


class AdminActionConflict(AdminActionError):
    """A request ID or reference is already recorded with different facts."""


def _committed(host, request):
    if host.store is None:
        raise AdminActionError("audited actions require persistent storage")
    if host.storage_failed is not None:
        raise OSError("state storage failed; restart is required") from host.storage_failed
    receipt = host.admin_audit.get(request.request_id)
    if receipt is not None and receipt.request != request:
        raise AdminActionConflict("request ID is already recorded with different facts")
    return receipt


def _idle_account(host, key, actions):
    account = host.economy.account_view(key)
    if host.economy.is_busy(key):
        raise AdminActionError(f"account is busy; {actions} require an idle account")
    return dict(nn=account.nn, player_id=account.player_id, name_hex=account.name.hex())


def _digest(request, **facts):
    """Fingerprint the request and the state it was applied to, for the receipt."""
    document = dict(version=1, request=request.model_dump(mode="json"), **facts)
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _now():
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _commit(host, accounts, services, receipt):
    audit = host.admin_audit.stage_receipt(receipt)
    host.persist_changes(accounts, services | {"admin_audit": audit.snapshot()})
    host.admin_audit.publish_state(audit)


def apply_prize_award(host, request):
    receipt = _committed(host, request)
    if receipt is not None:
        return receipt
    key = request.account_key
    account = _idle_account(host, key, "audited awards")
    current_points = host.economy.prize_points(key)
    row = host.prize.snapshot()["accounts"].get(key, {})
    receipt = PrizeAwardReceipt(
        request=request,
        preview_digest=_digest(
            request, action="prize-award", account=account, current_points=current_points,
            pending=row.get("pending"), known_awards=row.get("known_awards", []),
        ),
        committed_at=_now(),
        current_points=current_points,
        points_on_redemption=min(
            99999, current_points + request.top30 + request.lucky + request.rookies
        ),
    )
    with host.economy.edit_accounts((key,)) as economy:
        prize = host.prize.stage_state(economy, key)
        added = prize.grant_award(
            key, request.award_id, top30=request.top30, lucky=request.lucky,
            rookies=request.rookies, lucky_cards=request.lucky_cards,
        )
        if not added:
            raise AdminActionConflict("award ID already exists without this audited request")
        _commit(host, economy.snapshot()["accounts"], {"prize": prize.snapshot()}, receipt)
    host.prize.publish_state(prize)
    return receipt


def apply_event_grant(host, request):
    """Queue an event claim; the game's own collection screen delivers it."""
    receipt = _committed(host, request)
    if receipt is not None:
        return receipt
    key, event_id = request.account_key, request.event_id
    account = _idle_account(host, key, "audited grants")
    config = host.events.config(event_id)
    member = host.events.member(key, event_id)
    if member is None:
        raise AdminActionError("grant requires a registered event member")
    cards = tuple(
        GrantedEventCard(
            **card.model_dump(), deck=255,
            points=host.economy.catalog.points(
                card.model, card.attack, card.physical, card.magical
            ),
        )
        for card in request.cards
    )
    rows = [
        row for row in host.event_cards.snapshot()["grants"]
        if row["key"] == key and row["event_id"] == event_id
    ]
    if any(row["id"] == request.grant_id for row in rows):
        raise AdminActionConflict("grant ID already exists without this audited request")
    receipt = EventGrantReceipt(
        request=request,
        preview_digest=_digest(
            request, action="event-grant", account=account, member_identity=member.identity,
            eligibility=member.eligibility, collection_enabled=config.card_service_enabled,
            cards=[card.model_dump(mode="json") for card in cards], grants=rows,
        ),
        committed_at=_now(),
        cards=cards,
        eligibility=member.eligibility,
        collection_enabled=config.card_service_enabled,
    )
    staged = host.event_cards.stage_state(host.economy, host.events, (key,))
    staged.grant(
        key, event_id, request.grant_id,
        cards=tuple(Card(*card.full()) for card in cards), money=request.money,
    )
    _commit(host, {}, {"event_cards": staged.snapshot()}, receipt)
    host.event_cards.publish_state(staged)
    return receipt


def apply_event_member(host, request):
    """Set eligibility or add trusted progress for a registered event member."""
    receipt = _committed(host, request)
    if receipt is not None:
        return receipt
    key, event_id = request.account_key, request.event_id
    account = _idle_account(host, key, "event corrections")
    config = host.events.config(event_id)
    member = host.events.member(key, event_id)
    if member is None:
        raise AdminActionError("event corrections require a registered event member")
    staged = host.events.stage_state(host.economy)
    if isinstance(request, EventEligibilityRequest):
        staged.set_eligibility(key, event_id, request.value)
    elif not staged.update_progress(
        key, event_id, request.update_id,
        steps_delta=request.steps_delta, completed_missions=request.completed_missions,
    ):
        raise AdminActionConflict("progress ID already exists without this audited request")
    before = EventMemberState.from_member(member)
    after = EventMemberState.from_member(staged.member(key, event_id))
    receipt = EventMemberReceipt(
        request=request,
        preview_digest=_digest(
            request, account=account, configuration=config.fingerprint(), member_identity=member.identity,
            member_name=member.name.hex(), before=before.model_dump(), after=after.model_dump(),
        ),
        committed_at=_now(),
        member_identity=member.identity,
        before=before,
        after=after,
    )
    _commit(host, {}, {"events": staged.snapshot()}, receipt)
    host.events.publish_state(staged)
    return receipt
