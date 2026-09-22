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
            kind text NOT NULL CHECK (
                kind IN ('correction', 'constraint', 'direction', 'preference')
            ),
            file text NOT NULL,
            start_line integer,
            author text NOT NULL DEFAULT '',
            occurred_at timestamptz,
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
            ON pull_request_guidance (pull_request_id, occurred_at, id)
        """
    )


def downgrade() -> None:
    raise NotImplementedError
