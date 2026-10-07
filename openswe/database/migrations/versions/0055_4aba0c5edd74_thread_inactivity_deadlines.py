"""Thread inactivity deadlines"""

from alembic import op

revision = "4aba0c5edd74"
down_revision = ["6f06bbadfc02", "e74e583ea10e"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE thread_inactivity (
            thread_id text PRIMARY KEY REFERENCES thread (thread_id) ON DELETE CASCADE,
            last_activity_at timestamptz NOT NULL,
            due_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX thread_inactivity_due_idx ON thread_inactivity (due_at)")
    op.execute(
        "COMMENT ON TABLE thread_inactivity IS 'One pending thread_inactive event per idle "
        "thread: armed when its last open turn ends, cleared when a turn starts or the event "
        "is emitted.'"
    )


def downgrade() -> None:
    raise NotImplementedError
