"""Human-approved PR media upload requests.

One row is one content-addressed upload request: exact bytes (SHA-256), target
repository id, pull request number, and expiry. Approving is a single-statement
compare-and-set (``pending -> approved``), so two concurrent approvers cannot
both claim execution.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pr_media_request (
            fingerprint text PRIMARY KEY,
            thread_id text NOT NULL,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'completed', 'failed')),
            owner text NOT NULL,
            repo text NOT NULL,
            repo_id bigint NOT NULL,
            pull_number integer NOT NULL CHECK (pull_number > 0),
            pull_title text NOT NULL DEFAULT '',
            file_name text NOT NULL,
            content_type text NOT NULL,
            size_bytes bigint NOT NULL CHECK (size_bytes > 0),
            digest text NOT NULL,
            media_base64 text NOT NULL,
            requested_by text NOT NULL DEFAULT '',
            requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at_epoch bigint NOT NULL,
            decided_by text,
            decided_at timestamptz,
            asset_url text,
            error text
        )
        """
    )

    op.execute(
        """
        CREATE INDEX pr_media_request_thread_idx
            ON pr_media_request (thread_id, requested_at DESC)
        """
    )


def downgrade() -> None:
    raise NotImplementedError
