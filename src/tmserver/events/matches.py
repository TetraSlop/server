"""One event round for participants admitted by events.matchmaking.

Client order: EventGameInit01 -> EventCard07 -> ordinary 08/09/0A/0C ->
EventResult0D -> Ready0E -> Continue11 (header 2, local Ans3). The coordinator
routes EInitReq, selection and admission. Hands are borrowed from authenticated
selections or configuration; the round leaves owned inventory, ratings, card
prizes and rewards alone. Layout: TetraPackets flows/event-matches.md.
"""

from dataclasses import dataclass, replace
from functools import partial

from tmserver.checks import uint
from tmserver.game import special_rules as special
from tmserver.game.models import Card, Delivery
from tmserver.game.errors import MatchError
from tmserver.game.match import MatchService
from tmserver.game.packets import decode, numbers, packet


_uint = partial(uint, error=MatchError)


def event_session_init(session, *, rating, balance, points, count, rt):
    """B2/13 EInit: Stat presence releases the gate; its value is not a mode.

    M is parsed but does not replace client balance. This builder cannot repair
    a stale profile balance. N (not header) is the session token.
    """
    _uint(rating, 0x7FFFFFFF, what="rating")
    _uint(balance, 99999999, what="balance")
    _uint(points, 65535, what="points")
    _uint(count, 1000, what="inventory count")
    _uint(rt, 0x7FFFFFFF, what="RT")
    return packet(
        0xB2,
        0x13,
        body=f"@EInit=/N={session}/RA={rating}/M={balance}/CP={points}/S={count}/RT={rt}/Stat=0",
    )


def event_session_quit(session):
    """B2/17 EQuit D0 with Stat 0: flags178/179/17A become (s&1,not(s&2),s&2)."""
    return packet(0xB2, 0x17, session, "@EQuit=/D=0/Stat=0")


@dataclass(frozen=True)
class EventParticipant:
    peer: object
    character_id: int
    name: bytes
    nickname_id: int
    cards: tuple[Card, ...]
    rating: int = 300
    balance: int = 10000
    points: int = 1


@dataclass(frozen=True)
class EventRoundOutcome:
    """Trusted facts for a separate configured event policy, never an award."""

    character_ids: tuple[int, ...]
    scores: tuple[int, ...]
    classes: tuple[int, ...]
    aborted: bool


class _EventCombat(MatchService):
    """Reuse the turn engine, replacing only the ordinary final-result branch.

    Private boundary is intentionally small and covered by full-round tests.
    Battle-time promotion still runs as on the original rendered event cards.
    Final inventory restoration/growth, ordinary stats and prizes never run.
    """

    def _end_turn(self):
        if self.sequence < self.last_sequence:
            return super()._end_turn()
        if self.source is not None:
            self._adopt_effects(special.finish_turn(self._effect_cells(), self.source))
        self.sequence += 1
        self.active = (self.active + 1) % self.player_count
        self.phase = "result"
        self.cancel_pending()
        self.completed_matches += 1
        self.ready.clear()
        self.continues.clear()
        # D0 is consumed without a proven semantic store; zero is the explicit
        # local fixture value. Client result classification comes from the board.
        body = f"@EventResult=/P={self.player_count}" + "".join(
            f"@No{i}=/D=0" for i in range(self.player_count)
        )
        return self._emit(packet(0x43, 0x0D, self.last_sequence, body))


class EventMatchRound:
    """One headless-tested borrowed-hand round, not an enabled E service.

    Participants must be authenticated/admitted by the caller. Two or three
    humans are supported; supplying cpu_cards enables an explicitly synthetic
    second seat with one human. Global mode8 rejects this matchmaking route and
    is not identified as an NPC menu.
    Caller emits ordered Deliveries using one stable learned service identity.
    """

    def __init__(
        self,
        participants,
        *,
        cpu_cards=None,
        seed=1,
        catalog=None,
        start_fields=None,
        turn_limit=2,
        departure_rule=1,
        observation=1,
        rules_backend=None,
    ):
        participants = tuple(participants)
        count = 2 if cpu_cards is not None else len(participants)
        self._combat = _EventCombat(
            mode="computer" if cpu_cards is not None else "versus",
            player_count=count,
            seed=seed,
            catalog=catalog,
            start_fields=start_fields,
            ai_cards=cpu_cards,
            rules_backend=rules_backend,
        )
        self._combat.game_rules.update(tl=turn_limit, gs=departure_rule)
        self._combat.table_rules["in"] = observation
        self._participants = participants
        self._seat = {p.peer: i for i, p in enumerate(participants)}
        self._hands = {}
        for p in participants:
            # Event result receipts store character IDs as signed DWORDs.
            if p.character_id > 0x7FFFFFFF:
                raise MatchError("event character ID exceeds a signed DWORD")
            self._hands[p.peer] = self._normalize_hand(p.cards)
            self._combat.register(
                p.peer,
                p.character_id,
                p.name,
                p.nickname_id,
                rating=p.rating,
                balance=p.balance,
                points=p.points,
            )
        if cpu_cards is not None:
            self._combat.ai_cards = self._normalize_hand(cpu_cards)
        self._started = False
        self._closed = False
        self._aborted = False
        self._released = set()

    def _normalize_hand(self, cards):
        points = self._combat.catalog.points
        return tuple(
            replace(c, points=points(c.model, c.attack, c.physical, c.magical), deck=255)
            for c in cards
        )

    @property
    def phase(self):
        return "finished" if self._closed else self._combat.phase

    @property
    def pending_work(self):
        return not self._closed and self._combat.pending_work

    @property
    def sequence(self):
        return self._combat.sequence

    @property
    def active(self):
        return self._combat.active

    @property
    def players(self):
        return tuple(self._combat.players)

    def playack(self, peer):
        """Admission projection; fills a configured CPU seat without starting."""
        return self._combat.playack(peer)

    def rules(self):
        """Independent dictionaries for the published metadata and PLAYACK."""
        return dict(self._combat.game_rules), dict(self._combat.table_rules)

    @property
    def outcome(self):
        if not self._started or (self._combat.phase != "result" and not self._aborted):
            return None
        return EventRoundOutcome(
            tuple(p.character_id for p in self.players),
            tuple(self._combat._scores()),
            tuple(self._combat.result_classes()),
            self._aborted,
        )

    def begin(self):
        if self._started or self._closed:
            return []
        initial = self._combat.begin()
        out = []
        for delivery in initial:
            msg = decode(delivery.payload)
            f = msg.sections["VsGameInit"]
            body = "@EventGameInit=" + "".join("/" + k + "=" + f[k] for k in ("N", "ID", "CN", "R"))
            out.append(Delivery(delivery.peer, packet(0x43, 1, body=body)))
        moves = []
        for peer, seat in self._seat.items():
            body = "@CardSelect=/C=5" + "".join(
                f"@N{i}=/D={c.full()}" for i, c in enumerate(self._hands[peer])
            )
            moves += self._combat.handle(peer, packet(0x43, 7, seat, body))
        for peer, seat in self._seat.items():
            body = "@EventCard=/Ok=1" + "".join(
                f"/D{seat * 5 + i}={c.reduced()}" for i, c in enumerate(self._hands[peer])
            )
            out.append(Delivery(peer, packet(0x43, 7, body=body)))
        out.extend(d for d in moves if decode(d.payload).subtype != 7)
        self._started = True
        return out

    def handle(self, peer, payload):
        if peer not in self._seat:
            raise MatchError("not an event participant")
        msg = decode(payload)
        if msg is None or msg.primary != 0x43:
            return None
        if self._closed:
            return []
        if not self._started:
            raise MatchError("event round has not begun")
        seat = self._seat[peer]
        if msg.subtype == 0x0E:
            if (
                set(msg.sections) != {"Ready"}
                or set(msg.sections["Ready"]) != {"Ans"}
                or numbers(msg.sections["Ready"]["Ans"], 1) != [seat]
                or msg.argument != self._combat.sequence
            ):
                raise MatchError("invalid event result Ready")
            if self._combat.phase != "result":
                raise MatchError("event result is not ready")
            if peer in self._released:
                return []
            self._released.add(peer)
            self._combat.ready.add(peer)
            if len(self._released) == len(self._participants):
                self._closed = True
            body = (
                "@Continue=/Ans="
                + "|".join(["3"] * self._combat.player_count)
                + "/B="
                + "|".join(["0"] * self._combat.player_count)
            )
            return [Delivery(peer, packet(0x43, 0x11, 2, body))]
        if msg.subtype not in (9, 10, 11):
            # Admission, borrowed-hand choice, prizes, Quit and Break require
            # the external event/session lifecycle. Never feed ordinary routes.
            raise MatchError("message is outside the isolated event combat contract")
        if peer in self._released:
            raise MatchError("participant already released")
        return self._combat.handle(peer, payload)

    def advance(self, max_battles=1):
        return [] if self._closed else self._combat.advance(max_battles)

    def observe(self, watchdog, epoch=None):
        return None if self._closed else watchdog.observe(self._combat, epoch)

    def expired(self, watchdog, epoch=None):
        return None if self._closed else watchdog.expired(self._combat, epoch)

    def cancel(self):
        """Adapter cancellation; no invented departure packet or award."""
        self._combat.cancel_pending()
        self._aborted = True
        self._closed = True

    def snapshot(self):
        state = self._combat.snapshot()
        state.update(
            phase=self.phase,
            event_round=True,
            released_count=len(self._released),
            aborted=self._aborted,
        )
        return state

    def observer_snapshot(self):
        return self._combat.observer_snapshot()
