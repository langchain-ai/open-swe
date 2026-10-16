"""Expedited cards wait for the author to mark drafts ready"""

from alembic import op

revision = "a9b72c4a6ad1"
down_revision = "0a3a4d007239"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE expedited_approval ADD COLUMN awaiting_ready boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    raise NotImplementedError
