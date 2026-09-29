"""Audited event grants queue claims; the original F service delivers assets.

Call synchronously on the live owner's event loop or under the offline writer
lease. Only the grant and audit journals change, and both publish after commit.
"""

from datetime import UTC, datetime
import hashlib
import json

from tmserver.cards.model import Card
from tmserver.economy.errors import EconomyError

from .actions import AdminActionConflict, AdminActionError, _committed
from .audit import (
    EventGrantCard as EventGrantCard,
    EventGrantPreview,
    EventGrantReceipt,
    EventGrantRequest,
    GrantedEventCard,
)


def _grant(service, request, cards):
    try:
        added = service.grant(
            request.account_key,
            request.event_id,
            request.grant_id,
            cards=tuple(Card(*card.full()) for card in cards),
            money=request.money,
        )
    except EconomyError as exc:
        raise AdminActionError(str(exc)) from exc
    if not added:
        raise AdminActionConflict("grant ID already exists without this audited request")


def preview_event_grant(host, request: EventGrantRequest) -> EventGrantPreview:
    receipt = _committed(host, request)
    if receipt is not None:
        return EventGrantPreview(
            request=request,
            digest=receipt.preview_digest,
            cards=receipt.cards,
            eligibility=receipt.eligibility,
            collection_enabled=receipt.collection_enabled,
            already_committed=receipt,
        )
    try:
        account = host.economy.account_view(request.account_key)
        if host.economy.is_busy(request.account_key):
            raise AdminActionError("account is busy; audited grants require an idle account")
        config = host.events.config(request.event_id)
        member = host.events.member(request.account_key, request.event_id)
        if member is None:
            raise AdminActionError("grant requires a registered event member")
        cards = tuple(
            GrantedEventCard(
                **card.model_dump(),
                points=host.economy.catalog.points(
                    card.model, card.attack, card.physical, card.magical
                ),
                deck=255,
            )
            for card in request.cards
        )
    except EconomyError as exc:
        raise AdminActionError(str(exc)) from exc
    rows = [
        row
        for row in host.event_cards.snapshot()["grants"]
        if row["key"] == request.account_key and row["event_id"] == request.event_id
    ]
    if any(row["id"] == request.grant_id for row in rows):
        raise AdminActionConflict("grant ID already exists without this audited request")
    staged = host.event_cards.stage_state(host.economy, host.events, (request.account_key,))
    _grant(staged, request, cards)
    relevant = dict(
        action="event-grant",
        version=1,
        request=request.model_dump(mode="json"),
        cards=[card.model_dump(mode="json") for card in cards],
        account=dict(nn=account.nn, player_id=account.player_id, name_hex=account.name.hex()),
        member_identity=member.identity,
        eligibility=member.eligibility,
        collection_enabled=config.card_service_enabled,
        grants=rows,
    )
    digest = hashlib.sha256(
        json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EventGrantPreview(
        request=request,
        digest=digest,
        cards=cards,
        eligibility=member.eligibility,
        collection_enabled=config.card_service_enabled,
    )


def apply_event_grant(host, request: EventGrantRequest, preview_digest: str) -> EventGrantReceipt:
    receipt = _committed(host, request)
    if receipt is not None:
        return receipt
    preview = preview_event_grant(host, request)
    if preview_digest != preview.digest:
        raise AdminActionConflict("preview is stale; review a fresh preview before applying")
    receipt = EventGrantReceipt(
        request=request,
        preview_digest=preview.digest,
        committed_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        cards=preview.cards,
        eligibility=preview.eligibility,
        collection_enabled=preview.collection_enabled,
    )
    cards = host.event_cards.stage_state(host.economy, host.events, (request.account_key,))
    _grant(cards, request, receipt.cards)
    audit = host.admin_audit.stage_receipt(receipt)
    host.persist_changes({}, {"event_cards": cards.snapshot(), "admin_audit": audit.snapshot()})
    host.event_cards.publish_state(cards)
    host.admin_audit.publish_state(audit)
    return receipt
