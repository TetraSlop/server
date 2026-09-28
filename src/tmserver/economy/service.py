"""Local ordinary-shop service; no sockets or implicit filesystem writes.

Integration:
    shop = EconomyService(seed=1)
    shop.provision(authenticated_key, nn=game_identity, name=b'LocalPlayer')
    replies = shop.handle(authenticated_key, payload)  # None: unrelated family
    # Send replies as NOTICE from the taught shop identity (raw POL ID 0x300).
    profile = shop.profile_bytes(authenticated_key)  # U/g/TM0DataFile
    # Persist shop.snapshot() after mutations; EconomyService.restore(snapshot)
    # restores committed accounts. Call disconnect(key) when the client leaves.

Mapped wire contracts: SHOP_INVENTORY, SHOP_SEQUENCE, ECONOMY_DETAILS,
PACK_GENERATION and INVENTORY_BATCHING in ../protocol_map. This implements
ordinary B0/00 and B2 shop traffic, not auction, trade, events or Prize Center.

Explicit synthetic policy: provision defaults to 10,000 T and a ten-card
Starter's Pack. Python's RNG supplies the recovered local generator; historical
online randomness/entitlements are unknown. Ordinary catalogue categories 1/2,
and the account's guild pack, are purchasable; free Pauper packs require <5
cards. Rank/event packs are unavailable. No server-set price is sent: the
native UI uses its own decoded catalogue prices and ignores Init /M.

Buy stages one transaction, including overflow up to 1030 cards. Same-pack
retries replay its exact Card response. Get atomically commits debit, cards and
intervening edits only after capacity <=1000. A disconnect discards that pending
transaction; profile/snapshot expose the last committed state. Existing-account
Sell/Erase/Decks commit immediately. Mode2 is only a marker, never erase-all.
Duplicate edits cannot be distinguished from intentional identical-card edits
on this wire; preserve ordered delivery and do not invent automatic retries.
Malformed messages fail atomically with EconomyError; integration should log
and close/resync the connection, not report success. Insufficient funds or an
unavailable pack receive the mapped nonpositive Card response instead.
"""

import random
import re
import struct
from collections import Counter
from contextlib import contextmanager
from copy import copy, deepcopy
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field

from tmserver.cards.catalog import Catalog, collection_points
from tmserver.cards.model import Card
from tmserver.economy.errors import EconomyError
from tmserver.cards.edits import apply_card_edits, parse_edit_records
from tmserver.rankings.stats import PracticeStats
from tmserver.economy.shop_offers import ShopOffers

MAX_MONEY = 99_999_999
MAX_CARDS = 1000
PROFILE_SIZE = 0x3028
SHOP_RAW = 0x300
_HEADER = re.compile(rb"GTM0G([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})")
_SECTION = re.compile(r"([A-Za-z][A-Za-z0-9]*|[0-9]+)=(.*)\Z")
_FIELD = re.compile(r"([A-Za-z][A-Za-z0-9]*)=([^/@]*)\Z")


def _int(text, minimum=0, maximum=0xFFFFFFFF):
    if not isinstance(text, str) or not re.fullmatch(r"-?[0-9]{1,10}", text):
        raise EconomyError("invalid decimal field")
    number = int(text)
    if not minimum <= number <= maximum:
        raise EconomyError("number outside supported field width")
    return number


def _hex(text, maximum):
    if len(text) % 2 or len(text) > maximum * 2 or not re.fullmatch(r"[0-9A-Fa-f]*", text):
        raise EconomyError("invalid hexadecimal string")
    value = bytes.fromhex(text)
    if b"\0" in value:
        raise EconomyError("embedded string terminator")
    return value


def _keys(fields, expected):
    if set(fields) != set(expected):
        raise EconomyError("unexpected or missing fields")


def parse_named(payload):
    """Strict, bounded named envelope parser, separate from the BEL protocol."""
    match = _HEADER.match(payload)
    if not match or len(payload) > 13 + 447:
        raise EconomyError("invalid named envelope")
    family, secondary, subtype, session = (int(x, 16) for x in match.groups())
    if secondary != 0:
        raise EconomyError("unsupported secondary envelope byte")
    try:
        body = payload[13:].decode("ascii")
    except UnicodeDecodeError as exc:
        raise EconomyError("non-ASCII named body") from exc
    if not body.startswith("@") or any(ord(c) < 32 or ord(c) > 126 for c in body):
        raise EconomyError("invalid named body")
    sections = {}
    for part in body[1:].split("@"):
        parsed = _SECTION.fullmatch(part)
        if not parsed or parsed[1] in sections:
            raise EconomyError("invalid or repeated section")
        name, tail = parsed.groups()
        fields = {}
        if tail:
            if not tail.startswith("/"):
                raise EconomyError("invalid section body")
            for item in tail[1:].split("/"):
                field_match = _FIELD.fullmatch(item)
                if not field_match or field_match[1] in fields:
                    raise EconomyError("invalid or repeated field")
                fields[field_match[1]] = field_match[2]
        sections[name] = fields
    return family, subtype, session, sections


def named(family, subtype, session, body):
    encoded = body.encode("ascii")
    if len(encoded) > 447 or any(x < 32 or x > 126 for x in encoded):
        raise EconomyError("response exceeds native envelope capacity")
    if any(not 0 <= x <= 255 for x in (family, subtype, session)):
        raise EconomyError("invalid response header")
    return f"GTM0G{family:02X}00{subtype:02X}{session:02X}".encode() + encoded


@dataclass
class _State:
    balance: int
    cards: list
    names: dict = dataclass_field(default_factory=lambda: {1: b"Deck One", 2: b"Deck Two"})


@dataclass
class _Pending:
    pack: int
    reply: bytes
    state: _State


@dataclass
class _Account:
    nn: int
    player_id: int
    name: bytes
    guild: int
    template: bytes
    committed: _State
    session: int | None = None
    last_session: int = 0
    pending: _Pending | None = None
    offers: ShopOffers | None = None


@dataclass(frozen=True)
class AccountView:
    key: str
    nn: int
    player_id: int
    name: bytes
    guild: int
    rating: int
    balance: int
    cards: tuple
    deck_names: tuple


@dataclass(frozen=True, eq=False)
class ExchangeReservation:
    """In-process capability; only the service that issued it can commit it."""

    accounts: tuple[AccountView, AccountView]


@dataclass(frozen=True, eq=False)
class MatchReservation:
    """Capability for one table member; refreshed after each committed round."""

    account: AccountView


@dataclass(frozen=True, eq=False)
class AuctionReservation:
    """Exclusive capability for a short escrow operation or collection session."""

    account: AccountView


@dataclass(frozen=True, eq=False)
class PrizeReservation:
    """Exclusive Prize Center capability, including its independent points."""

    account: AccountView
    points: int


class EconomyService:
    def __init__(self, *, seed=None, catalog=None, shop_offers=None):
        self.catalog = catalog or Catalog()
        self.shop_offers = ShopOffers() if shop_offers is None else shop_offers
        if not isinstance(self.shop_offers, ShopOffers):
            raise EconomyError("validated shop offers required")
        self.rng = random.Random(seed)
        self._accounts = {}
        self._reservations = {}

    @contextmanager
    def edit_accounts(self, keys):
        """Stage a synchronous operation; publish only after its body succeeds.

        The caller must save the staged accounts before leaving this context.
        Do not await inside it. Other accounts, catalogues and services are
        neither copied nor replaced. Reservation capabilities keep identity.
        """
        keys = tuple(dict.fromkeys(keys))
        staged = copy(self)
        staged._accounts = {key: deepcopy(self._account(key)) for key in keys}
        staged._reservations = {
            key: self._reservations[key] for key in keys if key in self._reservations
        }
        staged.rng = deepcopy(self.rng)
        yield staged
        self._accounts.update(staged._accounts)
        for key in keys:
            if key in staged._reservations:
                self._reservations[key] = staged._reservations[key]
            else:
                self._reservations.pop(key, None)
        self.rng.setstate(staged.rng.getstate())

    def account_view(self, key):
        a = self._account(key)
        return AccountView(
            key,
            a.nn,
            a.player_id,
            a.name,
            a.guild,
            struct.unpack_from("<I", a.template, 0x30)[0],
            a.committed.balance,
            tuple(a.committed.cards),
            tuple(sorted(a.committed.names.items())),
        )

    def save_table_preferences(self, key, *, game=None, table=None):
        """Save the mapped groups while preserving inventory and live sessions.

        The pure validator completes before the profile template changes. This
        writes preferences only; active table rules remain the adapter's state.
        """
        from tmserver.profiles.preferences import apply_preferences

        account = self._account(key)
        updated = apply_preferences(account.template, game=game, table=table)
        account.template = updated

    def is_reserved(self, key):
        """Adapters should also deny table admission while this is true."""
        self._account(key)
        return key in self._reservations

    def is_busy(self, key):
        """Public admission check covering reservations and an ordinary shop."""
        account = self._account(key)
        return (
            key in self._reservations or account.session is not None or account.pending is not None
        )

    def save_options(self, key, values):
        """Persist the twelve mapped profile bytes as one validated update.

        Original1014D9F0 imports these offsets. Cu/Vi are preserved as raw
        bytes because this PC build has no proved editor semantics for them.
        Other ranges follow RULES_SETTINGS; CT accepts the importer's30..70.
        """
        mapping = {
            "Ar": (0xB0, 0, 1),
            "Cu": (0xB1, 0, 255),
            "Vi": (0xB2, 0, 255),
            "Se": (0xB3, 0, 3),
            "Bgm": (0xB4, 0, 3),
            "Per": (0xB5, 0, 1),
            "Ran": (0xB6, 0, 1),
            "CMD": (0x100, 0, 1),
            "CAD": (0x101, 0, 1),
            "CL": (0x102, 0, 20),
            "CT": (0x103, 30, 70),
            "HNSS": (0x126, 0, 1),
        }
        if set(values) != set(mapping) or any(
            type(values[k]) is not int or not lo <= values[k] <= hi
            for k, (_, lo, hi) in mapping.items()
        ):
            raise EconomyError("invalid or incomplete player options")
        account = self._account(key)
        template = bytearray(account.template)
        for key, (offset, _, _) in mapping.items():
            template[offset] = values[key]
        account.template = bytes(template)

    def save_ranking_privacy(self, key, value):
        """Continue/PR is the same persisted byte as options/Ran.

        This preference may change while a match owns the inventory reservation.
        Updating only the template preserves that reservation and pending awards.
        """
        if type(value) is not int or value not in (0, 1):
            raise EconomyError("ranking privacy must be zero or one")
        account = self._account(key)
        template = bytearray(account.template)
        template[0xB6] = value
        account.template = bytes(template)

    def _reservation_view(self, reservation, expected_type, name):
        if not isinstance(reservation, expected_type):
            raise EconomyError(f"invalid {name} reservation")
        view = reservation.account
        if (
            self._reservations.get(view.key) is not reservation
            or self.account_view(view.key) != view
        ):
            raise EconomyError(f"stale {name} reservation")
        return view

    def _release_reservation(self, reservation, expected_type, name):
        if not isinstance(reservation, expected_type):
            raise EconomyError(f"invalid {name} reservation")
        key = reservation.account.key
        if self._reservations.get(key) is not reservation:
            return False
        del self._reservations[key]
        return True

    def reserve_auction(self, key):
        account = self._account(key)
        if self.is_reserved(key) or account.session is not None or account.pending is not None:
            raise EconomyError("auction account is busy")
        reservation = AuctionReservation(self.account_view(key))
        self._reservations[key] = reservation
        return reservation

    def reserve_delivery(self, key):
        """Exclusive capability for a trusted non-auction card/money delivery."""
        return self.reserve_auction(key)

    def commit_delivery(
        self, reservation, *, add=(), credit=0, cards=None, deck_names=None, keep_reserved=True
    ):
        """Validate owned output and bounded credit from a trusted grant ledger.

        Uses the shared escrow boundary but creates no auction ledger entries.
        The caller must consume its durable grant in the same persisted update.
        """
        return self.commit_auction(
            reservation,
            add=add,
            credit=credit,
            cards=cards,
            deck_names=deck_names,
            allow_discard=True,
            keep_reserved=keep_reserved,
        )

    def release_delivery(self, reservation):
        return self.release_auction(reservation)

    def prize_points(self, key):
        return struct.unpack_from("<I", self._account(key).template, 0x134)[0]

    def reserve_prize(self, key):
        account = self._account(key)
        if self.is_reserved(key) or account.session is not None or account.pending is not None:
            raise EconomyError("Prize Center account is busy")
        points = self.prize_points(key)
        if points > 99999:
            raise EconomyError("prize points exceed native capacity")
        token = PrizeReservation(self.account_view(key), points)
        self._reservations[key] = token
        return token

    def release_prize(self, reservation):
        return self._release_reservation(reservation, PrizeReservation, "Prize Center")

    def commit_prize(
        self,
        reservation,
        *,
        add=(),
        cards=None,
        deck_names=None,
        debit=0,
        credit=0,
        allow_discard=False,
        keep_reserved=True,
    ):
        """Atomically commit prize points and cards; ordinary money is unchanged.

        The trusted Prize Center proves awards, pack cost and generated cards.
        This boundary checks ownership, capacity, multiplicity and the original
        unsigned99999 point cap. Options/unrelated profile fields are preserved.
        """
        view = self._reservation_view(reservation, PrizeReservation, "Prize Center")
        if self.prize_points(view.key) != reservation.points:
            raise EconomyError("stale Prize Center reservation")
        if (
            type(debit) is not int
            or type(credit) is not int
            or not 0 <= debit <= reservation.points
            or not 0 <= credit <= 3 * 65535
            or debit
            and credit
        ):
            raise EconomyError("invalid prize-point transfer")
        points = min(99999, reservation.points + credit) - debit
        expected = list(view.cards) + [self.catalog.validate(c) for c in add]
        final = [self.catalog.validate(c) for c in (expected if cards is None else cards)]
        before, after = Counter(c.reduced() for c in expected), Counter(c.reduced() for c in final)
        if after - before or not allow_discard and before != after:
            raise EconomyError("prize output creates, changes or unexpectedly discards cards")
        self._check_inventory(final)
        names = self._deck_names(dict(view.deck_names) if deck_names is None else deck_names)
        template = bytearray(self._accounts[view.key].template)
        struct.pack_into("<I", template, 0x134, points)
        self._accounts[view.key].committed = _State(view.balance, final, names)
        self._accounts[view.key].template = bytes(template)
        if keep_reserved:
            refreshed = PrizeReservation(self.account_view(view.key), points)
            self._reservations[view.key] = refreshed
            return refreshed
        self.release_prize(reservation)
        return None

    def release_auction(self, reservation):
        return self._release_reservation(reservation, AuctionReservation, "auction")

    def commit_auction(
        self,
        reservation,
        *,
        remove=(),
        add=(),
        debit=0,
        credit=0,
        cards=None,
        deck_names=None,
        allow_discard=False,
        keep_reserved=False,
    ):
        """Commit a trusted auction ledger's escrow transfer with independent checks.

        The ledger proves the provenance of credited money/added escrow cards.
        This boundary verifies the capability, owned removals, exact balances,
        card attributes, multiplicity, capacity and deck slots. A supplied full
        inventory may edit deck associations; reduced-card discards are allowed
        only when explicitly requested by the collection coordinator. Returns a
        refreshed capability when keep_reserved; stale capabilities never replay.
        """
        view = self._reservation_view(reservation, AuctionReservation, "auction")
        if any(type(x) is not int or not 0 <= x <= MAX_MONEY for x in (debit, credit)):
            raise EconomyError("invalid auction money transfer")
        balance = view.balance - debit + credit
        if debit > view.balance or not 0 <= balance <= MAX_MONEY:
            raise EconomyError("auction balance outside capacity")
        expected = list(view.cards)
        for card in remove:
            self.catalog.validate(card)
            try:
                expected.remove(card)
            except ValueError as exc:
                raise EconomyError("auction removal is not owned") from exc
        expected.extend(self.catalog.validate(c) for c in add)
        final = [self.catalog.validate(c) for c in (expected if cards is None else cards)]
        before, after = Counter(c.reduced() for c in expected), Counter(c.reduced() for c in final)
        if after - before or not allow_discard and before != after:
            raise EconomyError("auction output creates, changes or unexpectedly discards cards")
        self._check_inventory(final)
        names = self._deck_names(dict(view.deck_names) if deck_names is None else deck_names)
        self._accounts[view.key].committed = _State(balance, final, names)
        if keep_reserved:
            refreshed = AuctionReservation(self.account_view(view.key))
            self._reservations[view.key] = refreshed
            return refreshed
        self.release_auction(reservation)
        return None

    def reserve_match(self, key):
        account = self._account(key)
        if self.is_reserved(key) or account.session is not None or account.pending is not None:
            raise EconomyError("match account is busy")
        reservation = MatchReservation(self.account_view(key))
        self._reservations[key] = reservation
        return reservation

    def release_match(self, reservation):
        return self._release_reservation(reservation, MatchReservation, "match")

    def _match_view(self, reservation):
        return self._reservation_view(reservation, MatchReservation, "match")

    def match_account(self, reservation):
        """Public immutable view after checking a live table capability."""
        return self._match_view(reservation)

    def commit_match_money(self, changes):
        """Apply a trusted round ledger's money changes to every account at once.

        Each change is (live reservation, debit, credit). This primitive neither
        decides a reward formula nor deduplicates a logical round; the durable
        coordinator does both. Returns fresh capabilities indexed by account.
        Insufficient funds, overflow or one stale participant reject all changes.
        """
        changes = tuple(changes)
        if not 1 <= len(changes) <= 3:
            raise EconomyError("expected one to three match money changes")
        states = {}
        for reservation, debit, credit in changes:
            view = self._match_view(reservation)
            if view.key in states:
                raise EconomyError("duplicate match money account")
            if any(type(x) is not int or not 0 <= x <= MAX_MONEY for x in (debit, credit)):
                raise EconomyError("invalid match money amount")
            if debit > view.balance or view.balance - debit + credit > MAX_MONEY:
                raise EconomyError("match balance would underflow or overflow")
            states[view.key] = _State(
                view.balance - debit + credit, list(view.cards), dict(view.deck_names)
            )
        for key, state in states.items():
            self._accounts[key].committed = state
        refreshed = {key: MatchReservation(self.account_view(key)) for key in states}
        self._reservations.update(refreshed)
        return refreshed

    def commit_match_transfers(self, participants, transfers):
        """Remove settled prize losses atomically and return pending winner cards.

        participants maps each ordinary seat0..n-1 to (reservation or None,
        grown selected five in original-slot order). None denotes a trusted
        local computer hand, never a client-supplied inventory. transfers are a
        completed PrizeLedger's immutable PrizeAwards. Every source slot/full
        tuple and deck255 destination is independently verified. Winner cards
        are returned for durable result staging, not inserted before overflow.
        The caller must persist those awards and these losses as one document.
        Logical-round deduplication belongs to the durable match coordinator.
        """
        if (
            not isinstance(participants, dict)
            or len(participants) not in (2, 3)
            or set(participants) != set(range(len(participants)))
            or any(type(seat) is not int for seat in participants)
        ):
            raise EconomyError("expected every ordinary match seat")
        hands = {}
        views = {}
        indices = {}
        keys = set()
        for seat, (reservation, hand) in participants.items():
            hand = tuple(
                self.catalog.validate(Card.from_card(c))
                for c in hand
            )
            if len(hand) != 5:
                raise EconomyError("prize settlement needs every grown original slot")
            hands[seat] = hand
            if reservation is not None:
                view = self._match_view(reservation)
                if view.key in keys:
                    raise EconomyError("account occupies more than one prize seat")
                keys.add(view.key)
                views[seat] = view
                indices[seat] = self._selected_indices(view, hand)
        if not views:
            raise EconomyError("prize settlement requires a human account")
        awards = {seat: [] for seat in participants}
        lost = {seat: set() for seat in participants}
        seen = set()
        for transfer in transfers:
            winner, loser, slot = transfer.winner, transfer.loser, transfer.original_slot
            if (
                any(type(x) is not int for x in (winner, loser, slot))
                or winner not in hands
                or loser not in hands
                or winner == loser
                or not 0 <= slot < 5
                or (loser, slot) in seen
            ):
                raise EconomyError("invalid or repeated prize source identity")
            source = Card.from_card(transfer.source_card)
            card = Card.from_card(transfer.card)
            if source != hands[loser][slot] or card != replace(source, deck=255):
                raise EconomyError("prize source or received copy was altered")
            seen.add((loser, slot))
            lost[loser].add(slot)
            awards[winner].append(card)
        states = {}
        for seat, view in views.items():
            removed = {indices[seat][slot] for slot in lost[seat]}
            cards = [c for i, c in enumerate(view.cards) if i not in removed]
            self._check_inventory(cards)
            states[view.key] = _State(view.balance, cards, dict(view.deck_names))
        for key, state in states.items():
            self._accounts[key].committed = state
        refreshed = {key: MatchReservation(self.account_view(key)) for key in states}
        self._reservations.update(refreshed)
        return refreshed, {seat: tuple(cards) for seat, cards in awards.items()}

    def commit_match_rewards(self, reservation, earned=(), *, cards=None, deck_names=None):
        """Accept a trusted result coordinator's earned cards and explicit edits.

        Growth/statistics were committed by commit_match_growth. This separate
        Continue barrier handles automatic CBC gains plus overflow Erase/Decks.
        The coordinator proves earned-card provenance; this boundary prevents
        fabricated output, altered stats, bad multiplicity or >1000-card saves.
        Ordinary money, result statistics and prize points are unchanged.
        """
        view = self._match_view(reservation)
        earned = tuple(self.catalog.validate(c) for c in earned)
        if len(earned) > 30 or any(c.deck != 255 for c in earned):
            raise EconomyError("invalid generated result reward batch")
        expected = list(view.cards) + list(earned)
        final = [self.catalog.validate(c) for c in (expected if cards is None else cards)]
        if Counter(c.reduced() for c in final) - Counter(c.reduced() for c in expected):
            raise EconomyError("result output creates or alters card stats")
        self._check_inventory(final)
        names = self._deck_names(dict(view.deck_names) if deck_names is None else deck_names)
        self._accounts[view.key].committed = _State(view.balance, final, names)
        refreshed = MatchReservation(self.account_view(view.key))
        self._reservations[view.key] = refreshed
        return refreshed

    def _selected_indices(self, view, cards):
        if len(cards) != 5:
            raise EconomyError("a match requires exactly five selected cards")
        available = set(range(len(view.cards)))
        indices = []
        for card in cards:
            card = self.catalog.validate(Card.from_card(card))
            index = next((i for i in sorted(available) if view.cards[i] == card), None)
            if index is None:
                raise EconomyError("selected card is not owned or multiplicity exceeded")
            available.remove(index)
            indices.append(index)
        return indices

    def validate_match_selection(self, reservation, cards):
        try:
            self._selected_indices(self._match_view(reservation), cards)
            return True
        except (EconomyError, AttributeError, TypeError):
            return False

    def commit_match_growth(
        self,
        rounds,
        stats_by_key=None,
        payouts_by_key=None,
        rating_changes_by_key=None,
        *,
        vs_player_game=False,
    ):
        """Atomically apply the trusted match engine's completed original-five round.

        Each item is (reservation, original five cards, final five cards) in
        original selected-slot order. Ownership/count/money/decks are unchanged.
        A card may promote along P/M→X→A and gain at most one capped stat point
        at the result screen. Exact event eligibility/RNG belongs to the engine;
        this boundary independently rejects broader stat/identity mutation.
        Optional stats_by_key covers exactly those accounts with their completed
        PracticeStats. Only a single legal transition from the current profile
        is accepted. Optional payouts_by_key must cover those same accounts
        with actual credits from the durable money coordinator's quote; absent
        this mapping only zero-reward transitions are admitted. This method does
        not credit the balance: record_result does so in the same joint save.
        Optional rating_changes_by_key contains original-ring RatingQuotes from
        a trusted RatingPolicy for every account. Only their exact D2/sequence
        transition is allowed; missing mapping preserves strict fixed ratings.
        vs_player_game selects the native VS. Player Games transition. It requires
        two or three complete human results and cannot authorize arbitrary T2 edits.
        Cards and statistics commit together. Existing
        trusted callers may omit it to retain the prior card-only API.
        Returns refreshed reservations for rematch; old tokens cannot replay.
        """
        if not 1 <= len(rounds) <= 3:
            raise EconomyError("expected one to three human round results")
        if (
            type(vs_player_game) is not bool
            or vs_player_game
            and (len(rounds) < 2 or stats_by_key is None)
        ):
            raise EconomyError(
                "VS. Player Games needs completed statistics for at least two humans"
            )
        if stats_by_key is not None and not isinstance(stats_by_key, dict):
            raise EconomyError("result statistics must be an account mapping")
        if payouts_by_key is not None and (
            stats_by_key is None or not isinstance(payouts_by_key, dict)
        ):
            raise EconomyError("result payouts need an account mapping and completed statistics")
        if rating_changes_by_key is not None:
            if stats_by_key is None or not isinstance(rating_changes_by_key, dict):
                raise EconomyError("rating changes need account mapping and completed statistics")
            from tmserver.rankings.history import RatingHistoryError, validate_quote

            try:
                proofs = [validate_quote(quote) for quote in rating_changes_by_key.values()]
            except RatingHistoryError as exc:
                raise EconomyError(str(exc)) from exc
            if (
                not proofs
                or any(key != quote.key for key, quote in rating_changes_by_key.items())
                or len({quote.match_id for quote in proofs}) != 1
                or len({quote.scores for quote in proofs}) != 1
                or len({quote.seat for quote in proofs}) != len(proofs)
            ):
                raise EconomyError("incoherent rating participant proofs")
        states = {}
        templates = {}
        for reservation, original, updated in rounds:
            view = self._match_view(reservation)
            if view.key in states or len(updated) != 5:
                raise EconomyError("duplicate account or incomplete match result")
            indices = self._selected_indices(view, original)
            cards = list(view.cards)
            for index, new in zip(indices, updated):
                old = cards[index]
                new = self.catalog.validate(Card.from_card(new))
                if (new.model, new.arrows, new.deck) != (old.model, old.arrows, old.deck):
                    raise EconomyError("match growth cannot change model, arrows or deck")
                allowed = {old.kind, 2, 3} if old.kind < 2 else {old.kind, 3}
                if new.kind not in allowed:
                    raise EconomyError("invalid match type promotion")
                row = self.catalog.cards[old.model]
                delta = 0
                for field, base in (
                    ("attack", "attack"),
                    ("physical", "physical_defense"),
                    ("magical", "magical_defense"),
                ):
                    before, after = getattr(old, field), getattr(new, field)
                    cap = max(before, min(before + 1, row[base]))
                    if not before <= after <= cap:
                        raise EconomyError("match stat increase exceeds recovered growth limit")
                    delta += after - before
                if delta > 1:
                    raise EconomyError("result growth can increment only one stat")
                cards[index] = new
            self._check_inventory(cards)
            states[view.key] = _State(view.balance, cards, dict(view.deck_names))
            if stats_by_key is not None:
                result = stats_by_key.get(view.key)
                if not isinstance(result, PracticeStats):
                    raise EconomyError("missing or invalid completed result statistics")
                previous = PracticeStats.from_profile(self._accounts[view.key].template)
                reward = 0 if payouts_by_key is None else payouts_by_key.get(view.key)
                if type(reward) is not int or not 0 <= reward <= MAX_MONEY:
                    raise EconomyError("missing or invalid authoritative result payout")
                change = (
                    None if rating_changes_by_key is None else rating_changes_by_key.get(view.key)
                )
                if rating_changes_by_key is not None and change is None:
                    raise EconomyError("missing authoritative rating proof")
                if change is None:
                    coherent = any(
                        result
                        == previous.next_result(
                            outcome, reward, fully_tied=tied, vs_player_game=vs_player_game
                        )
                        for outcome, tied in ((-1, False), (0, False), (0, True), (1, False))
                    )
                else:
                    from tmserver.rankings.stats import ResultStatsError

                    try:
                        coherent = result == previous.next_result(
                            (change.result_class < 2) - (change.result_class > 2),
                            reward,
                            fully_tied=len(set(change.scores)) == 1,
                            rating_change=change,
                            vs_player_game=vs_player_game,
                        )
                    except ResultStatsError as exc:
                        raise EconomyError(str(exc)) from exc
                if not coherent:
                    raise EconomyError("result statistics are not one coherent quoted transition")
                templates[view.key] = result.apply_to_profile(self._accounts[view.key].template)
        if stats_by_key is not None and set(stats_by_key) != set(states):
            raise EconomyError("result statistics must cover exactly the completed accounts")
        if payouts_by_key is not None and set(payouts_by_key) != set(states):
            raise EconomyError("result payouts must cover exactly the completed accounts")
        if rating_changes_by_key is not None and set(rating_changes_by_key) != set(states):
            raise EconomyError("rating changes must cover exactly the completed accounts")
        # Validate every participant before replacing any committed state.
        for key, state in states.items():
            self._accounts[key].committed = state
            if key in templates:
                self._accounts[key].template = templates[key]
        refreshed = {key: MatchReservation(self.account_view(key)) for key in states}
        self._reservations.update(refreshed)
        return refreshed

    def reserve_exchange(self, keys):
        """Reserve two idle accounts atomically; snapshots/profile stay committed."""
        if len(keys) != 2 or keys[0] == keys[1]:
            raise EconomyError("exchange requires two distinct accounts")
        accounts = [self._account(key) for key in keys]
        if any(key in self._reservations for key in keys) or any(
            a.session is not None or a.pending for a in accounts
        ):
            raise EconomyError("exchange account is busy")
        reservation = ExchangeReservation(tuple(self.account_view(key) for key in keys))
        for key in keys:
            self._reservations[key] = reservation
        return reservation

    def release_exchange(self, reservation):
        """Idempotent rollback: only staged service state is discarded."""
        released = False
        for view in reservation.accounts:
            if self._reservations.get(view.key) is reservation:
                del self._reservations[view.key]
                released = True
        return released

    @staticmethod
    def _deck_names(names):
        if set(names) != {1, 2} or any(
            not isinstance(n, bytes) or len(n) > 17 or b"\0" in n for n in names.values()
        ):
            raise EconomyError("invalid persisted deck names")
        return dict(names)

    def commit_exchange(self, reservation, cards_by_key, deck_names_by_key=None):
        """Atomic two-account replacement, conserving card stats except discards.

        The trade coordinator proves per-side selections and allowed discards.
        This boundary additionally forbids created cards, changed stats, money
        changes, stale reservations, oversized saves and duplicate deck slots.
        No awaits or callbacks occur between the two final state assignments.
        """
        keys = {v.key for v in reservation.accounts}
        if set(cards_by_key) != keys or len(keys) != 2:
            raise EconomyError("exchange output does not cover both accounts")
        if deck_names_by_key is not None and set(deck_names_by_key) != keys:
            raise EconomyError("exchange names do not cover both accounts")
        states = {}
        before = Counter()
        after = Counter()
        for view in reservation.accounts:
            if (
                self._reservations.get(view.key) is not reservation
                or self.account_view(view.key) != view
            ):
                raise EconomyError("stale exchange reservation")
            cards = [self.catalog.validate(c) for c in cards_by_key[view.key]]
            self._check_inventory(cards)
            names = self._deck_names(
                deck_names_by_key[view.key]
                if deck_names_by_key is not None
                else dict(view.deck_names)
            )
            states[view.key] = _State(view.balance, cards, names)
            before.update(c.reduced() for c in view.cards)
            after.update(c.reduced() for c in cards)
        if after - before:
            raise EconomyError("exchange would create or alter card stats")
        for key, state in states.items():
            self._accounts[key].committed = state
        self.release_exchange(reservation)

    def apply_deck_changes(self, key, cards, names=None):
        """Persist delayed deck-only edits after Get, when the account is idle.

        Native rename can detach its editor and send after Get. The coordinator
        authenticates the last trade session; this API still checks ownership
        and cannot affect balance, card count or stats.
        """
        a = self._account(key)
        if self.is_reserved(key) or a.session is not None or a.pending:
            raise EconomyError("account is busy")
        self._apply_deck_changes(a, cards, names)

    def apply_match_deck_changes(self, reservation, cards, names=None):
        """Owned deck-only edits under a live match capability; refresh token.

        Used by the event preselection screen before match admission. Full card
        identities/stats/count and money stay unchanged; caller owns the native
        session/phase checks and must replace its old capability with the result.
        """
        view = self._match_view(reservation)
        self._apply_deck_changes(self._account(view.key), cards, names)
        refreshed = MatchReservation(self.account_view(view.key))
        self._reservations[view.key] = refreshed
        return refreshed

    def _apply_deck_changes(self, a, cards, names):
        cards = [self.catalog.validate(c) for c in cards]
        names = {} if names is None else dict(names)
        if set(names) - {1, 2} or len(names) > 1:
            raise EconomyError("invalid delayed rename")
        fields = {"Decks": {"C": str(len(cards))}}
        for i, c in enumerate(cards):
            fields[str(i)] = {"D": "|".join(map(str, c.reduced() + (c.deck,)))}
        for i, name in names.items():
            if not isinstance(name, bytes):
                raise EconomyError("invalid delayed name")
            fields[f"DN{i}"] = {"S": name.hex()}
        self._edit(a, 0x22, fields)

    def _draw(self):
        return self.rng.randrange(32768)

    def provision(
        self,
        key,
        *,
        nn=1,
        player_id=1,
        name=b"LocalPlayer",
        guild=1,
        balance=10_000,
        cards=None,
        profile_template=None,
    ):
        """Explicit account creation, never called from untrusted ShReq fields."""
        if key in self._accounts:
            raise EconomyError("account already provisioned")
        if not isinstance(key, str) or not key or len(key) > 128:
            raise EconomyError("account key must be a stable authenticated string")
        if (
            type(nn) is not int
            or not 0 <= nn < 1 << 64
            or type(player_id) is not int
            or not 0 <= player_id < 1 << 32
        ):
            raise EconomyError("identity out of range")
        if not isinstance(name, bytes) or len(name) > 16 or b"\0" in name:
            raise EconomyError("name exceeds native profile field")
        if (
            type(guild) is not int
            or guild not in range(4)
            or type(balance) is not int
            or not 0 <= balance <= MAX_MONEY
        ):
            raise EconomyError("invalid initial guild/balance")
        if profile_template is None:
            template = bytearray(PROFILE_SIZE)
            # This diagnostic header is not the rating at +30. Historical
            # online first-profile values are unknown; retain the local
            # rating0/sequence0 and preselected guild1 policy. Native bootstrap
            # 100A7CAF..100A7CFD supplies the fresh preference values below.
            struct.pack_into("<I", template, 0, 300)
            template[0xB3:0xB6] = bytes((2, 2, 1))
            template[0x102:0x104] = bytes((5, 60))
            profile_template = bytes(template)
        if len(profile_template) != PROFILE_SIZE:
            raise EconomyError("profile template must be canonical size")
        if cards is None:
            cards = [
                self.catalog.generate(self.catalog.select(6, guild, self._draw), self._draw)
                for _ in range(10)
            ]
        cards = [self.catalog.validate(c) for c in cards]
        self._check_inventory(cards)
        self._accounts[key] = _Account(
            nn, player_id, name, guild, bytes(profile_template), _State(balance, cards)
        )

    def initialize_profile(self, key, payload):
        """Authenticate Init80 and optionally select an unregistered guild.

        GLD0 is the initial probe, including for an already registered account:
        the original caller sends before its concurrent profile read completes.
        A nonzero selection may change only guild0 to1..3. Same-guild retries
        return Ans2 again so the original client strictly reloads the save.
        No starter cards, money, rating, or options are reset by registration.
        The adapter must persist before sending this reply.
        """
        family, subtype, argument, sections = parse_named(payload)
        if family != 0x80 or argument != 0 or set(sections) != {"Init"}:
            raise EconomyError("invalid profile initialization envelope")
        fields = sections["Init"]
        _keys(fields, ("NN", "HID", "Dm", "Vol", "CN", "GID", "L", "GLD"))
        account = self._account(key)
        for field, expected in (("NN", account.nn), ("GID", account.player_id)):
            if (
                not re.fullmatch(r"[0-9A-Fa-f]{16}", fields[field])
                or int(fields[field], 16) != expected
            ):
                raise EconomyError("profile initialization identity mismatch")
        if _hex(fields["CN"], 16) != account.name:
            raise EconomyError("profile initialization name mismatch")
        _int(fields["HID"], maximum=0)  # Original10087FCF always serializes0.
        _int(fields["Dm"], maximum=255)
        _int(fields["Vol"], maximum=65535)
        _int(fields["L"], minimum=-0x80000000, maximum=0x7FFFFFFF)
        guild = _int(fields["GLD"], maximum=3)
        if guild:
            if account.guild not in (0, guild):
                raise EconomyError("registered guild cannot be changed through Init")
            if account.guild == 0:
                if self.is_busy(key):
                    raise EconomyError("guild registration requires an idle account")
                account.guild = guild
        return named(0x81, subtype, 0, "@InitAns=/Ans=" + ("2" if guild else "1"))

    @staticmethod
    def _check_inventory(cards):
        if len(cards) > MAX_CARDS:
            raise EconomyError("inventory exceeds persisted capacity")
        slots = [c.deck for c in cards if c.deck != 255]
        if len(slots) != len(set(slots)):
            raise EconomyError("two cards occupy one deck slot")

    def _account(self, key):
        try:
            return self._accounts[key]
        except KeyError as exc:
            raise EconomyError("account is not provisioned") from exc

    def inventory(self, key):
        return tuple(self._account(key).committed.cards)

    def balance(self, key):
        return self._account(key).committed.balance

    def validate_selection(self, key, cards):
        """Read-only full-card multiset check for the practice match service."""
        try:
            if self.is_reserved(key):
                return False
            supplied = [Card.from_card(c) for c in cards]
            for c in supplied:
                self.catalog.validate(c)
            wanted = Counter(c.full() for c in supplied)
            owned = Counter(c.full() for c in self.inventory(key))
            return all(count <= owned[values] for values, count in wanted.items())
        except (AttributeError, EconomyError, TypeError):
            return False

    def disconnect(self, key):
        account = self._account(key)
        account.pending = None
        account.session = None

    def _init(self, account):
        s = account.committed
        # Original Init treats header byte1 as failure; /N carries the session.
        return named(
            0xB2,
            0x13,
            0,
            f"@Init=/N={account.session}/RA={struct.unpack_from('<I', account.template, 0x30)[0]}"
            f"/M={s.balance}/CP={collection_points(s.cards)}/S={len(s.cards)}"
            + account.offers.fields(),
        )

    def handle(self, key, payload):
        """Return response payloads, [] for accepted no-ack commands, or None."""
        if not payload.startswith((b"GTM0GB0", b"GTM0GB2", b"GTM0Gb0", b"GTM0Gb2")):
            return None
        family, subtype, session, sections = parse_named(payload)
        account = self._account(key)
        if self.is_reserved(key):
            raise EconomyError("account is reserved by an exchange")
        if family == 0xB0:
            if subtype != 0 or set(sections) != {"ShReq"}:
                raise EconomyError("only ordinary shop entry is implemented")
            fields = sections["ShReq"]
            _keys(fields, ("NN", "HID", "Dm", "Vol", "CN"))
            if (
                not re.fullmatch(r"[0-9A-Fa-f]{16}", fields["NN"])
                or int(fields["NN"], 16) != account.nn
            ):
                raise EconomyError("ShReq identity does not match authenticated account")
            _int(fields["HID"])
            _int(fields["Dm"], maximum=255)
            _int(fields["Vol"], maximum=65535)
            if _hex(fields["CN"], 16) != account.name:
                raise EconomyError("ShReq name does not match provisioned account")
            if account.pending:
                raise EconomyError("cannot reinitialize a pending purchase")
            if account.session is None:
                account.last_session = account.last_session % 255 + 1
                account.session = account.last_session
                account.offers = self.shop_offers
            return [named(0xB1, 0, session, "@ShEnter=/EN=1"), self._init(account)]
        if account.session is None:
            # A retry of the last completed Quit is harmless; all edits stay closed.
            if (
                subtype == 0x17
                and session == account.last_session
                and sections == {"Quit": {"No": str(session)}}
            ):
                return [named(0xB2, 0x17, session, f"@Quit=/N={session}/D=0/B=0")]
            raise EconomyError("shop session is not active")
        if session != account.session:
            raise EconomyError("stale shop session")
        if subtype == 0x14:
            if set(sections) != {"Buy"}:
                raise EconomyError("expected Buy")
            _keys(sections["Buy"], ("No",))
            pack_id = _int(sections["Buy"]["No"], maximum=255)
            if account.pending:
                if pack_id == account.pending.pack:
                    return [account.pending.reply]
                return [named(0xB2, 0x14, session, "@Card=/S=0")]
            state = account.committed
            pack = self.catalog.packs[pack_id] if pack_id < len(self.catalog.packs) else None
            available = pack_id in account.offers.visible(
                self.catalog, account.guild, state.balance, len(state.cards)
            )
            if not available or pack["price"] > state.balance:
                return [named(0xB2, 0x14, session, "@Card=/S=0")]
            incoming = [
                self.catalog.generate(
                    self.catalog.select(pack_id, account.guild, self._draw), self._draw
                )
                for _ in range(pack["card_count"])
            ]
            if not 1 <= len(incoming) <= 30 or len(state.cards) + len(incoming) > 1030:
                raise EconomyError("pack exceeds working inventory capacity")
            body = f"@Card=/S={len(incoming)}" + "".join(
                f"@N{i}=/D=" + "|".join(map(str, card.full())) for i, card in enumerate(incoming)
            )
            reply = named(0xB2, 0x14, session, body)
            account.pending = _Pending(
                pack_id,
                reply,
                _State(state.balance - pack["price"], state.cards + incoming, dict(state.names)),
            )
            return [reply]
        if subtype in (0x15, 0x17):
            token = "Get" if subtype == 0x15 else "Quit"
            if set(sections) != {token}:
                raise EconomyError("expected " + token)
            _keys(sections[token], ("No",))
            if _int(sections[token]["No"], maximum=255) != session:
                raise EconomyError("body session does not match envelope")
            if subtype == 0x15:
                if account.pending:
                    self._check_inventory(account.pending.state.cards)
                    account.committed = account.pending.state
                    account.pending = None
                return []
            if account.pending:
                raise EconomyError("purchase must complete before Quit")
            account.session = None
            return [named(0xB2, 0x17, session, f"@Quit=/N={session}/D=0/B=0")]
        if subtype not in (0x10, 0x16, 0x22):
            raise EconomyError("unsupported ordinary shop operation")
        self._edit(account, subtype, sections)
        return []

    def _edit(self, account, subtype, sections):
        token = {0x10: "Erase", 0x16: "Sell", 0x22: "Decks"}[subtype]
        if token not in sections:
            raise EconomyError("expected " + token)
        head = sections[token]
        if subtype == 0x10:
            mode = _int(head.get("Mode"), maximum=2)
            if mode == 2:
                if sections != {"Erase": {"Mode": "2"}}:
                    raise EconomyError("Mode2 carries no card list")
                return  # Recorded completion marker, not evidence of any deletion.
            if mode != 1:
                raise EconomyError("unsupported Erase mode")
            _keys(head, ("Mode", "C"))
        else:
            _keys(head, ("C",))
        values, renamed = parse_edit_records(sections, token, number=_int, error=EconomyError)
        current = account.pending.state if account.pending else account.committed
        cards, indices = apply_card_edits(
            current.cards, values, discard=subtype != 0x22, error=EconomyError
        )
        balance = current.balance
        if subtype == 0x16:
            balance = min(
                MAX_MONEY,
                balance + sum(self.catalog.sale_value(current.cards[i]) for i in indices),
            )
        updated = _State(balance, cards, current.names | renamed)
        if account.pending:
            account.pending.state = updated
        else:
            account.committed = updated

    def profile_bytes(self, key):
        """Canonical committed save; pending purchases require Get or disconnect."""
        a = self._account(key)
        s = a.committed
        output = bytearray(a.template)
        struct.pack_into("<IQ", output, 4, a.player_id, a.nn)
        output[0x10:0x20] = a.name.ljust(16, b"\0")
        struct.pack_into(
            "<iHBBHH",
            output,
            0x34,
            s.balance,
            collection_points(s.cards),
            0,
            a.guild,
            len(s.cards),
            0,
        )
        for number, offset in ((1, 0x104), (2, 0x115)):
            output[offset : offset + 17] = s.names[number].ljust(17, b"\0")
        output[0x148:] = b"\0" * (PROFILE_SIZE - 0x148)
        for i, card in enumerate(s.cards):
            output[0x148 + 12 * i : 0x154 + 12 * i] = card.compact()
        return bytes(output)

    def snapshot(self):
        """JSON-compatible durable state; in-flight transactions intentionally excluded."""
        return {
            "version": 1,
            "accounts": {
                key: {
                    "nn": a.nn,
                    "player_id": a.player_id,
                    "name_hex": a.name.hex(),
                    "guild": a.guild,
                    "balance": a.committed.balance,
                    "cards": [list(c.full()) for c in a.committed.cards],
                    "deck_names": {str(i): n.hex() for i, n in a.committed.names.items()},
                    "template_hex": a.template.hex(),
                    "last_session": a.last_session,
                }
                for key, a in self._accounts.items()
            },
        }

    @classmethod
    def restore(cls, snapshot, *, seed=None, catalog=None, shop_offers=None):
        if snapshot.get("version") != 1 or not isinstance(snapshot.get("accounts"), dict):
            raise EconomyError("unsupported economy snapshot")
        service = cls(seed=seed, catalog=catalog, shop_offers=shop_offers)
        for key, data in snapshot["accounts"].items():
            service.provision(
                key,
                nn=data["nn"],
                player_id=data["player_id"],
                name=_hex(data["name_hex"], 16),
                guild=data["guild"],
                balance=data["balance"],
                cards=[Card(*values) for values in data["cards"]],
                profile_template=bytes.fromhex(data["template_hex"]),
            )
            _keys(data["deck_names"], ("1", "2"))
            service._accounts[key].committed.names = {
                int(i): _hex(value, 17) for i, value in data["deck_names"].items()
            }
            service._accounts[key].last_session = _int(str(data["last_session"]), maximum=255)
        return service
