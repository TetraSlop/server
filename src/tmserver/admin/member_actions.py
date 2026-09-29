"""Preview and atomically commit trusted event membership corrections."""

from datetime import UTC, datetime
import hashlib
import json

from tmserver.economy.errors import EconomyError

from .actions import AdminActionConflict, AdminActionError, _committed
from .audit import (
    EventEligibilityRequest,
    EventMemberPreview,
    EventMemberReceipt,
    EventMemberRequest,
    EventMemberState,
)


def _change(events, request):
    if isinstance(request, EventEligibilityRequest):
        events.set_eligibility(request.account_key, request.event_id, request.value)
    elif not events.update_progress(
        request.account_key, request.event_id, request.update_id,
        steps_delta=request.steps_delta, completed_missions=request.completed_missions,
    ):
        raise AdminActionConflict("progress ID already exists without this audited request")


def preview_event_member(host, request: EventMemberRequest) -> EventMemberPreview:
    receipt = _committed(host, request)
    if receipt is not None:
        return EventMemberPreview(
            request=request, digest=receipt.preview_digest,
            member_identity=receipt.member_identity, before=receipt.before, after=receipt.after,
            already_committed=receipt,
        )
    try:
        account = host.economy.account_view(request.account_key)
        if host.economy.is_busy(request.account_key):
            raise AdminActionError("account is busy; event corrections require an idle account")
        config = host.events.config(request.event_id)
        member = host.events.member(request.account_key, request.event_id)
        if member is None:
            raise AdminActionError("event corrections require a registered event member")
        staged = host.events.stage_state(host.economy)
        _change(staged, request)
    except EconomyError as exc:
        raise AdminActionError(str(exc)) from exc
    before = EventMemberState.from_member(member)
    after = EventMemberState.from_member(staged.member(request.account_key, request.event_id))
    relevant = dict(
        version=1, request=request.model_dump(mode="json"), configuration=config.fingerprint(),
        account=dict(nn=account.nn, player_id=account.player_id, name_hex=account.name.hex()),
        member_identity=member.identity, member_name=member.name.hex(),
        before=before.model_dump(), after=after.model_dump(),
    )
    digest = hashlib.sha256(
        json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EventMemberPreview(
        request=request, digest=digest, member_identity=member.identity, before=before, after=after,
    )


def apply_event_member(host, request: EventMemberRequest, preview_digest: str) -> EventMemberReceipt:
    receipt = _committed(host, request)
    if receipt is not None:
        return receipt
    preview = preview_event_member(host, request)
    if preview_digest != preview.digest:
        raise AdminActionConflict("preview is stale; review a fresh preview before applying")
    receipt = EventMemberReceipt(
        request=request, preview_digest=preview.digest,
        committed_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        member_identity=preview.member_identity, before=preview.before, after=preview.after,
    )
    events = host.events.stage_state(host.economy)
    _change(events, request)
    audit = host.admin_audit.stage_receipt(receipt)
    host.persist_changes({}, {"events": events.snapshot(), "admin_audit": audit.snapshot()})
    host.events.publish_state(events)
    host.admin_audit.publish_state(audit)
    return receipt
