"""Review guide sessions and the changed lines each person has approved in them."""

from alembic import op

revision = "f5e9fd030c0f"
down_revision = "b652546da9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE review_guide_session (
            thread_id text PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            slack_channel_id text NOT NULL,
            workspace_slug text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        "CREATE INDEX review_guide_session_pull_request_idx "
        "ON review_guide_session (pull_request_id)"
    )

    # line_key hashes (path, sign, text) so a line stays seen across rebases and force-pushes.
    op.execute(
        """
        CREATE TABLE review_guide_seen_line (
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            line_key text NOT NULL,
            seen_count integer NOT NULL,
            PRIMARY KEY (user_id, pull_request_id, line_key)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
