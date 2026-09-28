"""Initial relational economy and versioned service journals.

Revision ID: 0001_state
"""

from alembic import op

revision = "0001_state"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL: later model changes must use a new migration.
    op.execute("""
        CREATE TABLE state_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            revision BIGINT NOT NULL CHECK (revision >= 0),
            snapshot_version INTEGER CHECK (snapshot_version = 1),
            import_sha256 VARCHAR(64)
        )
    """)
    op.execute("""
        CREATE TABLE accounts (
            key TEXT PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 128),
            nn NUMERIC(20,0) NOT NULL UNIQUE CHECK (nn BETWEEN 0 AND 18446744073709551615),
            player_id BIGINT NOT NULL CHECK (player_id BETWEEN 0 AND 4294967295),
            name BYTEA NOT NULL CHECK (octet_length(name) <= 16),
            guild INTEGER NOT NULL CHECK (guild BETWEEN 0 AND 3),
            balance INTEGER NOT NULL CHECK (balance BETWEEN 0 AND 99999999),
            deck_names JSONB NOT NULL,
            template BYTEA NOT NULL CHECK (octet_length(template) = 12328),
            last_session SMALLINT NOT NULL CHECK (last_session BETWEEN 0 AND 255)
        )
    """)
    op.execute("""
        CREATE TABLE cards (
            account_key TEXT NOT NULL REFERENCES accounts(key) ON DELETE CASCADE,
            slot SMALLINT NOT NULL CHECK (slot BETWEEN 0 AND 999),
            model SMALLINT NOT NULL CHECK (model BETWEEN 0 AND 249),
            attack SMALLINT NOT NULL CHECK (attack BETWEEN 0 AND 255),
            kind SMALLINT NOT NULL CHECK (kind BETWEEN 0 AND 3),
            physical SMALLINT NOT NULL CHECK (physical BETWEEN 0 AND 255),
            magical SMALLINT NOT NULL CHECK (magical BETWEEN 0 AND 255),
            points SMALLINT NOT NULL CHECK (points BETWEEN 0 AND 255),
            arrows SMALLINT NOT NULL CHECK (arrows BETWEEN 0 AND 255),
            deck SMALLINT NOT NULL CHECK (deck BETWEEN 0 AND 49 OR deck = 255),
            PRIMARY KEY (account_key, slot)
        )
    """)
    op.execute("""
        CREATE TABLE service_state (
            name TEXT PRIMARY KEY CHECK (name IN (
                'auction', 'prize', 'result_rewards', 'match_economy', 'ratings',
                'character_profiles', 'events', 'event_cards', 'event_results'
            )),
            payload JSONB NOT NULL
        )
    """)
    op.execute("INSERT INTO state_meta (id, revision) VALUES (1, 0)")


def downgrade():
    for table in ("service_state", "cards", "accounts", "state_meta"):
        op.drop_table(table)
