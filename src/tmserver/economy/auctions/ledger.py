"""Auction escrow; the caller saves accounts and the ledger together.

Server choice: no fees, a caller-supplied Unix-second clock, listings of 1..168
hours, 0..3 automatic reoffers, full bid escrow, separate refunds and no bids on
your own listing. BC is a revision token chosen by this server. Layout:
TetraPackets irc/A.md and file/auction-files.md.
"""

from copy import copy
from dataclasses import dataclass, replace
from functools import partial

from tmserver.cards.catalog import collection_points
from tmserver.checks import stable_id, uint
from tmserver.economy.models import MAX_MONEY
from tmserver.economy.auctions.models import (
    Actor,
    AuctionError,
    Bid,
    CollectionReservation,
    Listing,
    Pending,
)

from tmserver.economy.auctions.persistence import AuctionSnapshot, ExternalReturn

INCREMENTS = (20, 50, 100, 200, 500, 1000)
MAX_EXHIBITS = 30
MAX_DELIVERY = 30
_KINDS = ("payment", "returned", "won", "refund")


_uint = partial(uint, error=AuctionError, what="auction integer")


@dataclass
class _Collection:
    public: CollectionReservation
    token: object
    ids: dict
    phase: int = 0


class AuctionEconomy:
    def __init__(self, economy):
        self.economy = economy
        self._listings = {}
        self._history = {}
        self._pending = ()
        self._next_id = 1
        self._next_pending = 1
        self._collections = {}
        self._external_returns = {}

    def stage_state(self, economy, keys):
        """Isolate selected collection editors; retain their capability identity."""
        staged = copy(self)
        staged.economy = economy
        staged._external_returns = dict(self._external_returns)
        staged._listings = dict(self._listings)  # Frozen rows.
        staged._history = dict(self._history)  # Tuples of frozen bids.
        staged._collections = dict(self._collections)
        for key in set(keys).intersection(self._collections):
            staged._collections[key] = replace(self._collections[key])
        return staged

    def publish_state(self, staged):
        self._pending = staged._pending
        self._next_pending = staged._next_pending
        self._external_returns = staged._external_returns
        self._listings = staged._listings
        self._history = staged._history
        self._next_id = staged._next_id
        self._collections = staged._collections

    def _actor(self, key):
        v = self.economy.account_view(key)
        return Actor(
            v.key, v.nn, v.player_id, v.name, v.guild, v.rating, collection_points(v.cards)
        )

    def listings(self, *, active_only=True):
        return tuple(v for v in self._listings.values() if not active_only or v.status == "active")

    def listing(self, auction_id):
        try:
            return self._listings[auction_id]
        except KeyError as exc:
            raise AuctionError("auction ID does not exist", -8620) from exc

    def history(self, auction_id):
        self.listing(auction_id)
        return self._history[auction_id]

    def pending(self, key):
        self.economy.account_view(key)
        return tuple(p for p in self._pending if p.key == key)

    def enqueue_returned(self, key, source_id, cards):
        """Trusted recovery bridge, not an auction wire request.

        Atomically import an earned-card batch once. An already-active collection
        keeps its frozen IDs; this batch waits for the next collection session.
        Persist jointly with the source reward ledger before acknowledging it.
        """
        self.economy.account_view(key)
        stable_id(source_id, error=AuctionError, what="external return source ID")
        cards = self.economy.catalog.validate_batch(cards, error=AuctionError)
        value = (key, cards)
        if source_id in self._external_returns:
            if self._external_returns[source_id] != value:
                raise AuctionError("external return ID reused for different cards")
            return False
        if cards:
            _uint(self._next_pending + len(cards) - 1, minimum=1)
        entries = tuple(
            Pending(self._next_pending + i, key, "returned", card=c) for i, c in enumerate(cards)
        )
        self._pending += entries
        self._next_pending += len(cards)
        self._external_returns[source_id] = value
        return True

    def prune_external_returns(self):
        """Startup only, after match and result recovery: forget every source ID.

        Sources are match rounds (MatchEconomy.abort) and pending result rewards
        (ResultRewardService.recover). Once startup recovery has closed every
        round and recovered every pending row, neither can enqueue an old source
        again. The returned cards themselves stay pending for collection.
        """
        count = len(self._external_returns)
        self._external_returns = {}
        return count

    def active_exhibits(self, key):
        return sum(v.status == "active" and v.seller.key == key for v in self._listings.values())

    def _descriptor(self, card, values):
        metadata = tuple(values)
        if metadata:
            if len(metadata) != 16:
                raise AuctionError("invalid exhibit descriptor", -8609)
            for i, value in enumerate(metadata):
                _uint(value, 0xFFFFFFFF if i < 4 else 0xFFFF if i < 8 else 255)
            if (metadata[0], *metadata[8:14]) != (
                card.model,
                card.attack,
                card.physical,
                card.kind,
                card.magical,
                card.points,
                card.arrows,
            ):
                raise AuctionError("exhibit descriptor disagrees with owned card", -8609)
            if metadata[1] != self.economy.catalog.sale_value(card):
                raise AuctionError("exhibit sale value disagrees with owned card", -8609)
        return metadata

    def exhibit(self, key, card, opening, increment, hours, relists, *, now, item_metadata=()):
        _uint(now)
        _uint(opening, MAX_MONEY, 1)
        _uint(hours, 168, 1)
        _uint(relists, 3)
        if type(increment) is not int or increment not in INCREMENTS:
            raise AuctionError("invalid minimum bid increase")
        _uint(now + hours * 3600 * (relists + 1))
        _uint(self._next_id, minimum=1)
        card = self.economy.catalog.validate(card)
        view = self.economy.account_view(key)
        if self.active_exhibits(key) >= MAX_EXHIBITS or len(view.cards) <= 5:
            raise AuctionError("exhibit limit or minimum five retained cards")
        owned = next((c for c in view.cards if c.reduced() == card.reduced()), None)
        if owned is None:
            raise AuctionError("exhibited card is not owned", -8608)
        metadata = self._descriptor(owned, item_metadata)
        listing = Listing(
            self._next_id,
            self._actor(key),
            replace(owned, deck=255),
            opening,
            increment,
            hours,
            relists,
            now,
            now + hours * 3600,
            metadata,
        )
        token = self.economy.reserve_auction(key)
        try:
            self.economy.commit_auction(token, remove=(owned,))
        except Exception:
            self.economy.release_auction(token)
            raise
        self._listings[listing.id] = listing
        self._history[listing.id] = ()
        self._next_id += 1
        return listing

    def bid(self, key, auction_id, amount, expected_revision, *, now):
        _uint(now)
        _uint(amount, MAX_MONEY, 1)
        _uint(expected_revision)
        listing = self.listing(auction_id)
        if (
            listing.status != "active"
            or now >= listing.expires
            or now < max(listing.created, listing.bid_at)
        ):
            raise AuctionError("auction is not open", -680)
        if key == listing.seller.key:
            raise AuctionError("seller cannot bid on own exhibit")
        if expected_revision != listing.revision:
            raise AuctionError("stale auction revision", -680)
        if amount < listing.minimum_bid:
            raise AuctionError("bid below required minimum", -8611)
        if amount > self.economy.balance(key):
            raise AuctionError("not enough money", -8610)
        _uint(listing.revision + 1)
        actor = self._actor(key)
        updated = replace(
            listing, bidder=actor, amount=amount, bid_at=now, revision=listing.revision + 1
        )
        pending = self._pending
        if listing.bidder is not None:
            _uint(self._next_pending, minimum=1)
            pending += (Pending(self._next_pending, listing.bidder.key, "refund", listing.amount),)
        bid = Bid(auction_id, actor, amount, now, updated.revision)
        token = self.economy.reserve_auction(key)
        try:
            self.economy.commit_auction(token, debit=amount)
        except Exception:
            self.economy.release_auction(token)
            raise
        self._listings[auction_id] = updated
        self._history[auction_id] += (bid,)
        self._pending = pending
        if listing.bidder is not None:
            self._next_pending += 1
        return updated

    def advance(self, now):
        """Settle/reoffer expired listings once; no account is silently credited."""
        _uint(now)
        listings = dict(self._listings)
        pending = list(self._pending)
        next_pending = self._next_pending
        changed = []
        for old in self._listings.values():
            if old.status != "active" or now < old.expires:
                continue
            item = old
            while item.bidder is None and item.reoffers < item.relists and now >= item.expires:
                item = replace(
                    item, reoffers=item.reoffers + 1, expires=item.expires + item.hours * 3600
                )
            if now >= item.expires:
                _uint(next_pending + (1 if item.bidder is not None else 0), minimum=1)
                if item.bidder is None:
                    pending.append(
                        Pending(next_pending, item.seller.key, "returned", card=item.card)
                    )
                    item = replace(item, status="returned", closed_at=item.expires)
                    next_pending += 1
                else:
                    pending.extend(
                        (
                            Pending(next_pending, item.seller.key, "payment", item.amount),
                            Pending(next_pending + 1, item.bidder.key, "won", card=item.card),
                        )
                    )
                    item = replace(item, status="sold", closed_at=item.expires)
                    next_pending += 2
            _uint(item.revision)
            listings[item.id] = item
            changed.append(item)
        self._listings, self._pending, self._next_pending = listings, tuple(pending), next_pending
        return tuple(changed)

    def reserve_collection(self, key):
        if key in self._collections:
            raise AuctionError("collection already active", -8621)
        token = self.economy.reserve_auction(key)
        groups = {
            kind: tuple(p for p in self._pending if p.key == key and p.kind == kind)
            for kind in _KINDS
        }
        for kind in ("returned", "won"):
            groups[kind] = groups[kind][:MAX_DELIVERY]
        result = CollectionReservation(
            key,
            token.account,
            sum(p.amount for p in groups["payment"]),
            tuple(p.card for p in groups["returned"]),
            tuple(p.card for p in groups["won"]),
            sum(p.amount for p in groups["refund"]),
        )
        self._collections[key] = _Collection(
            result, token, {kind: tuple(p.id for p in entries) for kind, entries in groups.items()}
        )
        return result

    def _collection(self, reservation, phase):
        state = self._collections.get(getattr(reservation, "key", None))
        if state is None or state.public is not reservation or state.phase != phase:
            raise AuctionError("stale or out-of-order collection stage", -8621)
        return state

    def _money(self, reservation, phase, kind):
        state = self._collection(reservation, phase)
        ids = set(state.ids[kind])
        room = MAX_MONEY - state.token.account.balance
        paid = 0
        pending = []
        for entry in self._pending:
            if entry.id in ids:
                amount = min(entry.amount, room - paid)
                paid += amount
                if amount < entry.amount:
                    pending.append(replace(entry, amount=entry.amount - amount))
            else:
                pending.append(entry)
        refreshed = self.economy.commit_auction(state.token, credit=paid, keep_reserved=phase != 3)
        self._pending = tuple(pending)
        state.token = refreshed
        state.phase += 1
        if phase == 3:
            del self._collections[reservation.key]
        return paid

    def commit_payment(self, reservation):
        """Durable credit before wire emission; native has no money acknowledgement."""
        return self._money(reservation, 0, "payment")

    def collection_inventory(self, reservation):
        state = self._collections.get(getattr(reservation, "key", None))
        if state is None or state.public is not reservation or state.phase not in (1, 2):
            raise AuctionError("collection is not waiting for card acceptance", -8621)
        incoming = reservation.returned if state.phase == 1 else reservation.won
        return state.token.account.cards + incoming

    def _cards(self, reservation, phase, kind, cards, deck_names):
        state = self._collection(reservation, phase)
        incoming = reservation.returned if phase == 1 else reservation.won
        refreshed = self.economy.commit_auction(
            state.token,
            add=incoming,
            cards=cards,
            deck_names=deck_names,
            allow_discard=True,
            keep_reserved=True,
        )
        ids = set(state.ids[kind])
        self._pending = tuple(p for p in self._pending if p.id not in ids)
        state.token = refreshed
        state.phase += 1

    def commit_returned(self, reservation, cards=None, deck_names=None):
        """First Get, including an empty stage; inventory includes accepted returns."""
        self._cards(reservation, 1, "returned", cards, deck_names)

    def commit_purchased(self, reservation, cards=None, deck_names=None):
        """Second Get, including an empty stage; inventory includes accepted bids."""
        self._cards(reservation, 2, "won", cards, deck_names)

    def commit_refund(self, reservation):
        return self._money(reservation, 3, "refund")

    def release_collection(self, reservation):
        state = self._collections.get(getattr(reservation, "key", None))
        if state is None or state.public is not reservation:
            return False
        self.economy.release_auction(state.token)
        del self._collections[reservation.key]
        return True

    def snapshot(self):
        """Ledger only; persist in the same atomic document as economy.snapshot()."""
        return AuctionSnapshot(
            version=1,
            next_id=self._next_id,
            next_pending=self._next_pending,
            listings=list(self._listings.values()),
            history=[bid for bids in self._history.values() for bid in bids],
            pending=list(self._pending),
            external_returns=[
                ExternalReturn(id=source_id, key=key, cards=list(cards))
                for source_id, (key, cards) in sorted(self._external_returns.items())
            ],
        ).model_dump(mode="json")

    @classmethod
    def restore(cls, economy, snapshot=None):
        result = cls(economy)
        if snapshot is None:
            return result
        try:
            saved = AuctionSnapshot.read(snapshot)
            result._next_id = saved.next_id
            result._next_pending = saved.next_pending

            def actor(value):
                if value is None:
                    return
                view = economy.account_view(value.key)
                # Init80 can register guild0 once. Preserve that earlier actor;
                # a saved registered guild must still equal the current guild.
                if (value.nn, value.player_id, value.name) != (
                    view.nn,
                    view.player_id,
                    view.name,
                ) or value.guild not in (0, view.guild):
                    raise AuctionError("auction actor does not match its account")

            for item in saved.listings:
                actor(item.seller)
                actor(item.bidder)
                economy.catalog.validate(item.card)
                _uint(item.id, result._next_id - 1, 1)
                if item.id in result._listings or item.card.deck != 255 or item.seller is None:
                    raise AuctionError("invalid or duplicate stored listing")
                _uint(item.reoffers, item.relists)
                if (
                    item.increment not in INCREMENTS
                    or item.expires != item.created + item.hours * 3600 * (item.reoffers + 1)
                ):
                    raise AuctionError("invalid stored auction schedule")
                if (
                    item.status == "active"
                    and item.closed_at != 0
                    or item.status in ("sold", "returned")
                    and item.closed_at != item.expires
                    or item.status == "cancelled"
                    and not item.created <= item.closed_at < item.expires
                ):
                    raise AuctionError("invalid auction close timestamp")
                if item.bidder is None:
                    if item.amount or item.bid_at or item.status == "sold":
                        raise AuctionError("invalid stored empty bid")
                else:
                    _uint(item.amount, MAX_MONEY, item.opening)
                    if (
                        item.bidder.key == item.seller.key
                        or not item.created <= item.bid_at < item.expires
                        or item.status in ("returned", "cancelled")
                    ):
                        raise AuctionError("invalid stored winning bid")
                result._descriptor(item.card, item.item_metadata)
                result._listings[item.id] = item
                result._history[item.id] = ()
            for bid in saved.history:
                actor(bid.bidder)
                item = result.listing(bid.auction_id)
                _uint(bid.amount, MAX_MONEY, item.opening)
                _uint(bid.revision, item.revision, 1)
                if (
                    bid.bidder is None
                    or bid.bidder.key == item.seller.key
                    or not item.created <= bid.timestamp < item.expires
                ):
                    raise AuctionError("invalid stored historical bid")
                previous = result._history[item.id]
                if (
                    bid.revision != len(previous) + 1
                    or previous
                    and (
                        bid.amount < previous[-1].amount + item.increment
                        or bid.timestamp < previous[-1].timestamp
                    )
                ):
                    raise AuctionError("invalid stored bid order")
                result._history[item.id] += (bid,)
            for item in result._listings.values():
                history = result._history[item.id]
                if (
                    item.revision != len(history)
                    or (item.bidder is None) != (not history)
                    or history
                    and (
                        history[-1].bidder,
                        history[-1].amount,
                        history[-1].timestamp,
                        history[-1].revision,
                    )
                    != (item.bidder, item.amount, item.bid_at, item.revision)
                ):
                    raise AuctionError("winning bid disagrees with history")
            ids = set()
            for value in saved.pending:
                if value.card is not None:
                    economy.catalog.validate(value.card)
                economy.account_view(value.key)
                _uint(value.id, result._next_pending - 1, 1)
                if value.id in ids:
                    raise AuctionError("invalid duplicate pending delivery")
                if value.kind in ("payment", "refund"):
                    _uint(value.amount, MAX_MONEY, 1)
                    if value.card is not None:
                        raise AuctionError("money delivery contains a card")
                elif value.card is None or value.card.deck != 255 or value.amount != 0:
                    raise AuctionError("invalid pending card")
                ids.add(value.id)
                result._pending += (value,)
            for key in {v.seller.key for v in result._listings.values()}:
                if result.active_exhibits(key) > MAX_EXHIBITS:
                    raise AuctionError("stored exhibit count exceeds capacity")
            for row in saved.external_returns:
                source_id, key = row.id, row.key
                economy.account_view(key)
                stable_id(source_id, error=AuctionError, what="external return ID")
                if source_id in result._external_returns:
                    raise AuctionError("duplicate external return ID")
                cards = economy.catalog.validate_batch(row.cards, error=AuctionError)
                result._external_returns[source_id] = (key, cards)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, AuctionError):
                raise
            raise AuctionError("invalid auction snapshot") from exc
        return result
