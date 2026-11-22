"""Offline PostgreSQL schema migration.

Set TM_DATABASE_URL; this tool never launches the game or starts protocol ports.
It takes the same writer lock as the game server.
"""

import argparse
import os
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from tmserver.storage.postgres import database_engine
from tmserver.storage.schema import WRITER_LOCK


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("TM_DATABASE_URL"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("upgrade")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("set TM_DATABASE_URL or supply --database-url")
    upgrade(args.database_url)
    print("Database schema is current.")


def cli():
    try:
        main()
    except sa.exc.SQLAlchemyError:
        raise SystemExit(
            "Database command failed; check connectivity and schema permissions."
        ) from None
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    cli()
