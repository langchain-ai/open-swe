"""Ensure the UI invalidation outbox exists"""

from alembic import op

revision = "3d321fd486ec"
down_revision = ["51b34e89263b", "f4030c2b06bc"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A database stamped past 1c56fc371df9 without running it has no outbox; elsewhere a no-op.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ui_invalidation (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            topic text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ui_invalidation_topic_idx ON ui_invalidation (topic, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ui_invalidation_created_idx ON ui_invalidation (created_at)"
    )


def downgrade() -> None:
    raise NotImplementedError
