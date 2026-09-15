from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE expedited_approval (
            id uuid PRIMARY KEY,
            pull_request_id uuid NOT NULL REFERENCES pull_request (id) ON DELETE CASCADE,
            thread_id text NOT NULL DEFAULT '',
            head_sha text NOT NULL,
            diff_fingerprint text NOT NULL DEFAULT '',
            state text NOT NULL CHECK (
                state IN ('waiting', 'open', 'merging', 'merged', 'rejected', 'superseded', 'failed')
            ),
            detail text NOT NULL DEFAULT '',
            slack_channel_id text NOT NULL DEFAULT '',
            slack_thread_ts text NOT NULL DEFAULT '',
            slack_message_ts text NOT NULL DEFAULT '',
            run_config jsonb NOT NULL DEFAULT '{}'::jsonb,
            cron_id text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX expedited_approval_active_idx
            ON expedited_approval (pull_request_id)
            WHERE state IN ('waiting', 'open', 'merging')
        """
    )

    op.execute(
        """
        CREATE TABLE expedited_approval_vote (
            approval_id uuid NOT NULL REFERENCES expedited_approval (id) ON DELETE CASCADE,
            github_login text NOT NULL,
            slack_user_id text NOT NULL DEFAULT '',
            decision text NOT NULL CHECK (decision IN ('approve', 'reject')),
            github_review_id bigint,
            feedback text NOT NULL DEFAULT '',
            voted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (approval_id, github_login)
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
