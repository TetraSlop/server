"""Optional rating coordinator: joint in-memory growth/stat/history commit.

The adapter selects mode explicitly and persists snapshot with economy before
delivering ResultData. No socket, CLI, file write, or implicit profile reset.
"""

from copy import copy

from tmserver.economy_service import EconomyError
from tmserver.local_rating_history import LocalRatingHistory, RatingHistoryError
from tmserver.result_stats import PracticeStats


class RatingPolicyError(EconomyError):
    pass


class RatingPolicy:

    def __init__(self, economy, *, mode="preserve", snapshot=None):
        if mode not in ("preserve", "local-history"):
            raise RatingPolicyError("rating mode must be preserve or local-history")
        self.economy = economy
        self.mode = mode
        if snapshot is not None:
            if (
                type(snapshot) is not dict
                or set(snapshot) != {"version", "history"}
                or type(snapshot["version"]) is not int
                or snapshot["version"] != 1
            ):
                raise RatingPolicyError("invalid rating policy snapshot")
            snapshot = snapshot["history"]
        try:
            self.history = LocalRatingHistory.restore(snapshot)
        except RatingHistoryError as exc:
            raise RatingPolicyError(str(exc)) from exc
        for key in self.history.snapshot()["accounts"]:
            self.economy.account_view(key)  # A journal cannot create accounts.

    def stage_state(self, economy):
        # Commits replace validated history; they never mutate the shared history.
        staged = copy(self)
        staged.economy = economy
        return staged

    def publish_state(self, staged):
        self.history = staged.history

    @property
    def enabled(self):
        return self.mode == "local-history"

    def snapshot(self):
        # Mode is an explicit startup choice; preserving ratings must not erase
        # previously saved history merely because this run disables the policy.
        return {"version": 1, "history": self.history.snapshot()}

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
            raise RatingPolicyError("local-history rating does not support departed rounds")
        if (
            type(seats) is not dict
            or not 1 <= len(seats) <= 3
            or len(set(seats.values())) != len(seats)
        ):
            raise RatingPolicyError("rating seats require distinct authenticated accounts")
        try:
            staged = LocalRatingHistory.restore(self.history.snapshot())
            quotes = {}
            for seat, key in seats.items():
                previous = PracticeStats.from_profile(self.economy.profile_bytes(key))
                if not staged.enrolled(key):
                    staged.enroll(key, sequence=previous.sequence)
                current = staged.state(key)
                if (
                    current.sequence != previous.sequence
                    or current.revision > 0
                    and current.rating != previous.rating
                ):
                    raise RatingPolicyError(
                        "rating history/profile mismatch; explicit migration is required"
                    )
                quotes[seat] = staged.quote(key, match_id, scores, seat)
        except RatingHistoryError as exc:
            raise RatingPolicyError(str(exc)) from exc
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
                raise RatingPolicyError("preserve mode cannot authorize rating changes")
            return self.economy.commit_match_growth(
                rounds, stats_by_key, payouts_by_key, vs_player_game=vs_player_game
            )
        if type(rating_changes_by_key) is not dict or not rating_changes_by_key:
            raise RatingPolicyError("enabled rating policy requires quoted result proofs")
        try:
            staged = LocalRatingHistory.restore(self.history.snapshot())
            staged.commit_many(tuple(rating_changes_by_key.values()))
        except RatingHistoryError as exc:
            raise RatingPolicyError(str(exc)) from exc
        refreshed = self.economy.commit_match_growth(
            rounds,
            stats_by_key,
            payouts_by_key,
            rating_changes_by_key,
            vs_player_game=vs_player_game,
        )
        self.history = staged
        return refreshed
