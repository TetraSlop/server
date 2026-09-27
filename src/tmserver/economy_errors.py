"""Shared rejection type for card validation and account operations."""


class EconomyError(ValueError):
    """Rejected request; no account mutation was applied."""
