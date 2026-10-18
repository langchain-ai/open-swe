"""Artifact comment replies"""

import sqlalchemy as sa
from alembic import op

revision = "11b8f4073839"
down_revision = "9f867d4517fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("thread_plan_comment", sa.Column("parent_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        "thread_plan_comment_parent_fk",
        "thread_plan_comment",
        "thread_plan_comment",
        ["thread_id", "parent_id"],
        ["thread_id", "id"],
        ondelete="SET NULL (parent_id)",
    )


def downgrade() -> None:
    raise NotImplementedError
