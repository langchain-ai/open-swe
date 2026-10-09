"""Reviewer pick DM card location"""

from alembic import op

revision = "b438344a06b7"
down_revision = ["4ce55eec2786", "42e9e3af43e3", "b58096f6b755", "f11526182620"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE human_review_participant "
        "ADD COLUMN IF NOT EXISTS dm_channel_id text NOT NULL DEFAULT '', "
        "ADD COLUMN IF NOT EXISTS dm_ts text NOT NULL DEFAULT '', "
        "ADD COLUMN IF NOT EXISTS dm_text text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    raise NotImplementedError
