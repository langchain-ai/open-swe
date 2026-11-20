"""Add announcement approvals"""

from alembic import op

revision = "758a156e43d0"
down_revision = "3d2a1cb2c1bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE announcement_delivery ADD COLUMN slack_user_id text")
    op.execute("""
        CREATE TABLE announcement_approval (
            id uuid PRIMARY KEY,
            thread_id text NOT NULL,
            announcement_id uuid NOT NULL UNIQUE REFERENCES announcement (id) ON DELETE CASCADE,
            recipient_count integer NOT NULL,
            payload_digest text NOT NULL,
            status text NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users (id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users (id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE INDEX announcement_approval_thread_idx ON announcement_approval (thread_id)")
    op.execute(
        "CREATE INDEX announcement_approval_creator_idx ON announcement_approval (created_by)"
    )
    op.execute(
        "CREATE INDEX announcement_approval_updater_idx ON announcement_approval (updated_by)"
    )


def downgrade() -> None:
    raise NotImplementedError
