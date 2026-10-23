"""Concierge background threads and Slack timezones"""

from alembic import op

revision = "f27418fe107a"
down_revision = "a7c12a7f1462"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The Slack member's IANA timezone, refreshed from their profile on each message.
    op.execute("ALTER TABLE user_identity ADD COLUMN timezone text NOT NULL DEFAULT ''")
    op.execute(
        """
        CREATE TABLE concierge_background (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            thread_id text,
            dm_thread_id text,
            instructions text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX concierge_background_user_idx ON concierge_background (user_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX concierge_background_thread_idx ON concierge_background (thread_id)"
    )


def downgrade() -> None:
    raise NotImplementedError
