"""Track task tool admissions without held connections"""

from alembic import op

revision = "6803741a06a6"
down_revision = "2316c218f0c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE task_tool_admission (
            id uuid PRIMARY KEY,
            thread_id text NOT NULL,
            exclusive boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ON task_tool_admission (thread_id)")


def downgrade() -> None:
    raise NotImplementedError
