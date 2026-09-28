"""Durable event outcome receipts and explicit local step-counting policy."""

import re
from copy import copy


class EventResults:
    def __init__(self):
        self._rows = {}

    def stage_state(self):
        staged = copy(self)
        staged._rows = dict(self._rows)  # Existing receipts are never edited.
        return staged

    def publish_state(self, staged):
        self._rows = staged._rows

    @staticmethod
    def _validate(row):
        fields = {"id", "event", "characters", "keys", "scores", "classes", "policy"}
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("invalid event result fields")
        for k in ("id", "event"):
            if not isinstance(row[k], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", row[k]):
                raise ValueError("invalid event result identifier")
        if any(not isinstance(row[k], list) for k in ("scores", "characters", "keys", "classes")):
            raise ValueError("event result seat fields must be arrays")
        n = len(row["scores"])
        if n not in (2, 3) or any(len(row[k]) != n for k in ("characters", "keys", "classes")):
            raise ValueError("invalid event result seats")
        if (
            any(type(x) is not int or not 0 <= x <= 25 for x in row["scores"])
            or sum(row["scores"]) != n * 5
        ):
            raise ValueError("invalid completed event score")
        if (
            any(type(x) is not int or not 0 < x <= 0x7FFFFFFF for x in row["characters"])
            or len(set(row["characters"])) != n
        ):
            raise ValueError("invalid event character identities")
        expected = [
            2 + sum(y > x for y in row["scores"]) - sum(y < x for y in row["scores"])
            for x in row["scores"]
        ]
        if any(type(x) is not int for x in row["classes"]) or row["classes"] != expected:
            raise ValueError("event classes disagree with scores")
        keys = [k for k in row["keys"] if k is not None]
        if (
            not keys
            or any(not isinstance(k, str) or not k or len(k) > 128 for k in keys)
            or len(set(keys)) != len(keys)
        ):
            raise ValueError("invalid human event accounts")
        if row["policy"] not in ("none", "wins", "rounds"):
            raise ValueError("unknown local event progress policy")

    def record(self, host, event_id, round_id, outcome, keys, policy="none"):
        if outcome.aborted:
            raise ValueError("aborted event is not a completed outcome")
        row = dict(
            id=round_id,
            event=event_id,
            characters=list(outcome.character_ids),
            keys=list(keys),
            scores=list(outcome.scores),
            classes=list(outcome.classes),
            policy=policy,
        )
        self._validate(row)
        old = self._rows.get(row["id"])
        if old is not None:
            if old != row:
                raise ValueError("event result replay differs")
            return False
        updates = []
        for seat, key in enumerate(row["keys"]):
            if key is None:
                continue
            view = host.economy.account_view(key)
            member = host.member(key, row["event"])
            if view.player_id != row["characters"][seat] or member is None:
                raise ValueError("event result differs from authenticated membership")
            delta = int(
                row["policy"] == "rounds" or row["policy"] == "wins" and row["classes"][seat] < 2
            )
            if delta and (member.eligibility < 0 or member.steps + delta > 0x7FFFFFFF):
                raise ValueError("event progress is outside the supported member state")
            if delta:
                updates.append((key, delta))
        if updates:
            staged = host.stage_state(host.economy)
            for key, delta in updates:
                staged.update_progress(key, row["event"], row["id"], steps_delta=delta)
            host.publish_state(staged)
        self._rows[row["id"]] = row
        return True

    def snapshot(self):
        return {
            "version": 1,
            "rounds": [
                {k: list(v) if isinstance(v, list) else v for k, v in row.items()}
                for row in self._rows.values()
            ],
        }

    @classmethod
    def restore(cls, snapshot=None):
        obj = cls()
        if snapshot is None:
            return obj
        if (
            not isinstance(snapshot, dict)
            or set(snapshot) != {"version", "rounds"}
            or snapshot["version"] != 1
            or not isinstance(snapshot["rounds"], list)
        ):
            raise ValueError("invalid event result snapshot")
        for row in snapshot["rounds"]:
            obj._validate(row)
            if row["id"] in obj._rows:
                raise ValueError("duplicate event result receipt")
            obj._rows[row["id"]] = {
                k: list(v) if isinstance(v, list) else v for k, v in row.items()
            }
        return obj
