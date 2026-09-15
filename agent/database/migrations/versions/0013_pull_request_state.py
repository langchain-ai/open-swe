"""Pull request state: merge/head columns plus per-head checks and review threads.

Enough of GitHub's answer is stored that the dashboard can serve a PR's health
without a live fan-out, and ``github_updated_at`` orders the writers so an
out-of-order webhook cannot undo a newer one.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pull_request
            ADD COLUMN draft boolean NOT NULL DEFAULT false,
            ADD COLUMN head_sha text NOT NULL DEFAULT '',
            ADD COLUMN base_sha text NOT NULL DEFAULT '',
            ADD COLUMN mergeable_state text NOT NULL DEFAULT '',
            ADD COLUMN merged_at timestamptz,
            ADD COLUMN closed_at timestamptz,
            ADD COLUMN github_updated_at timestamptz,
            ADD COLUMN last_synced_at timestamptz
        """
    )

    op.execute(
        """
        CREATE INDEX pull_request_stale_sync_idx
            ON pull_request (last_synced_at NULLS FIRST)
            WHERE state IN ('open', 'draft')
        """
    )

    op.execute(
        """
        CREATE TABLE pull_request_check (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            head_sha text NOT NULL,
            kind text NOT NULL CHECK (kind IN ('check_run', 'status')),
            external_id text NOT NULL,
            name text NOT NULL DEFAULT '',
            status text NOT NULL DEFAULT '',
            conclusion text NOT NULL DEFAULT '',
            details_url text NOT NULL DEFAULT '',
            github_updated_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (pull_request_id, head_sha, kind, external_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX pull_request_check_head_idx
            ON pull_request_check (pull_request_id, head_sha)
        """
    )

    op.execute(
        """
        CREATE TABLE pull_request_review_thread (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            node_id text NOT NULL,
            is_resolved boolean NOT NULL DEFAULT false,
            path text NOT NULL DEFAULT '',
            line integer,
            author text NOT NULL DEFAULT '',
            body text NOT NULL DEFAULT '',
            url text NOT NULL DEFAULT '',
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (pull_request_id, node_id)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
