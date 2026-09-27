"""Offline PostgreSQL migration, JSON import and JSON export.

Set TM_DATABASE_URL; this tool never launches the game or starts protocol ports.
Every command takes the same writer lock as the game server.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from tmserver.database_schema import WRITER_LOCK
from tmserver.postgres_store import PostgresStateStore, database_engine
from tmserver.snapshot_validation import validate_snapshot


def upgrade(url):
    engine = database_engine(url)
    try:
        with engine.begin() as connection:
            if not connection.scalar(
                sa.text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": WRITER_LOCK}
            ):
                raise OSError("stop the game server before migrating its database")
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def import_json(store, path, *, event_configs=()):
    if store.load() is not None:
        raise ValueError("import requires an empty database; existing state is never replaced")
    raw = Path(path).read_bytes()
    snapshot = json.loads(raw)
    source = validate_snapshot(snapshot, event_configs=event_configs)
    digest = hashlib.sha256(raw).hexdigest()
    store.save(snapshot, import_sha256=digest)
    # Reload from SQL, not from the writer's snapshot cache.
    restored = validate_snapshot(store.load(), event_configs=event_configs)
    for key in snapshot["accounts"]:
        if source.profile_bytes(key) != restored.profile_bytes(key):
            raise OSError("imported native profile differs from its source")
    return digest


def export_json(store, path):
    snapshot = store.load()
    if snapshot is None:
        raise ValueError("database has no saved state")
    # Exclusive creation protects both the old save and any previous export.
    with Path(path).open("x", encoding="utf8") as stream:
        stream.write(json.dumps(snapshot, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("TM_DATABASE_URL"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("upgrade")
    importing = commands.add_parser("import-json")
    importing.add_argument("path", type=Path)
    importing.add_argument(
        "--events", type=Path, help="same event configuration used by the server"
    )
    commands.add_parser("export-json").add_argument("path", type=Path)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("set TM_DATABASE_URL or supply --database-url")
    if args.command == "upgrade":
        upgrade(args.database_url)
        print("Database schema is current.")
    else:
        with PostgresStateStore(args.database_url) as store:
            if args.command == "import-json":
                from tmserver.event_configuration import load_event_setup

                configs = load_event_setup(args.events)[0] if args.events else ()
                digest = import_json(store, args.path, event_configs=configs)
                print(f"Imported and verified native profiles. Source SHA-256: {digest}")
            else:
                export_json(store, args.path)
                print(f"Exported saved state to {args.path}.")


def cli():
    try:
        main()
    except sa.exc.SQLAlchemyError:
        raise SystemExit(
            "Database command failed; check connectivity and schema permissions."
        ) from None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    cli()
