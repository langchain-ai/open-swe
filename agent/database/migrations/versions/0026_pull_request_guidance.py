"""Where the author steered a pull request, as the reviewer verified it."""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pull_request_guidance (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            quote_hash text NOT NULL,
            quote text NOT NULL,
            summary text NOT NULL,
            author text NOT NULL DEFAULT '',
            turn_index integer,
            reviewer_thread_id text NOT NULL DEFAULT '',
            head_sha text NOT NULL DEFAULT '',
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    # A re-review starts from the same messages and re-derives points it already
    # recorded, so the quote is the identity: the newest verdict replaces the
    # old row rather than accumulating one per push.
    op.execute(
        """
        CREATE UNIQUE INDEX pull_request_guidance_quote_idx
            ON pull_request_guidance (pull_request_id, quote_hash)
        """
    )

    op.execute(
        """
        CREATE INDEX pull_request_guidance_pr_idx
            ON pull_request_guidance (pull_request_id, turn_index, id)
        """
    )

    # The commit the last completed review stands behind. A point is a claim
    # about one commit, so this is what decides which points are still true —
    # including when a review recognises none, which writes no point at all.
    op.execute(
        """
        CREATE TABLE pull_request_guidance_review (
            pull_request_id uuid PRIMARY KEY REFERENCES pull_request (id) ON DELETE CASCADE,
            head_sha text NOT NULL,
            completed_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
