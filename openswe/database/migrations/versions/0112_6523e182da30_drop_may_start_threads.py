"""Drop integration_repository.may_start_threads.

GitHub Actions workflows no longer start threads with their OIDC tokens, so
nothing reads the per-repository grant that allowed it.
"""

from alembic import op

revision = "6523e182da30"
down_revision = ["59c9a4be00db", "758a156e43d0", "5123042028cf", "2d6d31428885"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE integration_repository DROP COLUMN IF EXISTS may_start_threads")


def downgrade() -> None:
    raise NotImplementedError
