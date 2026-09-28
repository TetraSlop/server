"""Backend-neutral save contract and supported service journals."""

from typing import Protocol

SERVICE_NAMES = frozenset(
    {
        "auction",
        "prize",
        "result_rewards",
        "match_economy",
        "ratings",
        "character_profiles",
        "events",
        "event_cards",
        "event_results",
    }
)


class StateStore(Protocol):
    def load(self) -> dict | None: ...

    def save(self, snapshot: dict) -> None: ...

    def save_accounts(self, accounts: dict) -> None: ...

    def save_changes(self, accounts: dict, services: dict) -> None: ...
