"""Database records. The game domain deliberately does not import this module."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from tmserver.storage.base import SERVICE_NAMES

SCHEMA_REVISION = "0003_identities"
WRITER_LOCK = 0x54455452414D4153
CARD_FIELDS = (
    "model",
    "attack",
    "kind",
    "physical",
    "magical",
    "points",
    "arrows",
    "deck",
)

metadata = sa.MetaData()
state_meta = sa.Table(
    "state_meta",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("revision", sa.BigInteger, nullable=False),
    sa.Column("snapshot_version", sa.Integer),
    sa.Column("import_sha256", sa.String(64)),
    sa.CheckConstraint("id = 1"),
    sa.CheckConstraint("revision >= 0"),
    sa.CheckConstraint("snapshot_version = 1"),
)
accounts = sa.Table(
    "accounts",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("nn", sa.Numeric(20, 0), nullable=False, unique=True),
    sa.Column("player_id", sa.BigInteger, nullable=False),
    sa.Column("name", sa.LargeBinary, nullable=False),
    sa.Column("guild", sa.Integer, nullable=False),
    sa.Column("balance", sa.Integer, nullable=False),
    sa.Column("deck_names", JSONB, nullable=False),
    sa.Column("template", sa.LargeBinary, nullable=False),
    sa.Column("last_session", sa.SmallInteger, nullable=False),
    sa.CheckConstraint("nn BETWEEN 0 AND 18446744073709551615"),
    sa.CheckConstraint("player_id BETWEEN 0 AND 4294967295"),
    sa.CheckConstraint("guild BETWEEN 0 AND 3"),
    sa.CheckConstraint("length(key) BETWEEN 1 AND 128"),
    sa.CheckConstraint("octet_length(name) <= 16"),
    sa.CheckConstraint("balance BETWEEN 0 AND 99999999"),
    sa.CheckConstraint("last_session BETWEEN 0 AND 255"),
    sa.CheckConstraint("octet_length(template) = 12328"),
)
cards = sa.Table(
    "cards",
    metadata,
    sa.Column(
        "account_key",
        sa.Text,
        sa.ForeignKey("accounts.key", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("slot", sa.SmallInteger, primary_key=True),
    *(sa.Column(field, sa.SmallInteger, nullable=False) for field in CARD_FIELDS),
    sa.CheckConstraint("slot BETWEEN 0 AND 999"),
    sa.CheckConstraint("model BETWEEN 0 AND 249"),
    sa.CheckConstraint("kind BETWEEN 0 AND 3"),
    *(
        sa.CheckConstraint(f"{field} BETWEEN 0 AND 255")
        for field in ("attack", "physical", "magical", "points", "arrows")
    ),
    sa.CheckConstraint("deck BETWEEN 0 AND 49 OR deck = 255"),
)
service_state = sa.Table(
    "service_state",
    metadata,
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("payload", JSONB, nullable=False),
    sa.CheckConstraint("name IN (" + ", ".join(repr(n) for n in sorted(SERVICE_NAMES)) + ")"),
)
