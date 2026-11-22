"""Client card-prize order for ordinary matches, separate from account storage.

Client routines: 100C90EA..100C93E2, 100CAAE3..100CAC6E, 100CAEDD..100CAF59 and
100CB117..100CB1BA. Details: TetraPackets flows/results-and-prizes.md.
"""

from dataclasses import dataclass, replace
from typing import Any


class PrizeError(ValueError):
    pass


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
    surviving: tuple  # Per-seat tuple[PrizeCard]; original slots remain stable.
    awards: tuple  # Per-seat tuple[PrizeAward] in original transfer order.
    transfers: tuple  # All PrizeAward entries in global presentation order.


def poll_computer_prize(choice, raw_rng, denominator=60):
    """Original 100CA258..100CA284 for an ambiguous simulated-player prize.

    The caller has already checked simulated-player/local-simulation flags.
    Sole/perfect prizes belong to PrizeLedger's automatic path and draw nothing.
    """
    if not isinstance(choice, PrizeChoice) or not 2 <= len(choice.options) <= 5:
        raise PrizeError("computer choice needs an ambiguous prize candidate list")
    indices = tuple(o.hand_index for o in choice.options)
    if any(type(i) is not int or not 0 <= i < 5 for i in indices) or len(set(indices)) != len(
        indices
    ):
        raise PrizeError("prize candidates must be distinct compacted hand indices")
    if type(denominator) is not int or not 1 <= denominator <= 0x7FFFFFFF:
        raise PrizeError("prize pacing denominator must be a positive signed DWORD")

    def draw():
        value = raw_rng()
        if type(value) is not int or not 0 <= value <= 32767:
            raise PrizeError("prize raw_rng must return CRT values0..32767")
        return value

    if draw() % denominator >= 2:
        return None
    return indices[draw() % len(indices)]


def choose_computer_prize(choice, raw_rng):
    """Immediate scheduler policy; retain both original successful-poll draws."""
    return poll_computer_prize(choice, raw_rng, denominator=1)


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
        if self.player_count not in (2, 3):
            raise PrizeError("ordinary prizes require two or three players")
        if (
            any(type(x) is not int or not 0 <= x <= 255 for x in self.scores)
            or sum(self.scores) == 0
        ):
            raise PrizeError("scores must be nonnegative bytes with a positive total")
        if len(hands) != self.player_count:
            raise PrizeError("one hand is required for each seat")
        self._hands = []
        for hand in hands:
            hand = tuple(hand)
            if len(hand) != 5 or any(not isinstance(c, PrizeCard) for c in hand):
                raise PrizeError("each input hand must contain five PrizeCards")
            if any(type(c.original_slot) is not int for c in hand) or sorted(
                c.original_slot for c in hand
            ) != list(range(5)):
                raise PrizeError("each hand must identify every original slot0..4 once")
            if any(
                type(c.current_owner) is not int or not 0 <= c.current_owner < self.player_count
                for c in hand
            ):
                raise PrizeError("capture owners must identify an ordinary seat")
            # The service/economy card dataclasses are immutable and share deck.
            # Validate the received-card conversion before any selection mutates.
            try:
                for c in hand:
                    if not getattr(
                        getattr(type(c.card), "__dataclass_params__", None), "frozen", False
                    ):
                        raise TypeError("card dataclass must be frozen")
                    if type(c.card.deck) is not int or c.card.deck not in (*range(50), 255):
                        raise ValueError("deck must be 0..49 or 255")
                    replace(c.card, deck=255)
            except (AttributeError, TypeError, ValueError) as exc:
                raise PrizeError("cards must support an immutable deck255 copy") from exc
            self._hands.append(list(hand))
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
            raise PrizeError("prize choices are incomplete")
        return self._settlement

    def select(self, winner, hand_index, *, choice_id):
        """Apply one authenticated winner choice for exactly pending.choice_id.

        choice_id is an internal generation guard, not an original wire field.
        GetSelect carries only winner-seat header and hand index; see limitations.
        A repeated/stale generation raises without mutation.
        """
        pending = self._pending
        if pending is None:
            raise PrizeError("there is no outstanding prize choice")
        if type(choice_id) is not int or choice_id != pending.choice_id:
            raise PrizeError("stale prize choice")
        if type(winner) is not int or winner != pending.winner:
            raise PrizeError("only the current winning seat can choose")
        if type(hand_index) is not int or hand_index not in tuple(
            o.hand_index for o in pending.options
        ):
            raise PrizeError("prize must be an eligible captured card")
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
        transfers = tuple(self._transfers)
        self._settlement = PrizeSettlement(
            tuple(tuple(hand) for hand in self._hands),
            tuple(
                tuple(a for a in transfers if a.winner == seat) for seat in range(self.player_count)
            ),
            transfers,
        )
