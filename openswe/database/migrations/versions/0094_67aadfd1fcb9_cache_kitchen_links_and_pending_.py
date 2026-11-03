"""Cache kitchen links and pending breakout choices"""

from alembic import op

revision = "67aadfd1fcb9"
down_revision = ["c4f9b8528cee", "51127aaa9d2e", "53b42abdbed7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE slack_kitchen_link (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL REFERENCES slack_channel(id) ON DELETE CASCADE,
            kitchen_channel_id text REFERENCES slack_channel(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX slack_kitchen_link_channel ON slack_kitchen_link(channel_id)
    """)
    op.execute("""
        CREATE TABLE slack_breakout_choice (
            id uuid PRIMARY KEY,
            channel_id text NOT NULL REFERENCES slack_channel(id) ON DELETE CASCADE,
            thread_ts text NOT NULL,
            destination_channel_ids jsonb NOT NULL,
            breakout_command jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE INDEX slack_breakout_choice_created_at ON slack_breakout_choice(created_at)
    """)


def downgrade() -> None:
    raise NotImplementedError
