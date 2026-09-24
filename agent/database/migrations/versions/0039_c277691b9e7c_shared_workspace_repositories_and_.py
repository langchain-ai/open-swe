"""Shared workspace repositories and installation access"""

from alembic import op

revision = "c277691b9e7c"
down_revision = "0a80973775d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace ADD COLUMN all_repositories BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE workspace_repository DROP CONSTRAINT workspace_repository_pkey")
    op.execute("ALTER TABLE workspace_repository ADD PRIMARY KEY (repository_id, workspace_id)")


def downgrade() -> None:
    raise NotImplementedError
