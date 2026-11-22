"""Allow the administration audit journal alongside its jointly committed awards.

Revision ID: 0002_admin_audit
"""

from alembic import op

revision = "0002_admin_audit"
down_revision = "0001_state"
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL, independent of future changes to the runtime journal catalog.
    op.execute("ALTER TABLE service_state DROP CONSTRAINT service_state_name_check")
    op.execute("""
        ALTER TABLE service_state ADD CONSTRAINT service_state_name_check CHECK (name IN (
            'auction', 'prize', 'result_rewards', 'match_economy', 'ratings',
            'character_profiles', 'events', 'event_cards', 'event_results', 'admin_audit'
        ))
    """)

