"""Optional rating history using the client's offline 32-result ring.

The client's constructor (10100FD6..10101040) starts 32 neutral entries at rating
200. Each result (100CC7E8..100CC840) replaces entry sequence & 31, averages
50 * (entry + 2) and increments the WORD sequence. Server choice: applying this
offline ring to hosted matches. Details: TetraPackets
client/ratings-and-statistics.md.

No account is enrolled implicitly. Save the snapshot with the result and profile
commits before sending D2; a crash between separate saves is not atomic.
"""

from dataclasses import dataclass
from functools import partial
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from tmserver.checks import stable_id, uint
from tmserver.game.departure_rules import result_classes
from tmserver.economy.errors import EconomyError


_uint = partial(uint, error=EconomyError)
_identifier = partial(stable_id, error=EconomyError)


class _Saved(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class _Receipt(_Saved):
    match_id: str
    scores: list[int]
    seat: int


class _Initial(_Saved):
    entries: list[int]
    sequence: int


class _Account(_Saved):
    initial: _Initial
    results: list[_Receipt]


class _Ledger(_Saved):
    """Saved ledger shape; ranges, the ring and receipts are checked by replay."""

    version: Annotated[int, Field(ge=1, le=1)]
    policy: Literal["original-offline-ring-v1"]
    accounts: dict[str, _Account]


def _scores(scores, seat):
    if not isinstance(scores, (tuple, list)) or len(scores) not in (2, 3):
        raise EconomyError("scores must contain two or three server seats")
    maximum = 16 if len(scores) == 2 else 25
    for score in scores:
        _uint(score, maximum, what="score")
    if not 1 <= sum(scores) <= maximum:
        raise EconomyError("score total exceeds the ordinary board")
    _uint(seat, len(scores) - 1, what="server seat")
    return tuple(scores)


def classify_result(scores, seat):
    """Native class0..4 from the match result ranking; rated rounds have no departures."""
    return result_classes(_scores(scores, seat))[seat]


@dataclass(frozen=True)
class HistoryState:
    entries: tuple = (2,) * 32
    sequence: int = 0
    revision: int = 0  # Server stale-quote guard; not a native/profile field.

    def __post_init__(self):
        if type(self.entries) is not tuple or len(self.entries) != 32:
            raise EconomyError("history must be an immutable32-entry tuple")
        for item in self.entries:
            _uint(item, 4, what="result class")
        _uint(self.sequence, 65535, what="WORD sequence")
        _uint(self.revision, (1 << 63) - 1, what="revision")

    @property
    def rating(self):
        return sum(50 * (item + 2) for item in self.entries) // 32

    def next(self, result_class):
        _uint(result_class, 4, what="result class")
        entries = list(self.entries)
        entries[self.sequence & 31] = result_class
        return HistoryState(tuple(entries), (self.sequence + 1) & 65535, self.revision + 1)


@dataclass(frozen=True)
class RatingQuote:
    key: str
    match_id: str
    scores: tuple
    seat: int
    result_class: int
    before: HistoryState
    after: HistoryState

    @property
    def d2(self):
        """Authoritative ResultData D2 when this optional policy is selected."""
        return self.after.rating


class LocalRatingHistory:
    def __init__(self):
        self._initial = {}
        self._states = {}
        self._receipts = {}

    def __copy__(self):
        """Independent account maps; a commit replaces a receipt map, never edits it."""
        clone = LocalRatingHistory()
        clone._initial, clone._states = dict(self._initial), dict(self._states)
        clone._receipts = dict(self._receipts)
        return clone

    def committed(self, quote):
        return quote.match_id in self._receipts.get(quote.key, {})

    def enrolled(self, key):
        _identifier(key, what="account key")
        return key in self._states

    def enroll(self, key, *, history=None, sequence=0):
        """Explicit fresh enrollment; never infer old history from profile rating.

        Default reproduces original constructor class 2, rating 200, sequence 0.
        An imported history must be supplied by trusted local configuration.
        No existing enrollment can be reset through this method.
        """
        _identifier(key, what="account key")
        if key in self._states:
            raise EconomyError("account already enrolled")
        if history is not None and not isinstance(history, (list, tuple)):
            raise EconomyError("history must be an explicit sequence")
        state = HistoryState((2,) * 32 if history is None else tuple(history), sequence)
        self._initial[key] = self._states[key] = state
        self._receipts[key] = {}
        return state

    def state(self, key):
        _identifier(key, what="account key")
        try:
            return self._states[key]
        except KeyError as exc:
            raise EconomyError("account has not opted into local rating") from exc

    def quote(self, key, match_id, scores, seat):
        before = self.state(key)
        _identifier(match_id, what="stable match ID")
        scores = _scores(scores, seat)
        completed = self._receipts[key].get(match_id)
        if completed is not None:
            if completed.scores != scores or completed.seat != seat:
                raise EconomyError("completed match ID reused with different result")
            return completed
        result_class = classify_result(scores, seat)
        return RatingQuote(
            key, match_id, scores, seat, result_class, before, before.next(result_class)
        )

    def commit(self, quote):
        """Commit once in memory; caller owns joint durable persistence."""
        return self.commit_many((quote,))[quote.key]

    def commit_many(self, quotes):
        """Validate every participant before changing any account.

        This is the only rating quote check: each quote must equal the quote
        this ledger issues now, so altered fields and stale quotes both fail.
        Quotes must refer to one stable match ID and distinct enrolled accounts.
        Committed duplicates are idempotent, even after later rounds. Quoting
        does not reserve an account; a competing result invalidates stale quotes.
        """
        if not isinstance(quotes, (list, tuple)) or not 1 <= len(quotes) <= 3:
            raise EconomyError("expected one to three rating quotes")
        seen = set()
        seats = set()
        updates = []
        result = {}
        match_id = None
        scores = None
        for quote in quotes:
            if type(quote) is not RatingQuote:
                raise EconomyError("expected a rating quote")
            if quote.key in seen:
                raise EconomyError("duplicate account in result")
            seen.add(quote.key)
            if quote.seat in seats:
                raise EconomyError("duplicate participant seat")
            seats.add(quote.seat)
            if match_id is None:
                match_id = quote.match_id
                scores = quote.scores
            if quote.match_id != match_id:
                raise EconomyError("batch spans different matches")
            if quote.scores != scores:
                raise EconomyError("participants disagree on score order")
            expected = self.quote(quote.key, quote.match_id, quote.scores, quote.seat)
            if quote != expected:
                raise EconomyError("stale or altered rating quote")
            if quote.match_id not in self._receipts[quote.key]:
                updates.append(quote)
            result[quote.key] = quote.after
        for quote in updates:
            self._states[quote.key] = quote.after
            self._receipts[quote.key] = {**self._receipts[quote.key], quote.match_id: quote}
        return result

    def snapshot(self):
        """JSON-safe, replayable ledger; receipts retained for lifetime dedupe."""
        return {
            "version": 1,
            "policy": "original-offline-ring-v1",
            "accounts": {
                key: {
                    "initial": {
                        "entries": list(self._initial[key].entries),
                        "sequence": self._initial[key].sequence,
                    },
                    "results": [
                        {
                            "match_id": quote.match_id,
                            "scores": list(quote.scores),
                            "seat": quote.seat,
                        }
                        for quote in self._receipts[key].values()
                    ],
                }
                for key in sorted(self._states)
            },
        }

    @classmethod
    def restore(cls, snapshot):
        service = cls()
        if snapshot is None:
            return service
        for key, record in _Ledger.model_validate(snapshot).accounts.items():
            service.enroll(key, history=record.initial.entries, sequence=record.initial.sequence)
            for event in record.results:
                _identifier(event.match_id, what="stable match ID")
                if event.match_id in service._receipts[key]:
                    raise EconomyError("duplicate durable rating receipt")
                service.commit(service.quote(key, event.match_id, event.scores, event.seat))
        return service
