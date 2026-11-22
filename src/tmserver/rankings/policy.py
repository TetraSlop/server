"""Optional rating coordinator: growth, statistics and history commit together.

The adapter selects the mode and saves the snapshot with the economy before
delivering ResultData.
"""

import time
from copy import copy

from tmserver.rankings.errors import RankingError
from tmserver.rankings.history import LocalRatingHistory
from tmserver.rankings.periods import RankingPeriods
from tmserver.rankings.stats import PracticeStats


def _matches_profile(state, profile):
    return state.sequence == profile.sequence and (
        state.revision == 0 or state.rating == profile.rating
    )


class RatingPolicy:
    def __init__(self, economy, *, mode="preserve", snapshot=None, weekly=False, clock=time.time):
        if mode not in ("preserve", "local-history"):
            raise RankingError("rating mode must be preserve or local-history")
        if type(weekly) is not bool or weekly and mode != "local-history":
            raise RankingError("weekly rankings require explicit local-history mode")
        if not callable(clock):
            raise RankingError("rating clock must be callable")
        self.economy = economy
        self.mode = mode
        self._weekly = weekly
        self.clock = clock
        periods = None
        if snapshot is not None:
            if (
                type(snapshot) is not dict
                or type(snapshot.get("version")) is not int
                or snapshot["version"] not in (1, 2)
                or set(snapshot)
                != (
                    {"version", "history"}
                    if snapshot["version"] == 1
                    else {"version", "history", "periods"}
                )
            ):
                raise RankingError("invalid rating policy snapshot")
            if snapshot["version"] == 2:
                periods = snapshot["periods"]
                if periods is None:
                    raise RankingError("version2 rating policy requires initialized periods")
            snapshot = snapshot["history"]
        self.history = LocalRatingHistory.restore(snapshot)
        self.periods = RankingPeriods.restore(periods)
        for key in set(self.history.snapshot()["accounts"]) | self.periods.account_keys:
            self.economy.account_view(key)  # A journal cannot create accounts.

    def stage_state(self, economy):
        # Commits replace validated history/periods; shared originals stay intact.
        staged = copy(self)
        staged.economy = economy
        return staged

    def publish_state(self, staged):
        self.history = staged.history
        self.periods = staged.periods

    @property
    def enabled(self):
        return self.mode == "local-history"

    @property
    def weekly(self):
        return self._weekly

    def validate_profiles(self):
        """Reject stale local histories before startup recovery or admission.

        Preserved mode and unenrolled accounts require no migration. A fresh
        enrollment has not changed the existing scalar rating yet; only its
        sequence must agree until the first local result commits.
        """
        if not self.enabled:
            return
        mismatched = [
            key
            for key in sorted(self.history.snapshot()["accounts"])
            if not _matches_profile(
                self.history.state(key),
                PracticeStats.from_profile(self.economy.profile_bytes(key)),
            )
        ]
        if mismatched:
            raise RankingError(
                "rating history/profile mismatch for accounts "
                + ", ".join(mismatched)
                + "; explicit migration is required"
            )

    def snapshot(self):
        # Mode is an explicit startup choice; preserving ratings must not erase
        # previously saved history merely because this run disables the policy.
        snapshot = {"version": 1, "history": self.history.snapshot()}
        periods = self.periods.snapshot()
        if periods is not None:
            snapshot.update(version=2, periods=periods)
        return snapshot

    def advance_week(self):
        """Prepare rollover in isolation; the caller owns durable publication."""
        if not self.weekly:
            return False
        staged = RankingPeriods.restore(self.periods.snapshot())
        changed = staged.advance(self.clock())
        if changed:
            self.periods = staged
        return changed

    def weekly_history(self):
        """Return a read-only projection; reads never advance or enable a period."""
        return self.periods.historical_stats() if self.weekly else {}

    def is_ranked(self, key):
        """Local rankings require a committed result matching the saved profile.

        Enrollment alone preserves the old scalar rating. It cannot establish
        a rank under the local ring policy; neither can a stale saved ring after
        playing with that policy disabled. Preserved mode retains its old rules.
        """
        if not self.enabled:
            return True
        if not self.history.enrolled(key):
            return False
        state = self.history.state(key)
        stats = PracticeStats.from_profile(self.economy.profile_bytes(key))
        return state.revision > 0 and (state.rating, state.sequence) == (
            stats.rating,
            stats.sequence,
        )

    def quote(self, match_id, seats, scores, *, departed_seats=()):
        """Return human-seat→RatingQuote, or None under preserved default.

        seats maps wire positions to authenticated account keys. Explicit enable
        enrolls missing accounts at their CURRENT profile sequence with neutral
        history; it does not change their existing rating until a result commits.
        Departures are not ordinary rating outcomes and must abort this rated
        round until their native policy has been proven and deliberately added.
        """
        if not self.enabled:
            return None
        if departed_seats:
            raise RankingError("local-history rating does not support departed rounds")
        if (
            type(seats) is not dict
            or not 1 <= len(seats) <= 3
            or len(set(seats.values())) != len(seats)
        ):
            raise RankingError("rating seats require distinct authenticated accounts")
        staged = LocalRatingHistory.restore(self.history.snapshot())
        quotes = {}
        for seat, key in seats.items():
            previous = PracticeStats.from_profile(self.economy.profile_bytes(key))
            if not staged.enrolled(key):
                staged.enroll(key, sequence=previous.sequence)
            if not _matches_profile(staged.state(key), previous):
                raise RankingError(
                    "rating history/profile mismatch; explicit migration is required"
                )
            quotes[seat] = staged.quote(key, match_id, scores, seat)
        # Enrollment is independent metadata only, after all accounts validate.
        # There is no ring result update and no profile mutation at quote time.
        self.history = staged
        return quotes

    def commit_growth(
        self,
        rounds,
        stats_by_key=None,
        payouts_by_key=None,
        *,
        rating_changes_by_key=None,
        vs_player_game=False,
    ):
        """Commit profile/cards and the validated rating journal synchronously.

        Copy/validate the entire history transaction first; economy validates all
        owned cards/stats before any account replacement. After it succeeds the
        staged journal assignment cannot fail. Parent must still atomically save
        both snapshots and fail-close if that durable save fails.
        """
        if not self.enabled:
            if rating_changes_by_key is not None:
                raise RankingError("preserve mode cannot authorize rating changes")
            return self.economy.commit_match_growth(
                rounds, stats_by_key, payouts_by_key, vs_player_game=vs_player_game
            )
        if type(rating_changes_by_key) is not dict or not rating_changes_by_key:
            raise RankingError("enabled rating policy requires quoted result proofs")
        staged = LocalRatingHistory.restore(self.history.snapshot())
        staged.commit_many(tuple(rating_changes_by_key.values()))
        if any(key != quote.key for key, quote in rating_changes_by_key.items()):
            raise RankingError("rating quotes must be keyed by their own account")
        if staged.snapshot() == self.history.snapshot():
            # Every quote was already committed: a replayed result must not
            # be counted again in the weekly period or the profiles.
            raise RankingError("match result was already committed")
        periods = self.periods
        if self.weekly:
            if not isinstance(stats_by_key, dict):
                raise RankingError("weekly rankings require completed result statistics")
            results = {}
            for key in rating_changes_by_key:
                result = stats_by_key.get(key)
                if not isinstance(result, PracticeStats):
                    raise RankingError("missing or invalid weekly result statistics")
                previous = PracticeStats.from_profile(self.economy.profile_bytes(key))
                results[key] = (
                    (result.reward_sum - previous.reward_sum) & 0xFFFFFFFF,
                    result.rating,
                )
            periods = RankingPeriods.restore(self.periods.snapshot())
            periods.record(self.clock(), results)
        refreshed = self.economy.commit_match_growth(
            rounds,
            stats_by_key,
            payouts_by_key,
            rating_changes_by_key,
            vs_player_game=vs_player_game,
        )
        self.history = staged
        self.periods = periods
        return refreshed
