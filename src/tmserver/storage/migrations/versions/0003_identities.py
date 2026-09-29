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


def downgrade():
    retained = op.get_bind().exec_driver_sql("""
        SELECT EXISTS (
            SELECT 1 FROM service_state WHERE name = 'identities'
            AND payload NOT IN ('null'::jsonb, '{"version":1,"links":[]}'::jsonb)
        )
    """).scalar_one()
    if retained:
        raise RuntimeError("cannot downgrade while identity ownership links are retained")
    op.execute("DELETE FROM service_state WHERE name = 'identities'")
    op.execute("ALTER TABLE service_state DROP CONSTRAINT service_state_name_check")
    op.execute("""
        ALTER TABLE service_state ADD CONSTRAINT service_state_name_check CHECK (name IN (
            'auction', 'prize', 'result_rewards', 'match_economy', 'ratings',
            'character_profiles', 'events', 'event_cards', 'event_results', 'admin_audit'
        ))
    """)
