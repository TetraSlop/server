"""Alembic runs on the connection supplied by database.py."""

from alembic import context

from tmserver.database_schema import metadata

context.configure(connection=context.config.attributes["connection"], target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
