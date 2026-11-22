"""Retain verified identity ownership in the same commit as its game account."""

from alembic import op

revision = "0003_identities"
down_revision = "0002_admin_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE service_state DROP CONSTRAINT service_state_name_check")
    op.execute("""
        ALTER TABLE service_state ADD CONSTRAINT service_state_name_check CHECK (name IN (
            'auction', 'prize', 'result_rewards', 'match_economy', 'ratings',
            'character_profiles', 'events', 'event_cards', 'event_results', 'admin_audit', 'identities'
        ))
    """)

