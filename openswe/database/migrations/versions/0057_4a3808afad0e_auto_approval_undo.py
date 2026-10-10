"""Auto approval undo: who withdrew Open SWE's automatic approval of a pull request, and why."""

from alembic import op

revision = "4a3808afad0e"
down_revision = ["9ec28aa0b2a8", "b438344a06b7", "da651a8f67cc"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE auto_approval_undo (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            github_review_id bigint NOT NULL,
            justification text NOT NULL,
            undone_by_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            request_id uuid REFERENCES human_review_request (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(
        "CREATE INDEX auto_approval_undo_pull_request_idx ON auto_approval_undo (pull_request_id)"
    )
    op.execute("CREATE INDEX auto_approval_undo_request_idx ON auto_approval_undo (request_id)")


def downgrade() -> None:
    raise NotImplementedError
