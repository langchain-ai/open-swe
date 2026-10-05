"""Cover event log kind aggregation"""

from alembic import op

revision = "d38f102fe528"
down_revision = "f8280ead09c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE event_log ADD COLUMN action text GENERATED ALWAYS AS "
        "(COALESCE(payload->>'action', '')) STORED"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE event_log DROP COLUMN action")
