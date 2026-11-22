"""Saved-state validation, the PostgreSQL store and its migrations."""


def open_store(url):
    """Open the PostgreSQL writer; the caller owns and must close it."""
    try:
        from tmserver.storage.postgres import PostgresStateStore
    except ImportError:
        raise ValueError('PostgreSQL storage requires installation with ".[postgres]"') from None
    return PostgresStateStore(url)
