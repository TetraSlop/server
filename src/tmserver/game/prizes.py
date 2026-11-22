"""Client card-prize order for ordinary matches, separate from account storage.

Client routines: 100C90EA..100C93E2, 100CAAE3..100CAC6E, 100CAEDD..100CAF59 and
100CB117..100CB1BA. Details: TetraPackets flows/results-and-prizes.md.
"""

from dataclasses import dataclass, replace
from typing import Any

from tmserver.game.errors import MatchError


@dataclass(frozen=True)
class PrizeCard:
    """One original selected slot, in the client's current compacted hand."""

    original_slot: int
    card: Any
    current_owner: int


@dataclass(frozen=True)
class PrizeOption:
    hand_index: int
    original_slot: int
    card: Any


@dataclass(frozen=True)
class PrizeChoice:
    choice_id: int
    winner: int
    loser: int
    options: tuple


@dataclass(frozen=True)
class PrizeAward:
    winner: int
    loser: int
    hand_index: int
    original_slot: int
    source_card: Any
    card: Any  # Permanent received copy has deck255, unlike source_card.


@dataclass(frozen=True)
class PrizeSettlement:
    transfers: tuple  # All PrizeAward entries in global presentation order.


def choose_computer_prize(choice, raw_rng):
    """Original 100CA258..100CA284 for an ambiguous simulated-player prize.

    The client polls rand() % 60 < 2 before choosing; the server chooses at once
    but keeps that first draw so the CRT stream matches a successful poll.
    Sole/perfect prizes belong to PrizeLedger's automatic path and draw nothing.
    """
    raw_rng()
    return choice.options[raw_rng() % len(choice.options)].hand_index


class PrizeLedger:
    """Resolve one completed ordinary2/3-player round.

    hands[seat] must have five immutable cards, in ascending occupied-board
    order (the original result reconstruction), with stable original_slot0..4.
    Scores and owner numbers use wire/server seats, not each client's rotated
    view. CBC cards belong outside these hands and this ownership ledger.

    Construction and each accepted choice resolve every subsequent automatic
    sole-candidate/perfect award, stopping at the next genuine user choice.
    No RNG, rendering, timing, or persistence is performed.
    """

    def __init__(self, scores, hands):
        self.scores = tuple(scores)
        self.player_count = len(self.scores)
        self._hands = [list(hand) for hand in hands]
        self.order = tuple(
            sorted(range(self.player_count), key=lambda seat: (-self.scores[seat], seat))
        )
        self._pairs = tuple(
            (winner, loser)
            for i, winner in enumerate(self.order[:-1])
            for loser in self.order[i + 1 :]
            if self.scores[winner] > self.scores[loser]
        )
        self._pair = 0
        self._transfers = []
        self._choice_serial = 0
        self._pending = None
        self._settlement = None
        self._advance()

    @property
    def pending(self):
        return self._pending

    @property
    def complete(self):
        return self._settlement is not None

    @property
    def transfers(self):
        """Immutable partial audit; not authorization for a partial commit."""
        return tuple(self._transfers)

    def settlement(self):
        if self._settlement is None:
            raise MatchError("prize choices are incomplete")
        return self._settlement

    def select(self, winner, hand_index, *, choice_id):
        """Apply one authenticated winner choice for exactly pending.choice_id.

        choice_id is an internal generation guard, not an original wire field.
        GetSelect carries only winner-seat header and hand index; see limitations.
        A repeated/stale generation raises without mutation.
        """
        pending = self._pending
        if pending is None:
            raise MatchError("there is no outstanding prize choice")
        if choice_id != pending.choice_id:
            raise MatchError("stale prize choice")
        if winner != pending.winner:
            raise MatchError("only the current winning seat can choose")
        if hand_index not in tuple(o.hand_index for o in pending.options):
            raise MatchError("prize must be an eligible captured card")
        award = self._grant(pending.winner, pending.loser, hand_index)
        self._pending = None
        self._pair += 1  # A user-selected pair is necessarily nonperfect.
        self._advance()
        return award

    def _grant(self, winner, loser, hand_index):
        chosen = self._hands[loser].pop(hand_index)
        award = PrizeAward(
            winner,
            loser,
            hand_index,
            chosen.original_slot,
            chosen.card,
            replace(chosen.card, deck=255),
        )
        self._transfers.append(award)
        perfect = self.scores[winner] == sum(self.scores)
        if not perfect:
            self._hands[loser] = [
                replace(c, current_owner=loser) if c.current_owner == winner else c
                for c in self._hands[loser]
            ]
        self._hands[winner] = [
            replace(c, current_owner=winner) if c.current_owner == loser else c
            for c in self._hands[winner]
        ]
        return award

    def _advance(self):
        while self._pair < len(self._pairs):
            winner, loser = self._pairs[self._pair]
            options = tuple(
                PrizeOption(i, c.original_slot, c.card)
                for i, c in enumerate(self._hands[loser])
                if c.current_owner == winner
            )
            if not options:
                self._pair += 1
                continue
            perfect = self.scores[winner] == sum(self.scores)
            if len(options) > 1 and not perfect:
                self._choice_serial += 1
                self._pending = PrizeChoice(self._choice_serial, winner, loser, options)
                return
            self._grant(winner, loser, options[0].hand_index)
            if not perfect or not self._hands[loser]:
                self._pair += 1
        self._settlement = PrizeSettlement(tuple(self._transfers))
