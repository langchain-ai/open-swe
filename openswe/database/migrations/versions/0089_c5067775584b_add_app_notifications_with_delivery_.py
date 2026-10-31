"""Add app notifications with delivery receipts."""

from alembic import op

revision = "c5067775584b"
down_revision = "c664a63ef3bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""
        CREATE TABLE app_notification (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
            recipient_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            dedupe_key text NOT NULL,
            title text NOT NULL,
            body text NOT NULL,
            url text NOT NULL,
            fallback_text text NOT NULL,
            origin jsonb,
            fallback_at timestamptz NOT NULL,
            delivered_at timestamptz,
            fallback_sent_at timestamptz
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX app_notification_dedupe ON app_notification (recipient_id, dedupe_key)"
    )
    op.execute(
        "CREATE INDEX app_notification_pending ON app_notification (fallback_at) WHERE delivered_at IS NULL AND fallback_sent_at IS NULL"
    )


def downgrade() -> None:
    raise NotImplementedError
