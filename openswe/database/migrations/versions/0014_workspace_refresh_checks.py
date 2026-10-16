"""Constrain ``refresh_status`` and ``refresh_kind`` to the values the record model allows.

``snapshot_status`` was created with a check in 0013; these two were not, so a
stray value could be written and then fail validation on the way back out.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE workspace
            ADD CONSTRAINT workspace_refresh_status_check
            CHECK (refresh_status IN ('never', 'refreshing', 'success', 'failed'))
        """
    )

    # Nullable: a workspace that has never been refreshed names no kind.
    op.execute(
        """
        ALTER TABLE workspace
            ADD CONSTRAINT workspace_refresh_kind_check
            CHECK (refresh_kind IN ('full', 'update'))
        """
    )


def downgrade() -> None:
    raise NotImplementedError
