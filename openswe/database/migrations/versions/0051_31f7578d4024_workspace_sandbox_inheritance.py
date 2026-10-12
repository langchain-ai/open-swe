"""Workspace sandbox inheritance"""

from alembic import op

revision = "31f7578d4024"
down_revision = "f8280ead09c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace ADD COLUMN inherit_default_sandbox boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    raise NotImplementedError
