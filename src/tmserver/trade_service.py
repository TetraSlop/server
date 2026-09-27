"""Two-player A2 card exchange, using the public economy reservation API.

No networking, disk writes, NPC exchange recipes or hidden account creation.
See TRADE_SERVICE_NOTES.md for adapter consent, identity, persistence and reload
requirements. In particular Delivery.reload_profile means close/resync that
client; Dead/Quit alone cannot undo an already displayed M4 reconciliation.
"""

import re
from dataclasses import dataclass, field, replace

from tmserver.economy_service import EconomyError, collection_points, named, parse_named


class TradeError(ValueError):
    """Rejected input, with no ownership mutation or completion acknowledgement."""


@dataclass(frozen=True)
class Delivery:
    peer: object
    payload: bytes
    reload_profile: bool = False


def integer(value, maximum=0xFFFFFFFF):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{1,10}", value)
        or int(value) > maximum
    ):
        raise TradeError("invalid unsigned decimal field")
    return int(value)


def fields(value, keys):
    if set(value) != set(keys):
        raise TradeError("unexpected or missing fields")


def numbers(value, count, maximum=255):
    parts = value.split("|")
    if len(parts) != count:
        raise TradeError("incorrect numeric tuple length")
    return tuple(integer(part, maximum) for part in parts)


def match_owned(cards, requested, excluded=()):
    """Reduced wire tuples have no instance ID; consume stable matching copies."""
    available = set(range(len(cards))) - set(excluded)
    result = []
    for value in requested:
        index = next((i for i in sorted(available) if cards[i].reduced() == value), None)
        if index is None:
            raise TradeError("card not owned or multiplicity exceeded")
        available.remove(index)
        result.append(index)
    return result


@dataclass
class _Trade:
    peers: tuple
    reservation: object
    session: int
    base: list
    names: list
    offers: list = field(default_factory=lambda: [(), ()])
    uploads: list = field(default_factory=lambda: [None, None])
    selections: list = field(default_factory=lambda: [(0,) * 30, (0,) * 30])
    modes: list = field(default_factory=lambda: [0, 0])
    answers: set = field(default_factory=set)
    got: set = field(default_factory=set)
    phase: str = "negotiating"
    final: list | None = None


class TradeService:
    def __init__(self, economy, *, first_session=1, transactions=None):
        if not 1 <= first_session <= 255:
            raise TradeError("session must fit a nonzero byte")
        self.economy = economy
        # Pure-domain callers commit in memory; the server supplies Commerce.
        self.transactions = economy if transactions is None else transactions
        self._next_session = first_session
        self._bindings = {}
        self._consent = {}
        self._active = {}
        self._closed = {}

    def bind(self, peer, account_key):
        """Bind only authenticated peers; a second connection cannot share a key."""
        self.economy.account_view(account_key)
        if peer in self._active:
            raise TradeError("cannot rebind an active trader")
        if any(p != peer and key == account_key for p, key in self._bindings.items()):
            raise TradeError("account already bound to another peer")
        if self._bindings.get(peer) != account_key:
            self._closed.pop(peer, None)
            self._consent = {p: q for p, q in self._consent.items() if p != peer and q != peer}
        self._bindings[peer] = account_key

    def _key(self, peer):
        if peer not in self._bindings:
            raise TradeError("unauthenticated trade peer")
        return self._bindings[peer]

    def authorize_pair(self, initiator, recipient):
        """Adapter calls only after positive, authenticated peer TrAns consent.

        Side0 is the client which sends TrEnt (10015814..10015833), not a sort
        of nickname strings. Do not call this just because TrEnt named a peer.
        """
        if initiator == recipient or self._key(initiator) == self._key(recipient):
            raise TradeError("trade requires distinct peers")
        if initiator in self._active or recipient in self._active:
            raise TradeError("trade peer is already active")
        self._consent[initiator] = recipient

    def begin(self, side0, side1):
        """Trusted adapter/test entry after mutual handshake; returns both Init."""
        keys = (self._key(side0), self._key(side1))
        if side0 == side1 or side0 in self._active or side1 in self._active:
            raise TradeError("trade peer is already active or duplicated")
        try:
            reservation = self.economy.reserve_exchange(keys)
        except EconomyError as exc:
            raise TradeError(str(exc)) from exc
        trade = _Trade(
            (side0, side1),
            reservation,
            self._next_session,
            [list(v.cards) for v in reservation.accounts],
            [dict(v.deck_names) for v in reservation.accounts],
        )
        self._next_session = self._next_session % 255 + 1
        for peer in trade.peers:
            self._active[peer] = trade
            self._closed.pop(peer, None)
        self._consent = {
            p: q for p, q in self._consent.items() if p not in trade.peers and q not in trade.peers
        }
        body = f"@Init=/ID={trade.session}"
        for side, view in enumerate(reservation.accounts):
            body += f"@No{side}=/RA={view.rating}/M={view.balance}/CP={collection_points(view.cards)}/S={len(view.cards)}"
        return self._both(trade, 0x18, body)

    def status(self, peer):
        trade = self._active.get(peer)
        if trade is None:
            return None
        return {
            "session": trade.session,
            "side": trade.peers.index(peer),
            "phase": trade.phase,
            "offer_counts": [len(x) for x in trade.offers],
            "modes": list(trade.modes),
            "answered": sorted(trade.answers),
            "got": sorted(trade.got),
            "working_counts": [len(x) for x in (trade.final or trade.base)],
        }

    @staticmethod
    def _both(trade, subtype, body, reload=False):
        return [
            Delivery(peer, named(0xA2, subtype, trade.session, body), reload)
            for peer in trade.peers
        ]

    @staticmethod
    def _data(trade, side):
        body = f"@Data=/P={side}/M={trade.modes[side]}/D=" + "|".join(
            map(str, trade.selections[side])
        )
        return Delivery(trade.peers[1 - side], named(0xA2, 0x1B, trade.session, body))

    @staticmethod
    def _offer_packets(trade, side):
        cards = trade.offers[side]
        if not cards:
            return [
                Delivery(
                    trade.peers[1 - side], named(0xA2, 0x1A, trade.session, f"@Card=/P={side}/E=0")
                )
            ]
        deliveries = []
        start = 0
        while start < len(cards):
            count = min(15, len(cards) - start)
            while True:
                chunk = cards[start : start + count]
                body = f"@Card=/P={side}/E={len(cards)}/S={start}/C={count}"
                body += "".join(
                    f"@N{i}=/D=" + "|".join(map(str, c.reduced())) for i, c in enumerate(chunk)
                )
                if len(body) <= 447:
                    break
                count -= 1
            deliveries.append(
                Delivery(trade.peers[1 - side], named(0xA2, 0x1A, trade.session, body))
            )
            start += count
        return deliveries

    def handle(self, peer, payload):
        if not payload.startswith((b"GTM0GA0", b"GTM0GA2", b"GTM0Ga0", b"GTM0Ga2")):
            return None
        try:
            family, subtype, session, sections = parse_named(payload)
            if family == 0xA0:
                if "TrID" in sections:
                    return None  # Peer-directed service identity announcement, not registration.
                return self._entry(peer, subtype, session, sections)
            trade = self._active.get(peer)
            if trade is None:
                return self._late(peer, subtype, session, sections)
            if trade.session != session:
                raise TradeError("stale trade session")
            side = trade.peers.index(peer)
            token = {
                0x19: "Quit",
                0x1A: "Card",
                0x1B: "Data",
                0x1C: "Ans",
                0x1D: "Get",
                0x10: "Erase",
                0x22: "Decks",
            }.get(subtype)
            if token is None or token not in sections:
                raise TradeError("unsupported trade operation")
            if integer(sections[token].get("P"), 1) != side:
                raise TradeError("side does not match authenticated peer")
            if subtype == 0x19:
                fields(sections, ("Quit",))
                fields(sections["Quit"], ("P",))
                return self._abort(trade, 0x19, side)
            if subtype == 0x1A:
                return self._offer(trade, side, sections)
            if subtype == 0x1B:
                return self._selection(trade, side, sections)
            if subtype == 0x1C:
                return self._answer(trade, side, sections)
            if subtype == 0x1D:
                return self._get(trade, side, sections)
            self._edit(trade, side, subtype, sections)
            return []
        except EconomyError as exc:
            raise TradeError(str(exc)) from exc

    def _entry(self, peer, subtype, session, sections):
        if subtype != 0 or session != 0 or set(sections) != {"TrEnt"}:
            raise TradeError("invalid trade registration")
        f = sections["TrEnt"]
        fields(f, ("NN", "HID", "Dm", "Vol"))
        ids = f["NN"].split("|")
        if len(ids) != 2 or any(not re.fullmatch(r"[0-9A-Fa-f]{16}", value) for value in ids):
            raise TradeError("invalid trade participant identities")
        for key, maximum in (("HID", 0x7FFFFFFF), ("Dm", 255), ("Vol", 65535)):
            numbers(f[key], 2, maximum)
        own = self.economy.account_view(self._key(peer))
        if int(ids[0], 16) != own.nn:
            raise TradeError("registration sender identity mismatch")
        other = self._consent.get(peer)
        if other is None or self.economy.account_view(self._key(other)).nn != int(ids[1], 16):
            raise TradeError("trade pair lacks authenticated consent")
        deliveries = self.begin(peer, other)
        # Both clients poll A1; recipient first learned the service via peer TrID.
        return [
            Delivery(p, named(0xA1, 0, 0, "@TrEntAns=/Ans=1")) for p in (peer, other)
        ] + deliveries

    def _offer(self, trade, side, sections):
        if trade.phase == "settling":
            raise TradeError("offers are locked after completion")
        f = sections["Card"]
        total = integer(f.get("E"), 30)
        if total == 0:
            fields(f, ("P", "E"))
            fields(sections, ("Card",))
            cards = ()
        else:
            fields(f, ("P", "E", "S", "C"))
            start, count = integer(f["S"], 30), integer(f["C"], 15)
            if count == 0 or start + count > total:
                raise TradeError("invalid offer page bounds")
            fields(sections, {"Card"} | {f"N{i}" for i in range(count)})
            values = []
            for i in range(count):
                fields(sections[f"N{i}"], ("D",))
                value = numbers(sections[f"N{i}"]["D"], 6, 65535)
                if value[0] >= 250 or value[2] > 3 or any(x > 255 for x in value[1:]):
                    raise TradeError("invalid reduced card")
                values.append(value)
            upload = trade.uploads[side]
            if start == 0:
                assembled = tuple(values)
            elif upload is not None and upload[0] == total and start == len(upload[1]):
                assembled = upload[1] + tuple(values)
            elif (
                upload is not None
                and upload[0] == total
                and upload[1][start : start + count] == tuple(values)
            ):
                return []  # Exact earlier page retry while still assembling.
            else:
                raise TradeError("noncontiguous offer page")
            indices = match_owned(trade.base[side], assembled)
            if len(assembled) != total:
                trade.uploads[side] = total, assembled
                trade.phase = "negotiating"
                trade.answers.clear()
                trade.modes[side] = 1
                return []
            cards = tuple(trade.base[side][i] for i in indices)
        if cards == trade.offers[side] and trade.uploads[side] is None:
            return []
        trade.offers[side] = cards
        trade.uploads[side] = None
        trade.selections[1 - side] = (0,) * 30
        trade.modes[side] = 1 if cards else 0
        trade.modes[1 - side] = min(trade.modes[1 - side], 1)
        trade.phase = "negotiating"
        trade.answers.clear()
        return self._offer_packets(trade, side) + [self._data(trade, 1 - side)]

    def _selection(self, trade, side, sections):
        fields(sections, ("Data",))
        fields(sections["Data"], ("P", "M", "D"))
        if trade.phase == "settling":
            raise TradeError("selection is locked after completion")
        f = sections["Data"]
        mode = integer(f["M"], 2)
        selection = numbers(f["D"], 30, 1)
        if any(selection[len(trade.offers[1 - side]) :]):
            raise TradeError("selection refers beyond peer offer")
        if mode == 2 and any(x is not None for x in trade.uploads):
            raise TradeError("cannot confirm an incomplete offer")
        changed = selection != trade.selections[side]
        if mode == 2 and changed and trade.modes[1 - side] == 2:
            raise TradeError("changed selection must be unconfirmed before reacceptance")
        if mode < 2 or changed:
            trade.phase = "negotiating"
            trade.answers.clear()
            if changed:
                trade.modes[1 - side] = min(trade.modes[1 - side], 1)
        trade.modes[side] = mode
        trade.selections[side] = selection
        result = [self._data(trade, side)]
        if trade.modes == [2, 2] and trade.phase != "challenge":
            trade.phase = "challenge"
            result += self._both(trade, 0x1C, "@Data=/M=3")
        return result

    def _answer(self, trade, side, sections):
        fields(sections, ("Ans",))
        fields(sections["Ans"], ("P", "M"))
        if integer(sections["Ans"]["M"], 2) != 2:
            raise TradeError("challenge requires ready M2")
        if trade.phase == "settling":
            return []  # Never replay M4: reconciliation is destructive in the client.
        if trade.phase != "challenge" or trade.modes != [2, 2]:
            return []  # A cancellation/offer edit may overtake an earlier M3 answer.
        if side in trade.answers:
            return []
        trade.answers.add(side)
        if len(trade.answers) < 2:
            return []
        final = []
        for owner in (0, 1):
            offered_indices = match_owned(
                trade.base[owner], [c.reduced() for c in trade.offers[owner]]
            )
            selected_by_peer = trade.selections[1 - owner]
            kept = [c for i, c in enumerate(trade.base[owner]) if i not in offered_indices]
            kept += [
                trade.base[owner][i]
                for pos, i in enumerate(offered_indices)
                if not selected_by_peer[pos]
            ]
            kept += [
                replace(c, deck=255)
                for pos, c in enumerate(trade.offers[1 - owner])
                if trade.selections[owner][pos] == 1
            ]
            if len(kept) > 1030:
                raise TradeError("exchange exceeds temporary inventory capacity")
            final.append(kept)
        trade.final = final
        trade.phase = "settling"
        return self._both(trade, 0x1C, "@Data=/M=4")

    def _get(self, trade, side, sections):
        fields(sections, ("Get",))
        fields(sections["Get"], ("P",))
        if trade.phase != "settling":
            raise TradeError("Get precedes completed confirmation")
        if len(trade.final[side]) > 1000:
            raise TradeError("overflow discards are still required")
        if side in trade.got:
            return []
        if len(trade.got) == 0:
            trade.got.add(side)
            return []
        cards = {view.key: trade.final[i] for i, view in enumerate(trade.reservation.accounts)}
        names = {view.key: trade.names[i] for i, view in enumerate(trade.reservation.accounts)}
        self.transactions.commit_exchange(trade.reservation, cards, names)
        result = []
        for i, peer in enumerate(trade.peers):
            answer = named(0xA2, 0x1E, trade.session, f"@Quit=/P={i}/B=0")
            self._closed[peer] = trade.session, i, answer
            del self._active[peer]
            result.append(Delivery(peer, answer))
        return result

    def _edit_values(self, subtype, side, sections):
        token = "Erase" if subtype == 0x10 else "Decks"
        f = sections[token]
        if integer(f.get("P"), 1) != side:
            raise TradeError("edit side mismatch")
        if subtype == 0x10:
            mode = integer(f.get("Mode"), 2)
            if mode == 2:
                fields(f, ("P", "Mode"))
                fields(sections, ("Erase",))
                return [], {}
            if mode != 1:
                raise TradeError("unknown Erase mode")
            fields(f, ("P", "Mode", "C"))
        else:
            fields(f, ("P", "C"))
        count = integer(f["C"], 15 if subtype == 0x10 else 13)
        expected = {token} | {str(i) for i in range(count)}
        rename = set(sections) - expected if subtype == 0x22 else set()
        if rename - {"DN1", "DN2"} or len(rename) > 1 or set(sections) != expected | rename:
            raise TradeError("invalid edit sections")
        names = {}
        for key in rename:
            fields(sections[key], ("S",))
            text = sections[key]["S"]
            if len(text) > 34 or len(text) % 2 or not re.fullmatch("[0-9a-fA-F]*", text):
                raise TradeError("invalid deck name encoding")
            value = bytes.fromhex(text)
            if b"\0" in value:
                raise TradeError("embedded deck name terminator")
            names[int(key[2:])] = value
        values = []
        for i in range(count):
            fields(sections[str(i)], ("D",))
            value = numbers(sections[str(i)]["D"], 6 if subtype == 0x10 else 7, 65535)
            if value[0] >= 250 or value[2] > 3 or any(x > 255 for x in value[1:]):
                raise TradeError("invalid edit card")
            if subtype == 0x22 and value[6] >= 50 and value[6] != 255:
                raise TradeError("unknown deck slot")
            values.append(value)
        return values, names

    def _edit(self, trade, side, subtype, sections):
        values, names = self._edit_values(subtype, side, sections)
        current = trade.final[side] if trade.phase == "settling" else trade.base[side]
        excluded = (
            ()
            if trade.phase == "settling"
            else match_owned(current, [c.reduced() for c in trade.offers[side]])
        )
        indices = match_owned(current, [v[:6] for v in values], excluded)
        updated = list(current)
        if subtype == 0x10:
            updated = [c for i, c in enumerate(current) if i not in indices]
        else:
            slots = [v[6] for v in values if v[6] != 255]
            if len(slots) != len(set(slots)):
                raise TradeError("duplicate deck assignment")
            updated = [replace(c, deck=255) if c.deck in slots else c for c in current]
            for i, value in zip(indices, values):
                updated[i] = replace(updated[i], deck=value[6])
        if trade.phase == "settling":
            trade.final[side] = updated
        else:
            trade.base[side] = updated
        trade.names[side].update(names)

    def _late(self, peer, subtype, session, sections):
        self._key(peer)
        closed = self._closed.get(peer)
        if closed is None or closed[0] != session:
            raise TradeError("no active matching trade session")
        _, side, answer = closed
        if subtype == 0x1D and sections == {"Get": {"P": str(side)}}:
            return [Delivery(peer, answer)]
        if subtype == 0x22 and "Decks" in sections:
            values, names = self._edit_values(subtype, side, sections)
            cards = self.economy.inventory(self._key(peer))
            indices = match_owned(cards, [v[:6] for v in values])
            self.transactions.apply_deck_changes(
                self._key(peer),
                [replace(cards[i], deck=value[6]) for i, value in zip(indices, values)],
                names,
            )
            return []
        raise TradeError("trade is already committed; only Get retry or delayed Decks is accepted")

    def _abort(self, trade, subtype=0x27, side=0):
        self.economy.release_exchange(trade.reservation)
        for peer in trade.peers:
            self._active.pop(peer, None)
            self._closed.pop(peer, None)
        token = "Quit" if subtype == 0x19 else "Dead"
        return self._both(trade, subtype, f"@{token}=/P={side}", reload=True)

    def abort(self, peer):
        """Adapter timeout/error rollback; both clients must reload their profile."""
        trade = self._active.get(peer)
        return self._abort(trade) if trade is not None else []

    def disconnect(self, peer):
        result = self.abort(peer)
        self._bindings.pop(peer, None)
        self._closed.pop(peer, None)
        self._consent = {p: q for p, q in self._consent.items() if p != peer and q != peer}
        return [delivery for delivery in result if delivery.peer != peer]
