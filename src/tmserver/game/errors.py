"""Match failures handled by the table's abort/release boundary."""


class MatchError(ValueError):
    """Rejected client input; the adapter should log it, not fabricate an ack."""


class RulesExecutionError(ValueError):
    """An injected rules operation failed; abort this round without retrying it."""
