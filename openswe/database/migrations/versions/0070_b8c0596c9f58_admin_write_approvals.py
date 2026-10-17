"""Admin write approvals"""

from alembic import op

revision = "b8c0596c9f58"
down_revision = "c8d8a5c65df3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE admin_write_approval (
            id uuid PRIMARY KEY,
            thread_id text NOT NULL UNIQUE,
            owner_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            approval jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
