"""Granted F/90 event prizes and their collection.

The caller saves the economy, EventHost and this ledger together before replying.
Money has no client acknowledgement, so it is claimed before EcmInit. Cards are
claimed only by B2/Get, before reveal or after an overflow Erase/Decks. Server
choice: prizes come only from explicit grants. Layout: TetraPackets
flows/event-cards.md.
"""

import struct
from copy import copy
from dataclasses import dataclass, replace
from functools import partial

from tmserver.cards.catalog import collection_points
from tmserver.economy.models import MAX_MONEY
from tmserver.cards.model import Card
from tmserver.checks import uint
from tmserver.economy.errors import EconomyError
from tmserver.economy.packets import named, parse_named
from tmserver.cards.edits import apply_card_edits, parse_edit_records
from tmserver.events.models import EventError
from tmserver.events.packets import _id, _keys, _number

EventCardError = EventError


_integer = partial(uint, error=EventCardError, what="event delivery integer")


@dataclass
class _Grant:
    key: str
    event_id: str
    id: str
    cards: tuple
    money: int
    card_offset: int = 0
    money_claimed: int = 0


@dataclass
class _Collection:
    event_id: str
    number: int
    token: object
    selected: tuple
    incoming: tuple
    cards: list
    names: dict
    replies: tuple
    accepted: bool = False


class EventCardService:

    def __init__(self, economy, host):
        if host.economy is not economy:
            raise EventCardError("event host uses a different economy")
        self.economy = economy
        self.host = host
        self._grants = {}
        self._last = {}
        self._active = {}
        self._closed = {}

    def stage_state(self, economy, host, keys):
        """Copy claim offsets and editors only for participating accounts."""
        keys = frozenset(keys)
        staged = copy(self)
        staged.economy, staged.host = economy, host
        staged._grants = {
            identity: replace(row) if row.key in keys else row
            for identity, row in self._grants.items()
        }
        staged._last = dict(self._last)
        staged._closed = dict(self._closed)
        staged._active = dict(self._active)
        for key in keys.intersection(self._active):
            row = self._active[key]
            staged._active[key] = replace(row, cards=list(row.cards), names=dict(row.names))
        return staged

    def publish_state(self, staged):
        self._grants = staged._grants
        self._last = staged._last
        self._closed = staged._closed
        self._active = staged._active

    def grant(self, key, event_id, grant_id, *, cards=(), money=0):
        """Trusted policy input only. Exact durable ID replay never pays twice."""
        self.host.config(event_id)
        _id(grant_id)
        if self.host.member(key, event_id) is None:
            raise EventCardError("grant requires a registered event member")
        cards = self.economy.catalog.validate_batch(
            (Card.from_card(c) for c in cards), error=EventCardError
        )
        _integer(money, MAX_MONEY)
        identity = (key, event_id, grant_id)
        old = self._grants.get(identity)
        if old is not None:
            if (old.cards, old.money) != (cards, money):
                raise EventCardError("grant ID replay differs")
            return False
        self._grants[identity] = _Grant(key, event_id, grant_id, cards, money)
        return True

    def pending(self, key, event_id):
        self.economy.account_view(key)
        self.host.config(event_id)
        rows = [g for g in self._grants.values() if g.key == key and g.event_id == event_id]
        return {
            "cards": sum(len(g.cards) - g.card_offset for g in rows),
            "money": sum(g.money - g.money_claimed for g in rows),
        }

    def is_active(self, key):
        return key in self._active

    def _init(self, key, view, number, money, cards):
        rt = struct.unpack_from("<I", self.economy.profile_bytes(key), 0x128)[0]
        body = f"@EcmInit=/N={number}/RA={view.rating}/M={view.balance}/CP={collection_points(view.cards)}/S={len(view.cards)}/RT={rt}/PM={money}/C={len(cards)}"
        body += "".join(f"@N{i}=/D=" + "|".join(map(str, c.full())) for i, c in enumerate(cards))
        return named(0xB2, 0x13, 0, body)

    def _entry(self, key, payload, event_id):
        if event_id is None:
            if len(self.host.configs) != 1:
                raise EventCardError("event card service identity is ambiguous")
            event_id = next(iter(self.host.configs))
        self.host.config(event_id)
        current = self._active.get(key)
        if current is not None:
            self.host.authenticate_entry(key, payload, route="card")
            if current.event_id != event_id:
                raise EventCardError("another event collection is active")
            return list(current.replies)
        replies = self.host.handle(key, payload, event_id=event_id, route="card")
        if int(parse_named(replies[0])[3]["CardEnt"]["Ans"]) <= 0:
            return replies
        token = self.economy.reserve_auction(key)
        view = token.account
        try:
            number = self._last.get(key, 0) % 255 + 1
            rows = [
                (ident, g)
                for ident, g in self._grants.items()
                if g.key == key and g.event_id == event_id
            ]
            money = 0
            credits = []
            for ident, g in rows:
                amount = min(g.money - g.money_claimed, MAX_MONEY - view.balance - money)
                if amount:
                    credits.append((ident, amount))
                    money += amount
            # At most nine unclaimed cards, in grant order, that fit the
            # original 447-byte named body.
            selected = [(ident, i) for ident, g in rows for i in range(g.card_offset, len(g.cards))]
            selected = selected[:9]
            while True:
                incoming = [self._grants[ident].cards[i] for ident, i in selected]
                try:
                    init = self._init(key, view, number, money, incoming)
                    break
                except EconomyError:
                    if not selected:
                        raise
                    selected.pop()
            token = self.economy.commit_auction(token, credit=money, keep_reserved=True)
        except Exception:
            self.economy.release_auction(token)
            self.host.complete_admission(key)
            raise
        for ident, amount in credits:
            self._grants[ident].money_claimed += amount
        self._last[key] = number
        self._active[key] = _Collection(
            event_id,
            number,
            token,
            tuple(selected),
            tuple(incoming),
            list(view.cards) + incoming,
            dict(view.deck_names),
            tuple(replies) + (init,),
            not incoming,
        )
        self._closed.pop(key, None)
        self.host.complete_admission(key)
        return replies + [init]

    def handle(self, key, payload, *, event_id=None):
        if payload[:7].upper() not in (b"GTM0G90", b"GTM0GB2"):
            return None
        family, subtype, number, sections = parse_named(payload)
        if family == 0x90:
            return self._entry(key, payload, event_id)
        state = self._active.get(key)
        if state is None:
            previous = self._closed.get(key)
            if (
                previous is not None
                and previous[0] == number
                and subtype == 0x17
                and sections == {"Quit": {"No": str(number)}}
            ):
                if event_id is not None and event_id != previous[2]:
                    raise EventCardError("wrong event collection service")
                return [previous[1]]
            return None
        if number != state.number or event_id is not None and event_id != state.event_id:
            raise EventCardError("wrong event collection session/service")
        if subtype in (0x15, 0x17):
            token = "Get" if subtype == 0x15 else "Quit"
            if sections != {token: {"No": str(number)}}:
                raise EventCardError("invalid event collection barrier")
            if subtype == 0x15:
                if state.accepted:
                    return []
                for ident, index in state.selected:
                    g = self._grants[ident]
                    if index < g.card_offset:
                        raise EventCardError("stale event grant selection")
                refreshed = self.economy.commit_auction(
                    state.token,
                    add=state.incoming,
                    cards=state.cards,
                    deck_names=state.names,
                    allow_discard=True,
                    keep_reserved=True,
                )
                for ident, index in state.selected:
                    self._grants[ident].card_offset = index + 1
                state.token = refreshed
                state.accepted = True
                # An entry retry after Get must never reimport the claimed cards.
                state.replies = (
                    state.replies[0],
                    self._init(key, refreshed.account, number, 0, ()),
                )
                return []
            if not state.accepted:
                raise EventCardError("cards require Get before Quit")
            reply = named(0xB2, 0x17, number, f"@Quit=/N={number}/D=0/B=0")
            self.economy.release_auction(state.token)
            del self._active[key]
            self._closed[key] = (number, reply, state.event_id)
            return [reply]
        if subtype in (0x10, 0x22):
            cards, names = self._edit(state, subtype, sections)
            if state.accepted:
                state.token = self.economy.commit_auction(
                    state.token, cards=cards, deck_names=names, allow_discard=True, keep_reserved=True
                )
                state.replies = (
                    state.replies[0],
                    self._init(key, state.token.account, number, 0, ()),
                )
            state.cards, state.names = cards, names
            return []
        raise EventCardError("unsupported event collection operation")

    @staticmethod
    def _edit(state, subtype, sections):
        token = "Erase" if subtype == 0x10 else "Decks"
        if subtype == 0x10 and sections == {"Erase": {"Mode": "2"}}:
            return list(state.cards), dict(state.names)
        fields = sections.get(token, {})
        _keys(fields, ("Mode", "C") if subtype == 0x10 else ("C",))
        if subtype == 0x10 and fields["Mode"] != "1":
            raise EventCardError("invalid event Erase mode")
        values, renamed = parse_edit_records(sections, token, number=_number, error=EventCardError)
        cards, _ = apply_card_edits(
            state.cards, values, discard=subtype == 0x10, error=EventCardError
        )
        return cards, state.names | renamed

    def disconnect(self, key):
        state = self._active.pop(key, None)
        if state is not None:
            self.economy.release_auction(state.token)
        self._closed.pop(key, None)

    def snapshot(self):
        return {
            "version": 1,
            "last_sessions": dict(self._last),
            "grants": [
                {
                    "key": g.key,
                    "event_id": g.event_id,
                    "id": g.id,
                    "cards": [list(c.full()) for c in g.cards],
                    "money": g.money,
                    "card_offset": g.card_offset,
                    "money_claimed": g.money_claimed,
                }
                for g in self._grants.values()
            ],
        }

    @classmethod
    def restore(cls, economy, host, snapshot=None):
        result = cls(economy, host)
        if snapshot is None:
            return result
        try:
            _keys(snapshot, ("version", "last_sessions", "grants"))
            if snapshot["version"] != 1:
                raise EventCardError("unsupported event grant snapshot")
            for key, value in snapshot["last_sessions"].items():
                economy.account_view(key)
                _integer(value, 255)
                if value == 0:
                    raise EventCardError("invalid saved event session")
                result._last[key] = value
            for row in snapshot["grants"]:
                _keys(
                    row, ("key", "event_id", "id", "cards", "money", "card_offset", "money_claimed")
                )
                cards = tuple(Card(*c) for c in row["cards"])
                if not result.grant(
                    row["key"], row["event_id"], row["id"], cards=cards, money=row["money"]
                ):
                    raise EventCardError("duplicate saved grant")
                g = result._grants[(row["key"], row["event_id"], row["id"])]
                g.card_offset = _integer(row["card_offset"], len(cards))
                g.money_claimed = _integer(row["money_claimed"], g.money)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            if isinstance(exc, EventCardError):
                raise
            raise EventCardError("invalid event card snapshot") from exc
        return result
