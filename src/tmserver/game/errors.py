"""Rejected match input shared by rules, models and packet handling."""


class MatchError(ValueError):
    """Rejected client input; the adapter should log it, not fabricate an ack."""
