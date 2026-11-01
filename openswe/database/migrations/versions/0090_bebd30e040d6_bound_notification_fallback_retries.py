"""Bound notification fallback retries"""

from alembic import op

revision = "bebd30e040d6"
down_revision = "c5067775584b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE app_notification ADD COLUMN fallback_attempts integer NOT NULL DEFAULT 0, ADD COLUMN fallback_failed_at timestamptz, ADD COLUMN fallback_claim uuid"
    )
    op.execute("DROP INDEX app_notification_pending")
    op.execute(
        "CREATE INDEX app_notification_pending ON app_notification (fallback_at) WHERE delivered_at IS NULL AND fallback_sent_at IS NULL AND fallback_failed_at IS NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
