"""Shared rejection type for rankings, ratings and result statistics."""

from tmserver.economy.errors import EconomyError


class RankingError(EconomyError):
    """Rejected ranking, rating or statistics input; nothing was changed."""
