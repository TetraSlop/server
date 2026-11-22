"""Immutable operator requests and jointly committed prize/event receipts."""

import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tmserver.checks import STABLE_ID


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


StableId = Annotated[str, Field(pattern=f"^{STABLE_ID}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
AwardPoints = Annotated[int, Field(ge=0, le=65535)]
PrizePoints = Annotated[int, Field(ge=0, le=99999)]
CardByte = Annotated[int, Field(ge=0, le=255)]
Eligibility = Annotated[int, Field(ge=-(1 << 31), le=(1 << 31) - 1)]


class _OperatorRequest(_ImmutableModel):
    request_id: StableId
    account_key: Annotated[str, Field(min_length=1, max_length=128)]
    operator: Annotated[str, Field(min_length=1, max_length=128)]
    source: Literal["cli", "web"]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("account_key", "operator", "reason")
    @classmethod
    def persistent_text(cls, value):
        if "\0" in value:
            raise ValueError("administration text cannot contain a NUL character")
        return value

    @field_validator("operator", "reason")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("operator and reason must contain non-whitespace text")
        return value


class PrizeAwardRequest(_OperatorRequest):
    award_id: StableId
    top30: AwardPoints = 0
    lucky: AwardPoints = 0
    rookies: AwardPoints = 0
    lucky_cards: tuple[int, int, int] = (65535, 65535, 65535)

    @field_validator("lucky_cards")
    @classmethod
    def card_models(cls, value):
        if any(card not in range(250) and card != 65535 for card in value):
            raise ValueError("Lucky Card model IDs must be 0..249 or 65535")
        return value


class _Receipt(_ImmutableModel):
    preview_digest: Digest
    committed_at: Annotated[str, Field(min_length=1, max_length=40)]
    status: Literal["queued"] = "queued"

    @field_validator("committed_at")
    @classmethod
    def utc_timestamp(cls, value):
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError("audit timestamp must include the UTC offset")
        return value


class PrizeAwardReceipt(_Receipt):
    request: PrizeAwardRequest
    current_points: PrizePoints
    points_on_redemption: PrizePoints

    @model_validator(mode="after")
    def redemption_points(self):
        request = self.request
        expected = min(99999, self.current_points + request.top30 + request.lucky + request.rookies)
        if self.points_on_redemption != expected:
            raise ValueError("receipt redemption points differ from the confirmed award")
        return self


class EventGrantCard(_ImmutableModel):
    model: Annotated[int, Field(ge=0, le=249)]
    attack: CardByte
    kind: Annotated[int, Field(ge=0, le=3)]
    physical: CardByte
    magical: CardByte
    arrows: CardByte


class GrantedEventCard(EventGrantCard):
    points: CardByte
    deck: Annotated[int, Field(ge=255, le=255)]

    def full(self):
        return (
            self.model,
            self.attack,
            self.kind,
            self.physical,
            self.magical,
            self.points,
            self.arrows,
            self.deck,
        )


class EventGrantRequest(_OperatorRequest):
    event_id: StableId
    grant_id: StableId
    money: Annotated[int, Field(ge=0, le=99999999)] = 0
    cards: Annotated[tuple[EventGrantCard, ...], Field(max_length=30)] = ()


class EventGrantReceipt(_Receipt):
    action: Literal["event-grant"] = "event-grant"
    request: EventGrantRequest
    cards: Annotated[tuple[GrantedEventCard, ...], Field(max_length=30)]
    eligibility: Eligibility
    collection_enabled: bool

    @model_validator(mode="after")
    def confirmed_cards(self):
        inputs = tuple(card.model_dump() for card in self.request.cards)
        actual = tuple(card.model_dump(exclude={"points", "deck"}) for card in self.cards)
        if actual != inputs:
            raise ValueError("receipt cards differ from the confirmed event grant")
        return self


EventSteps = Annotated[int, Field(ge=0, le=0x7FFFFFFF)]
MissionMask = Annotated[int, Field(ge=0, le=0xFFFFFFFF)]


class EventEligibilityRequest(_OperatorRequest):
    action: Literal["event-eligibility"] = "event-eligibility"
    event_id: StableId
    value: Eligibility


class EventProgressRequest(_OperatorRequest):
    action: Literal["event-progress"] = "event-progress"
    event_id: StableId
    update_id: StableId
    steps_delta: EventSteps = 0
    completed_missions: MissionMask = 0


EventMemberRequest = Annotated[
    EventEligibilityRequest | EventProgressRequest, Field(discriminator="action")
]


class EventMemberState(_ImmutableModel):
    eligibility: Eligibility
    steps: EventSteps
    completed_missions: MissionMask

    @classmethod
    def from_member(cls, member):
        return cls(**{name: getattr(member, name) for name in cls.model_fields})


class EventMemberReceipt(_Receipt):
    action: Literal["event-member"] = "event-member"
    status: Literal["applied"] = "applied"
    request: EventMemberRequest
    member_identity: Annotated[int, Field(ge=0, le=0xFFFFFFFFFFFFFFFF)]
    before: EventMemberState
    after: EventMemberState

    @model_validator(mode="after")
    def confirmed_change(self):
        changes = (
            dict(eligibility=self.request.value)
            if isinstance(self.request, EventEligibilityRequest)
            else dict(
                steps=self.before.steps + self.request.steps_delta,
                completed_missions=self.before.completed_missions | self.request.completed_missions,
            )
        )
        if self.after.model_dump() != self.before.model_dump() | changes:
            raise ValueError("receipt member state differs from the confirmed change")
        if isinstance(self.request, EventProgressRequest) and self.before.eligibility < 0:
            raise ValueError("excluded event member cannot progress")
        return self


class _Journal(_ImmutableModel):
    version: Annotated[int, Field(ge=1, le=1)]
    receipts: tuple[PrizeAwardReceipt, ...]


class _JournalV2(_ImmutableModel):
    version: Annotated[int, Field(ge=2, le=2)]
    receipts: tuple[PrizeAwardReceipt | EventGrantReceipt, ...]


class _JournalV3(_ImmutableModel):
    version: Annotated[int, Field(ge=3, le=3)]
    receipts: tuple[PrizeAwardReceipt | EventGrantReceipt | EventMemberReceipt, ...]


class AdminAudit:
    """Request IDs are global; award IDs remain scoped to their original account."""

    def __init__(self):
        self._receipts = {}

    def get(self, request_id):
        return self._receipts.get(request_id)

    def stage_receipt(self, receipt):
        if receipt.request.request_id in self._receipts:
            raise ValueError("administration request ID is already recorded")
        staged = AdminAudit()
        staged._receipts = dict(self._receipts)
        staged._receipts[receipt.request.request_id] = receipt
        return staged

    def publish_state(self, staged):
        self._receipts = staged._receipts

    def snapshot(self):
        if any(isinstance(receipt, EventMemberReceipt) for receipt in self._receipts.values()):
            return _JournalV3(version=3, receipts=tuple(self._receipts.values())).model_dump(
                mode="json"
            )
        if any(isinstance(receipt, EventGrantReceipt) for receipt in self._receipts.values()):
            return _JournalV2(version=2, receipts=tuple(self._receipts.values())).model_dump(
                mode="json"
            )
        return _Journal(version=1, receipts=tuple(self._receipts.values())).model_dump(mode="json")

    @classmethod
    def restore(cls, economy, prize, snapshot, *, event_cards=None):
        audit = cls()
        if snapshot is None:
            return audit
        # JSON permits the arrays used for immutable tuples while preserving
        # strict number/string validation. No Python-side coercion is enabled.
        models = {1: _Journal, 2: _JournalV2, 3: _JournalV3}
        version = snapshot.get("version") if isinstance(snapshot, dict) else None
        model = models.get(version, _Journal) if type(version) is int else _Journal
        journal = model.model_validate_json(json.dumps(snapshot))
        awards = set()
        grants = set()
        updates = set()
        events = None if event_cards is None else event_cards.host
        prize_rows = prize.snapshot()["accounts"]
        grant_rows = {
            (row["key"], row["event_id"], row["id"]): row
            for row in (() if event_cards is None else event_cards.snapshot()["grants"])
        }
        for receipt in journal.receipts:
            request = receipt.request
            # Input defaults help operators draft requests, but durable facts
            # must be complete. Redemption removes the original award bundle,
            # so defaulting a missing saved amount could invent its history.
            if receipt.model_fields_set != set(type(receipt).model_fields) or (
                request.model_fields_set != set(type(request).model_fields)
            ):
                raise ValueError("saved audit receipts require every normalized field")
            economy.account_view(request.account_key)
            if request.request_id in audit._receipts:
                raise ValueError("duplicate administration request receipt")
            if isinstance(receipt, EventMemberReceipt):
                member = None if events is None else events.member(request.account_key, request.event_id)
                if member is None or member.identity != receipt.member_identity:
                    raise ValueError("audit receipt has no matching event member")
                if (receipt.after.steps > member.steps
                        or receipt.after.completed_missions & ~member.completed_missions):
                    raise ValueError("audit receipt exceeds its recorded event progress")
                if isinstance(request, EventProgressRequest):
                    update_key = (request.account_key, request.event_id, request.update_id)
                    facts = events.progress_update(request.account_key, request.event_id, request.update_id)
                    if update_key in updates or facts != (request.steps_delta, request.completed_missions):
                        raise ValueError("audit receipt differs from its recorded event progress")
                    updates.add(update_key)
                # Later actions can change eligibility. Receipts retain the
                # original before/after facts, not a copy of today's member.
                audit._receipts[request.request_id] = receipt
                continue
            if isinstance(receipt, EventGrantReceipt):
                grant_key = (request.account_key, request.event_id, request.grant_id)
                row = grant_rows.get(grant_key)
                if grant_key in grants:
                    raise ValueError("duplicate administration event grant receipt")
                if row is None:
                    raise ValueError("audit receipt has no jointly recorded event grant")
                if row["money"] != request.money or row["cards"] != [
                    list(card.full()) for card in receipt.cards
                ]:
                    raise ValueError("audit receipt differs from its recorded event grant")
                grants.add(grant_key)
                audit._receipts[request.request_id] = receipt
                continue
            award_key = (request.account_key, request.award_id)
            if award_key in awards:
                raise ValueError("duplicate administration request or award receipt")
            row = prize_rows.get(request.account_key, {})
            if request.award_id not in row.get("known_awards", ()):
                raise ValueError("audit receipt has no jointly recorded Prize Center award")
            pending = row.get("pending")
            if pending is not None and pending["id"] == request.award_id:
                expected = dict(
                    id=request.award_id,
                    top30=request.top30,
                    lucky=request.lucky,
                    rookies=request.rookies,
                    lucky_cards=list(request.lucky_cards),
                )
                if dict(pending, lucky_cards=list(pending["lucky_cards"])) != expected:
                    raise ValueError("audit receipt differs from its pending Prize Center award")
            audit._receipts[request.request_id] = receipt
            awards.add(award_key)
        return audit
