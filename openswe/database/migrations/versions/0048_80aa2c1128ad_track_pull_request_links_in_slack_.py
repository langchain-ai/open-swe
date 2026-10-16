"""Track pull request links in Slack threads"""

from alembic import op

revision = "80aa2c1128ad"
down_revision = "61e8a2c88c54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE slack_pull_request_link (
            team_id text NOT NULL,
            channel_id text NOT NULL,
            thread_ts text NOT NULL,
            pr_url text NOT NULL,
            message_ts text NOT NULL,
            first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (team_id, channel_id, thread_ts, pr_url)
        )
        """
    )
    op.execute("CREATE INDEX slack_pull_request_link_pr_idx ON slack_pull_request_link (pr_url)")
    op.execute(
        "COMMENT ON TABLE slack_pull_request_link IS "
        "'Passive, deduplicated PR links observed in Slack threads; does not start agent runs.'"
    )


def downgrade() -> None:
    raise NotImplementedError
