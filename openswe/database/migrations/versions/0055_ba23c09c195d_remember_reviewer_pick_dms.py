"""Remember reviewer pick DMs"""

from alembic import op

revision = "ba23c09c195d"
down_revision = ["6f06bbadfc02", "a823229a905f"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE human_review_participant "
        "ADD COLUMN slack_pick_messages JSONB NOT NULL DEFAULT '[]'"
    )


def downgrade() -> None:
    raise NotImplementedError
