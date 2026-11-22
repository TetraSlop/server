"""Atomic saves for one game-server writer.

All affected accounts and service journals commit together. A session advisory
lock fences other application writers. We never reconnect a failed writer:
its in-memory state must be discarded and reloaded by a fresh server.
"""

from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from tmserver.storage.schema import (
    CARD_FIELDS,
    SCHEMA_REVISION,
    WRITER_LOCK,
    accounts,
    cards,
    service_state,
    state_meta,
)
from tmserver.storage.validation import ACCOUNT_FIELDS, SERVICE_NAMES


def database_engine(url):
    parsed = sa.engine.make_url(url)
    if parsed.drivername not in ("postgresql", "postgresql+psycopg"):
        raise ValueError("use a postgresql+psycopg database URL")
    return sa.create_engine(
        parsed.set(drivername="postgresql+psycopg"),
        poolclass=NullPool,
        isolation_level="REPEATABLE READ",
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=10000 -c lock_timeout=5000",
        },
    )


class PostgresStateStore(AbstractContextManager):
    """Caller owns this context and must keep it open for the server's lifetime."""

    def __init__(self, url):
        self.engine = database_engine(url)
        self.connection = None
        try:
            self.connection = self.engine.connect()
            with self.connection.begin():
                if not self.connection.scalar(
                    sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": WRITER_LOCK}
                ):
                    raise OSError("another Tetra Master writer owns this database")
                revision = (
                    self.connection.execute(sa.text("SELECT version_num FROM alembic_version"))
                    .scalars()
                    .all()
                )
                if revision != [SCHEMA_REVISION]:
                    raise OSError("database schema is not current; run tmserver-db upgrade")
                self.revision = self.connection.scalar(sa.select(state_meta.c.revision))
                if self.revision is None:
                    raise OSError("database metadata is missing")
            self.snapshot = self._read()
        except (SQLAlchemyError, OSError) as exc:
            self.close()
            if isinstance(exc, OSError):
                raise
            raise OSError(
                "cannot open database; check connectivity and run tmserver-db upgrade"
            ) from exc

    @contextmanager
    def _writer(self, failure):
        """Fail closed: any error closes the writer, so it never reconnects with old memory."""
        if self.connection is None:
            raise OSError("database writer is closed or failed; restart is required")
        try:
            yield
        except (SQLAlchemyError, OSError, ValueError, KeyError, TypeError) as exc:
            self.close()
            raise OSError(failure + "; restart is required") from exc

    def _read(self):
        with self.connection.begin():
            meta = self.connection.execute(sa.select(state_meta)).mappings().one()
            if meta["revision"] != self.revision:
                raise OSError("database revision changed outside this writer")
            if meta["snapshot_version"] is None:
                return None
            snapshot = {"version": meta["snapshot_version"], "accounts": {}}
            for row in self.connection.execute(
                sa.select(accounts).order_by(accounts.c.key)
            ).mappings():
                snapshot["accounts"][row["key"]] = {
                    "nn": int(row["nn"]),
                    "player_id": row["player_id"],
                    "name_hex": bytes(row["name"]).hex(),
                    "guild": row["guild"],
                    "balance": row["balance"],
                    "deck_names": row["deck_names"],
                    "template_hex": bytes(row["template"]).hex(),
                    "last_session": row["last_session"],
                    "cards": [],
                }
            for row in self.connection.execute(
                sa.select(cards).order_by(cards.c.account_key, cards.c.slot)
            ).mappings():
                inventory = snapshot["accounts"][row["account_key"]]["cards"]
                if row["slot"] != len(inventory):
                    raise OSError("database inventory slots are not contiguous")
                inventory.append([row[field] for field in CARD_FIELDS])
            for name, payload in self.connection.execute(sa.select(service_state)):
                snapshot[name] = payload
            return snapshot

    def load(self):
        with self._writer("database read failed"):
            return self._read()

    def save(self, snapshot):
        """Commit every account and journal in snapshot; rows it leaves out are kept."""
        with self._writer("database save failed"):
            if set(snapshot) - (SERVICE_NAMES | {"version", "accounts"}) or snapshot["version"] != 1:
                raise ValueError("unsupported snapshot fields or version")
            services = {name: snapshot[name] for name in SERVICE_NAMES if name in snapshot}
            # A whole snapshot creates the saved state even when it has no rows yet.
            self._commit(snapshot["accounts"], services, create=True)

    def save_changes(self, updates, services):
        """Atomically replace supplied accounts and journals; retain all others."""
        with self._writer("database account/journal commit failed"):
            if set(services) - SERVICE_NAMES:
                raise ValueError("unsupported service journals")
            self._commit(updates, services)

    def _commit(self, updates, services, *, create=False):
        baseline = self.snapshot or {"accounts": {}}
        previous = baseline["accounts"]
        # Copy before committing: callers cannot subsequently mutate our baseline.
        changed = {
            key: deepcopy(account)
            for key, account in updates.items()
            if account != previous.get(key)
        }
        journals = {
            name: deepcopy(payload)
            for name, payload in services.items()
            if name not in baseline or payload != baseline[name]
        }
        with self.connection.begin():
            if not changed and not journals and not (create and self.snapshot is None):
                # Even a no-op must notice a lost connection or outside writer.
                self._check_revision()
                return
            self._advance_revision()
            for key, account in sorted(changed.items()):
                self._save_account(key, account, previous.get(key))
            self._save_services(journals)
        if self.snapshot is None:
            self.snapshot = {"version": 1, "accounts": {}}
        self.snapshot["accounts"].update(changed)
        self.snapshot.update(journals)
        self.revision += 1

    def _check_revision(self):
        if self.connection.scalar(sa.select(state_meta.c.revision)) != self.revision:
            raise OSError("database revision changed outside this writer")

    def _advance_revision(self):
        updated = self.connection.execute(
            state_meta.update()
            .where(state_meta.c.id == 1, state_meta.c.revision == self.revision)
            .values(revision=self.revision + 1, snapshot_version=1)
        )
        if updated.rowcount != 1:
            raise OSError("database revision changed outside this writer")

    def _save_account(self, key, account, old):
        if set(account) != ACCOUNT_FIELDS:
            raise ValueError("unsupported account fields")
        values = {
            field: account[field]
            for field in (
                "nn",
                "player_id",
                "guild",
                "balance",
                "deck_names",
                "last_session",
            )
        }
        values.update(
            name=bytes.fromhex(account["name_hex"]),
            template=bytes.fromhex(account["template_hex"]),
        )
        if old is None:
            self.connection.execute(accounts.insert().values(key=key, **values))
        else:
            self.connection.execute(accounts.update().where(accounts.c.key == key).values(**values))
        if old is None or account["cards"] != old["cards"]:
            self.connection.execute(cards.delete().where(cards.c.account_key == key))
            rows = [
                {
                    "account_key": key,
                    "slot": slot,
                    **dict(zip(CARD_FIELDS, card, strict=True)),
                }
                for slot, card in enumerate(account["cards"])
            ]
            if rows:
                self.connection.execute(cards.insert(), rows)

    def _save_services(self, journals):
        for name, payload in sorted(journals.items()):
            insert = postgresql.insert(service_state).values(name=name, payload=payload)
            self.connection.execute(
                insert.on_conflict_do_update(
                    index_elements=[service_state.c.name], set_={"payload": insert.excluded.payload}
                )
            )

    def close(self):
        try:
            if self.connection is not None:
                self.connection.close()
        finally:
            self.connection = None
            self.engine.dispose()

    def __exit__(self, *exc):
        self.close()
