"""Event outcome receipts and step counting (server choice)."""

from copy import copy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_Identifier = Annotated[str, Field(pattern=r"\A[A-Za-z0-9_.:-]{1,128}\z")]
_AccountKey = Annotated[str, Field(min_length=1, max_length=128)]


class _Result(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    id: _Identifier
    event: _Identifier
    characters: list[Annotated[int, Field(ge=1, le=0x7FFFFFFF)]]
    keys: list[_AccountKey | None]
    scores: list[Annotated[int, Field(ge=0, le=25)]] = Field(min_length=2, max_length=3)
    classes: list[int]
    policy: Literal["none", "wins", "rounds"]

    @model_validator(mode="after")
    def completed_outcome(self):
        n = len(self.scores)
        if any(len(values) != n for values in (self.characters, self.keys, self.classes)):
            raise ValueError("invalid event result seats")
        if sum(self.scores) != n * 5:
            raise ValueError("invalid completed event score")
        if len(set(self.characters)) != n:
            raise ValueError("invalid event character identities")
        expected = [
            2 + sum(y > x for y in self.scores) - sum(y < x for y in self.scores)
            for x in self.scores
        ]
        if self.classes != expected:
            raise ValueError("event classes disagree with scores")
        keys = [key for key in self.keys if key is not None]
        if not keys or len(set(keys)) != len(keys):
            raise ValueError("invalid human event accounts")
        return self


class _Journal(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    version: Literal[1]
    rounds: list[_Result]


class EventResults:
    def __init__(self):
        self._rows = {}

    def stage_state(self):
        staged = copy(self)
        staged._rows = dict(self._rows)  # Existing receipts are never edited.
        return staged

    def publish_state(self, staged):
        self._rows = staged._rows

    def record(self, host, event_id, round_id, outcome, keys, policy="none"):
        if outcome.aborted:
            raise ValueError("aborted event is not a completed outcome")
        row = _Result(
            id=round_id,
            event=event_id,
            characters=list(outcome.character_ids),
            keys=list(keys),
            scores=list(outcome.scores),
            classes=list(outcome.classes),
            policy=policy,
        )
        old = self._rows.get(row.id)
        if old is not None:
            if old != row:
                raise ValueError("event result replay differs")
            return False
        updates = []
        for seat, key in enumerate(row.keys):
            if key is None:
                continue
            view = host.economy.account_view(key)
            member = host.member(key, row.event)
            if view.player_id != row.characters[seat] or member is None:
                raise ValueError("event result differs from authenticated membership")
            delta = int(row.policy == "rounds" or row.policy == "wins" and row.classes[seat] < 2)
            if delta and (member.eligibility < 0 or member.steps + delta > 0x7FFFFFFF):
                raise ValueError("event progress is outside the supported member state")
            if delta:
                updates.append((key, delta))
        if updates:
            staged = host.stage_state(host.economy)
            for key, delta in updates:
                staged.update_progress(key, row.event, row.id, steps_delta=delta)
            host.publish_state(staged)
        self._rows[row.id] = row
        return True

    def snapshot(self):
        return {"version": 1, "rounds": [row.model_dump() for row in self._rows.values()]}

    @classmethod
    def restore(cls, snapshot=None):
        obj = cls()
        if snapshot is None:
            return obj
        for row in _Journal.model_validate(snapshot).rounds:
            if row.id in obj._rows:
                raise ValueError("duplicate event result receipt")
            obj._rows[row.id] = row
        return obj
