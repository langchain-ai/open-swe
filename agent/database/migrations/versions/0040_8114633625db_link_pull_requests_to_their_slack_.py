"""Link pull requests to their Slack trigger and model"""

from alembic import op

revision = "8114633625db"
down_revision = ["b306ce224014", "d66151b3273b", "e0af88280850", "e2e5dd945e52"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE pull_request "
        "ADD COLUMN slack_team_id text NOT NULL DEFAULT '', "
        "ADD COLUMN slack_channel_id text NOT NULL DEFAULT '', "
        "ADD COLUMN slack_thread_ts text NOT NULL DEFAULT '', "
        "ADD COLUMN slack_message_ts text NOT NULL DEFAULT '', "
        "ADD COLUMN opening_model_id text NOT NULL DEFAULT '', "
        "ADD COLUMN opening_effort text NOT NULL DEFAULT '', "
        "ADD COLUMN langsmith_run_id text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    raise NotImplementedError
