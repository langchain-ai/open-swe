"""Route bug reports from workspace Slack channels to a kitchen

A bound channel may name a kitchen channel of its workspace: Open SWE then
starts a thread there for each bug report posted in the channel. Off by
default, and cleared when the kitchen's binding goes.
"""

from alembic import op

revision = "781d3dca85a0"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE workspace_slack_channel
        ADD COLUMN bug_report_kitchen_id text
            REFERENCES workspace_slack_channel (channel_id) ON DELETE SET NULL,
        ADD CONSTRAINT workspace_slack_channel_bug_reports_leave
            CHECK (bug_report_kitchen_id <> channel_id)
    """)


def downgrade() -> None:
    raise NotImplementedError
