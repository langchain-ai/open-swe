"""Remember who created an automation by user id"""

from alembic import op

revision = "0aae6a97dd3b"
down_revision = "4fad0d9bf9eb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE automation ADD COLUMN created_by_user_id uuid "
        "REFERENCES users (id) ON DELETE SET NULL"
    )
    op.execute(
        """
        UPDATE automation a SET created_by_user_id = (
            SELECT i.user_id FROM user_identity i
            WHERE i.provider = 'github' AND lower(i.login) = lower(a.created_by)
            ORDER BY i.last_seen_at DESC LIMIT 1
        )
        WHERE a.created_by IS NOT NULL
        """
    )


def downgrade() -> None:
    raise NotImplementedError
