"""Automatic CBC rewards, accepted at the client's Continue step.

Growth and statistics are already part of the saved result; this module stages
only earned extras and the result's Erase/Decks edits. Server choice: interrupted
extras come back through the auction Returned Card screen. Save economy, auction
and this snapshot together before sending completion.
"""

import hashlib
import json
import re
from copy import copy
from dataclasses import dataclass, replace

from tmserver.economy.models import AccountView, MatchReservation
from tmserver.cards.model import Card
from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import parse_named
from tmserver.cards.edits import apply_card_edits, parse_edit_records


class ResultRewardError(EconomyError):
    pass


def _int(value, maximum=0xFFFFFFFF):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{1,10}", value)
        or int(value) > maximum
    ):
        raise ResultRewardError("invalid result edit integer")
    return int(value)


def _fields(value, names):
    if set(value) != set(names):
        raise ResultRewardError("unexpected result edit fields")


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ResultRewardError("result requires a stable unique reward ID")
    return value


def _digest(cards):
    return hashlib.sha256(
        json.dumps([c.full() for c in cards], separators=(",", ":")).encode()
    ).hexdigest()


@dataclass
class _Pending:
    key: str
    reward_id: str
    seat: int
    extras: tuple
    token: object = None
    cards: list | None = None
    names: dict | None = None


@dataclass(frozen=True)
class _Accepted:
    """Ephemeral deck editor; immutable so staged saves can safely share it."""

    seat: int
    token: MatchReservation | None
    account: AccountView
    profile_basis: bytes


class ResultRewardService:
    def __init__(self, economy, auction):
        self.economy, self.auction = economy, auction
        self._known = {}
        self._pending = {}
        self._accepted = {}

    def stage_state(self, economy, auction, keys):
        staged = copy(self)
        staged.economy, staged.auction = economy, auction
        staged._known = dict(self._known)
        staged._accepted = dict(self._accepted)
        staged._pending = dict(self._pending)
        for key in set(keys).intersection(self._pending):
            row = self._pending[key]
            staged._pending[key] = replace(
                row,
                cards=None if row.cards is None else list(row.cards),
                names=None if row.names is None else dict(row.names),
            )
        return staged

    def publish_state(self, staged):
        self._known = staged._known
        self._pending = staged._pending
        self._accepted = staged._accepted

    def _cards(self, cards):
        values = tuple(
            self.economy.catalog.validate(Card.from_card(c))
            for c in cards
        )
        if len(values) > 30 or any(c.deck != 255 for c in values):
            raise ResultRewardError("generated reward batch exceeds native capacity or has a deck")
        return values

    def stage(self, key, reservation, reward_id, seat, extras):
        """Trusted engine snapshot, after atomic original-five growth/stat commit.

        reward_id must be unique per logical match round/account, including a
        table-instance ID. It is not supplied by the client. Replaying the same
        ID/content is a no-op; reusing an ID for different cards is rejected.
        """
        _id(reward_id)
        if type(seat) is not int or seat not in (0, 1, 2):
            raise ResultRewardError("unsupported result seat")
        view = self.economy.match_account(reservation)
        if view.key != key:
            raise ResultRewardError("result account differs from table reservation")
        extras = self._cards(extras)
        digest = _digest(extras)
        identity = (key, reward_id)
        if identity in self._known:
            if self._known[identity] != digest:
                raise ResultRewardError("reward ID reused for different cards")
            return False
        if key in self._pending:
            raise ResultRewardError("previous result has not been accepted or recovered")
        self._pending[key] = _Pending(
            key,
            reward_id,
            seat,
            extras,
            reservation,
            list(view.cards) + list(extras),
            dict(view.deck_names),
        )
        self._known[identity] = digest
        self._accepted.pop(key, None)
        return True

    def has_pending(self, key):
        return key in self._pending

    def status(self, key):
        pending = self._pending.get(key)
        if pending is None:
            return None
        return dict(
            reward_id=pending.reward_id,
            seat=pending.seat,
            extra_count=len(pending.extras),
            working_count=None if pending.cards is None else len(pending.cards),
            recovery_only=pending.token is None,
        )

    def reservation(self, key):
        pending = self._pending.get(key)
        if pending is not None:
            return pending.token
        accepted = self._accepted.get(key)
        return accepted.token if accepted is not None else None

    def _profile_basis(self, key):
        profile = bytearray(self.economy.profile_bytes(key))
        profile[0x34:0x38] = b"\0" * 4
        return hashlib.sha256(profile).digest()

    def refresh_reservation(self, key, reservation):
        """Rebind an accepted late-Decks context after a trusted money change.

        A new round's stake or abort refund replaces the table capability, but
        leaves the accepted result's inventory and deck editor intact until
        CardSelect. Only balance may differ: canonical profile bytes, identity,
        cards, deck associations and names must otherwise match exactly. This
        does not rebind pending grants, revive an idle context or issue rewards.
        """
        if key in self._pending:
            raise ResultRewardError("pending result cannot change its reservation")
        accepted = self._accepted.get(key)
        if accepted is None:
            return False
        if accepted.token is None:
            raise ResultRewardError("released result context cannot be rebound")
        view = self.economy.match_account(reservation)
        if (
            view.key != key
            or replace(view, balance=accepted.account.balance) != accepted.account
            or self._profile_basis(key) != accepted.profile_basis
        ):
            raise ResultRewardError("result context changed beyond its money balance")
        self._accepted[key] = replace(accepted, token=reservation, account=view)
        return True

    def commit(self, key):
        """Call only for an authenticated, validated Continue; return fresh token."""
        pending = self._pending.get(key)
        if pending is None:
            token = self.reservation(key)
            if token is None:
                if key in self._accepted:
                    return None  # Already accepted and released.
                raise ResultRewardError("no active result to accept")
            self.economy.match_account(token)
            return token
        if pending.token is None:
            raise ResultRewardError("restored result requires recovery, not native resume")
        token = self.economy.commit_match_rewards(
            pending.token, pending.extras, cards=pending.cards, deck_names=pending.names
        )
        del self._pending[key]
        self._accepted[key] = _Accepted(
            pending.seat, token, token.account, self._profile_basis(key)
        )
        return token

    def save_continue_privacy(self, key, value):
        """Save validated Continue/PR without invalidating its own deck editor.

        Only rebase a still-current profile fingerprint. An unrelated earlier
        profile change must continue to invalidate a later money-only refresh.
        The caller stages this with result acceptance in one transaction.
        """
        accepted = self._accepted.get(key)
        current = accepted is not None and accepted.profile_basis == self._profile_basis(key)
        self.economy.save_ranking_privacy(key, value)
        if current:
            self._accepted[key] = replace(accepted, profile_basis=self._profile_basis(key))

    def release_context(self, key):
        """After table-token release, keep only a bounded delayed-Decks context.

        The adapter clears it on disconnect/new admission/CardSelect. Any other
        account mutation or reservation invalidates it. No late Erase admitted.
        """
        if key in self._pending:
            raise ResultRewardError("unaccepted result cannot become an idle edit context")
        accepted = self._accepted.get(key)
        if accepted is None:
            return False
        if self.economy.is_reserved(key):
            raise ResultRewardError("release table token before idle edit context")
        if self.economy.account_view(key) != accepted.account:
            del self._accepted[key]
            raise ResultRewardError("account changed before result context release")
        self._accepted[key] = replace(accepted, token=None)
        return True

    def handle(self, key, payload):
        """43/10 Erase and 43/22 Decks only; None leaves other messages to match.

        After acceptance only delayed Decks is admitted, either with its live
        table token or a release_context snapshot of an unchanged idle account.
        Adapter copies reservation(key) after edits with a live token and calls
        forget(key) on disconnect/new admission/new CardSelect.
        """
        if not payload.startswith(b"GTM0G43"):
            return None
        family, subtype, seat, sections = parse_named(payload)
        if subtype not in (0x10, 0x22):
            return None
        pending = self._pending.get(key)
        accepted = self._accepted.get(key)
        if pending is None and accepted is None:
            raise ResultRewardError("no result inventory edit context")
        expected_seat = pending.seat if pending is not None else accepted.seat
        if seat != expected_seat:
            raise ResultRewardError("result edit header is not authenticated seat")
        if pending is not None:
            if pending.token is None:
                raise ResultRewardError("restored rewards require collection recovery")
            self.economy.match_account(pending.token)
            cards, names = list(pending.cards), dict(pending.names)
        else:
            if subtype != 0x22:
                raise ResultRewardError("only delayed Decks is accepted after Continue")
            if accepted.token is None:
                view = self.economy.account_view(key)
                if self.economy.is_reserved(key) or view != accepted.account:
                    del self._accepted[key]
                    raise ResultRewardError("released result edit context is no longer current")
            else:
                view = self.economy.match_account(accepted.token)
            cards, names = list(view.cards), dict(view.deck_names)
        token = "Erase" if subtype == 0x10 else "Decks"
        if token not in sections:
            raise ResultRewardError("missing result edit section")
        f = sections[token]
        if _int(f.get("P"), 2) != seat:
            raise ResultRewardError("result edit body side mismatch")
        if subtype == 0x10:
            mode = _int(f.get("Mode"), 2)
            if mode == 2:
                _fields(sections, ("Erase",))
                _fields(f, ("P", "Mode"))
                return []
            if mode != 1:
                raise ResultRewardError("unsupported result Erase mode")
            _fields(f, ("P", "Mode", "C"))
        else:
            _fields(f, ("P", "C"))
        values, renamed = parse_edit_records(
            sections, token, number=_int, error=ResultRewardError
        )
        cards, indices = apply_card_edits(
            cards, values, discard=subtype == 0x10, error=ResultRewardError
        )
        names.update(renamed)
        if pending is not None:
            pending.cards, pending.names = cards, names
        else:
            if accepted.token is None:
                try:
                    self.economy.apply_deck_changes(key, [cards[i] for i in indices], renamed)
                except EconomyError:
                    del self._accepted[key]
                    raise
                self._accepted[key] = _Accepted(
                    seat,
                    None,
                    self.economy.account_view(key),
                    self._profile_basis(key),
                )
            else:
                refreshed = self.economy.commit_match_rewards(
                    accepted.token, cards=cards, deck_names=names
                )
                self._accepted[key] = _Accepted(
                    seat, refreshed, refreshed.account, self._profile_basis(key)
                )
        return []

    def recover(self, key):
        """Move unaccepted extras once to the real Returned Card collection UI.

        Unaccepted Erase/Decks changes roll back; earned cards never vanish.
        Does not release a live match token: table/session cleanup owns that.
        An active auction collection is not invalidated; these IDs wait behind it.
        """
        pending = self._pending.get(key)
        if pending is None:
            return False
        source = (
            "result:"
            + hashlib.sha256(
                json.dumps([key, pending.reward_id], separators=(",", ":")).encode()
            ).hexdigest()
        )
        self.auction.enqueue_returned(key, source, pending.extras)
        del self._pending[key]
        self._accepted.pop(key, None)
        return True

    def recover_all(self):
        count = 0
        for key in tuple(self._pending):
            count += self.recover(key)
        return count

    def prune_known(self):
        """Startup only, after recover_all: keep reward IDs of pending rows only.

        Reward IDs embed the round's table instance ID, so no later stage can
        reuse one from an earlier process. A pending row keeps its ID because
        restore checks the row against it.
        """
        pending = {(row.key, row.reward_id) for row in self._pending.values()}
        stale = [identity for identity in self._known if identity not in pending]
        for identity in stale:
            del self._known[identity]
        return len(stale)

    def forget(self, key):
        if key in self._pending:
            raise ResultRewardError("recover or accept pending rewards before leaving table")
        self._accepted.pop(key, None)

    def snapshot(self):
        # No wire/GUI resumption is invented. Restored pending rows are recovery-
        # only; unaccepted edits intentionally roll back to committed account data.
        return {
            "version": 1,
            "known": [
                {"key": key, "id": rid, "digest": digest}
                for (key, rid), digest in sorted(self._known.items())
            ],
            "pending": [
                {
                    "key": p.key,
                    "id": p.reward_id,
                    "seat": p.seat,
                    "extras": [list(c.full()) for c in p.extras],
                }
                for p in self._pending.values()
            ],
        }

    @classmethod
    def restore(cls, economy, auction, snapshot=None):
        result = cls(economy, auction)
        if snapshot is None:
            return result
        try:
            if not isinstance(snapshot, dict) or set(snapshot) != {"version", "known", "pending"}:
                raise ResultRewardError("unsupported result reward snapshot fields")
            if snapshot["version"] != 1:
                raise ResultRewardError("unsupported result reward snapshot")
            for row in snapshot["known"]:
                if not isinstance(row, dict) or set(row) != {"key", "id", "digest"}:
                    raise ResultRewardError("unsupported known result reward fields")
                economy.account_view(row["key"])
                rid = _id(row["id"])
                identity = (row["key"], rid)
                if identity in result._known or not re.fullmatch(r"[0-9a-f]{64}", row["digest"]):
                    raise ResultRewardError("invalid known result reward")
                result._known[identity] = row["digest"]
            for row in snapshot["pending"]:
                if not isinstance(row, dict) or set(row) != {"key", "id", "seat", "extras"}:
                    raise ResultRewardError("unsupported pending result reward fields")
                key = row["key"]
                economy.account_view(key)
                rid = _id(row["id"])
                extras = result._cards(Card(*c) for c in row["extras"])
                if (
                    key in result._pending
                    or type(row["seat"]) is not int
                    or row["seat"] not in (0, 1, 2)
                ):
                    raise ResultRewardError("invalid pending result identity")
                if result._known.get((key, rid)) != _digest(extras):
                    raise ResultRewardError("pending reward disagrees with durable ID")
                result._pending[key] = _Pending(key, rid, row["seat"], extras)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            if isinstance(exc, ResultRewardError):
                raise
            raise ResultRewardError("invalid result reward snapshot") from exc
        return result
