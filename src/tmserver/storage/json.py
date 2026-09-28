"""The save boundary shared by the legacy JSON and PostgreSQL backends."""

import hashlib
import json
import os
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
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


_UNREAD = object()


@contextmanager
def _writer_lock(path):
    """Serialize cooperative local writers across the compare/replace boundary.

    Keep this empty sidecar in place: unlinking it could split concurrent locks
    across different files. Closing the descriptor releases the operating-system
    lock, including when the process exits unexpectedly.
    """
    with path.with_name(path.name + ".lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


@contextmanager
def writer_lease(path):
    """Own a local JSON economy until service or offline administration ends.

    This cooperative lifetime lease is separate from each store commit lock.
    Keep its stable .writer.lock sidecar in place; process exit releases the
    operating-system lock without needing stale-owner files or timeouts.
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as resources:
        try:
            resources.enter_context(_writer_lock(path.with_name(path.name + ".writer")))
        except OSError as exc:
            raise OSError(
                f"Cannot acquire JSON writer lease for {path}; another server or admin "
                "command may be using this save."
            ) from exc
        yield


class JsonStateStore:
    """Local atomic snapshots with optimistic, fail-closed writer checks.

    A store remembers the file it loaded or committed. It never merges external
    edits into a running economy or recreates a save that disappeared. Failed
    stores must be discarded along with the server's in-memory state.
    """

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.failed = False
        self._fingerprint = _UNREAD

    @contextmanager
    def _operation(self):
        if self.failed:
            raise OSError("JSON writer failed; restart is required")
        try:
            yield
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.failed = True
            raise OSError("JSON save operation failed; restart is required") from exc

    def _load(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            raw = None
        fingerprint = None if raw is None else hashlib.sha256(raw).digest()
        if self._fingerprint is not _UNREAD and fingerprint != self._fingerprint:
            raise OSError("JSON save changed or disappeared outside this writer")
        snapshot = None if raw is None else json.loads(raw)
        if raw is not None and not isinstance(snapshot, dict):
            raise ValueError("JSON save must contain a snapshot object")
        self._fingerprint = fingerprint
        return snapshot

    def load(self):
        with self._operation():
            return self._load()

    def save(self, snapshot):
        with self._operation(), _writer_lock(self.path):
            current = self._load()
            if not isinstance(snapshot, dict):
                raise TypeError("JSON save must contain a snapshot object")
            if current == snapshot:
                return
            raw = (json.dumps(snapshot, indent=2) + "\n").encode("utf8")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.path.parent,
                    prefix=self.path.name + ".",
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
                if os.name != "nt":
                    # Persist the rename as well as the file contents on POSIX.
                    directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                self._fingerprint = hashlib.sha256(raw).digest()
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def save_accounts(self, accounts):
        self.save_changes(accounts, {})

    def save_changes(self, accounts, services):
        # JSON has one physical commit unit. Preserve every other account and
        # journal from the last complete file, without scanning live services.
        with self._operation():
            if set(services) - SERVICE_NAMES:
                raise ValueError("unsupported service journals")
            snapshot = self._load()
            if snapshot is None:
                snapshot = {"version": 1, "accounts": {}}
            if snapshot["version"] != 1:
                raise ValueError("unsupported JSON save version")
            if not isinstance(snapshot["accounts"], dict):
                raise TypeError("JSON accounts must be an object")
            snapshot["accounts"].update(accounts)
            snapshot.update(services)
            self.save(snapshot)
