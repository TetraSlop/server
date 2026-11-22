"""Watchdog for stalled matches.

Clients time out their own choices by sending normal CardSelect, PutData or
BattleSelect packets, and have no known resume handshake. Server choice: the grace
periods, on a monotonic clock. The watchdog only observes; it never advances a
match, sends a packet or changes ownership.
"""

import math
import time
from dataclasses import dataclass
from typing import Hashable

TURN_SECONDS = (15, 30, 60, 180, 300, 600)


@dataclass(frozen=True)
class Timeout:
    reason: str
    epoch: object
    phase: str
    sequence: int
    round_id: int
    missing_peers: tuple[Hashable, ...]
    started: float
    deadline: float


class MatchWatchdog:
    """Call observe after each handle/advance; expired also observes current state.

    Pass the current MatchService as both match and epoch, or a distinct
    generation token as epoch. A query for an old epoch returns None and never
    switches the tracked match. Repeated observations do not renew a deadline.
    All methods run synchronously under the adapter's existing match lock.

    Only application state counts as progress. The unseated lobby has no
    deadline until GameReady/Req begins the start handshake; that handshake has
    one ack_timeout budget. The admitted computer menu has its own deadline.
    Result Ready and Continue are separate idle stages. A
    pending repeated battle uses processing_timeout; no per-BattleData refresh
    occurs while the same turn and target remain pending.
    """

    def __init__(
        self,
        *,
        clock=time.monotonic,
        ack_timeout=120,
        decision_grace=60,
        result_timeout=900,
        processing_timeout=30,
        configuration_timeout=900,
    ):
        self.clock = clock
        for name, value in [
            ("ack_timeout", ack_timeout),
            ("decision_grace", decision_grace),
            ("result_timeout", result_timeout),
            ("processing_timeout", processing_timeout),
            ("configuration_timeout", configuration_timeout),
        ]:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number")
            if value < 0 or (name != "decision_grace" and value == 0):
                raise ValueError(f"{name} must be positive (grace may be zero)")
            setattr(self, name, float(value))
        self._match = None
        self._epoch = None
        self._last_time = None
        self._clear()

    def _clear(self):
        self._turn = None
        self._decision_started = None
        self._decision_deadline = None
        self._wait_key = None
        self._wait = None

    def _now(self):
        value = float(self.clock())
        if not math.isfinite(value) or (self._last_time is not None and value < self._last_time):
            raise ValueError("watchdog clock must be finite and monotonic")
        self._last_time = value
        return value

    @staticmethod
    def _limit(match):
        tl = match.game_rules["tl"]
        if type(tl) is not int or not 0 <= tl < len(TURN_SECONDS):
            raise ValueError("tl must be one of the six original timer indices")
        return TURN_SECONDS[tl]

    def _arm(self, key, reason, match, peers, now, duration, *, started=None, deadline=None):
        if self._wait_key != key:
            self._wait_key = key
            self._wait = Timeout(
                reason,
                self._epoch,
                match.phase,
                match.sequence,
                match.completed_matches,
                tuple(peers),
                now if started is None else started,
                now + duration if deadline is None else deadline,
            )
        else:
            # Missing participants and phase may change without granting more time.
            old = self._wait
            self._wait = Timeout(
                reason,
                old.epoch,
                match.phase,
                match.sequence,
                match.completed_matches,
                tuple(peers),
                old.started,
                old.deadline,
            )
        return self._wait

    def observe(self, match, epoch=None):
        """Return current immutable wait description, or None when no client waits."""
        now = self._now()
        epoch = match if epoch is None else epoch
        if match is not self._match or epoch != self._epoch:
            self._match, self._epoch = match, epoch
            self._clear()
        humans = tuple(
            p.peer
            for p in match.players
            if p.peer is not None and not getattr(p, "departed", False)
        )
        phase, round_id, sequence = match.phase, match.completed_matches, match.sequence
        if humans and phase == "lobby" and match.start_pending:
            missing = tuple(p.peer for p in match.players if p.peer is not None and not p.accepted)
            return self._arm(
                ("start", round_id), "start_handshake", match, missing, now, self.ack_timeout
            )
        if not humans or phase in ("lobby", "finished"):
            self._clear()
            return None
        if phase == "computer_menu":
            self._turn = None
            self._decision_started = self._decision_deadline = None
            return self._arm(
                ("computer_menu", round_id),
                "computer_configuration",
                match,
                humans,
                now,
                self.configuration_timeout,
            )
        if phase == "selection":
            missing = tuple(p.peer for p in match.players if p.peer is not None and not p.selected)
            self._turn = None
            self._decision_started = self._decision_deadline = None
            if not missing:
                self._wait_key = self._wait = None
                return None
            return self._arm(
                ("selection", round_id),
                "card_selection",
                match,
                missing,
                now,
                self._limit(match) + self.decision_grace,
            )
        if phase in ("turn", "battle"):
            turn = (round_id, sequence)
            if self._turn != turn:
                self._turn = turn
                self._decision_started = self._decision_deadline = None
                self._wait_key = self._wait = None
            missing = tuple(peer for peer in humans if peer not in match.acks)
            if missing:
                return self._arm(("ack", turn), "turn_ack", match, missing, now, self.ack_timeout)
            if self._decision_started is None:
                self._decision_started = now
                self._decision_deadline = now + self._limit(match) + self.decision_grace
            if match.pending_work:
                # The target is semantically stable across tie continuations.
                # Advancing to a different target is genuine processing progress.
                target = match._pending_battle
                return self._arm(
                    ("processing", turn, target),
                    "server_processing",
                    match,
                    humans,
                    now,
                    self.processing_timeout,
                )
            if not 0 <= match.active < len(match.players):
                raise ValueError("active seat outside current player list")
            peer = match.players[match.active].peer
            if peer is None or getattr(match.players[match.active], "substitute", 0):
                # CPU decisions normally happen synchronously on the final ack.
                # If the adapter leaves one pending, diagnose server processing.
                return self._arm(
                    ("cpu", turn), "server_processing", match, humans, now, self.processing_timeout
                )
            reason = "battle_selection" if phase == "battle" else "placement"
            return self._arm(
                ("decision", turn),
                reason,
                match,
                (peer,),
                now,
                0,
                started=self._decision_started,
                deadline=self._decision_deadline,
            )
        if phase == "result":
            self._turn = None
            self._decision_started = self._decision_deadline = None
            missing = tuple(peer for peer in humans if peer not in match.ready)
            if missing:
                return self._arm(
                    ("result_ready", round_id),
                    "result_ready",
                    match,
                    missing,
                    now,
                    self.result_timeout,
                )
            missing = tuple(peer for peer in humans if peer not in match.continues)
            if missing:
                return self._arm(
                    ("result_continue", round_id),
                    "result_continue",
                    match,
                    missing,
                    now,
                    self.result_timeout,
                )
            self._wait_key = self._wait = None
            return None
        raise ValueError(f"unknown match phase {phase!r}")

    def expired(self, match, epoch=None):
        """Return Timeout at/after deadline, else None; old generations are inert.

        The caller must recheck the generation before acting and close sessions
        outside its match lock. No wire timeout or forced move is fabricated.
        """
        epoch = match if epoch is None else epoch
        if match is not self._match or epoch != self._epoch:
            return None
        wait = self.observe(match, epoch)
        return wait if wait is not None and self._last_time >= wait.deadline else None
