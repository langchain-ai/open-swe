"""Store the configured review channel for expedited review cards."""

from alembic import op

revision = "e0398fc570ab"
down_revision = "84e4e97b6fec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE human_review_request ADD COLUMN broadcast_channel_id text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE human_review_request DROP COLUMN broadcast_channel_id")
