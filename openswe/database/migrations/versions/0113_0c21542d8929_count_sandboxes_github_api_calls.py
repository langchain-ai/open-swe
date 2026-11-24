"""Count sandboxes' GitHub API calls.

Sandboxes on the GitHub App installation's token share its one hourly quota with Robin's
reviews and approvals; each thread's calls are counted by minute so a runaway thread can
be capped over a rolling hour.
"""

from alembic import op

revision = "0c21542d8929"
down_revision = "41745a231a10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sandbox_github_call (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            thread_id text NOT NULL,
            minute timestamptz NOT NULL,
            calls integer NOT NULL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX sandbox_github_call_thread_minute "
        "ON sandbox_github_call (thread_id, minute)"
    )
    op.execute("CREATE INDEX sandbox_github_call_minute ON sandbox_github_call (minute)")
    op.execute(
        "COMMENT ON TABLE sandbox_github_call IS 'GitHub API calls a thread''s sandbox made "
        "on the App installation''s token, by minute; rows older than an hour are purged.'"
    )


def downgrade() -> None:
    raise NotImplementedError
