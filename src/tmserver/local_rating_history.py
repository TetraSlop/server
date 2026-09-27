"""Opt-in durable rating using original client-local 32-result arithmetic.

Constructor10100FD6..10101040 initializes32 neutral entries/rating200.
Result100CC7E8..100CC840 replaces oldsequence&31, averages50*(entry+2),
then increments the WORD sequence. Applying that offline algorithm to hosted
matches is an explicit replacement-server policy, not historical server proof.

No account is enrolled implicitly. This module does not mutate profiles, RN,
money or game state. Persist snapshot jointly with result/profile commits before
sending D2. A process crash between separate saves is not an atomic commit.
"""

import re
from dataclasses import dataclass


class RatingHistoryError(ValueError):
    pass


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise RatingHistoryError("invalid " + label)
    return value


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise RatingHistoryError("invalid " + label)
    return value


def _scores(scores, seat):
    if not isinstance(scores, (tuple, list)) or len(scores) not in (2, 3):
        raise RatingHistoryError("scores must contain two or three server seats")
    maximum = 16 if len(scores) == 2 else 25
    for score in scores:
        _integer(score, 0, maximum, "score")
    if not 1 <= sum(scores) <= maximum:
        raise RatingHistoryError("score total exceeds the ordinary board")
    _integer(seat, 0, len(scores) - 1, "server seat")
    return tuple(scores)


def classify_result(scores, seat):
    """Native class0..4; lower is better, ties compare equal in either order."""
    scores = _scores(scores, seat)
    return 2 + sum(x > scores[seat] for x in scores) - sum(x < scores[seat] for x in scores)


@dataclass(frozen=True)
class HistoryState:
    entries: tuple = (2,) * 32
    sequence: int = 0
    revision: int = 0  # Server stale-quote guard; not a native/profile field.

    def __post_init__(self):
        if type(self.entries) is not tuple or len(self.entries) != 32:
            raise RatingHistoryError("history must be an immutable32-entry tuple")
        for item in self.entries:
            _integer(item, 0, 4, "result class")
        _integer(self.sequence, 0, 65535, "WORD sequence")
        _integer(self.revision, 0, (1 << 63) - 1, "revision")

    @property
    def rating(self):
        return sum(50 * (item + 2) for item in self.entries) // 32

    def next(self, result_class):
        _integer(result_class, 0, 4, "result class")
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


def validate_quote(quote):
    """Validate a trusted coordinator's typed proof, independently of a ledger."""
    if type(quote) is not RatingQuote:
        raise RatingHistoryError("expected an immutable rating quote")
    _identifier(quote.key, "account key")
    _identifier(quote.match_id, "stable match ID")
    if type(quote.scores) is not tuple:
        raise RatingHistoryError("quoted scores must be immutable")
    result_class = classify_result(quote.scores, quote.seat)
    _integer(quote.result_class, 0, 4, "result class")
    if type(quote.before) is not HistoryState or type(quote.after) is not HistoryState:
        raise RatingHistoryError("rating quote needs immutable history states")
    if quote.result_class != result_class or quote.after != quote.before.next(result_class):
        raise RatingHistoryError("rating quote does not match original arithmetic")
    return quote


class LocalRatingHistory:
    def __init__(self):
        self._initial = {}
        self._states = {}
        self._receipts = {}

    def enrolled(self, key):
        _identifier(key, "account key")
        return key in self._states

    def enroll(self, key, *, history=None, sequence=0):
        """Explicit fresh enrollment; never infer old history from profile rating.

        Default reproduces original constructor class2/rating200/sequence0.
        An imported history must be supplied by trusted local configuration.
        No existing enrollment can be reset through this method.
        """
        _identifier(key, "account key")
        if key in self._states:
            raise RatingHistoryError("account already enrolled")
        if history is not None and not isinstance(history, (list, tuple)):
            raise RatingHistoryError("history must be an explicit sequence")
        state = HistoryState((2,) * 32 if history is None else tuple(history), sequence)
        self._initial[key] = self._states[key] = state
        self._receipts[key] = {}
        return state

    def state(self, key):
        _identifier(key, "account key")
        try:
            return self._states[key]
        except KeyError as exc:
            raise RatingHistoryError("account has not opted into local rating") from exc

    def quote(self, key, match_id, scores, seat):
        before = self.state(key)
        _identifier(match_id, "stable match ID")
        scores = _scores(scores, seat)
        completed = self._receipts[key].get(match_id)
        if completed is not None:
            if completed.scores != scores or completed.seat != seat:
                raise RatingHistoryError("completed match ID reused with different result")
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

        Quotes must refer to one stable match ID and distinct enrolled accounts.
        Committed duplicates are idempotent, even after later rounds. Quoting
        does not reserve an account; a competing result invalidates stale quotes.
        """
        if not isinstance(quotes, (list, tuple)) or not 1 <= len(quotes) <= 3:
            raise RatingHistoryError("expected one to three rating quotes")
        seen = set()
        seats = set()
        updates = []
        result = {}
        match_id = None
        scores = None
        for quote in quotes:
            validate_quote(quote)
            if quote.key in seen:
                raise RatingHistoryError("duplicate account in result")
            seen.add(quote.key)
            if quote.seat in seats:
                raise RatingHistoryError("duplicate participant seat")
            seats.add(quote.seat)
            if match_id is None:
                match_id = quote.match_id
                scores = quote.scores
            if quote.match_id != match_id:
                raise RatingHistoryError("batch spans different matches")
            if quote.scores != scores:
                raise RatingHistoryError("participants disagree on score order")
            expected = self.quote(quote.key, quote.match_id, quote.scores, quote.seat)
            if quote != expected:
                raise RatingHistoryError("stale or altered rating quote")
            if quote.match_id not in self._receipts[quote.key]:
                updates.append(quote)
            result[quote.key] = quote.after
        for quote in updates:
            self._states[quote.key] = quote.after
            self._receipts[quote.key][quote.match_id] = quote
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
        if (
            type(snapshot) is not dict
            or set(snapshot) != {"version", "policy", "accounts"}
            or type(snapshot["version"]) is not int
            or snapshot["version"] != 1
            or snapshot["policy"] != "original-offline-ring-v1"
            or type(snapshot["accounts"]) is not dict
        ):
            raise RatingHistoryError("invalid rating snapshot")
        for key, record in snapshot["accounts"].items():
            if (
                type(record) is not dict
                or set(record) != {"initial", "results"}
                or type(record["initial"]) is not dict
                or set(record["initial"]) != {"entries", "sequence"}
                or type(record["initial"]["entries"]) is not list
                or type(record["results"]) is not list
            ):
                raise RatingHistoryError("invalid account rating ledger")
            service.enroll(
                key, history=record["initial"]["entries"], sequence=record["initial"]["sequence"]
            )
            for event in record["results"]:
                if type(event) is not dict or set(event) != {"match_id", "scores", "seat"}:
                    raise RatingHistoryError("invalid rating receipt")
                _identifier(event["match_id"], "stable match ID")
                if event["match_id"] in service._receipts[key]:
                    raise RatingHistoryError("duplicate durable rating receipt")
                service.commit(
                    service.quote(key, event["match_id"], event["scores"], event["seat"])
                )
        return service
