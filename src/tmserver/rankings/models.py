"""Validated historical ranking values shared by ranking views and periods."""

from dataclasses import dataclass


class RankingError(ValueError):
    """Malformed or unauthorized local request; no partial snapshot is stored."""


def _uint(value, bits, name):
    if type(value) is not int or not 0 <= value < (1 << bits):
        raise RankingError(f"{name} must be uint{bits}")
    return value


@dataclass(frozen=True)
class RankingHistory:
    """Optional explicit data, not computed from current profile by this module.

    Previous positions are in RR order0..4. Zero means unavailable. Positive
    values1..127 are the original signed-byte representable display range.
    """

    weekly_prize: int | None = None
    last_week_rating: int | None = None
    rookie: bool = False
    anonymous: bool = False
    qualified: bool = True
    previous_ranks: tuple[int, ...] = (0, 0, 0, 0, 0)

    def __post_init__(self):
        for name in ("weekly_prize", "last_week_rating"):
            value = getattr(self, name)
            if value is not None:
                _uint(value, 32, name)
        for name in ("rookie", "anonymous", "qualified"):
            if type(getattr(self, name)) is not bool:
                raise RankingError(f"{name} must be boolean")
        if (
            not isinstance(self.previous_ranks, tuple)
            or len(self.previous_ranks) != 5
            or any(type(value) is not int or not 0 <= value <= 127 for value in self.previous_ranks)
        ):
            raise RankingError("previous_ranks requires five integers0..127 in RR order")
