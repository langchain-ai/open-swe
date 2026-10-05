"""Index analytics retention cutoffs"""

from alembic import op

revision = "1aa8c2dab8c8"
down_revision = "f8280ead09c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "CREATE INDEX outbox_acknowledged_at_idx ON outbox (acknowledged_at) "
        "WHERE state = 'acknowledged'"
    )
    op.execute("CREATE INDEX events_occurred_at_idx ON events (occurred_at)")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP INDEX events_occurred_at_idx")
    op.execute("DROP INDEX outbox_acknowledged_at_idx")
