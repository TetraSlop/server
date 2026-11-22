"""In-memory stand-in for the PostgreSQL store.

Each commit keeps a JSON round trip of the document, the same values a JSONB or
relational reload returns, so a new Server built on the same store after a
"restart" reloads exactly what was committed. Failure boundaries (lost commit
acknowledgements, rollback, writer fencing) stay on real PostgreSQL.
"""

import json
from unittest.mock import Mock, patch

from tmserver.storage.validation import SERVICE_NAMES

# The CLI requires a URL; tests that patch open_store never connect to it.
DATABASE = ["--database-url", "postgresql+psycopg://tests.invalid/memory"]


def _copy(value):
    return None if value is None else json.loads(json.dumps(value))


class MemoryStore:
    def __init__(self, snapshot=None):
        self.snapshot = _copy(snapshot)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def load(self):
        return _copy(self.snapshot)

    def save(self, snapshot):
        self.snapshot = _copy(snapshot)

    def save_changes(self, accounts, services):
        if set(services) - SERVICE_NAMES:
            raise ValueError("unsupported service journals")
        snapshot = self.load() or {"version": 1, "accounts": {}}
        snapshot["accounts"].update(accounts)
        snapshot.update(services)
        self.snapshot = _copy(snapshot)


def opened_by(module, store=None):
    """Patch module.open_store to hand out store (default: a new MemoryStore)."""
    return patch(f"{module}.open_store", return_value=MemoryStore() if store is None else store)


def failing_commits(message="injected save failure"):
    """Fail every MemoryStore commit with OSError, as a failed PostgreSQL COMMIT would."""
    error = OSError(message)
    return patch.multiple(
        MemoryStore, save=Mock(side_effect=error), save_changes=Mock(side_effect=error)
    )
