"""Per-thread documents.

``thread_document`` holds one JSON document per thread and kind, such as an
incident thread's latest report, its curated history, and its postmortem. It
keys on the thread id without a foreign key, because threads live in the
LangGraph server and a document such as an incident's history outlives the
thread's retention.
"""

from alembic import op

revision = "1f2e15ae1093"
down_revision = ["30ab9d51b9ec", "47544c5c96d8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE thread_document (
            id uuid PRIMARY KEY,
            thread_id text NOT NULL,
            kind text NOT NULL,
            document jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX thread_document_thread_kind ON thread_document (thread_id, kind)"
    )
    op.execute("CREATE INDEX thread_document_kind ON thread_document (kind)")


def downgrade() -> None:
    raise NotImplementedError
