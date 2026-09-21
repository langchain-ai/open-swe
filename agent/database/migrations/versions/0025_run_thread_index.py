"""Index recorded runs for per-thread cost aggregation."""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX run_projection_thread_idx ON run_projection (workspace_id, thread_id)")


def downgrade() -> None:
    op.execute("DROP INDEX run_projection_thread_idx")
