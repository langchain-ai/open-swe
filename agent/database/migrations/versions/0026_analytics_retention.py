"""Coordinate retention across replicas."""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE analytics_retention_schedule (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            next_run_at timestamptz NOT NULL DEFAULT '-infinity'
        )
    """)
    op.execute("INSERT INTO analytics_retention_schedule DEFAULT VALUES")


def downgrade() -> None:
    raise NotImplementedError
