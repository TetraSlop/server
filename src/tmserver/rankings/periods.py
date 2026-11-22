"""Weekly ranking periods.

Server choice: UTC weeks, with no rookie rules, awards or backfill. Only newly
committed results belong here; RatingPolicy validates proofs and prevents replay.
Weekly prize totals wrap at 32 bits, the client's field width. A finished week's
last ratings stay frozen until the next rollover. Turning the feature on records
only later play in that week.
"""

from collections.abc import Mapping
from functools import partial

from tmserver.checks import uint
from tmserver.economy.errors import EconomyError

POLICY = "local-utc-week-v1"
WEEK = 7 * 24 * 60 * 60
MONDAY_OFFSET = 3 * 24 * 60 * 60  # The Unix epoch was a Thursday.
DWORD_MAX = 0xFFFFFFFF


_uint = partial(uint, maximum=DWORD_MAX, error=EconomyError, what="weekly value")


def _key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise EconomyError("invalid weekly account key")
    return value


def _week(now):
    if type(now) not in (int, float) or not 0 <= now <= DWORD_MAX:
        raise EconomyError("clock must be a finite nonnegative native timestamp")
    return (int(now) + MONDAY_OFFSET) // WEEK * WEEK - MONDAY_OFFSET


class RankingPeriods:
    def __init__(self):
        self._week_start = None
        self._current = {}  # Account -> (weekly prize total, last committed rating).
        self._previous = {}  # Account -> frozen last-week rating.

    @property
    def account_keys(self):
        return frozenset(self._current) | frozenset(self._previous)

    def advance(self, now):
        week = _week(now)
        if self._week_start is not None:
            if week < self._week_start:
                raise EconomyError("weekly clock moved to an earlier week")
            if week == self._week_start:
                return False
        self._previous = (
            {key: rating for key, (_, rating) in self._current.items()}
            if self._week_start is not None and week == self._week_start + WEEK
            else {}
        )
        self._current = {}
        self._week_start = week
        return True

    def record(self, now, contributions):
        """Apply a trusted fresh result; validate every row before rolling over."""
        if not isinstance(contributions, Mapping):
            raise EconomyError("weekly contributions must map accounts to results")
        rows = {}
        for key, values in contributions.items():
            _key(key)
            if not isinstance(values, (tuple, list)) or len(values) != 2:
                raise EconomyError("weekly contribution requires reward and rating")
            rows[key] = (_uint(values[0]), _uint(values[1]))
        self.advance(now)
        for key, (reward, rating) in rows.items():
            total = self._current.get(key, (0, 0))[0]
            self._current[key] = ((total + reward) & DWORD_MAX, rating)

    def historical_stats(self):
        """Account -> (weekly prize or None, last week's rating or None)."""
        return {
            key: (self._current[key][0] if key in self._current else None, self._previous.get(key))
            for key in sorted(self.account_keys)
        }

    def snapshot(self):
        if self._week_start is None:
            return None
        return {
            "version": 1,
            "policy": POLICY,
            "week_start": self._week_start,
            "current": {
                key: {"prize": prize, "rating": rating}
                for key, (prize, rating) in sorted(self._current.items())
            },
            "previous": dict(sorted(self._previous.items())),
        }

    @classmethod
    def restore(cls, snapshot=None):
        result = cls()
        if snapshot is None:
            return result
        if (
            type(snapshot) is not dict
            or set(snapshot) != {"version", "policy", "week_start", "current", "previous"}
            or type(snapshot["version"]) is not int
            or snapshot["version"] != 1
            or snapshot["policy"] != POLICY
        ):
            raise EconomyError("invalid weekly snapshot")
        week = snapshot["week_start"]
        if (
            type(week) is not int
            or not -MONDAY_OFFSET <= week <= _week(DWORD_MAX)
            or (week + MONDAY_OFFSET) % WEEK
        ):
            raise EconomyError("invalid saved UTC week")
        if type(snapshot["current"]) is not dict or type(snapshot["previous"]) is not dict:
            raise EconomyError("weekly account records must be objects")
        for key, row in snapshot["current"].items():
            _key(key)
            if type(row) is not dict or set(row) != {"prize", "rating"}:
                raise EconomyError("invalid current weekly record")
            result._current[key] = (_uint(row["prize"]), _uint(row["rating"]))
        result._previous = {
            _key(key): _uint(rating) for key, rating in snapshot["previous"].items()
        }
        result._week_start = week
        return result
