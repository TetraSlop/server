"""Opt-in original-client spectator preview, disabled until native validation.

Bootstrap fields have original-consumer tests. Delays are local heuristics:
role4 has no recovered presentation-complete acknowledgement. This module
cannot promise liveness for a slow observer and never gates participant play.
"""

import math
import time
from collections import deque
from dataclasses import dataclass, field

from tmserver.game.models import Delivery
from tmserver.game.errors import MatchError
from tmserver.game.packets import decode, packet
from tmserver.game.departure_rules import next_turn


class SpectatorError(MatchError):
    pass


def _text(values):
    return "|".join(map(str, values))


def snapshot_fields(snapshot):
    """Encode current16/25-cell geometry, owner and signed battle modifiers.

    F's low6 bits construct the tile/property; upper2 bits are owner+1 for
    ordinary cards. CH is restored into rendered+26 after point recomputation.
    Only stable boundary state is representable; combat markers are not sent.
    """
    n = len(snapshot.players)
    if n not in (2, 3) or len(snapshot.board) != (n + 2) ** 2:
        raise SpectatorError(
            "spectator preview requires two/three seats and matching board dimensions"
        )
    fields, modifiers, cards = [], [], []
    for index, cell in enumerate(snapshot.board):
        if cell.marker:
            raise SpectatorError("cannot snapshot a pending combat marker")
        if cell.model == 0xFFFF:
            if cell.card is not None:
                raise SpectatorError("empty snapshot cell has a card")
            fields.append(0)
            modifiers.append(0)
            continue
        if cell.card is not None:
            if (
                cell.owner not in range(n)
                or not 0 <= cell.model < 250
                or cell.model != cell.card.model
            ):
                raise SpectatorError("invalid visible snapshot card")
            if cell.property not in (0, 1, 2, 3):
                raise SpectatorError("ordinary snapshot property has no recovered F encoding")
            low = {0: 0, 1: 3, 2: 4, 3: 5}[cell.property]
            fields.append(low | ((cell.owner + 1) << 6))
            modifiers.append(cell.modifier & 255)
            cards.append((index, cell.card))
            continue
        if cell.owner not in (4, 8) or cell.modifier:
            raise SpectatorError("unsupported special tile ownership/modifier")
        if 0x8001 <= cell.model <= 0x8008:
            low = cell.model - 0x8000
        elif cell.model == 0x8009:
            low = 9 + cell.direction
        elif cell.model in (0x800A, 0x800B):
            low = 17 + cell.model - 0x800A
        else:
            raise SpectatorError("unknown spectator tile model")
        expected = {3: 1, 4: 2, 5: 3, 6: 0x10, 7: 0x20, 8: 0x30, 17: 0x40, 18: 0x50}.get(low, 0)
        if cell.property != expected or cell.owner != (8 if 3 <= low <= 5 else 4):
            raise SpectatorError("special tile state disagrees with recovered F constructor")
        fields.append(low)
        modifiers.append(0)
    # CBC tiles can produce additional cards beyond the selected five per
    # player. The exact WatchCard packet encoder enforces the native body cap.
    return tuple(fields), tuple(modifiers), tuple(cards)


def bootstrap(snapshot):
    """Pure WatchInfo/[WatchCard/current09] for selection or a stable turn.

    The caller publishes this ordered tuple once. Repeated snapshots cannot
    repair the original role4 live state machine.
    """
    if snapshot.phase not in ("selection", "turn"):
        raise SpectatorError("watch bootstrap requires selection or a clean turn")
    n = len(snapshot.players)
    if n not in (2, 3):
        raise SpectatorError("watch bootstrap requires two or three named seats")
    if not 0 <= snapshot.sequence <= 255 or snapshot.active not in range(n):
        raise SpectatorError("invalid watch sequence/active seat")
    names = []
    for p in snapshot.players:
        if (
            type(p.npc_id) is not int
            or not 0 <= p.npc_id <= 30
            or p.npc_id
            and (p.peer is not None or p.departed or p.substitute)
        ):
            raise SpectatorError("invalid native NPC snapshot")
        # C selects the complete original asset name (including its viewer
        # level threshold). CN remains safe for the separate sixteen-byte copy.
        name = p.name[:15] if p.npc_id else p.name
        if not name or len(name) > 15 or any(c < 32 or c > 126 for c in name):
            raise SpectatorError("watch name must fit the original NUL-terminated field")
        if not 0 <= p.remaining <= 5:
            raise SpectatorError("watch hand count outside original bounds")
        names.append(name.hex().upper())
    rules = dict(snapshot.game_rules)
    # VsGameInit's leading bm is absent here; WatchInfo shifts the vector.
    # WatchInfo R6 becomes P+D9. Both original watch setup branches create
    # the chat panel only when it is zero; in2 therefore omits that panel.
    if type(snapshot.observation) is not int or snapshot.observation not in (0, 1, 2):
        raise SpectatorError("invalid observation rule")
    r = [rules[k] for k in ("st", "cb", "ca", "tl", "du", "gs")] + [snapshot.observation]
    if any(type(v) is not int or not 0 <= v <= 255 for v in r):
        raise SpectatorError("invalid watch rule byte")
    body = f"@WatchInfo=/N={n}/CN=" + "|".join(names) + "/R=" + _text(r)
    # Q=1 is converted by the original WatchInfo receiver into departed state2.
    # C installs the original NPC or replacement personality and asset name.
    # The ordinary local CPU facade still has C=0; it is not a native NPC id.
    zeros = _text([0] * n)
    if any(
        type(p.departed) is not bool
        or type(p.substitute) is not int
        or not 0 <= p.substitute <= 30
        or p.substitute
        and (not p.departed or rules["gs"] != 0)
        for p in snapshot.players
    ):
        raise SpectatorError("invalid departure/replacement snapshot")
    body += "/Q=" + _text(int(p.departed) for p in snapshot.players)
    humans = n - sum(bool(p.npc_id) for p in snapshot.players)
    body += (
        "/C="
        + _text(p.npc_id or p.substitute for p in snapshot.players)
        + "/G="
        + zeros
        + f"|{humans}/L="
        + zeros
    )
    if snapshot.phase == "selection":
        return (packet(0x43, 0x25, 0, body),)
    fields, modifiers, cards = snapshot_fields(snapshot)
    turn = decode(snapshot.turn_packet) if snapshot.turn_packet else None
    if (
        turn is None
        or turn.primary != 0x43
        or turn.subtype != 9
        or turn.argument != snapshot.sequence
        or "TurnData" not in turn.sections
    ):
        raise SpectatorError("snapshot requires the exact current TurnData packet")
    if turn.sections["TurnData"].get("A") != str(snapshot.active):
        raise SpectatorError("current TurnData active seat differs from snapshot")
    body += "/H=" + _text(p.remaining for p in snapshot.players)
    body += (
        f"/T={snapshot.sequence}/S=0/A={snapshot.active}/F="
        + _text(fields)
        + "/CH="
        + _text(modifiers)
    )
    compact = f"@WatchCard=/T={snapshot.sequence}"
    for index, card in cards:
        compact += f"@N{index}=/D={card.reduced()}"
    return (packet(0x43, 0x25, 1, body), packet(0x43, 0x26, 0, compact), snapshot.turn_packet)


@dataclass
class _Observer:
    round_id: int
    waiting: bool = True
    queue: deque = field(default_factory=deque)
    outstanding: deque = field(default_factory=deque)
    bootstrapping: bool = False
    next_send: float = 0
    last_turn: int | None = None
    last_active: int | None = None
    result_seen: bool = False


class SpectatorService:
    """Bounded observer-only queues; no mutation of match, RNG or participants.

    All snapshot/publish calls belong under the adapter's match lock. handle
    and publish return Deliveries; periodic poll drains one due packet per
    observer. After successful socket drain, record_sent must be called under
    the same lock; an outstanding delivery prevents any additional live send.
    take_evicted returns (peer,reason) for observer-only disconnects.
    The root adapter must authorize table/room membership before handle.

    Delays start from actual emission and never catch up by flooding a queue.
    They are explicit preview policy, not recovered server timing guarantees.
    """

    PUBLIC_SUBTYPES = frozenset((8, 9, 0x0A, 0x0C, 0x0D, 0x23, 0x24, 0x27, 0x28))

    def __init__(
        self,
        *,
        enabled=False,
        clock=time.monotonic,
        bootstrap_delay=8.0,
        turn_delay=3.0,
        action_delay=3.0,
        packet_delay=0.1,
        max_backlog=256,
        max_observers=16,
    ):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        for delay in (bootstrap_delay, turn_delay, action_delay, packet_delay):
            if not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
                raise ValueError("spectator delays must be finite and nonnegative")
        if type(max_backlog) is not int or not 1 <= max_backlog <= 4096:
            raise ValueError("max_backlog must be1..4096")
        if type(max_observers) is not int or not 1 <= max_observers <= 256:
            raise ValueError("max_observers must be1..256")
        self.enabled, self.clock = enabled, clock
        self.bootstrap_delay, self.turn_delay, self.action_delay, self.packet_delay = (
            bootstrap_delay,
            turn_delay,
            action_delay,
            packet_delay,
        )
        self.max_backlog, self.max_observers = max_backlog, max_observers
        self._epoch = None
        self._observers = {}
        self._evicted = []

    def active(self, peer):
        return peer in self._observers

    @property
    def count(self):
        return len(self._observers)

    def disconnect(self, peer):
        return self._observers.pop(peer, None) is not None

    def take_evicted(self):
        output = tuple(self._evicted)
        self._evicted.clear()
        return output

    def _evict(self, peer, reason):
        if self.disconnect(peer):
            self._evicted.append((peer, reason))

    def reset(self, epoch):
        for peer in tuple(self._observers):
            self._evict(peer, "match_replaced")
        self._epoch = epoch

    def _sync(self, snapshot, epoch):
        if epoch is None:
            raise SpectatorError("match epoch object is required")
        if self._epoch is not epoch:
            self.reset(epoch)
        for peer, state in tuple(self._observers.items()):
            if snapshot.round_id != state.round_id:
                self._evict(peer, "round_replaced")

    def _attach(self, peer, state, snapshot):
        try:
            messages = bootstrap(snapshot)  # Validate the entire bootstrap atomically.
        except MatchError as exc:
            # Dense supported boards can exceed the native snapshot body cap.
            # Reject this observer without interrupting participant publication.
            raise SpectatorError(str(exc)) from exc
        state.waiting = False
        state.outstanding.extend(messages)
        state.bootstrapping = True
        state.last_turn = snapshot.sequence if snapshot.phase == "turn" else None
        state.last_active = snapshot.active if snapshot.phase == "turn" else None
        return [Delivery(peer, payload) for payload in messages]

    def delay_after(self, payload):
        """Preview policy after actual emission; not an original timing bound."""
        msg = decode(payload)
        if msg is None or msg.primary != 0x43:
            raise SpectatorError("not a spectator gameplay packet")
        return (
            self.bootstrap_delay
            if msg.subtype in (8, 0x25, 0x26)
            else (
                self.turn_delay
                if msg.subtype == 9
                else self.action_delay if msg.subtype in (0x0A, 0x0C) else self.packet_delay
            )
        )

    def record_sent(self, peer, payload, *, epoch):
        """Complete an ordered delivery after drain, ignoring retired epochs.

        The adapter never holds the match lock while awaiting socket I/O.
        It uses one bounded writer task per observer, preserves payload order,
        then reacquires the lock for this callback. Disconnect on failed I/O.
        """
        if not self.enabled or epoch is not self._epoch:
            return False
        state = self._observers.get(peer)
        if state is None:
            return False
        if not state.outstanding or state.outstanding[0] != payload:
            raise SpectatorError("spectator delivery completion is out of order")
        state.outstanding.popleft()
        delay = self.bootstrap_delay if state.bootstrapping else self.delay_after(payload)
        state.next_send = self.clock() + delay
        if not state.outstanding:
            state.bootstrapping = False
        return True

    def handle(self, peer, payload, snapshot, *, epoch):
        if not self.enabled:
            return None
        msg = decode(payload)
        if msg is None or msg.primary != 0x43 or msg.subtype != 0x25:
            if self.active(peer) and msg is not None and msg.primary == 0x43:
                raise SpectatorError("observer cannot submit participant gameplay actions")
            return None
        if msg.argument != 0 or msg.sections != {"Data": {"Watch": "0"}}:
            raise SpectatorError("unsupported watch request")
        self._sync(snapshot, epoch)
        if any(p.peer == peer for p in snapshot.players):
            raise SpectatorError("participant cannot become its own observer")
        if self.active(peer):
            return []  # Do not replay bootstrap on retry.
        if len(self._observers) >= self.max_observers:
            raise SpectatorError("spectator preview is full")
        if snapshot.phase not in ("selection", "turn", "battle"):
            raise SpectatorError("spectator preview requires an active round")
        state = _Observer(snapshot.round_id)
        if snapshot.phase == "battle":
            self._observers[peer] = state
            return []  # Next clean boundary only.
        out = self._attach(peer, state, snapshot)
        self._observers[peer] = state
        return out

    @staticmethod
    def _events(deliveries):
        """Collapse copies to different recipients, preserve repeated events.

        MatchService emits each semantic packet to its human peers contiguously.
        An identical packet to the same peer again is a new event (e.g. ties).
        """
        previous = None
        recipients = set()
        for delivery in deliveries:
            if delivery.payload == previous and delivery.peer not in recipients:
                recipients.add(delivery.peer)
                continue
            previous = delivery.payload
            recipients = {delivery.peer}
            yield delivery.payload

    def publish(self, deliveries, snapshot, *, epoch):
        if not self.enabled:
            return []
        # A stale scheduled continuation must not reset/replace current epoch.
        if self._epoch is not None and epoch is not self._epoch:
            return []
        self._sync(snapshot, epoch)
        events = []
        for payload in self._events(deliveries):
            msg = decode(payload)
            if msg is not None and msg.primary == 0x43 and msg.subtype in self.PUBLIC_SUBTYPES:
                events.append((msg, payload))
        out = []
        for peer, state in tuple(self._observers.items()):
            if state.waiting:
                if snapshot.phase in ("selection", "turn"):
                    try:
                        out += self._attach(peer, state, snapshot)
                    except SpectatorError:
                        self._evict(peer, "unsupported_snapshot")
                elif snapshot.phase in ("result", "finished"):
                    self._evict(peer, "round_ended_before_bootstrap")
                continue  # The snapshot already includes this publish batch.
            for msg, payload in events:
                if state.result_seen:
                    continue
                if msg.subtype == 9:
                    if msg.argument == state.last_turn:
                        continue
                    active = int(msg.sections["TurnData"]["A"])
                    if state.last_turn is not None:
                        departed = tuple(i for i, p in enumerate(snapshot.players) if p.departed)
                        substitutes = tuple(
                            i for i, p in enumerate(snapshot.players) if p.substitute
                        )
                        boundary = next_turn(
                            len(snapshot.players),
                            dict(snapshot.game_rules)["gs"],
                            departed,
                            state.last_turn,
                            state.last_active,
                            substitutes,
                        )
                        if (msg.argument, active) != (boundary.sequence, boundary.active):
                            self._evict(peer, "turn_gap")
                            break
                    state.last_turn = msg.argument
                    state.last_active = active
                state.queue.append(payload)
                if msg.subtype == 0x0D:
                    state.result_seen = True
                if len(state.queue) > self.max_backlog:
                    self._evict(peer, "backlog_limit")
                    break
        return out + self.poll(snapshot, epoch=epoch)

    def poll(self, snapshot, *, epoch):
        if not self.enabled or epoch is not self._epoch:
            return []
        self._sync(snapshot, epoch)
        now = self.clock()
        out = []
        for peer, state in self._observers.items():
            if state.waiting or state.outstanding or not state.queue or now < state.next_send:
                continue
            payload = state.queue.popleft()
            state.outstanding.append(payload)
            out.append(Delivery(peer, payload))
        return out
