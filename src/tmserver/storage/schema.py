"""Database records. The game domain deliberately does not import this module.

Constraints live only in the frozen migrations; these tables describe columns.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

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
)
service_state = sa.Table(
    "service_state",
    metadata,
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("payload", JSONB, nullable=False),
)
