"""Synchronous, audited Prize Center actions shared by CLI and authenticated web.

Call only on the live host's owner event loop, or under the offline writer lease.
There are no awaits between validation, persistence and publication.
"""

from datetime import UTC, datetime
import hashlib
import json

from tmserver.admin.audit import PrizeAwardPreview, PrizeAwardReceipt, PrizeAwardRequest
from tmserver.economy.errors import EconomyError


class AdminActionError(ValueError):
    """The requested action cannot be applied to the current account state."""


class AdminActionConflict(AdminActionError):
    """An ID or confirmation describes different facts from this action."""


def _committed(host, request):
    if host.store is None:
        raise AdminActionError("audited actions require persistent storage")
    if getattr(host, "storage_failed", None) is not None:
        raise OSError("state storage failed; restart is required") from host.storage_failed
    receipt = host.admin_audit.get(request.request_id)
    if receipt is not None and receipt.request != request:
        raise AdminActionConflict("request ID is already recorded with different facts")
    return receipt


def _grant(prize, request):
    try:
        added = prize.grant_award(
            request.account_key,
            request.award_id,
            top30=request.top30,
            lucky=request.lucky,
            rookies=request.rookies,
            lucky_cards=request.lucky_cards,
        )
    except EconomyError as exc:
        raise AdminActionError(str(exc)) from exc
    if not added:
        raise AdminActionConflict("award ID already exists without this audited request")


def preview_prize_award(host, request: PrizeAwardRequest) -> PrizeAwardPreview:
    receipt = _committed(host, request)
    if receipt is not None:
        return PrizeAwardPreview(
            request=request,
            digest=receipt.preview_digest,
            current_points=receipt.current_points,
            points_on_redemption=receipt.points_on_redemption,
            already_committed=receipt,
        )
    try:
        account = host.economy.account_view(request.account_key)
        if host.economy.is_busy(request.account_key):
            raise AdminActionError("account is busy; audited awards require an idle account")
        current_points = host.economy.prize_points(request.account_key)
    except EconomyError as exc:
        raise AdminActionError(str(exc)) from exc
    _grant(host.prize.stage_state(host.economy, request.account_key), request)
    row = host.prize.snapshot()["accounts"].get(request.account_key, {})
    relevant = dict(
        action="prize-award",
        version=1,
        request=request.model_dump(mode="json"),
        account=dict(nn=account.nn, player_id=account.player_id, name_hex=account.name.hex()),
        current_points=current_points,
        pending=row.get("pending"),
        known_awards=row.get("known_awards", []),
    )
    digest = hashlib.sha256(
        json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PrizeAwardPreview(
        request=request,
        digest=digest,
        current_points=current_points,
        points_on_redemption=min(
            99999, current_points + request.top30 + request.lucky + request.rookies
        ),
    )


def apply_prize_award(host, request: PrizeAwardRequest, preview_digest: str) -> PrizeAwardReceipt:
    receipt = _committed(host, request)
    if receipt is not None:
        return receipt
    preview = preview_prize_award(host, request)
    if preview_digest != preview.digest:
        raise AdminActionConflict("preview is stale; review a fresh preview before applying")
    receipt = PrizeAwardReceipt(
        request=request,
        preview_digest=preview.digest,
        committed_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        current_points=preview.current_points,
        points_on_redemption=preview.points_on_redemption,
    )
    audit = host.admin_audit.stage_receipt(receipt)
    with host.economy.edit_accounts((request.account_key,)) as economy:
        prize = host.prize.stage_state(economy, request.account_key)
        _grant(prize, request)
        host.persist_changes(
            economy.snapshot()["accounts"],
            {"prize": prize.snapshot(), "admin_audit": audit.snapshot()},
        )
    host.prize.publish_state(prize)
    host.admin_audit.publish_state(audit)
    return receipt
