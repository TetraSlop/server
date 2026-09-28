"""Durable match stakes and prize receipts, independent of sockets and UI.

Persist this snapshot with economy, ResultRewardService and auction before any
success packet. Versus payouts are an explicit zero-sum local policy; computer
payouts reuse proved offline client arithmetic, not a historical online rule.
"""

import hashlib
import json
import re
from copy import copy, deepcopy
from dataclasses import dataclass, field

from tmserver.cards.model import Card, card_values
from tmserver.economy.service import MAX_MONEY, EconomyError


class MatchEconomyError(EconomyError):
    pass


def _int(x, lo, hi):
    if type(x) is not int or not lo <= x <= hi:
        raise MatchEconomyError("invalid match economy integer")
    return x


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise MatchEconomyError("invalid durable round ID")
    return value


def _trunc(n, d):
    return (abs(n) // d) * (-1 if n < 0 else 1)


def _result(scores, ratings, n):
    scores, ratings = tuple(scores), tuple(ratings)
    if len(scores) != n or len(ratings) != n or n not in (2, 3):
        raise MatchEconomyError("invalid result extent")
    for x in scores:
        _int(x, 0, 25)
    for x in ratings:
        _int(x, 0, 99999)
    if not 1 <= sum(scores) <= 25:
        raise MatchEconomyError("invalid ordinary board score total")
    return scores, ratings


def offline_payout(scores, ratings, stake, seat=0):
    """Original100CC712..100CC7A6 within ordinary board/rating bounds.

    Returns signed arithmetic, including a possible negative high-rating value.
    The hosted policy credits max(0,value); online money animation ignores<=0.
    """
    scores, ratings = _result(scores, ratings, len(scores))
    _int(stake, 0, 2000)
    _int(seat, 0, len(scores) - 1)
    if len(set(scores)) == 1:
        return stake
    lower = sum(value < scores[seat] for value in scores)
    mean = _trunc(sum(ratings) - ratings[seat], len(scores) - 1)
    factor = 100 + _trunc(400 - mean, 2)
    perfect = 3 if scores[seat] == sum(scores) else 1
    return _trunc(factor * (stake + 100) * perfect * lower, 100)


def next_stake(stake, double):
    _int(stake, 0, 2000)
    _int(double, 0, 1)
    return min(2000, stake * (1 + double))


@dataclass
class _Round:
    id: str
    seats: dict
    mode: str
    player_count: int
    stake: int
    money_enabled: bool = True
    money: str = "escrow"
    payouts: dict = field(default_factory=dict)
    result: tuple | None = None
    cbc: dict = field(default_factory=dict)
    transfer_digest: str | None = None
    extras: dict = field(default_factory=dict)
    handed: set = field(default_factory=set)
    recovered: set = field(default_factory=set)
    closed: bool = False


class MatchEconomy:

    def __init__(self, economy):
        self.economy = economy
        self._rounds = {}

    def stage_state(self, economy, keys):
        keys = frozenset(keys)
        staged = copy(self)
        staged.economy = economy
        staged._rounds = {
            identity: deepcopy(row) if keys.intersection(row.seats.values()) else row
            for identity, row in self._rounds.items()
        }
        return staged

    def publish_state(self, staged):
        self._rounds = staged._rounds

    def status(self, round_id):
        row = self._row(round_id)
        return {
            "id": row.id,
            "seats": dict(row.seats),
            "stake": row.stake,
            "money": row.money,
            "money_enabled": row.money_enabled,
            "has_result": row.result is not None,
            "cards_settled": row.transfer_digest is not None,
            "handed": tuple(sorted(row.handed)),
            "recovered": tuple(sorted(row.recovered)),
            "closed": row.closed,
            "payouts": dict(row.payouts),
        }

    def _row(self, round_id):
        try:
            return self._rounds[_id(round_id)]
        except KeyError as exc:
            raise MatchEconomyError("unknown match round") from exc

    def _tokens(self, row, reservations):
        if not isinstance(reservations, dict) or set(reservations) != set(row.seats):
            raise MatchEconomyError("reservations do not cover every human seat")
        views = {seat: self.economy.match_account(token) for seat, token in reservations.items()}
        if any(view.key != row.seats[seat] for seat, view in views.items()):
            raise MatchEconomyError("round account differs from authenticated reservation")
        return views

    def _cards(self, cards):
        cards = tuple(self.economy.catalog.validate(Card.from_card(c)) for c in cards)
        if len(cards) > 30 or any(c.deck != 255 for c in cards):
            raise MatchEconomyError("invalid match reward batch")
        return cards

    def begin(
        self, round_id, reservations, stake, *, mode="versus", player_count=2, money_enabled=True
    ):
        """Debit once before VsInit, accepted ComGame06 or positive rematch.

        reservations maps HUMAN wire seats to live MatchReservations. Versus
        needs every seat; computer needs exactly one human. Caller must wait for
        every Continue acceptance before beginning a rematch round.
        """
        _id(round_id)
        _int(stake, 0, 2000)
        _int(player_count, 2, 3)
        if type(money_enabled) is not bool or not money_enabled and stake:
            raise MatchEconomyError("disabled match money requires zero stake")
        if mode not in ("versus", "computer"):
            raise MatchEconomyError("unsupported stake mode")
        if not isinstance(reservations, dict) or not reservations:
            raise MatchEconomyError("human reservations required")
        if any(type(s) is not int or not 0 <= s < player_count for s in reservations):
            raise MatchEconomyError("invalid human seat")
        if (mode == "versus" and set(reservations) != set(range(player_count))) or (
            mode == "computer" and len(reservations) != 1
        ):
            raise MatchEconomyError("mode and human seats disagree")
        views = {s: self.economy.match_account(t) for s, t in reservations.items()}
        if any(len(view.cards) < 5 for view in views.values()):
            raise MatchEconomyError("every human needs at least five owned cards before a round")
        seats = {s: v.key for s, v in views.items()}
        if len(set(seats.values())) != len(seats):
            raise MatchEconomyError("duplicate round account")
        existing = self._rounds.get(round_id)
        if existing is not None:
            if (
                existing.seats,
                existing.stake,
                existing.mode,
                existing.player_count,
                existing.money_enabled,
            ) != (seats, stake, mode, player_count, money_enabled):
                raise MatchEconomyError("round ID was reused with different admission")
            if existing.closed or existing.money != "escrow":
                raise MatchEconomyError("round admission is already finished")
            return dict(reservations)
        if any(
            not r.closed and set(seats.values()) & set(r.seats.values())
            for r in self._rounds.values()
        ):
            raise MatchEconomyError("account already has an unsettled round")
        refreshed = self.economy.commit_match_money([(t, stake, 0) for t in reservations.values()])
        self._rounds[round_id] = _Round(round_id, seats, mode, player_count, stake, money_enabled)
        return {s: refreshed[k] for s, k in seats.items()}

    def quote(self, round_id, reservations, scores, ratings):
        """Pure actual positive credits, capped before producing ResultData D1."""
        row = self._row(round_id)
        views = self._tokens(row, reservations)
        scores, ratings = _result(scores, ratings, row.player_count)
        if row.result is not None:
            if row.result != (scores, ratings):
                raise MatchEconomyError("completed result changed")
            return dict(row.payouts)
        if row.money != "escrow" or row.closed:
            raise MatchEconomyError("round cannot produce a result")
        if not row.money_enabled:
            return {s: 0 for s in views}
        if row.mode == "computer":
            return {
                s: min(MAX_MONEY - v.balance, max(0, offline_payout(scores, ratings, row.stake, s)))
                for s, v in views.items()
            }
        pot = row.stake * len(views)
        winners = [s for s in views if scores[s] == max(scores)]
        payouts = {s: 0 for s in views}
        share = pot // len(winners)
        for s in winners:
            payouts[s] = min(share, MAX_MONEY - views[s].balance)
        remainder = pot - sum(payouts.values())
        # Deterministic contributor refunds include division remainder and cap
        # overflow. No money is minted or silently discarded by versus play.
        for s in sorted(views):
            refund = min(remainder, row.stake, MAX_MONEY - views[s].balance - payouts[s])
            payouts[s] += refund
            remainder -= refund
        if remainder:
            raise MatchEconomyError("pot cannot fit contributor balances")
        return payouts

    def record_result(self, round_id, reservations, *, scores, ratings, cbc=None):
        """Credit once and hold CBC durably while ordinary choices are pending.

        Call after the original-five growth/stat update, before ResultData. Use
        quote beforehand for coherent D1/statistics. No player cards move here.
        """
        row = self._row(round_id)
        self._tokens(row, reservations)
        scores, ratings = _result(scores, ratings, row.player_count)
        cbc = {} if cbc is None else cbc
        if not isinstance(cbc, dict) or set(cbc) - set(row.seats):
            raise MatchEconomyError("CBC has an unknown human seat")
        cards = {s: self._cards(cbc.get(s, ())) for s in row.seats}
        if row.result is not None:
            if row.result != (scores, ratings) or row.cbc != cards:
                raise MatchEconomyError("result replay differs")
            return dict(reservations), dict(row.payouts)
        payouts = self.quote(round_id, reservations, scores, ratings)
        refreshed = self.economy.commit_match_money(
            [(t, 0, payouts[s]) for s, t in reservations.items()]
        )
        row.money = "paid"
        row.result = (scores, ratings)
        row.payouts = payouts
        row.cbc = cards
        return {s: refreshed[k] for s, k in row.seats.items()}, dict(payouts)

    @staticmethod
    def _transfer_digest(participants, transfers):
        body = {
            "hands": [[list(c.full()) for c in participants[s][1]] for s in sorted(participants)],
            "transfers": [
                [
                    a.winner,
                    a.loser,
                    a.original_slot,
                    card_values(a.source_card),
                    card_values(a.card),
                ]
                for a in transfers
            ],
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def settle_cards(self, round_id, participants, transfers):
        """Complete prize ledger only; persist losses plus durable award receipts.

        participants includes computer seats as(None,grown_original_five).
        Returns human seat→fresh token, human seat→(CBC + ordinary awards).
        Exact completed-round replay is a no-op even after restart; after restart
        recovery should be used instead of trying to resume the native scene.
        """
        row = self._row(round_id)
        if row.result is None:
            raise MatchEconomyError("result must precede card settlement")
        if not isinstance(participants, dict) or set(participants) != set(range(row.player_count)):
            raise MatchEconomyError("missing ordinary prize seat")
        participants = {
            s: (
                p[0],
                tuple(Card.from_card(c) for c in p[1]),
            )
            for s, p in participants.items()
        }
        reservations = {s: p[0] for s, p in participants.items() if p[0] is not None}
        self._tokens(row, reservations)
        transfers = tuple(transfers)
        digest = self._transfer_digest(participants, transfers)
        if row.transfer_digest is not None:
            if row.transfer_digest != digest:
                raise MatchEconomyError("card settlement replay differs")
            return dict(reservations), dict(row.extras)
        if row.closed:
            raise MatchEconomyError("round already closed")
        # Validate combined batches before any account loses a card.
        tentative = {s: list(row.cbc[s]) for s in row.seats}
        for award in transfers:
            if award.winner in tentative:
                tentative[award.winner].append(award.card)
        for cards in tentative.values():
            self._cards(cards)
        refreshed, awards = self.economy.commit_match_transfers(participants, transfers)
        row.transfer_digest = digest
        row.extras = {s: row.cbc[s] + tuple(awards[s]) for s in row.seats}
        return {s: refreshed[k] for s, k in row.seats.items()}, dict(row.extras)

    @staticmethod
    def _reward_id(row, key):
        return (
            "match:"
            + hashlib.sha256(json.dumps([row.id, key], separators=(",", ":")).encode()).hexdigest()
        )

    def handoff(self, round_id, reservations, result_rewards):
        """Stage combined awards in the existing native Continue/overflow flow."""
        row = self._row(round_id)
        self._tokens(row, reservations)
        if row.transfer_digest is None or row.recovered:
            raise MatchEconomyError("card settlement is not available for handoff")
        for s, key in row.seats.items():
            pending = result_rewards.status(key)
            if pending is not None and pending["reward_id"] != self._reward_id(row, key):
                raise MatchEconomyError("another result inventory transaction is pending")
        for s, key in row.seats.items():
            if s in row.handed:
                continue
            result_rewards.stage(key, reservations[s], self._reward_id(row, key), s, row.extras[s])
            row.handed.add(s)
        row.closed = True
        return dict(reservations)

    def abort(self, round_id, reservations, auction):
        """Refund unfinished escrow and recover unhanded earned cards once.

        A completed result keeps its payout. Unresolved ordinary choices cause
        no losses; CBC still recovers. A committed transfer recovers every award.
        Already handed cards belong to ResultRewardService's own recovery path.
        The caller must persist all ledgers atomically, then release table tokens.
        """
        row = self._row(round_id)
        views = self._tokens(row, reservations)
        if row.money == "escrow":
            refreshed = self.economy.commit_match_money(
                [(reservations[s], 0, row.stake) for s in views]
            )
            reservations = {s: refreshed[k] for s, k in row.seats.items()}
            row.money = "refunded"
        rewards = row.extras if row.transfer_digest is not None else row.cbc
        for s, key in row.seats.items():
            if s in row.handed or s in row.recovered:
                continue
            cards = rewards.get(s, ())
            if cards:
                auction.enqueue_returned(key, self._reward_id(row, key), cards)
            row.recovered.add(s)
        row.closed = True
        return dict(reservations)

    def recover_all(self, auction):
        """Startup only, before listeners: recover durable interrupted rounds."""
        targets = [row for row in self._rounds.values() if not row.closed]
        if any(self.economy.is_busy(key) for row in targets for key in row.seats.values()):
            raise MatchEconomyError("startup recovery requires idle accounts")
        count = 0
        for row in targets:
            reservations = {s: self.economy.reserve_match(key) for s, key in row.seats.items()}
            try:
                reservations = self.abort(row.id, reservations, auction)
                count += 1
            finally:
                for token in reservations.values():
                    self.economy.release_match(token)
        return count

    def snapshot(self):
        return {
            "version": 1,
            "rounds": [
                {
                    "id": r.id,
                    "seats": {str(s): k for s, k in r.seats.items()},
                    "mode": r.mode,
                    "player_count": r.player_count,
                    "stake": r.stake,
                    "money_enabled": r.money_enabled,
                    "money": r.money,
                    "payouts": {str(s): v for s, v in r.payouts.items()},
                    "result": r.result,
                    "cbc": {str(s): [list(c.full()) for c in cards] for s, cards in r.cbc.items()},
                    "transfer_digest": r.transfer_digest,
                    "extras": {
                        str(s): [list(c.full()) for c in cards] for s, cards in r.extras.items()
                    },
                    "handed": sorted(r.handed),
                    "recovered": sorted(r.recovered),
                    "closed": r.closed,
                }
                for r in self._rounds.values()
            ],
        }

    @classmethod
    def restore(cls, economy, snapshot=None):
        obj = cls(economy)
        if snapshot is None:
            return obj
        try:
            if set(snapshot) != {"version", "rounds"} or snapshot["version"] != 1:
                raise MatchEconomyError("invalid match economy snapshot")
            for d in snapshot["rounds"]:
                if set(d) - {"money_enabled"} != {
                    "id",
                    "seats",
                    "mode",
                    "player_count",
                    "stake",
                    "money",
                    "payouts",
                    "result",
                    "cbc",
                    "transfer_digest",
                    "extras",
                    "handed",
                    "recovered",
                    "closed",
                }:
                    raise MatchEconomyError("invalid saved round fields")
                rid = _id(d["id"])
                n = _int(d["player_count"], 2, 3)
                stake = _int(d["stake"], 0, 2000)
                seats = {int(s): k for s, k in d["seats"].items()}
                if any(str(s) not in d["seats"] or not 0 <= s < n for s in seats) or len(
                    set(seats.values())
                ) != len(seats):
                    raise MatchEconomyError("invalid saved human seats")
                for key in seats.values():
                    economy.account_view(key)
                if d["mode"] == "versus":
                    valid = set(seats) == set(range(n))
                elif d["mode"] == "computer":
                    valid = len(seats) == 1
                else:
                    valid = False
                if not valid or rid in obj._rounds:
                    raise MatchEconomyError("invalid saved mode or duplicate round")
                enabled = d.get("money_enabled", True)
                if type(enabled) is not bool or not enabled and stake:
                    raise MatchEconomyError("invalid saved money capability")
                row = _Round(rid, seats, d["mode"], n, stake, enabled)
                row.money = d["money"]
                row.closed = d["closed"]
                if row.money not in ("escrow", "paid", "refunded") or type(row.closed) is not bool:
                    raise MatchEconomyError("invalid saved money phase")
                row.result = None if d["result"] is None else _result(*d["result"], n)
                row.payouts = {int(s): _int(v, 0, MAX_MONEY) for s, v in d["payouts"].items()}
                row.cbc = {
                    int(s): obj._cards(Card(*c) for c in cards) for s, cards in d["cbc"].items()
                }
                row.extras = {
                    int(s): obj._cards(Card(*c) for c in cards) for s, cards in d["extras"].items()
                }
                row.transfer_digest = d["transfer_digest"]
                row.handed = set(d["handed"])
                row.recovered = set(d["recovered"])
                if (
                    any(type(s) is not int or s not in seats for s in (*row.handed, *row.recovered))
                    or row.handed & row.recovered
                ):
                    raise MatchEconomyError("invalid saved recovery seat")
                if row.result is None:
                    if (
                        row.money == "paid"
                        or row.payouts
                        or row.cbc
                        or row.transfer_digest is not None
                        or row.extras
                        or row.handed
                    ):
                        raise MatchEconomyError("uncompleted round contains result claims")
                elif (
                    row.money != "paid"
                    or set(row.payouts) != set(seats)
                    or set(row.cbc) != set(seats)
                ):
                    raise MatchEconomyError("completed result lacks payment/CBC receipt")
                elif not row.money_enabled and any(row.payouts.values()):
                    raise MatchEconomyError("disabled money round contains a payout")
                elif row.mode == "versus" and sum(row.payouts.values()) != row.stake * len(seats):
                    raise MatchEconomyError("saved versus payouts do not conserve the pot")
                elif row.mode == "computer" and any(
                    row.payouts[s] > max(0, offline_payout(*row.result, row.stake, s))
                    for s in seats
                ):
                    raise MatchEconomyError("saved computer payout exceeds original formula")
                if row.transfer_digest is None:
                    if row.extras or row.handed:
                        raise MatchEconomyError("unsettled cards contain handoff")
                elif not re.fullmatch("[0-9a-f]{64}", row.transfer_digest) or set(
                    row.extras
                ) != set(seats):
                    raise MatchEconomyError("invalid saved transfer receipt")
                elif any(row.extras[s][: len(row.cbc[s])] != row.cbc[s] for s in seats):
                    raise MatchEconomyError("saved settlement lost its CBC prefix")
                if row.closed and row.handed | row.recovered != set(seats):
                    raise MatchEconomyError("closed round has unhandled cards")
                if row.closed and row.money == "escrow":
                    raise MatchEconomyError("closed round retained unfinished escrow")
                if row.money == "refunded" and not row.closed:
                    raise MatchEconomyError("refund left round active")
                if not row.closed and any(
                    not old.closed and set(seats.values()) & set(old.seats.values())
                    for old in obj._rounds.values()
                ):
                    raise MatchEconomyError("overlapping active saved rounds")
                obj._rounds[rid] = row
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            if isinstance(exc, MatchEconomyError):
                raise
            raise MatchEconomyError("invalid saved match economy") from exc
        return obj
