"""Interactive reviews in any thread: a Slack thread, or the dashboard with no Slack at all."""

from alembic import op

revision = "97b42efe443b"
down_revision = ["c4f9b8528cee", "51127aaa9d2e", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every guide so far ran in a code channel, which is one session keyed at ts "0".
    op.execute(
        "ALTER TABLE review_guide_session ADD COLUMN slack_thread_ts text NOT NULL DEFAULT '0'"
    )


def downgrade() -> None:
    raise NotImplementedError
