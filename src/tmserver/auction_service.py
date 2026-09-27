"""Original PC auction wire/file projection over the durable local ledger.

No network or disk I/O. Caller advances the ledger clock, persists joint ledger
and accounts before replies, and sends B2 from the taught Shm identity. Search
snapshots and collection capabilities are deliberately connection-local.
"""

import re
import struct
import time
from copy import copy
from dataclasses import dataclass, replace

from tmserver.auction_economy import AuctionError, Listing
from tmserver.card_catalog import collection_points
from tmserver.economy_service import Card, EconomyError, named, parse_named
from tmserver.inventory_edits import apply_card_edits, parse_edit_records
from tmserver.tm_protocol import Command, encode, parse

EXHIBIT_PATH = "U/g/TM0_EXHIBITLIST"
BID_PATH = "U/g/TM0_BIDLIST"
METADATA_PATH = "b/g/TM0AucData"
PENDING_PATHS = ("u/g/TM0_PM", "u/g/TM0_BI", "u/g/TM0_DI", "u/g/TM0_RM")
ACK = b"\x06"


def _number(value, bits=32):
    if isinstance(value, str):
        value = value.encode("ascii")
    if not isinstance(value, bytes) or re.fullmatch(rb"-?[0-9]{1,10}", value) is None:
        raise EconomyError("invalid auction decimal")
    n = int(value)
    # Native DWORD serializers use signed %d. Smaller fields are unsigned.
    if not (-(1 << 31) if bits == 32 else 0) <= n < (1 << bits):
        raise EconomyError("auction decimal exceeds field width")
    return n & ((1 << bits) - 1)


def _decimal(value):
    return str(value if value < 0x80000000 else value - 0x100000000).encode()


def _name(value, limit=16):
    if (
        not isinstance(value, bytes)
        or len(value) > limit
        or any(c < 32 or c > 255 or c in (127,) for c in value)
    ):
        raise EconomyError("invalid auction name")
    return value


def _hex_name(value, limit):
    if re.fullmatch(r"[0-9a-fA-F]*", value) is None or len(value) % 2:
        raise EconomyError("invalid hexadecimal name")
    return _name(bytes.fromhex(value), limit)


def actor_record(actor):
    """UI identity/name fields; unknown actor metadata remains explicitly zero."""
    out = bytearray(0x38)
    if actor is not None:
        struct.pack_into("<QI", out, 0, actor.nn, actor.player_id)
        # Display copy is NUL-terminated even for a 16-byte account name.
        out[0x18:0x28] = actor.name[:15].ljust(16, b"\0")
    return bytes(out)


def actor_wire(actor):
    if actor is None:
        values = [b"0x0000000000000000", b"0", b"0", b"0", b"0", b"", *([b"0"] * 6)]
    else:
        values = [
            f"0x{actor.nn:016X}".encode(),
            _decimal(actor.player_id),
            b"0",
            b"0",
            b"0",
            actor.name[:15],
            *([b"0"] * 6),
        ]
    return ACK.join(values)


def descriptor(item):
    if item.item_metadata:
        return item.item_metadata
    c, a = item.card, item.seller
    return (
        c.model,
        0,
        a.rating,
        0,
        0,
        0,
        a.points & 65535,
        0,
        c.attack,
        c.physical,
        c.kind,
        c.magical,
        c.points,
        c.arrows,
        0,
        0,
    )


def listing_record(item):
    out = bytearray(0xC0)
    out[:0x38] = actor_record(item.seller)
    # NC is used for remaining duration. ED/LC/CD use this server's Unix clock;
    # the complete historical meanings of ED/LC are not established.
    struct.pack_into(
        "<6I3B",
        out,
        0x38,
        item.id,
        item.opening,
        item.increment,
        item.created,
        item.expires,
        item.bid_at or item.created,
        item.hours,
        item.relists,
        item.reoffers,
    )
    out[0x58:0x90] = actor_record(item.bidder)
    struct.pack_into(
        "<3I", out, 0x90, item.amount if item.bidder else item.opening, item.bid_at, item.revision
    )
    struct.pack_into("<4I4H8B", out, 0xA0, *descriptor(item))
    return bytes(out)


def bid_record(bid):
    return actor_record(bid.bidder) + struct.pack(
        "<4I", bid.auction_id, bid.amount, bid.timestamp, 0
    )


def listing_reply(token, item):
    scalar = [
        ("AI", item.id),
        ("SP", item.opening),
        ("BI", item.increment),
        ("ED", item.created),
        ("NC", item.expires),
        ("LC", item.bid_at or item.created),
        ("ET", item.hours),
        ("AM", item.relists),
        ("AC", item.reoffers),
        ("CM", item.amount if item.bidder else item.opening),
        ("CD", item.bid_at),
        ("BC", item.revision),
    ]
    commands = [Command(token, b""), Command(b"EI", actor_wire(item.seller))]
    commands += [Command(k.encode(), _decimal(v)) for k, v in scalar]
    commands += [
        Command(b"CB", actor_wire(item.bidder)),
        Command(b"II", ACK.join(_decimal(v) for v in descriptor(item))),
    ]
    return b"GTM0A" + encode(commands)


@dataclass
class _Collection:
    reservation: object
    session: int
    stage: int
    cards: list
    names: dict


class AuctionService:
    """handle(key,bytes)->replies|None; file/delete use account-scoped paths.

    Query operations never reserve an account. ER/BR reserve only their atomic
    ledger mutation. B0/01 collection owns the account until its second Get or
    disconnect. No client request exists for cancelling an active exhibit.
    """

    def __init__(self, ledger, *, clock=time.time, max_snapshots=64):
        if type(max_snapshots) is not int or not 1 <= max_snapshots <= 1024:
            raise ValueError("max_snapshots must be1..1024")
        self.ledger, self.economy, self.clock = ledger, ledger.economy, clock
        self.max_snapshots = max_snapshots
        self._snapshots, self._collections, self._sessions = {}, {}, {}
        self._next_id = 1

    def stage_state(self, ledger, keys):
        """Stage wire sessions against the transaction's account/ledger view."""
        staged = copy(self)
        staged.ledger, staged.economy = ledger, ledger.economy
        staged._sessions = dict(self._sessions)
        staged._snapshots = dict(self._snapshots)
        staged._collections = dict(self._collections)
        for key in keys:
            if key in self._snapshots:
                staged._snapshots[key] = dict(self._snapshots[key])
            if key in self._collections:
                row = self._collections[key]
                staged._collections[key] = replace(
                    row, cards=list(row.cards), names=dict(row.names)
                )
        return staged

    def publish_state(self, staged):
        self._sessions = staged._sessions
        self._snapshots = staged._snapshots
        self._collections = staged._collections
        self._next_id = staged._next_id

    def collection_active(self, key):
        return key in self._collections

    def disconnect(self, key):
        state = self._collections.pop(key, None)
        if state is not None:
            self.ledger.release_collection(state.reservation)
        self._snapshots.pop(key, None)

    def _actor(self, key, payload):
        fields = payload.split(ACK)
        if len(fields) != 12 or re.fullmatch(rb"0x[0-9a-fA-F]{16}", fields[0]) is None:
            raise EconomyError("auction actor requires twelve fields")
        numbers = [
            _number(fields[i], bits)
            for i, bits in (
                (1, 32),
                (2, 32),
                (3, 8),
                (4, 16),
                (6, 32),
                (7, 32),
                (8, 16),
                (9, 16),
                (10, 8),
                (11, 8),
            )
        ]
        account = self.economy.account_view(key)
        if (
            int(fields[0], 16) != account.nn
            or numbers[0] != account.player_id
            or _name(fields[5]) != account.name
        ):
            raise EconomyError("auction actor differs from authenticated account")
        return account

    @staticmethod
    def _error(token, error):
        return [b"GTM0A" + encode([Command(token, str(error).encode())])]

    def _filters(self, commands):
        filters, seen = [], set()
        for command in commands:
            token, value = command.token, command.payload
            if token in seen and token != b"IC":
                raise EconomyError("duplicate auction filter")
            seen.add(token)
            if token in (b"IO", b"IB"):
                filters.append((token, _name(value)))
            elif token == b"AI":
                filters.append((token, _number(value)))
            elif token in (b"MP", b"LT", b"IC"):
                values = tuple(_number(v) for v in value.split(ACK))
                if len(values) != (3 if token == b"IC" else 2) or values[-2] > values[-1]:
                    raise EconomyError("invalid auction range filter")
                if token == b"IC" and values[0] not in (0, 10):
                    raise EconomyError("unsupported card selector")
                filters.append((token, values))
            else:
                raise EconomyError("unsupported auction filter")
        return filters

    @staticmethod
    def _matches(item, filters, now, bidder=None, amount=None):
        for token, value in filters:
            if token == b"IO" and item.seller.name != value:
                return False
            if token == b"IB" and (bidder or item.bidder) is None:
                return False
            if token == b"IB" and (bidder or item.bidder).name != value:
                return False
            if token == b"AI" and item.id != value:
                return False
            if token == b"MP":
                n = amount if amount is not None else item.amount if item.bidder else item.opening
                if not value[0] <= n <= value[1]:
                    return False
            if token == b"LT" and not value[0] <= max(0, item.expires - now) // 3600 <= value[1]:
                return False
            if (
                token == b"IC"
                and not value[1]
                <= (item.card.model if value[0] == 0 else item.card.kind)
                <= value[2]
            ):
                return False
        return True

    def _query(self, key, operation, filters, now):
        snapshots = self._snapshots.setdefault(key, {})
        failure = b"SF" if operation == b"SI" else b"HF"
        if len(snapshots) >= self.max_snapshots or self._next_id > 0xFFFFFFFF:
            return self._error(failure, -8621)
        rows = []
        for item in sorted(
            self.ledger.listings(active_only=operation == b"SI"), key=lambda x: x.id
        ):
            if operation == b"SI":
                if self._matches(item, filters, now):
                    rows.append(listing_record(item))
            else:
                rows.extend(
                    bid_record(b)
                    for b in self.ledger.history(item.id)
                    if self._matches(item, filters, now, b.bidder, b.amount)
                )
        if operation == b"BH":
            rows.sort(key=lambda r: struct.unpack_from("<I", r, 0x3C)[0], reverse=True)
        # Bound work/result allocations; never silently truncate a matching set.
        if len(rows) > 1000:
            return self._error(failure, -8621)
        path = f'{EXHIBIT_PATH if operation == b"SI" else BID_PATH}_{self._next_id:08X}'
        response = b"GTM0A" + encode(
            [
                Command(b"SS" if operation == b"SI" else b"HS", path.encode()),
                Command(b"SN", str(len(rows)).encode()),
            ]
        )
        snapshots[path] = b"".join(rows)
        self._next_id += 1
        return [response]

    def handle(self, key, payload):
        if payload.startswith(b"GTM0A"):
            commands = parse(payload[5:])
            if not commands or commands[0].token not in (b"ER", b"BR", b"SI", b"BH"):
                return None
            operation = commands[0].token
            account = self._actor(key, commands[0].payload)
            now = int(self.clock())
            if not 0 <= now <= 0xFFFFFFFF:
                raise EconomyError("clock exceeds native auction timestamp width")
            if operation in (b"SI", b"BH"):
                return self._query(key, operation, self._filters(commands[1:]), now)
            expected = (
                [b"ER", b"SP", b"BI", b"ET", b"AM", b"II"]
                if operation == b"ER"
                else [b"BR", b"AI", b"BM", b"BC"]
            )
            if [c.token for c in commands] != expected:
                raise EconomyError("unexpected or missing auction fields")
            try:
                if operation == b"ER":
                    opening, increment = (_number(c.payload) for c in commands[1:3])
                    hours, relists = (_number(c.payload, 8) for c in commands[3:5])
                    parts = commands[5].payload.split(ACK)
                    if len(parts) != 16:
                        raise EconomyError("II requires sixteen fields")
                    item = tuple(
                        _number(v, 32 if i < 4 else 16 if i < 8 else 8) for i, v in enumerate(parts)
                    )
                    card = Card(item[0], item[8], item[10], item[9], item[11], item[12], item[13])
                    # Validate response capacity before removing a card. An ID
                    # with ten decimal digits bounds all allocated IDs.
                    listing_reply(
                        b"ES",
                        Listing(
                            0x80000000,
                            account,
                            card,
                            opening,
                            increment,
                            hours,
                            relists,
                            now,
                            now + hours * 3600,
                            item,
                        ),
                    )
                    result = self.ledger.exhibit(
                        key, card, opening, increment, hours, relists, now=now, item_metadata=item
                    )
                else:
                    auction_id, amount, revision = (_number(c.payload) for c in commands[1:])
                    preview = replace(
                        self.ledger.listing(auction_id),
                        bidder=account,
                        amount=amount,
                        bid_at=now,
                        revision=0x80000000,
                    )
                    listing_reply(b"BS", preview)  # Capacity before money escrow.
                    result = self.ledger.bid(key, auction_id, amount, revision, now=now)
            except AuctionError as error:
                return self._error(b"EF" if operation == b"ER" else b"BF", error.code)
            return [listing_reply(b"ES" if operation == b"ER" else b"BS", result)]
        if payload[:7].upper() not in (b"GTM0GB0", b"GTM0GB2"):
            return None
        family, subtype, session, sections = parse_named(payload)
        if family == 0xB0:
            if subtype != 1:
                return None
            return self._start_collection(key, session, sections)
        state = self._collections.get(key)
        if state is None:
            return None
        if session != state.session:
            raise EconomyError("stale auction collection session")
        if subtype == 0x15:
            if sections != {"Get": {"No": str(session)}}:
                raise EconomyError("incorrect collection Get")
            return self._accept_cards(key, state)
        if subtype in (0x10, 0x22):
            self._edit_collection(state, subtype, sections)
            return []
        raise EconomyError("unsupported or premature auction collection operation")

    def _start_collection(self, key, request_session, sections):
        account = self.economy.account_view(key)
        if set(sections) != {"ShReq"} or set(sections["ShReq"]) != {"NN", "HID", "Dm", "Vol", "CN"}:
            raise EconomyError("invalid collection ShReq")
        fields = sections["ShReq"]
        if (
            re.fullmatch(r"[0-9A-Fa-f]{16}", fields["NN"]) is None
            or int(fields["NN"], 16) != account.nn
        ):
            raise EconomyError("collection identity differs from authenticated account")
        if _hex_name(fields["CN"], 16) != account.name:
            raise EconomyError("collection name differs from authenticated account")
        _number(fields["HID"])
        _number(fields["Dm"], 8)
        _number(fields["Vol"], 16)
        if key in self._collections:
            raise EconomyError("collection already active; entry is not a replay")
        reservation = self.ledger.reserve_collection(key)
        session = self._sessions.get(key, 0) % 255 + 1
        # Prepare bounded card messages before any durable credit.
        try:
            cards = self._card_messages(session, reservation.returned)
            # Original10105390 treats header1 as Init failure. /N establishes
            # the later session; the Init header is success0, independently.
            init = named(
                0xB2,
                0x13,
                0,
                f"@Init=/N={session}/RA={account.rating}/M={account.balance}"
                f"/CP={collection_points(account.cards)}/S={len(account.cards)}/SP=0/SN=/CMP=0/HD=0",
            )
            paid = self.ledger.commit_payment(reservation)
        except Exception:
            self.ledger.release_collection(reservation)
            raise
        self._sessions[key] = session
        self._collections[key] = _Collection(
            reservation,
            session,
            0,
            list(self.ledger.collection_inventory(reservation)),
            dict(account.deck_names),
        )
        # Init alone is the original entry acknowledgement and remains queued
        # for the Init consumer. No invented positive B1 dependency is added.
        return [init, named(0xB2, 0x20, session, f"@Data=/M={paid}"), *cards]

    @staticmethod
    def _card_messages(session, cards):
        if len(cards) > 30:
            raise EconomyError("auction card stage exceeds original temporary buffer")
        if not cards:
            return [named(0xB2, 0x21, session, "@Card=/E=0/S=0/C=0")]
        output, start = [], 0
        while start < len(cards):
            tails = []
            for card in cards[start:]:
                tail = f"@N{len(tails)}=/D=" + "|".join(map(str, card.reduced()))
                body = f"@Card=/E={len(cards)}/S={start}/C={len(tails)+1}" + "".join(tails) + tail
                if len(body) > 447:
                    break
                tails.append(tail)
            if not tails:
                raise EconomyError("card cannot fit native page")
            body = f"@Card=/E={len(cards)}/S={start}/C={len(tails)}" + "".join(tails)
            output.append(named(0xB2, 0x21, session, body))
            start += len(tails)
        return output

    def _accept_cards(self, key, state):
        reservation, session = state.reservation, state.session
        if state.stage == 0:
            messages = self._card_messages(session, reservation.won)
            self.ledger.commit_returned(reservation, tuple(state.cards), state.names)
            state.stage = 1
            state.cards = list(self.ledger.collection_inventory(reservation))
            return messages
        self.ledger.commit_purchased(reservation, tuple(state.cards), state.names)
        refund = self.ledger.commit_refund(reservation)
        del self._collections[key]
        return [
            named(0xB2, 0x20, session, f"@Data=/M={refund}"),
            named(0xB2, 0x17, session, f"@Quit=/N={session}/D=0/B=0"),
        ]

    @staticmethod
    def _edit_collection(state, subtype, sections):
        token = "Erase" if subtype == 0x10 else "Decks"
        if subtype == 0x10 and sections == {"Erase": {"Mode": "2"}}:
            return
        head = sections.get(token)
        if head is None or set(head) != ({"Mode", "C"} if subtype == 0x10 else {"C"}):
            raise EconomyError("invalid collection edit header")
        if subtype == 0x10 and head["Mode"] != "1":
            raise EconomyError("invalid collection erase mode")
        values, renamed = parse_edit_records(
            sections,
            token,
            number=_number,
            name=lambda value: _hex_name(value, 17),
            error=EconomyError,
        )
        cards, _ = apply_card_edits(
            state.cards, values, discard=subtype == 0x10, error=EconomyError
        )
        state.cards, state.names = cards, state.names | renamed

    def file(self, key, path):
        self.economy.account_view(key)
        if path == METADATA_PATH:
            counts = [0] * 5
            for item in self.ledger.listings():
                price = item.amount if item.bidder else item.opening
                counts[0] += 1
                counts[
                    1 if price <= 1000 else 2 if price <= 2000 else 3 if price <= 5000 else 4
                ] += 1
            return struct.pack("<5I", *counts)
        if path in PENDING_PATHS:
            # The client tests read-success only; every byte is opaque/unread.
            # Publish all four markers when any stage is pending, no guessed
            # historical meaning is assigned to BI/DI filenames.
            return b"\0" if self.ledger.pending(key) else None
        return self._snapshots.get(key, {}).get(path)

    def delete(self, key, path):
        self.economy.account_view(key)
        snapshots = self._snapshots.get(key, {})
        for prefix in (EXHIBIT_PATH, BID_PATH):
            if path == prefix:
                for name in tuple(snapshots):
                    if name.startswith(prefix + "_"):
                        del snapshots[name]
                return True
            if re.fullmatch(re.escape(prefix) + r"_[0-9A-F]{8}", path):
                snapshots.pop(path, None)
                return True
        return False
