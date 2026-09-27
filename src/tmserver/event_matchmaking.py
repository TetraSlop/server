"""Opt-in event admission/matchmaking coordinator; no sockets or global enablement.

Every returned batch is ordered. Persist outcomes before finish(), emit source
identity exactly, and hold the adapter lock around all calls. Clock is injected
monotonic time; configured event eligibility uses EventHost's epoch clock.
"""

import math
import re
import struct
import time
import uuid
from dataclasses import dataclass, field, replace

from tmserver import native_special_rules as special
from tmserver.admission_identity import validate_admission
from tmserver.economy_service import collection_points
from tmserver.event_matches import (
    EventMatchRound,
    EventParticipant,
    event_session_init,
    event_session_quit,
)
from tmserver.event_service import EventError
from tmserver.match_service import Card, decode, numbers, packet
from tmserver.match_watchdog import MatchWatchdog
from tmserver.table_control import channel_exit, viewer_state


@dataclass(frozen=True)
class EventDelivery:
    peer: object
    source: int
    payload: bytes


@dataclass(frozen=True)
class EventTableConfig:
    event_id: str
    table_id: int
    channel: bytes
    player_count: int = 2
    cpu_cards: tuple | None = None
    turn_limit: int = 2
    departure_rule: int = 1
    seed: int = 1
    start_fields: tuple | None = None

    def __post_init__(self):
        if self.player_count not in (2, 3) or type(self.player_count) is not int:
            raise EventError("event table needs two/three seats")
        if self.cpu_cards is not None and (self.player_count != 2 or len(self.cpu_cards) != 5):
            raise EventError("synthetic event CPU needs one human and five CPU cards")
        if self.cpu_cards is not None:
            checked = []
            for card in self.cpu_cards:
                try:
                    values = tuple(getattr(card, k) for k in Card.__dataclass_fields__)
                except AttributeError as exc:
                    raise EventError("invalid event CPU card") from exc
                if any(type(v) is not int for v in values):
                    raise EventError("event CPU card fields must be integers")
                checked.append(Card(*values))
            object.__setattr__(self, "cpu_cards", tuple(checked))
        if type(self.turn_limit) is not int or not 0 <= self.turn_limit <= 5:
            raise EventError("unsupported event turn limit")
        if type(self.departure_rule) is not int or not 0 <= self.departure_rule <= 2:
            raise EventError("unsupported event departure rule")
        if type(self.seed) is not int or not 0 <= self.seed <= 0xFFFFFFFF:
            raise EventError("event seed must be an unsigned32-bit integer")
        if self.start_fields is not None:
            board = special.start_board(self.start_fields)
            size = (self.player_count + 2) ** 2
            if len(board.fields) != size:
                raise EventError("event board has wrong dimensions")
            if 18 in board.fields:
                raise EventError("event boards require original non-CBC tile values")
            if board.blocks > size - 5 * self.player_count:
                raise EventError("event board must leave five placement cells per player")
            object.__setattr__(self, "start_fields", board.fields)


@dataclass
class _Entry:
    peer: object
    key: str
    event_id: str
    reservation: object
    session: int
    replies: tuple
    deadline: float
    epoch: int | None = None
    selected: tuple | None = None
    selected_wire: tuple | None = None
    selection_reply: tuple = ()


@dataclass
class _Round:
    epoch: int
    config: EventTableConfig
    entries: tuple
    engine: EventMatchRound
    deadline: float
    phase: str = "proposal"
    consent: set = field(default_factory=set)
    admitted: set = field(default_factory=set)
    requested: set = field(default_factory=set)
    accepted: set = field(default_factory=set)
    viewers: set = field(default_factory=set)
    retry_at: float = 0
    watchdog: object = None


class EventMatchmaking:
    """Independent explicit tables per event, one round per fresh E admission.

    A supplied hand_provider(config, immutable_account_view) overrides the five
    authenticated selections with trusted borrowed cards. The default uses the
    original E selection. No historical pairing/award policy is supplied.
    finish(event_id,epoch) releases locks after caller durably records outcome.
    """

    def __init__(
        self,
        host,
        rooms,
        configs=(),
        *,
        hand_provider=None,
        clock=time.monotonic,
        admission_timeout=120,
        proposal_backoff=2,
        watchdog_factory=None,
        allow_observers=False,
        transactions=None,
    ):
        if rooms.host is not host or (hand_provider is not None and not callable(hand_provider)):
            raise EventError("coordinator needs matching host/rooms and trusted hand provider")
        if (
            type(admission_timeout) not in (int, float)
            or not math.isfinite(admission_timeout)
            or not 0 < admission_timeout < 3600
        ):
            raise EventError("bounded admission timeout required")
        if (
            type(proposal_backoff) not in (int, float)
            or not math.isfinite(proposal_backoff)
            or not 0 < proposal_backoff < admission_timeout
        ):
            raise EventError("proposal backoff must be positive and below timeout")
        self.host, self.rooms, self.economy = host, rooms, host.economy
        self.transactions = self.economy if transactions is None else transactions
        if type(allow_observers) is not bool:
            raise EventError("observer enablement must be boolean")
        self.allow_observers = allow_observers
        configs = tuple(configs)
        self.configs = {c.table_id: c for c in configs}
        if len(self.configs) != len(configs):
            raise EventError("duplicate event table")
        self.event_ids = frozenset(c.event_id for c in configs)
        # Validate the complete set on an isolated room map before any live bind.
        from tmserver.event_rooms import EventRooms

        check = EventRooms(host)
        for c in configs:
            if host.config(c.event_id).entry_answer != 1:
                raise EventError("globalmode8 is not the playable matchmaking path")
            check.bind_match_table(c.event_id, c.table_id, c.channel, c.player_count)
        self.clock = clock
        self.timeout = float(admission_timeout)
        self.backoff = float(proposal_backoff)
        self.hand_provider = hand_provider
        self.generation = uuid.uuid4().hex
        self.watchdog_factory = watchdog_factory or (lambda: MatchWatchdog(clock=clock))
        if not callable(self.watchdog_factory):
            raise EventError("watchdog factory must be callable")
        self._results = {}
        self._finished = {}
        self._errors = []
        self._closed_sessions = {}
        self._quit_replies = {}
        self._entries = {}
        self._rounds = {}
        self._last = {}
        self._serial = 0
        self._closures = []
        self._initial = []
        for c in configs:
            binding = self._binding(c.event_id)
            self._initial.extend(
                self._room_deliveries(
                    binding,
                    rooms.bind_match_table(c.event_id, c.table_id, c.channel, c.player_count),
                )
            )
        self._tables = {c.table_id: c.event_id for c in configs}

    def _binding(self, event_id):
        return self.rooms.room_for_service(self.host.config(event_id).room_id)

    def _slot(self, config):
        return next(
            t for t in self._binding(config.event_id).room.tables if t.table_id == config.table_id
        )

    def _round(self, event_id, epoch=None, table_id=None):
        return next(
            (
                r
                for r in self._rounds.values()
                if r.config.event_id == event_id
                and (epoch is None or r.epoch == epoch)
                and (table_id is None or r.config.table_id == table_id)
            ),
            None,
        )

    def _proposal(self, config):
        return packet(
            0xD2,
            body=f"@MuchMake=/TblNo={self._slot(config).table_number}/TblId={config.table_id:016X}",
        )

    @staticmethod
    def _room_deliveries(binding, replies):
        return [EventDelivery(peer, binding.room_id, payload) for peer, payload in replies]

    def _emit(self, r, deliveries):
        return [EventDelivery(d.peer, r.config.table_id, d.payload) for d in deliveries]

    def initial_updates(self):
        out, self._initial = self._initial, []
        return out

    def routes(self):
        return tuple((c.event_id, c.table_id, c.channel) for c in self.configs.values())

    def _entry(self, peer, key, event_id, payload):
        if peer is None:
            raise EventError("authenticated peer required")
        config = self.host.config(event_id)
        binding = self._binding(event_id)
        view = self.economy.account_view(key)
        if binding.room.members.get(peer, (None,))[0] != view.nn:
            raise EventError("E entry requires this authenticated room presence")
        old = self._entries.get(peer)
        if old is not None:
            self.host.authenticate_entry(key, payload, route="init")
            if old.key != key or old.event_id != event_id:
                raise EventError("different active event admission")
            return [EventDelivery(peer, config.init_id, p) for p in old.replies]
        if any(e.key == key for e in self._entries.values()):
            raise EventError("account already has an event connection")
        replies = self.host.handle(key, payload, event_id=event_id, route="init")
        admission = self.host.admission(key)
        if admission is None:
            return [EventDelivery(peer, config.init_id, p) for p in replies]
        if admission.mode != 4 or event_id not in self.event_ids:
            self.host.complete_admission(key)
            raise EventError("event has no supported playable table")
        token = None
        try:
            token = self.economy.reserve_match(key)
            view = token.account
            number = self._last.get(key, 0) % 255 + 1
            rt = struct.unpack_from("<I", self.economy.profile_bytes(key), 0x128)[0]
            replies.append(
                event_session_init(
                    number,
                    rating=view.rating,
                    balance=view.balance,
                    points=collection_points(view.cards),
                    count=len(view.cards),
                    rt=rt,
                )
            )
        except Exception:
            if token is not None:
                self.economy.release_match(token)
            self.host.complete_admission(key)
            raise
        self._last[key] = number
        self._entries[peer] = _Entry(
            peer, key, event_id, token, number, tuple(replies), self.clock() + self.timeout
        )
        self._finished.pop(peer, None)
        self._closed_sessions.pop(peer, None)
        self._quit_replies.pop(peer, None)
        self.host.complete_admission(key)
        return [EventDelivery(peer, config.init_id, p) for p in replies]

    def _admission_expired(self, event_id, deadline):
        return self.clock() >= deadline or self.host._now() >= self.host.config(event_id).end

    def _expire_waiting_entry(self, e):
        """Return expiry deliveries, or None when this entry is not due."""
        if e.epoch is not None:
            r = self._round(e.event_id, e.epoch)
            if (
                r is not None
                and r.phase in ("proposal", "backoff", "admission")
                and self._admission_expired(e.event_id, r.deadline)
            ):
                return self.cancel(e.event_id, r.epoch)
            return None
        if not self._admission_expired(e.event_id, e.deadline):
            return None
        del self._entries[e.peer]
        self.economy.release_match(e.reservation)
        if e.peer not in self._closures:
            self._closures.append(e.peer)
        return []

    def poll(self):
        out = list(self._initial)
        for e in tuple(self._entries.values()):
            if e.epoch is None:
                self._expire_waiting_entry(e)
        for table_id, c in self.configs.items():
            event_id = c.event_id
            r = self._rounds.get(table_id)
            if r is not None:
                if r.phase in ("proposal", "backoff", "admission") and self._admission_expired(
                    event_id, r.deadline
                ):
                    out += self.cancel(event_id, r.epoch)
                elif r.phase == "backoff" and self.clock() >= r.retry_at:
                    r.phase = "proposal"
                    r.consent.clear()
                    proposal = self._proposal(c)
                    out.extend(
                        EventDelivery(e.peer, self.host.config(event_id).room_id, proposal)
                        for e in r.entries
                    )
                elif r.phase == "playing" and r.engine.expired(r.watchdog, r.epoch) is not None:
                    out += self.cancel(event_id, r.epoch)
                continue
            config = self.host.config(event_id)
            if not config.start <= self.host._now() < config.end:
                continue
            eligible = [
                e
                for e in self._entries.values()
                if e.event_id == event_id and e.epoch is None and e.selected is not None
            ]
            needed = 1 if c.cpu_cards is not None else c.player_count
            if len(eligible) < needed:
                continue
            entries = tuple(eligible[:needed])
            try:
                participants = []
                for e in entries:
                    view = self.economy.match_account(e.reservation)
                    cards = (
                        e.selected
                        if self.hand_provider is None
                        else tuple(self.hand_provider(c, view))
                    )
                    participants.append(
                        EventParticipant(
                            e.peer,
                            view.player_id,
                            view.name,
                            view.nn,
                            cards,
                            view.rating,
                            view.balance,
                            collection_points(view.cards),
                        )
                    )
                # Validate/generate before touching room or proposal state.
                engine = EventMatchRound(
                    participants,
                    cpu_cards=c.cpu_cards,
                    seed=c.seed,
                    start_fields=c.start_fields,
                    turn_limit=c.turn_limit,
                    departure_rule=c.departure_rule,
                    observation=0 if self.allow_observers else 1,
                )
                watchdog = self.watchdog_factory()
                if not all(callable(getattr(watchdog, m, None)) for m in ("observe", "expired")):
                    raise EventError("invalid event watchdog")
            except Exception as exc:
                # A trusted policy/configuration failure is scoped to these
                # entries; do not strand reservations or stop unrelated events.
                self._errors.append((event_id, f"{type(exc).__name__}: {exc}"))
                for e in entries:
                    del self._entries[e.peer]
                    self.economy.release_match(e.reservation)
                    if e.peer not in self._closures:
                        self._closures.append(e.peer)
                continue
            self._serial += 1
            r = _Round(self._serial, c, entries, engine, min(e.deadline for e in entries))
            r.watchdog = watchdog
            binding = self._binding(event_id)
            game, table = engine.rules()
            out += self._room_deliveries(
                binding,
                self._slot(c).set_table(
                    1, c.player_count, participants[0].nickname_id, game, table
                ),
            )
            self._rounds[table_id] = r
            proposal = self._proposal(c)
            for e in entries:
                e.epoch = r.epoch
                out.append(EventDelivery(e.peer, binding.room_id, proposal))
        self._initial = []
        return out

    @staticmethod
    def _section(msg, name, keys=None):
        if set(msg.sections) != {name}:
            raise EventError("unexpected event control sections")
        fields = msg.sections[name]
        if keys is not None and set(fields) != set(keys):
            raise EventError("unexpected event control fields")
        return fields

    def _identity(self, e, msg, name):
        f = self._section(msg, name)
        view = self.economy.match_account(e.reservation)
        try:
            validate_admission(f, name, nn=view.nn, name=view.name, require_nn=True)
        except ValueError as exc:
            raise EventError(str(exc)) from exc

    def session(self, peer):
        entry = self._entries.get(peer)
        return None if entry is None else entry.session

    def _decks(self, key, msg, e=None):
        fields = msg.sections.get("Decks", {})
        if set(fields) != {"C"}:
            raise EventError("invalid event Decks header")
        count = numbers(fields["C"], 1)[0]
        if not 0 <= count <= 13:
            raise EventError("event Decks batch outside native bound")
        expected = {"Decks", *(str(i) for i in range(count))}
        renames = set(msg.sections) - expected
        if not expected <= set(msg.sections) or renames - {"DN1", "DN2"} or len(renames) > 1:
            raise EventError("invalid event Decks sections")
        view = (
            self.economy.account_view(key)
            if e is None
            else self.economy.match_account(e.reservation)
        )
        available = set(range(len(view.cards)))
        changes = []
        for i in range(count):
            f = msg.sections[str(i)]
            if set(f) != {"D"}:
                raise EventError("invalid event Decks card")
            values = numbers(f["D"], 7)
            if values[-1] not in (*range(50), 255):
                raise EventError("invalid event deck slot")
            index = next(
                (j for j in sorted(available) if view.cards[j].reduced() == tuple(values[:6])), None
            )
            if index is None:
                raise EventError("event deck card is not owned or exceeds multiplicity")
            available.remove(index)
            changes.append(replace(view.cards[index], deck=values[-1]))
        names = {}
        for rename in renames:
            f = msg.sections[rename]
            if set(f) != {"S"} or not re.fullmatch(r"(?:[0-9a-fA-F]{2}){0,17}", f["S"]):
                raise EventError("invalid event deck name")
            name = bytes.fromhex(f["S"])
            if b"\0" in name:
                raise EventError("invalid event deck name")
            names[int(rename[2:])] = name
        if e is None:
            self.transactions.apply_deck_changes(key, changes, names)
        else:
            # Track selected physical occurrences before a deck move changes
            # their full8 identity, then refresh the existing match capability.
            indices = []
            available = set(range(len(view.cards)))
            for card in e.selected or ():
                values = tuple(getattr(card, k) for k in Card.__dataclass_fields__)
                index = next(j for j in sorted(available) if view.cards[j].full() == values)
                indices.append(index)
                available.remove(index)
            e.reservation = self.transactions.apply_match_deck_changes(
                e.reservation, changes, names
            )
            if e.selected is not None:
                e.selected = tuple(Card(*e.reservation.account.cards[i].full()) for i in indices)
        return []

    def _session(self, peer, key, event_id, msg):
        config = self.host.config(event_id)
        e = self._entries.get(peer)
        if e is None:
            old = self._closed_sessions.get(peer)
            if (
                old is not None
                and old[:2] == (key, event_id)
                and msg.argument == old[2]
                and msg.subtype == 0x22
            ):
                return self._decks(key, msg)
            if old is not None and old[:2] == (key, event_id) and msg.subtype == 0x17:
                f = self._section(msg, "Quit", ("No",))
                if msg.argument == old[2] and numbers(f["No"], 1) == [old[2]]:
                    return [EventDelivery(peer, config.init_id, old[3])]
            raise EventError("no current event selection session")
        if e.key != key or e.event_id != event_id or msg.argument != e.session:
            raise EventError("event selection session mismatch")
        # Init-route traffic bypasses table control. It must expire the same
        # waiting admission before saving decks, replaying Select or closing it.
        expired = self._expire_waiting_entry(e)
        if expired is not None:
            return expired
        if msg.subtype == 0x22:
            return self._decks(key, msg, e)
        if msg.subtype == 7:
            if set(msg.sections) != {"CardSelect", *(f"N{i}" for i in range(5))} or msg.sections[
                "CardSelect"
            ] != {"C": "5"}:
                raise EventError("event requires exactly five selection records")
            if any(set(msg.sections[f"N{i}"]) != {"D"} for i in range(5)):
                raise EventError("invalid event selection fields")
            cards = tuple(Card.parse(msg.sections[f"N{i}"]["D"]) for i in range(5))
            if e.selected is not None:
                if cards != e.selected_wire:
                    raise EventError("event selection already accepted")
                return [EventDelivery(peer, config.init_id, p) for p in e.selection_reply]
            if e.epoch is not None or not self.economy.validate_match_selection(
                e.reservation, cards
            ):
                raise EventError("event selection is not the reserved owned five")
            e.selected = e.selected_wire = cards
            # Original child consumes Select first, then its outer state5 waits
            # EQuit. Selection confirmation itself is not a match-ready scene.
            e.selection_reply = (
                packet(0xB2, 7, e.session, "@Select=/A=1"),
                event_session_quit(e.session),
            )
            return [EventDelivery(peer, config.init_id, p) for p in e.selection_reply]
        if msg.subtype == 0x17:
            f = self._section(msg, "Quit", ("No",))
            if numbers(f["No"], 1) != [e.session]:
                raise EventError("event Quit session mismatch")
            reply = event_session_quit(e.session)
            self._closed_sessions[peer] = (key, event_id, e.session, reply)
            if e.epoch is None:
                del self._entries[peer]
                self.economy.release_match(e.reservation)
                out = []
            else:
                out = self.cancel(event_id, e.epoch)
                self._closures = [p for p in self._closures if p != peer]
            return [EventDelivery(peer, config.init_id, reply)] + out
        raise EventError("unsupported event selection message")

    def handle(self, peer, key, target, payload, *, joined_channels=()):
        msg = decode(payload)
        if msg is None:
            return None
        route = self.host.event_for_service(target)
        if route is not None and route[1] == "init" and msg.primary == 0x98:
            return self._entry(peer, key, route[0], payload)
        if route is not None and route[1] == "init" and msg.primary == 0xB2:
            return self._session(peer, key, route[0], msg)
        event_id = self._tables.get(target) or (route[0] if route and route[1] == "room" else None)
        if event_id is None:
            return None
        e = self._entries.get(peer)
        r = self._round(event_id, e.epoch) if e is not None and e.epoch is not None else None
        if e is None and msg.primary == 0x43 and msg.subtype == 5:
            old = self._quit_replies.get(peer)
            if old is not None and old[:3] == (key, event_id, target) and payload == old[3]:
                return [EventDelivery(peer, target, old[4])]
        if e is None and msg.primary == 0x43 and msg.subtype == 0x0E:
            finished = self._finished.get(peer)
            if finished is not None and finished[:3] == (key, event_id, target):
                f = self._section(msg, "Ready", ("Ans",))
                if msg.argument == finished[3] and numbers(f["Ans"], 1) == [finished[4]]:
                    return []
        if e is None or e.key != key or e.event_id != event_id or r is None or e.epoch != r.epoch:
            if msg.primary in (0xD3, 1, 0x41, 0x43):
                raise EventError("no current event proposal/participant")
            return None
        if target not in (self.host.config(event_id).room_id, r.config.table_id):
            raise EventError("participant message targets a different event table")
        # A final GameOK can arrive before the next poll and otherwise promote
        # an expired admission to a playing round with a fresh watchdog.
        if r.phase in ("proposal", "backoff", "admission") and self._admission_expired(
            event_id, r.deadline
        ):
            return self.cancel(event_id, r.epoch)
        if target == self.host.config(event_id).room_id:
            if msg.primary != 0xD3:
                return None
            if msg.subtype or msg.argument:
                raise EventError("invalid proposal response header")
            f = self._section(msg, "MuchMakeAns", ("Ans", "TblNo", "TblId"))
            if (
                numbers(f["TblNo"], 1) != [self._slot(r.config).table_number]
                or not re.fullmatch(r"[0-9a-fA-F]{16}", f["TblId"])
                or int(f["TblId"], 16) != r.config.table_id
            ):
                raise EventError("answer refers to another table")
            answer = numbers(f["Ans"], 1)[0]
            if answer not in (0, 1):
                raise EventError("unsupported proposal answer")
            if r.phase != "proposal":
                return []
            if not answer:
                # An early offer can reach the original room before its modal/
                # progress gates clear. Rejection is not a disconnect. Cancel
                # already-consented waits, then retry without extending the
                # overall deadline or accepting stale answers during backoff.
                out = [
                    EventDelivery(p, target, packet(0xD2, 1, body="@MuchMake=/Start=-1"))
                    for p in r.consent
                ]
                r.phase = "backoff"
                r.retry_at = self.clock() + self.backoff
                r.consent.clear()
                return out
            r.consent.add(peer)
            if len(r.consent) < len(r.entries):
                return []
            r.phase = "admission"
            return [
                EventDelivery(x.peer, target, packet(0xD2, 1, body="@MuchMake=/Start=1"))
                for x in r.entries
            ]
        viewing = viewer_state(msg)
        if viewing is not None:
            if peer not in r.admitted:
                raise EventError("Viewer requires admitted event membership")
            if viewing:
                r.viewers.add(peer)
            else:
                r.viewers.discard(peer)
            return []
        if channel_exit(msg):
            return self.cancel(event_id, r.epoch) if r.phase == "admission" else []
        if msg.primary == 0x43:
            if r.phase not in ("playing", "completed"):
                raise EventError("event battle has not started")
            if msg.subtype in (5, 0x27):
                seat = next(i for i, x in enumerate(r.entries) if x.peer == peer)
                if msg.subtype == 5:
                    f = self._section(msg, "Quit", ("No",))
                    if numbers(f["No"], 1) != [seat]:
                        raise EventError("cannot quit another event seat")
                    reply = packet(0x43, 5, msg.argument, f"@Quit=/No={seat}/B=0")
                    self._quit_replies[peer] = (key, event_id, target, payload, reply)
                    out = self.cancel(event_id, r.epoch)
                    self._closures = [p for p in self._closures if p != peer]
                    return [EventDelivery(peer, target, reply)] + out
                f = self._section(msg, "Break", ("ID",))
                if msg.argument != 3 or numbers(f["ID"], 1) != [e.reservation.account.player_id]:
                    raise EventError("invalid event Break identity")
                return self.cancel(event_id, r.epoch)
            out = self._emit(r, r.engine.handle(peer, payload))
            self._observe(event_id, r)
            if r.engine.phase == "finished":
                r.phase = "completed"
            return out
        if msg.primary == 0x41 and msg.subtype in (5, 13, 15):
            name = {5: "GameQT", 13: "GameExit", 15: "GameNG"}[msg.subtype]
            f = self._section(msg, name)
            if set(f) - ({"ID"} if msg.subtype == 5 else set()):
                raise EventError("invalid exit fields")
            if "ID" in f and int(f["ID"], 16) != e.reservation.account.nn:
                raise EventError("cannot eject another event participant")
            if r.phase not in ("proposal", "admission", "completed"):
                return []
            out = []
            if msg.subtype in (5, 13):
                out.append(
                    EventDelivery(
                        peer,
                        target,
                        packet(
                            0x41,
                            6 if msg.subtype == 5 else 14,
                            body="@GameQA=/QT=1" if msg.subtype == 5 else "@GameEA=/Exit=1",
                        ),
                    )
                )
            return out + self.cancel(event_id, r.epoch)
        if r.phase in ("proposal", "backoff") and msg.primary in (1, 0x41):
            raise EventError("table admission requires accepted proposal")
        if r.phase != "admission":
            return []
        if msg.primary == 0x41 and msg.subtype == 1:
            if msg.argument not in (0, 1):
                raise EventError("invalid GameEN argument")
            self._identity(e, msg, "GameEN")
            r.admitted.add(peer)
            return [EventDelivery(peer, target, packet(0x41, 2, body="@GameEA=/EN=1"))]
        if msg.primary == 1:
            if msg.subtype or msg.argument or peer not in r.admitted:
                raise EventError("Req before table admission")
            if r.config.channel.upper() not in {v.upper() for v in joined_channels}:
                raise EventError("Req requires table channel membership")
            self._identity(e, msg, "Req")
            r.requested.add(peer)
            return [EventDelivery(peer, target, r.engine.playack(peer))]
        if msg.primary == 0x41 and msg.subtype == 8:
            self._section(msg, "GameOK", ())
            if msg.argument or peer not in r.requested:
                raise EventError("GameOK before Req")
            r.accepted.add(peer)
            if len(r.accepted) < len(r.entries):
                return []
            # Build before phase assignment; begin is an in-memory atomic epoch.
            initial = r.engine.begin()
            r.phase = "playing"
            self._observe(event_id, r)
            binding = self._binding(event_id)
            game, table = r.engine.rules()
            out = self._room_deliveries(
                binding,
                self._slot(r.config).set_table(
                    2, r.config.player_count, r.engine.players[0].nickname_id, game, table
                ),
            )
            out += [EventDelivery(x.peer, target, packet(0x41, 9)) for x in r.entries]
            return out + self._emit(r, initial)
        return None

    def advance(self, event_id, epoch, max_battles=1):
        r = self._round(event_id, epoch)
        if r is None or r.epoch != epoch or r.phase != "playing":
            return []
        out = self._emit(r, r.engine.advance(max_battles))
        self._observe(event_id, r)
        return out

    def _observe(self, event_id, r):
        r.engine.observe(r.watchdog, r.epoch)
        outcome = r.engine.outcome
        if outcome is not None and not outcome.aborted:
            seat_keys = tuple(e.key for e in r.entries) + (None,) * (
                r.config.player_count - len(r.entries)
            )
            self._results[(event_id, r.epoch)] = (event_id, r.epoch, outcome, seat_keys)

    def results(self):
        """Frozen completed-board facts before EventResult delivery/all Ready.

        Adapter journals generation:event_id:epoch before emitting a batch.
        Facts survive cancellation/disconnection until acknowledge_result().
        """
        return tuple(self._results.values())

    def acknowledge_result(self, event_id, epoch):
        self._results.pop((event_id, epoch), None)

    def pending(self):
        return tuple(
            (r.config.event_id, r.epoch)
            for r in self._rounds.values()
            if r.phase == "playing" and r.engine.pending_work
        )

    def completed(self):
        """Immutable facts held until explicit durable finish acknowledgment."""
        return tuple(
            (r.config.event_id, r.epoch, r.engine.outcome)
            for r in self._rounds.values()
            if r.phase == "completed"
        )

    def _release(self, event_id, r):
        del self._rounds[r.config.table_id]
        for e in r.entries:
            self._entries.pop(e.peer, None)
            self.economy.release_match(e.reservation)
        binding = self._binding(event_id)
        return self._room_deliveries(binding, self._slot(r.config).set_table(0, 0, 0))

    def finish(self, event_id, epoch):
        r = self._round(event_id, epoch)
        if r is None or r.epoch != epoch:
            return []
        if r.phase != "completed":
            raise EventError("event outcome has not completed")
        for seat, e in enumerate(r.entries):
            self._finished[e.peer] = (e.key, event_id, r.config.table_id, r.engine.sequence, seat)
            self._closed_sessions[e.peer] = (
                e.key,
                event_id,
                e.session,
                event_session_quit(e.session),
            )
        out = self._release(event_id, r)
        self.acknowledge_result(event_id, epoch)
        return out

    def cancel(self, event_id, epoch):
        r = self._round(event_id, epoch)
        if r is None or r.epoch != epoch:
            return []
        self._observe(event_id, r)
        r.engine.cancel()
        # Table admission cancellation has a recovered negative start result.
        # Active game disconnect requires adapter closure; no invented43 abort.
        self._closures.extend(e.peer for e in r.entries if e.peer not in self._closures)
        out = []
        if r.phase in ("proposal", "backoff"):
            source = self.host.config(event_id).room_id
            out = [
                EventDelivery(peer, source, packet(0xD2, 1, body="@MuchMake=/Start=-1"))
                for peer in r.consent
            ]
        elif r.phase == "admission":
            out = [
                EventDelivery(
                    e.peer, r.config.table_id, packet(0x41, 0x10, body="@GameWa=/Error=-1")
                )
                for e in r.entries
            ]
        return out + self._release(event_id, r)

    def take_closures(self):
        """Affected aborted-round peers; adapter closes outside its lock."""
        peers, self._closures = tuple(self._closures), []
        return peers

    def take_errors(self):
        """Drain local policy failures for adapter diagnostics; never wire text."""
        errors, self._errors = tuple(self._errors), []
        return errors

    def disconnect(self, peer):
        self._finished.pop(peer, None)
        self._closed_sessions.pop(peer, None)
        self._quit_replies.pop(peer, None)
        e = self._entries.get(peer)
        if e is None:
            return []
        if e.epoch is not None:
            return self.cancel(e.event_id, e.epoch)
        del self._entries[peer]
        self.economy.release_match(e.reservation)
        return []

    def snapshot(self, event_id, *, epoch=None, table_id=None):
        r = self._round(event_id, epoch, table_id)
        return (
            None
            if r is None
            else dict(
                event_id=event_id,
                epoch=r.epoch,
                phase=r.phase,
                peers=tuple(e.peer for e in r.entries),
                consent=len(r.consent),
                admitted=len(r.admitted),
                requested=len(r.requested),
                accepted=len(r.accepted),
                engine=r.engine.snapshot(),
                deadline=r.deadline,
            )
        )

    def observer_context(self, target):
        event_id = self._tables.get(target)
        r = self._rounds.get(target)
        if r is None or r.phase not in ("playing", "completed"):
            return None
        return event_id, r.epoch, r.config, r.engine

    def participant(self, peer):
        return peer in self._entries

    def participant_room(self, peer):
        entry = self._entries.get(peer)
        return None if entry is None else self.host.config(entry.event_id).room_id

    def participant_table(self, peer):
        """The assigned proposal/match table; unpaired selection has no table."""
        entry = self._entries.get(peer)
        if entry is None or entry.epoch is None:
            return None
        current = self._round(entry.event_id, entry.epoch)
        return None if current is None else current.config.table_id
